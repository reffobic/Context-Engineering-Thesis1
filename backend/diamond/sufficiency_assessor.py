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
DEFAULT_MODEL = "qwen3.5:4b"
REQUEST_TIMEOUT = 120   # seconds — sufficiency call is short, but give extra space just in case

# Queries whose top-chunk score falls below this are escalated immediately
# without an LLM call (cheap fast-path).
SCORE_SHORTCUT_THRESHOLD = 0.30

# ── prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a routing judge for a code-search RAG system.
You will be given a user query and a set of retrieved code chunks.
Your job is to decide how to route the query.

Reply ONLY with valid JSON — no markdown, no explanation outside the JSON.
Always quote string values properly.
Schema:
{
  "route": "RAG" | "LC" | "UNANSWERABLE",
  "confidence": "high" | "medium" | "low",
  "reason": "<one sentence>"
}

Rules:
- RAG → chunks contain enough information to answer directly.
- LC  → answer likely exists in the codebase but not in these chunks.
        Use this when the query is about a real feature of this codebase
        but the retrieved chunks missed the right file.
- UNANSWERABLE → the query cannot be answered from this codebase at all.
        Use this when:
        * the query asks about a feature this software does not have
        * the query is about a completely different technology or domain
        * the query requires knowledge that no source file could contain
        Do NOT use LC as a fallback when the question is out of scope.
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

def _clean_json(s: str) -> str:
    # fix: "reason": Some text without opening quote"
    s = re.sub(
        r'"reason":\s*([^"\n{[][^\n]*)',
        lambda m: '"reason": "' + m.group(1).strip().rstrip('"}') + '"',
        s
    )
    return s

def _parse_response(raw: str) -> dict[str, Any]:
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

    # try raw first, then cleaned
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        data = json.loads(_clean_json(s))

    route = str(data.get("route", "LC")).upper()
    if route not in ("RAG", "LC", "UNANSWERABLE"):
        route = "LC"

    # backwards compat: if model returns sufficient=true/false instead of route
    if "sufficient" in data and "route" not in data:
        route = "RAG" if data["sufficient"] else "LC"

    confidence = str(data.get("confidence", "low")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    reason = str(data.get("reason", ""))

    return {
        "sufficient": route == "RAG",
        "confidence": confidence,
        "reason":     reason,
        "route":      route,
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
    # score shortcut stays LC (not UNANSWERABLE — we don't know, just bad retrieval)
    if top is not None and top < SCORE_SHORTCUT_THRESHOLD:
        return {
            "sufficient": False,
            "confidence": "high",
            "reason":     f"Top retrieval score {top:.3f} is below threshold.",
            "route":      "LC",
        }
        return {
            "sufficient": False,
            "confidence": "high",
            "reason":     f"Top retrieval score {top:.3f} is below threshold {SCORE_SHORTCUT_THRESHOLD}.",
            "route":      "LC",
        }

    # ── LLM call ─────────────────────────────────────────────────────────────
    chunk_text = _format_chunks(chunks)
    user_msg = (
        f"QUERY:\n{query}\n\n"
        f"RETRIEVED CHUNKS ({len(chunks)} chunks):\n{chunk_text}\n\n"
        "Is the context sufficient to answer the query? Reply with JSON only."
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