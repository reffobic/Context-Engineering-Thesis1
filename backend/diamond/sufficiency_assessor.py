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
import re
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"
REQUEST_TIMEOUT = 120   # seconds — sufficiency call is short, but give extra space just in case

# Queries whose top-chunk score falls below this are escalated immediately
# without an LLM call (cheap fast-path).
SCORE_SHORTCUT_THRESHOLD = 0.30

# ── prompt ────────────────────────────────────────────────────────────────────

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
- sufficient=true  → the chunks contain enough information to answer the query directly.
- sufficient=false → key information is missing; a broader search is needed.
- confidence reflects how certain you are of your judgment.
"""

_USER_TEMPLATE = """\
QUERY:
{query}

RETRIEVED CHUNKS ({n} chunks):
{chunks}

Is the context sufficient to answer the query? Reply with JSON only.
"""

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
- STRUCTURE → need callees, callers, imports, or cross-file dependency neighbors (graph expand).
- CONTEXT  → need more of the same file(s): surrounding lines, imports at top, class body (long context stitch).
- UNCLEAR  → cannot tell; prefer CONTEXT.
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
        path   = item.get("path") or meta.get("file") or "?"
        symbol = item.get("symbol") or meta.get("function") or ""
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
    model:  str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """
    Assess whether *chunks* are sufficient to answer *query*.

    Fast-path: if the top chunk score is below SCORE_SHORTCUT_THRESHOLD,
    escalate to LC immediately without an LLM call.

    Returns a routing dict with keys: sufficient, confidence, reason, route.
    """
    if not chunks:
        logger.info("assess_sufficiency: no chunks → routing to LC")
        return {
            "sufficient": False,
            "confidence": "high",
            "reason":     "No chunks were retrieved.",
            "route":      "LC",
        }

    # ── score-based shortcut ──────────────────────────────────────────────────
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

    payload = {
    "model":  model,
    "messages": [
        {"role": "system", "content": _SYSTEM},
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


def _parse_structure_response(raw: str) -> dict[str, Any]:
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
    data = json.loads(s)
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


def assess_structure_vs_context(
    query: str,
    chunks: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """
    After sufficiency fails: decide STRUCTURE (graph expand) vs CONTEXT (LC).

    Returns: need, confidence, reason.
    """
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
