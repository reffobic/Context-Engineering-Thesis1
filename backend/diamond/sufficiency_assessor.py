"""
SELF-ROUTE sufficiency assessor. (the 'unanswerable?' diamond)

Asks the local Ollama LLM whether the pruned context chunks are sufficient
to answer the query, or whether escalation to the long-context pipeline is needed.

Returns a dict:
    {
        "sufficient": bool,
        "confidence": "high" | "medium" | "low",
        "reason": str,
        "route": "RAG" | "LC"
    }

Method: combination of LLM-as-a-judge and a score threshold
"""

from __future__ import annotations

import json
import os
import re
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────

OLLAMA_BASE_URL = "http://localhost:11434"


def _default_assessor_model() -> str:
    """Prefer dedicated assessor env; fall back to legacy default (not OLLAMA_MODEL)."""
    return (
        os.environ.get("ASSESSOR_MODEL", "").strip()
        or os.environ.get("OLLAMA_ASSESSOR_MODEL", "").strip()
        or "llama3.2:3b"
    )


DEFAULT_MODEL = "llama3.2:3b"  # import-time fallback; runtime uses _default_assessor_model()
REQUEST_TIMEOUT = 120   # seconds — sufficiency call is short, but give extra space just in case

# Queries whose top-chunk score falls below this are escalated immediately
# without an LLM call (cheap fast-path).
SCORE_SHORTCUT_THRESHOLD = 0.30

# ── prompt ────────────────────────────────────────────────────────────────────

# Binary Self-Route judge (mode=SELF_ROUTE) — sufficiency only.
_SYSTEM = """\
You are a routing judge for a code-search RAG system.
You will be given a user query and a set of retrieved code chunks.
Your job is to decide whether the chunks are sufficient to answer the query.

Reply ONLY with a JSON object — no markdown, no explanation outside the JSON.
Schema:
{
  "sufficient": true | false,
  "confidence": "high" | "medium" | "low",
  "reason": "<one sentence>"
}

Rules:
- sufficient=true  → the chunks contain enough information to answer the query directly
  (return value, quoted lines, named callees, or the asked symbols are present).
- sufficient=false → key information is missing; a broader search is needed.
- Do NOT require a perfect essay — if the answerable facts are in the chunks, mark sufficient=true.
- confidence reflects how certain you are of your judgment.
"""

# GASR post-RAG gate: sufficiency + STRUCTURE|CONTEXT in one call.
_SYSTEM_RAG_GATE = """\
You are a routing judge for a repository code-search RAG system.
You will be given a user query and a set of retrieved code chunks.

Decide in one step:
(1) whether the chunks are sufficient to answer the query;
(2) if not, whether what is missing is STRUCTURE (graph neighbors) or CONTEXT (same-file long context).

Reply ONLY with a JSON object — no markdown, no explanation outside the JSON.
Schema:
{
  "sufficient": true | false,
  "need": "STRUCTURE" | "CONTEXT" | "UNCLEAR" | null,
  "confidence": "high" | "medium" | "low",
  "reason": "<one sentence>"
}

Rules:
- sufficient=true → chunks contain enough to answer directly (return value, quoted lines,
  named callees, or asked symbols present). Set need=null.
- Do NOT require a perfect essay — if the answerable facts are in the chunks, mark sufficient=true.
- sufficient=false → key information is missing. Then set need:
  - STRUCTURE → need callees, callers, imports, or cross-file dependency neighbors
    (e.g. "what does X call", "list neighbor symbols", "trace across files").
  - CONTEXT → need more of the same file(s): surrounding lines, file-top imports,
    full function/class body, "quote nearby lines".
  - UNCLEAR → cannot tell; prefer CONTEXT (safer than a wrong graph hop).
- Prefer CONTEXT when the miss is local to one function/file body.
- Prefer STRUCTURE when the query asks for cross-file calls/deps/neighbors.
- confidence reflects how certain you are of the whole judgment.
"""

_SYSTEM_AFTER_EXPAND = """\
You are a routing judge AFTER a graph-neighbor expand.
Chunks may include seed hits plus graph neighbors (callees/callers/imports).

Reply ONLY with JSON — no markdown outside the JSON.
Schema:
{
  "sufficient": true | false,
  "confidence": "high" | "medium" | "low",
  "reason": "<one sentence>"
}

Rules:
- sufficient=true if the query can be answered from these chunks — especially when it asks
  for callee/caller/neighbor/dependency *symbols* and those symbols (or their definitions)
  appear in the headers or bodies.
- sufficient=true for simple return-value / quote-from-shown-body questions if the body is here.
- sufficient=false only if the asked symbols or facts are still clearly absent.
- Prefer sufficient=true when unsure but relevant neighbor symbols are visible.
"""

_USER_TEMPLATE = """\
QUERY:
{query}

RETRIEVED CHUNKS ({n} chunks):
{chunks}

Is the context sufficient to answer the query? Reply with JSON only.
"""

_USER_RAG_GATE = """\
QUERY:
{query}

RETRIEVED CHUNKS ({n} chunks):
{chunks}

Are the chunks sufficient? If not, is the miss STRUCTURE or CONTEXT? Reply with JSON only.
"""

# Kept for tool registry / ablations; GASR ladder uses assess_rag_gate instead.
_STRUCTURE_SYSTEM = """\
You are a routing judge for a repository code Q&A system.
The current snippets are NOT sufficient. Decide what is missing.

Reply ONLY with JSON — no markdown outside the JSON.
Schema:
{
  "need": "STRUCTURE" | "CONTEXT" | "UNCLEAR",
  "confidence": "high" | "medium" | "low",
  "reason": "<one sentence>"
}

Rules:
- STRUCTURE → need callees, callers, imports, or cross-file dependency *neighbors*
  (examples: "what does X call", "list neighbor symbols", "trace across files").
- CONTEXT → need more of the *same file(s)*: surrounding lines, imports at top of file,
  full function/class body, "quote nearby lines", "what happens in surrounding lines".
- Prefer CONTEXT when the query is about one function/file body and the miss is local text.
- Prefer STRUCTURE when the query explicitly asks for cross-file calls/deps/neighbors.
- UNCLEAR → cannot tell; prefer CONTEXT (same-file stitch is safer than a wrong graph hop).
"""

_STRUCTURE_USER = """\
QUERY:
{query}

CURRENT CHUNKS ({n} chunks):
{chunks}

What is missing — STRUCTURE (graph neighbors) or CONTEXT (same-file long context)? JSON only.
"""

def _format_chunks(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, item in enumerate(items, 1):
        meta   = item.get("metadata") or {}
        path   = (
            item.get("path")
            or meta.get("file")
            or meta.get("file_path")
            or "?"
        )
        symbol = (
            item.get("symbol")
            or meta.get("function")
            or meta.get("name")
            or ""
        )
        header = f"[{i}] {path}" + (f" — {symbol}" if symbol else "")
        body   = (item.get("text") or item.get("code") or "").strip()
        parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


def _top_score(items: list[dict[str, Any]]) -> float | None:
    for key in ("final_score", "hybrid_score", "similarity"):
        v = items[0].get(key) if items else None
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _parse_response(raw: str) -> dict[str, Any]:
    """Extract JSON from the model reply, stripping think-tags and fences."""
    s = raw.strip()

    # strip </think> and everything before it (Qwen-style reasoning)
    for marker in ("</think>", "</Think>"):
        if marker in s:
            s = s.split(marker, 1)[-1].strip()

    # strip markdown fences
    if s.startswith("```"):
        lines = s.splitlines()
        s = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()

    # find first {...}
    m = re.search(r"\{[\s\S]*?\}", s)
    if m:
        s = m.group(0)

    data = json.loads(s)

    sufficient = bool(data.get("sufficient", False))
    confidence = str(data.get("confidence", "low")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    reason = str(data.get("reason", ""))

    return {
        "sufficient": sufficient,
        "confidence": confidence,
        "reason":     reason,
        "route":      "RAG" if sufficient else "LC",
    }


# ── public API ────────────────────────────────────────────────────────────────

def assess_sufficiency(
    query:  str,
    chunks: list[dict[str, Any]],
    model:  str | None = None,
    *,
    phase: str = "rag",
) -> dict[str, Any]:
    """
    Assess whether *chunks* are sufficient to answer *query*.

    Fast-path: if the top chunk score is below SCORE_SHORTCUT_THRESHOLD,
    escalate to LC immediately without an LLM call.

    ``phase="graph"`` uses a post-expand prompt that treats visible neighbor
    symbols as enough for STRUCTURE-style questions.

    Returns a routing dict with keys: sufficient, confidence, reason, route.
    """
    model = (model or "").strip() or _default_assessor_model()
    phase_norm = (phase or "rag").strip().lower()
    if phase_norm not in ("rag", "graph"):
        phase_norm = "rag"

    if not chunks:
        logger.info("assess_sufficiency: no chunks → routing to LC")
        return {
            "sufficient": False,
            "confidence": "high",
            "reason":     "No chunks were retrieved.",
            "route":      "LC",
        }

    # ── score-based shortcut (initial RAG only; after expand keep LLM judge) ─
    if phase_norm == "rag":
        top = _top_score(chunks)
        if top is not None and top < SCORE_SHORTCUT_THRESHOLD:
            logger.info(
                "assess_sufficiency: top score %.3f < %.3f threshold → LC (no LLM call)",
                top, SCORE_SHORTCUT_THRESHOLD,
            )
            return {
                "sufficient": False,
                "confidence": "high",
                "reason":     f"Top retrieval score {top:.3f} is below threshold {SCORE_SHORTCUT_THRESHOLD}.",
                "route":      "LC",
            }

    # ── LLM call ─────────────────────────────────────────────────────────────
    chunk_text = _format_chunks(chunks)
    user_msg   = _USER_TEMPLATE.format(
        query=query,
        n=len(chunks),
        chunks=chunk_text,
    )
    system = _SYSTEM_AFTER_EXPAND if phase_norm == "graph" else _SYSTEM

    payload = {
    "model":  model,
    "messages": [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_msg},
    ],
    "stream": False,
    "options": {"temperature": 0.0, "num_ctx": 4096},
    }

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",    # ← changed
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
    except requests.RequestException as exc:
        logger.warning("assess_sufficiency: Ollama call failed (%s) → defaulting to LC", exc)
        # Ollama call failed
        return {
            "sufficient": False,
            "confidence": "low",
            "reason":     f"Judge unavailable ({exc}); defaulting to LC for safety.",
            "route":      "LC",
        }



    try:
        result = _parse_response(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning(
            "assess_sufficiency: could not parse judge response (%s) → defaulting to LC\nRaw: %s",
            exc, raw[:300],
        )
        # Unparseable response
        return {
            "sufficient": False,
            "confidence": "low",
            "reason":     "Could not parse judge response; defaulting to LC for safety.",
            "route":      "LC",
        }

    logger.info(
        "assess_sufficiency: route=%s confidence=%s reason=%s",
        result["route"], result["confidence"], result["reason"],
    )
    return result


def _strip_judge_raw(raw: str) -> str:
    s = raw.strip()
    for marker in ("</think>", "</Think>"):
        if marker in s:
            s = s.split(marker, 1)[-1].strip()
    if s.startswith("```"):
        lines = s.splitlines()
        s = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    m = re.search(r"\{[\s\S]*?\}", s)
    if m:
        s = m.group(0)
    return s


def _parse_structure_response(raw: str) -> dict[str, Any]:
    data = json.loads(_strip_judge_raw(raw))
    need = str(data.get("need", "UNCLEAR")).upper()
    if need not in ("STRUCTURE", "CONTEXT", "UNCLEAR"):
        need = "UNCLEAR"
    confidence = str(data.get("confidence", "low")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    return {
        "need": need,
        "confidence": confidence,
        "reason": str(data.get("reason", "")),
    }


def _parse_rag_gate_response(raw: str) -> dict[str, Any]:
    data = json.loads(_strip_judge_raw(raw))
    sufficient = bool(data.get("sufficient", False))
    confidence = str(data.get("confidence", "low")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    reason = str(data.get("reason", ""))
    if sufficient:
        return {
            "sufficient": True,
            "need": None,
            "confidence": confidence,
            "reason": reason,
            "route": "RAG",
        }
    need_raw = data.get("need")
    if need_raw is None or str(need_raw).strip().lower() in ("", "null", "none"):
        need = "CONTEXT"
    else:
        need = str(need_raw).upper()
    if need not in ("STRUCTURE", "CONTEXT", "UNCLEAR"):
        need = "UNCLEAR"
    return {
        "sufficient": False,
        "need": need,
        "confidence": confidence,
        "reason": reason,
        "route": "LC" if need != "STRUCTURE" else "GRAPH",
    }


def assess_rag_gate(
    query: str,
    chunks: list[dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    """
    GASR post-RAG gate: one LLM call for sufficiency + STRUCTURE|CONTEXT.

    Returns: sufficient, need, confidence, reason, route.
    """
    model = (model or "").strip() or _default_assessor_model()

    if not chunks:
        logger.info("assess_rag_gate: no chunks → CONTEXT/LC")
        return {
            "sufficient": False,
            "need": "CONTEXT",
            "confidence": "high",
            "reason": "No chunks were retrieved.",
            "route": "LC",
        }

    top = _top_score(chunks)
    if top is not None and top < SCORE_SHORTCUT_THRESHOLD:
        logger.info(
            "assess_rag_gate: top score %.3f < %.3f → CONTEXT/LC (no LLM call)",
            top,
            SCORE_SHORTCUT_THRESHOLD,
        )
        return {
            "sufficient": False,
            "need": "CONTEXT",
            "confidence": "high",
            "reason": (
                f"Top retrieval score {top:.3f} is below threshold "
                f"{SCORE_SHORTCUT_THRESHOLD}."
            ),
            "route": "LC",
        }

    user_msg = _USER_RAG_GATE.format(
        query=query,
        n=len(chunks),
        chunks=_format_chunks(chunks),
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_RAG_GATE},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 4096},
    }

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
    except requests.RequestException as exc:
        logger.warning("assess_rag_gate: Ollama failed (%s) → CONTEXT/LC", exc)
        return {
            "sufficient": False,
            "need": "CONTEXT",
            "confidence": "low",
            "reason": f"Judge unavailable ({exc}); defaulting to CONTEXT/LC.",
            "route": "LC",
        }

    try:
        result = _parse_rag_gate_response(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning(
            "assess_rag_gate: parse failed (%s) → CONTEXT/LC\nRaw: %s",
            exc,
            raw[:300],
        )
        return {
            "sufficient": False,
            "need": "CONTEXT",
            "confidence": "low",
            "reason": "Could not parse judge response; defaulting to CONTEXT/LC.",
            "route": "LC",
        }

    logger.info(
        "assess_rag_gate: sufficient=%s need=%s confidence=%s reason=%s",
        result["sufficient"],
        result.get("need"),
        result["confidence"],
        result["reason"],
    )
    return result


def assess_structure_vs_context(
    query: str,
    chunks: list[dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    """
    After sufficiency fails: decide STRUCTURE (graph expand) vs CONTEXT (LC).

    Returns: need, confidence, reason.
    """
    model = (model or "").strip() or _default_assessor_model()
    if not chunks:
        return {
            "need": "CONTEXT",
            "confidence": "high",
            "reason": "No chunks available; escalate to long context.",
        }

    user_msg = _STRUCTURE_USER.format(
        query=query,
        n=len(chunks),
        chunks=_format_chunks(chunks),
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _STRUCTURE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 4096},
    }

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
    except requests.RequestException as exc:
        logger.warning(
            "assess_structure_vs_context: Ollama failed (%s) → UNCLEAR/CONTEXT", exc
        )
        return {
            "need": "CONTEXT",
            "confidence": "low",
            "reason": f"Judge unavailable ({exc}); defaulting to CONTEXT.",
        }

    try:
        result = _parse_structure_response(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning(
            "assess_structure_vs_context: parse failed (%s) → CONTEXT\nRaw: %s",
            exc,
            raw[:300],
        )
        return {
            "need": "CONTEXT",
            "confidence": "low",
            "reason": "Could not parse judge response; defaulting to CONTEXT.",
        }

    logger.info(
        "assess_structure_vs_context: need=%s confidence=%s reason=%s",
        result["need"],
        result["confidence"],
        result["reason"],
    )
    return result
