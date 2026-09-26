"""
Routing judges for the pipeline.
 
assess_sufficiency: the two "Enough" diamonds. Implements the pre-route method
from He et al., 2025 (arXiv:2505.20664).
    1. Pre-inference: the local model writes a brief plan for answering the
       query from the given chunks.
    2. The hidden state of the last decoded token at one intermediate layer is
       the capability embedding.
    3. A linear probe outputs P(q), the probability that these chunks are enough.
    4. P(q) >= tau means sufficient.
 
Two probes exist, one per stage, because the chunk distribution differs.
    stage="rag"    chunks after Prune_1800 (first diamond)
    stage="graph"  chunks after expand and re-prune (second diamond)
If the graph probe is missing, the rag probe is reused. If no probe exists,
the router answers "not sufficient".
 
"""

from __future__ import annotations

import json
import os
import re
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
import torch
 
from diamond.capability_embedding import (
    HF_MODEL_ID,
    extract_capability_embedding,
    format_chunks as _format_chunks,
)


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
# SCORE_SHORTCUT_THRESHOLD = 0.30

PROBE_DIR = Path(os.getenv("ROUTER_PROBE_DIR", str(Path(__file__).resolve().parent)))



# ── prompts ────────────────────────────────────────────────────────────────────

# _SYSTEM = """\
# You are a routing judge for a code-search RAG system.
# You will be given a user query and a set of retrieved code chunks.
# Your job is to decide whether the chunks are sufficient to answer the query.

# Reply ONLY with a JSON object — no markdown, no explanation outside the JSON.
# Schema:
# {
#   "sufficient": true | false,
#   "confidence": "high" | "medium" | "low",
#   "reason": "<one sentence>"
# }

"""
Rules:
- sufficient=true  → the chunks contain enough information to answer the query directly
  (return value, quoted lines, named callees, or the asked symbols are present).
- sufficient=false → key information is missing; a broader search is needed.
- Do NOT require a perfect essay — if the answerable facts are in the chunks, mark sufficient=true.
- confidence reflects how certain you are of your judgment.


_SYSTEM_AFTER_EXPAND = \
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

# _USER_TEMPLATE = """\
# QUERY:
# {query}

# RETRIEVED CHUNKS ({n} chunks):
# {chunks}

# Is the context sufficient to answer the query? Reply with JSON only.
# """

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



# ── pre-route probe ───────────────────────────────────────────────────────────
 
@lru_cache(maxsize=None)
def _load_probe(stage: str) -> dict[str, Any] | None:
    path = PROBE_DIR / f"router_probe_{stage}.pt"
    if not path.is_file():
        if stage != "rag":
            logger.warning("No %s probe at %s, falling back to the rag probe", stage, path)
            return _load_probe("rag")
        logger.warning("No rag probe at %s", path)
        return None
    ckpt = torch.load(path, map_location="cpu")
    if ckpt.get("hf_model") != HF_MODEL_ID:
        logger.warning(
            "Probe %s was trained on %s but ROUTER_HF_MODEL is %s",
            path.name, ckpt.get("hf_model"), HF_MODEL_ID,
        )
    return ckpt
 
 
def _p_sufficient(h: torch.Tensor, ckpt: dict[str, Any]) -> float:
    x = (h[ckpt["layer"]] - ckpt["mean"]) / ckpt["std"]
    return float(torch.sigmoid(torch.dot(x, ckpt["w"]) + ckpt["b"]).item())
 
 
def _confidence(p: float, tau: float) -> str:
    margin = abs(p - tau)
    if margin >= 0.25:
        return "high"
    if margin >= 0.10:
        return "medium"
    return "low"
 
 
def _not_sufficient(reason: str, confidence: str = "low") -> dict[str, Any]:
    return {
        "sufficient": False,
        "confidence": confidence,
        "reason": reason,
        "route": "LC",
        "p_rag": None,
        "tau": None,
        "layer": None,
        "pre_inference_tokens": None,
    }





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


# def _top_score(items: list[dict[str, Any]]) -> float | None:
#     for key in ("final_score", "hybrid_score", "similarity"):
#         v = items[0].get(key) if items else None
#         if v is not None:
#             try:
#                 return float(v)
#             except (TypeError, ValueError):
#                 pass
#     return None


# def _parse_response(raw: str) -> dict[str, Any]:
#     """Extract JSON from the model reply, stripping think-tags and fences."""
#     s = raw.strip()

#     # strip </think> and everything before it (Qwen-style reasoning)
#     for marker in ("</think>", "</Think>"):
#         if marker in s:
#             s = s.split(marker, 1)[-1].strip()

#     # strip markdown fences
#     if s.startswith("```"):
#         lines = s.splitlines()
#         s = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()

#     # find first {...}
#     m = re.search(r"\{[\s\S]*?\}", s)
#     if m:
#         s = m.group(0)

#     data = json.loads(s)

#     sufficient = bool(data.get("sufficient", False))
#     confidence = str(data.get("confidence", "low")).lower()
#     if confidence not in ("high", "medium", "low"):
#         confidence = "low"
#     reason = str(data.get("reason", ""))

#     return {
#         "sufficient": sufficient,
#         "confidence": confidence,
#         "reason":     reason,
#         "route":      "RAG" if sufficient else "LC",
#     }


# ── public API ────────────────────────────────────────────────────────────────

def assess_sufficiency(
    query:  str,
    chunks: list[dict[str, Any]],
    model:  str = DEFAULT_MODEL,
    stage: str = "rag",
) -> dict[str, Any]:
    """
    Estimate whether *chunks* are enough to answer *query*.
 
    Returns: sufficient, confidence, reason, route ("RAG" or "LC"),
    plus p_rag, tau, layer, pre_inference_tokens for the dashboard.
    A route of "LC" only means "not sufficient". The caller decides whether
    that leads to graph expand or long context.
    """
    model = (model or "").strip() or _default_assessor_model()
    phase_norm = (phase or "rag").strip().lower()
    if phase_norm not in ("rag", "graph"):
        phase_norm = "rag"

    if not chunks:
        return _not_sufficient("No chunks were retrieved.", "high")
 
    ckpt = _load_probe(stage)
    if ckpt is None:
        return _not_sufficient("Router probe not trained; treating as not sufficient.")
 
    try:
        h, meta = extract_capability_embedding(query, chunks)
        p = _p_sufficient(h, ckpt)
    except Exception as exc:   # model load, CUDA OOM, shape mismatch
        logger.warning("assess_sufficiency: router failed (%s)", exc)
        return _not_sufficient(f"Router failed ({exc}); treating as not sufficient.")
 
    tau = float(ckpt["tau"])
    ok = p >= tau
    result = {
        "sufficient": ok,
        "confidence": _confidence(p, tau),
        "reason": f"stage={stage} P(sufficient)={p:.3f} vs tau={tau:.2f} at layer {ckpt['layer'] + 1}.",
        "route": "RAG" if ok else "LC",
        "p_rag": p,
        "tau": tau,
        "layer": int(ckpt["layer"]) + 1,
        "pre_inference_tokens": meta["pre_inference_tokens"],
    }
    logger.info(
        "assess_sufficiency[%s]: sufficient=%s p=%.3f tau=%.2f pre_tokens=%d",
        stage, ok, p, tau, meta["pre_inference_tokens"],
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
