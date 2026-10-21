"""
preroute_gate.py

Alternative entry gate for the GASR ladder, wired in as mode="GAPA" in
mcp_server/server.py. Replaces ONLY the assess_rag callback that
run_gasr_ladder takes — assess_structure, expand_hop, re_prune,
assess_after_expand and build_lc are reused UNCHANGED from the existing
GASR wiring in server.py. This mode differs from GASR in exactly one
place: how the initial RAG-sufficiency decision gets made. GASR itself is
not modified anywhere in this file.

Two stages:
  Stage 1 — query text only. Cheap keyword heuristic. Catches the obvious
    cases and returns immediately, without looking at any retrieved chunk.
  Stage 2 — rule-based, on retrieval fields that already exist in this
    codebase today: final_score/hybrid_score/similarity (retrieval/
    retrieval_tool.py), chunk_type/dependencies (indexing/code_indexer.py,
    CodeChunk.to_metadata()). No LLM call, no trained model — same spirit
    as sufficiency_assessor.SCORE_SHORTCUT_THRESHOLD, generalized to a few
    more signals instead of score alone. DEFERs to the existing
    assess_sufficiency LLM judge when no rule is confident.

Known limitation, not fixed here: retrieval and pruning currently run
unconditionally at the top of run_pipeline() before any mode branch,
including this one. So Stage 1 saves the assess_sufficiency LLM call on
the queries it's confident about, but does NOT skip the retrieval call
itself the way a true pre-retrieval gate would — that would need
restructuring run_pipeline's top-level flow, out of scope for now. Name
this as a limitation in the writeup, not an oversight.

Does not use sim_code / sim_meta / sim_gap — that's the dual-embedding
design your teammate wrote up, not merged into this codebase yet
(INDEX_SCHEMA_VERSION is still "contextual-hybrid-v1" here). If that
branch lands, extend _stage2_rules with those fields; the join key is
still chunk_id either way.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Stage 1: query-only heuristic ──────────────────────────────────────────
# Deliberately small and conservative — these are the cases where the
# query alone is enough to be confident, not an attempt to cover everything.
# Everything else falls through to stage 2, which actually looks at what
# was retrieved.

_LC_KEYWORDS = (
    "entire file", "whole file", "every file", "across the codebase",
    "across the repo", "throughout", "entire codebase", "whole repo",
)
_RAG_KEYWORDS = (
    "what does", "what is", "return", "return value", "definition of",
)


def _stage1_query_only(query: str) -> dict[str, Any] | None:
    """Returns a decision dict if confident, else None (fall through)."""
    q = query.lower()

    if any(kw in q for kw in _LC_KEYWORDS):
        return {
            "sufficient": False,
            "confidence": "medium",
            "reason": f"Stage 1: query implies whole-codebase scope.",
            "route": "LC",
            "stage": "preroute_stage1",
        }

    if any(kw in q for kw in _RAG_KEYWORDS) and len(q.split()) <= 12:
        return {
            "sufficient": True,
            "confidence": "medium",
            "reason": "Stage 1: short, locally-scoped question (what/return/definition).",
            "route": "RAG",
            "stage": "preroute_stage1",
        }

    return None


# ── Stage 2: rule-based, on real retrieval fields ──────────────────────────

# Same idea as sufficiency_assessor.SCORE_SHORTCUT_THRESHOLD (0.30) —
# kept identical so the two are directly comparable, not coincidentally
# similar.
STAGE2_SCORE_FLOOR = 0.30
STAGE2_SCORE_CEILING = 0.55
STAGE2_MARGIN_CEILING = 0.05
STAGE2_MAX_DEPENDENCIES_FOR_RAG = 1
STAGE2_MIN_DEPENDENCIES_FOR_ESCALATE = 3
STAGE2_MIN_FILES_FOR_ESCALATE = 3


def _parse_dependencies(meta: dict[str, Any]) -> list[str]:
    """CodeChunk.to_metadata() JSON-serializes list fields — undo that."""
    raw = meta.get("dependencies")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _score(chunk: dict[str, Any]) -> float | None:
    for key in ("final_score", "hybrid_score", "similarity"):
        v = chunk.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _stage2_rules(query: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    sufficient=None signals "no rule fired" — the caller treats that as
    DEFER, not as a guess. This function never returns a low-confidence
    true/false; it only returns a verdict when a rule actually matched.
    """
    if not chunks:
        return {
            "sufficient": False,
            "confidence": "high",
            "reason": "Stage 2: no chunks retrieved.",
            "route": "LC",
            "stage": "preroute_stage2",
        }

    top = _score(chunks[0])
    second = _score(chunks[1]) if len(chunks) > 1 else 0.0
    second = second if second is not None else 0.0
    margin = (top - second) if top is not None else 0.0

    meta = chunks[0].get("metadata") or {}
    dependencies = _parse_dependencies(meta)
    candidate_files = len({(c.get("metadata") or {}).get("file", "") for c in chunks})

    # Very low top score: same threshold as today's SCORE_SHORTCUT_THRESHOLD.
    if top is not None and top < STAGE2_SCORE_FLOOR:
        return {
            "sufficient": False,
            "confidence": "high",
            "reason": f"Stage 2: top score {top:.3f} below floor {STAGE2_SCORE_FLOOR}.",
            "route": "LC",
            "stage": "preroute_stage2",
        }

    # High score, one dominant file, few dependencies: confidently local.
    if (
        top is not None
        and top >= STAGE2_SCORE_CEILING
        and margin >= STAGE2_MARGIN_CEILING
        and candidate_files <= 1
        and len(dependencies) <= STAGE2_MAX_DEPENDENCIES_FOR_RAG
    ):
        return {
            "sufficient": True,
            "confidence": "medium",
            "reason": (
                f"Stage 2: top score {top:.3f}, margin {margin:.3f}, "
                f"{len(dependencies)} dependencies, single file — concentrated evidence."
            ),
            "route": "RAG",
            "stage": "preroute_stage2",
        }

    # Many dependencies or several candidate files: likely needs structure.
    if (
        len(dependencies) >= STAGE2_MIN_DEPENDENCIES_FOR_ESCALATE
        or candidate_files >= STAGE2_MIN_FILES_FOR_ESCALATE
    ):
        return {
            "sufficient": False,
            "confidence": "medium",
            "reason": (
                f"Stage 2: {len(dependencies)} dependencies across "
                f"{candidate_files} candidate files — evidence looks scattered."
            ),
            "route": "LC",
            "stage": "preroute_stage2",
        }

    # No rule confident enough.
    return {
        "sufficient": None,
        "confidence": "low",
        "reason": "Stage 2: no rule fired confidently.",
        "route": None,
        "stage": "preroute_stage2_defer",
    }


# ── public entry point ──────────────────────────────────────────────────────

def preroute_gate(
    query: str,
    chunks: list[dict[str, Any]],
    fallback_assess: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """
    Drop-in replacement for assess_sufficiency as run_gasr_ladder's
    assess_rag callback. Same return contract: sufficient, confidence,
    reason, route — plus a "stage" key for reporting, which the ladder
    itself ignores.

    fallback_assess: zero-arg callable, e.g.
        lambda: assess_sufficiency(query, chunks)
    Called only when neither stage resolves the decision (DEFER).

    # --- once the memory branch (resolve_referential_prompt / Mem0) is
    # merged: resolve the query BEFORE stage 1 runs, here, so keyword
    # matching sees the resolved topic instead of a bare pronoun-laden
    # follow-up ("why does it fail" is useless to stage 1 as-is). This
    # must happen in this function, not inside stage1/stage2, since both
    # need the resolved text and neither should import the memory agent.
    #
    #     from agent.ollama_agent import resolve_referential_prompt  # once merged
    #     query = resolve_referential_prompt(query)  # uses short-term history
    #
    # Also worth adding once merged: if resolve_referential_prompt (or its
    # meta-memory / assistant-referential checks) signals this turn should
    # be answered from conversation history rather than routed at all,
    # short-circuit here with a dedicated "stage": "preroute_meta_memory"
    # decision rather than falling through to stage 1/2, which have no way
    # to represent "don't route this, answer from history instead."
    """
    decision = _stage1_query_only(query)
    if decision is not None:
        logger.info("preroute_gate: stage1 resolved — route=%s", decision["route"])
        return decision

    decision = _stage2_rules(query, chunks)
    if decision["sufficient"] is not None:
        logger.info("preroute_gate: stage2 resolved — route=%s", decision["route"])
        return decision

    logger.info("preroute_gate: deferring to LLM judge (assess_sufficiency)")
    fallback = dict(fallback_assess())
    fallback["stage"] = "preroute_defer_llm"
    return fallback


# ── Stage 2, generative variant: structured prompt, real LLM call ──────────
#
# This is the part _stage2_rules does NOT do: activate the model's own
# reasoning over the metadata, the way Chen et al.'s Pre-Route prompt and
# He et al.'s pre-inference trace both do. _stage2_rules is arithmetic on
# numbers; this asks a model to weigh them. Same Ollama call pattern as
# sufficiency_assessor.assess_sufficiency (same endpoint, same request
# shape) so results are timed and compared on equal footing — this is not
# a cheaper reimplementation of that call, it's a shorter PROMPT for it:
# metadata fields instead of full chunk text.
#
# preroute_gate() above does NOT call this — it is unchanged, still
# rule-based only. This is a separate entry point, preroute_gate_generative,
# so nothing already wired into server.py is touched by adding this.

import os as _os
import requests as _requests

_OLLAMA_BASE_URL = "http://localhost:11434"
_GENERATIVE_REQUEST_TIMEOUT = 60  # shorter than assess_sufficiency's 120s —
                                   # this prompt is metadata only, no chunk text


def _default_gate_model() -> str:
    return (
        _os.environ.get("ASSESSOR_MODEL", "").strip()
        or _os.environ.get("OLLAMA_ASSESSOR_MODEL", "").strip()
        or "llama3.2:3b"
    )


_GENERATIVE_SYSTEM = """\
You are a routing judge for a code-search RAG system.
You will be given metadata about the top retrieved chunk for a query —
NOT the chunk's text itself, only facts about it.
Decide whether this metadata suggests the retrieved chunk is enough to
answer the query, or whether the answer likely needs more context
(other files, related symbols) than what was retrieved.

Reply ONLY with a JSON object — no markdown, no explanation outside the JSON.
Schema:
{"sufficient": true|false, "confidence": "high"|"medium"|"low", "reason": "<one sentence>"}"""

_GENERATIVE_USER_TEMPLATE = """\
query: "{query}"
top_score: {top_score}
score_margin: {score_margin}
dependencies: {n_dependencies}
candidate_files: {candidate_files}
chunk_type: {chunk_type}

Reasoning steps:
1. Does a high top_score with a large score_margin suggest one chunk
   clearly dominates, or is the evidence spread thin?
2. Do several dependencies or several candidate_files suggest the answer
   requires following relationships beyond this one chunk?
3. If genuinely unsure, prefer sufficient=true (cheaper path)."""


def _parse_generative_response(raw: str) -> dict[str, Any]:
    """Same defensive style as sufficiency_assessor._parse_response —
    tolerate a model that wraps JSON in prose despite instructions."""
    import re as _re

    match = _re.search(r"\{.*\}", raw, _re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object found in response: {raw[:200]!r}")
    parsed = json.loads(match.group(0))
    sufficient = bool(parsed["sufficient"])
    return {
        "sufficient": sufficient,
        "confidence": str(parsed.get("confidence", "low")),
        "reason": str(parsed.get("reason", "")),
        "route": "RAG" if sufficient else "LC",
        # Optional: only present when the prompt asked for it (emit_need).
        "need": (str(parsed["need"]).upper() if parsed.get("need") else None),
    }


def _stage2_generative(
    query: str, chunks: list[dict[str, Any]], model: str | None = None
) -> dict[str, Any]:
    """
    Real LLM call, short prompt (metadata only, no chunk text). Same
    fail-safe convention as assess_sufficiency: any error defaults to
    sufficient=False / route=LC rather than guessing sufficient=True,
    since escalating on failure is the safer direction.
    """
    if not chunks:
        return {
            "sufficient": False, "confidence": "high",
            "reason": "Stage 2 (generative): no chunks retrieved.",
            "route": "LC", "stage": "preroute_stage2_generative",
        }

    top = _score(chunks[0])
    second = _score(chunks[1]) if len(chunks) > 1 else 0.0
    second = second if second is not None else 0.0
    margin = (top - second) if top is not None else 0.0
    meta = chunks[0].get("metadata") or {}
    dependencies = _parse_dependencies(meta)
    candidate_files = len({(c.get("metadata") or {}).get("file", "") for c in chunks})
    chunk_type = meta.get("chunk_type") or "unknown"

    user_msg = _GENERATIVE_USER_TEMPLATE.format(
        query=query,
        top_score=f"{top:.3f}" if top is not None else "unknown",
        score_margin=f"{margin:.3f}",
        n_dependencies=len(dependencies),
        candidate_files=candidate_files,
        chunk_type=chunk_type,
    )
    payload = {
        "model": (model or "").strip() or _default_gate_model(),
        "messages": [
            {"role": "system", "content": _GENERATIVE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "keep_alive": "10m",  # hold the model resident — avoids reload cost between benchmark calls
        "options": {"temperature": 0.0, "num_ctx": 2048},  # smaller ctx — no chunk text
    }

    try:
        resp = _requests.post(
            f"{_OLLAMA_BASE_URL}/api/chat", json=payload, timeout=_GENERATIVE_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
    except _requests.RequestException as exc:
        logger.warning("_stage2_generative: Ollama call failed (%s) → LC", exc)
        return {
            "sufficient": False, "confidence": "low",
            "reason": f"Generative judge unavailable ({exc}); defaulting to LC.",
            "route": "LC", "stage": "preroute_stage2_generative_error",
        }

    try:
        result = _parse_generative_response(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("_stage2_generative: unparseable response (%s) → LC", exc)
        return {
            "sufficient": False, "confidence": "low",
            "reason": "Could not parse generative judge response; defaulting to LC.",
            "route": "LC", "stage": "preroute_stage2_generative_error",
        }

    result["stage"] = "preroute_stage2_generative"
    return result


def preroute_gate_generative(
    query: str,
    chunks: list[dict[str, Any]],
    fallback_assess: Callable[[], dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    """
    Same shape as preroute_gate(), same Stage 1, but Stage 2 is the
    generative structured-prompt judge instead of fixed thresholds. Does
    NOT defer on ambiguity the way preroute_gate() does — the generative
    call already IS the judge call, there's nothing cheaper left to defer
    to except assess_sufficiency itself, which fallback_assess still
    covers if this call errors out.
    """
    decision = _stage1_query_only(query)
    if decision is not None:
        return decision

    decision = _stage2_generative(query, chunks, model=model)
    if decision["stage"] == "preroute_stage2_generative_error":
        logger.info("preroute_gate_generative: generative call failed, deferring to assess_sufficiency")
        fallback = dict(fallback_assess())
        fallback["stage"] = "preroute_defer_llm_after_generative_error"
        return fallback
    return decision


# ── Single-stage structured gate: metadata + six-step prompt, one LLM call ──
#
# This REPLACES preroute_gate() and preroute_gate_generative() as the
# intended GAPA mechanism going forward — those two stay in this file
# (already tested, zero risk to remove them) but server.py's PREROUTE
# branch should call THIS instead. See the accompanying server.py edit.
#
# Runs on RAW retrieval hits, not pruned/chunk text — reads only metadata,
# so it can sit BEFORE prune_context_with_stats in the pipeline. Pruning
# only happens afterward, and only on the branch that needs it (RAG-
# sufficient). Six steps, mirroring Chen et al.'s Pre-Route structure
# (task type -> characterization -> distribution pattern -> relationships
# -> decision rule -> decision+explanation), adapted from their doc-level
# fields (doc_type/title/length/head) to this repo's real fields.
#
# Uses sim_code/sim_meta/sim_gap IF present in metadata (the dual-embedding
# design, not merged as of tonight) and silently omits those prompt lines
# if absent, so this works against today's single-channel retrieval and
# upgrades automatically once/if that branch lands — no code change needed
# here when it does.

_STRUCTURED_SYSTEM = """\
You are a routing judge for a code-search RAG system.
You will be given metadata about the top retrieved chunk for a query —
NOT the chunk's text, only facts about it and the retrieval signals.
Work through the six steps below, then decide whether this metadata
suggests the retrieved chunk is enough to answer the query, or whether
the answer likely needs more context than what was retrieved.

Reply ONLY with a JSON object — no markdown, no explanation outside the JSON.
Schema:
{"sufficient": true|false, "confidence": "high"|"medium"|"low", "reason": "<one sentence>"}"""

_STRUCTURED_USER_TEMPLATE = """\
query: "{query}"

## Step 1 — task type
{task_type}

## Step 2 — chunk characterization
file: {file_path}
symbol type: {chunk_type}
{context_line}

## Step 3 — distribution pattern
top_score: {top_score}
score_margin: {score_margin}
candidate_files: {candidate_files}
{similarity_lines}

## Step 4 — relationships
dependencies: {n_dependencies}

## Step 5 — decision rule
Prefer sufficient=true unless step 3 or step 4 clearly indicate the
evidence is scattered across files or the chunk has significant external
dependencies the answer would need to follow.

## Step 6 — decision
State your reasoning in one sentence, then output the JSON verdict."""


# Optional extension (emit_need=True): the same single call also labels WHY a
# chunk is insufficient, so the ladder can skip its separate
# structure-vs-context call. Same labels as sufficiency_assessor.assess_rag_gate.
_NEED_SCHEMA_FROM = '"sufficient": true|false,'
_NEED_SCHEMA_TO = '"sufficient": true|false, "need": "STRUCTURE"|"CONTEXT"|null,'
_NEED_SUFFIX = """

If sufficient is false, also set "need":
"STRUCTURE" if the missing evidence is likely in other symbols or files that
this chunk calls, is called by, or imports (step 3 shows several candidate
files, or step 4 shows many dependencies).
"CONTEXT" if the chunk itself looks like a fragment and the answer needs the
rest of its file or the surrounding lines.
If sufficient is true, set "need" to null."""


def _guess_task_type(query: str) -> str:
    q = query.lower()
    if any(w in q for w in ("fail", "bug", "crash", "wrong", "unexpected", "broken")):
        return "debug — likely needs surrounding context"
    if any(w in q for w in ("why", "how does", "what causes", "affect")):
        return "explain-relational — may need cross-file context"
    if any(w in q for w in ("what does", "what is", "return")):
        return "explain-local — usually self-contained"
    if any(w in q for w in ("where is", "find", "locate")):
        return "locate — usually self-contained"
    return "unclear from query alone"


def preroute_structured_gate(
    query: str,
    raw_chunks: list[dict[str, Any]],
    model: str | None = None,
    emit_need: bool = False,
    compact: bool | None = None,
) -> dict[str, Any]:
    """
    The single-stage replacement for assess_rag. Call this on RAW
    retrieval output, BEFORE prune_context_with_stats — see server.py's
    updated GAPA branch. Same return contract as assess_sufficiency:
    sufficient, confidence, reason, route.

    # --- once the memory branch is merged, this is also where an initial
    # sufficiency check against conversation memory belongs — BEFORE any
    # of this runs, before retrieval even happens, not just before
    # pruning. If the query is answerable from what's already in short-
    # or long-term memory (a meta-memory / assistant-referential follow-up,
    # or a fact Mem0 already has), short-circuit here and skip retrieval,
    # this gate, and the whole GASR ladder entirely:
    #
    #     from agent.ollama_agent import resolve_referential_prompt  # once merged
    #     query = resolve_referential_prompt(query)
    #     # if the resolver flags this as answerable from history directly,
    #     # return a decision here with a dedicated stage, e.g.
    #     #   {"sufficient": True, "route": "MEMORY", "stage": "preroute_memory_check", ...}
    #     # and the caller (server.py) should treat that as "skip retrieval
    #     # entirely," not just "skip this LLM call" — a bigger short-
    #     # circuit than anything else in this file does today.
    """
    if not raw_chunks:
        return {
            "sufficient": False, "confidence": "high",
            "reason": "No chunks retrieved.", "route": "LC",
            "stage": "preroute_structured",
        }

    top_chunk = raw_chunks[0]
    top = _score(top_chunk)
    second = _score(raw_chunks[1]) if len(raw_chunks) > 1 else 0.0
    second = second if second is not None else 0.0
    margin = (top - second) if top is not None else 0.0

    meta = top_chunk.get("metadata") or {}
    dependencies = _parse_dependencies(meta)
    candidate_files = len({(c.get("metadata") or {}).get("file", "") for c in raw_chunks})
    file_path = meta.get("file", "unknown")
    chunk_type = meta.get("chunk_type", "unknown")
    # compact=None -> read GAPA_COMPACT_PROMPT (default off). Compact keeps the
    # same six steps but replaces the long context header with the symbol name.
    if compact is None:
        compact = _os.environ.get("GAPA_COMPACT_PROMPT", "").strip().lower() in {"1", "true", "yes"}
    if compact:
        symbol = meta.get("function") or meta.get("name") or ""
        context_line = f"symbol: {symbol}" if symbol else ""
    else:
        context_header = meta.get("context_header")
        context_line = f"description: {context_header}" if context_header else ""

    # Optional dual-embedding fields — only included if present (see module
    # docstring). Absent today; will populate automatically if that branch merges.
    sim_lines = []
    if "sim_code" in top_chunk:
        sim_lines.append(f"sim_code: {top_chunk['sim_code']}")
    if "sim_meta" in top_chunk:
        sim_lines.append(f"sim_meta: {top_chunk['sim_meta']}")
    if "sim_gap" in top_chunk:
        sim_lines.append(f"sim_gap: {top_chunk['sim_gap']}")
    similarity_lines = "\n".join(sim_lines)

    user_msg = _STRUCTURED_USER_TEMPLATE.format(
        query=query,
        task_type=_guess_task_type(query),
        file_path=file_path,
        chunk_type=chunk_type,
        context_line=context_line,
        top_score=f"{top:.3f}" if top is not None else "unknown",
        score_margin=f"{margin:.3f}",
        candidate_files=candidate_files,
        similarity_lines=similarity_lines,
        n_dependencies=len(dependencies),
    )
    system_msg = _STRUCTURED_SYSTEM
    if emit_need:
        system_msg = system_msg.replace(_NEED_SCHEMA_FROM, _NEED_SCHEMA_TO)
        user_msg += _NEED_SUFFIX
    payload = {
        "model": (model or "").strip() or _default_gate_model(),
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "keep_alive": "10m",  # hold the model resident — avoids reload cost between benchmark calls
        "options": {"temperature": 0.0, "num_ctx": 2048},
    }

    try:
        resp = _requests.post(
            f"{_OLLAMA_BASE_URL}/api/chat", json=payload, timeout=_GENERATIVE_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        resp_json = resp.json()
        raw = resp_json.get("message", {}).get("content", "")
        token_usage = {
            "prompt_tokens": resp_json.get("prompt_eval_count"),
            "completion_tokens": resp_json.get("eval_count"),
        }
    except _requests.RequestException as exc:
        logger.warning("preroute_structured_gate: Ollama call failed (%s) -> LC", exc)
        return {
            "sufficient": False, "confidence": "low",
            "reason": f"Structured judge unavailable ({exc}); defaulting to LC.",
            "route": "LC", "stage": "preroute_structured_error",
        }

    if _os.environ.get("PREROUTE_DEBUG"):
        print(f"\n--- RAW LLM OUTPUT (preroute_structured_gate) ---\n{raw}\n--- END ---\n", flush=True)

    try:
        result = _parse_generative_response(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("preroute_structured_gate: unparseable response (%s) -> LC", exc)
        return {
            "sufficient": False, "confidence": "low",
            "reason": "Could not parse structured judge response; defaulting to LC.",
            "route": "LC", "stage": "preroute_structured_error",
        }

    result["stage"] = "preroute_structured"
    result["token_usage"] = token_usage
    if emit_need:
        if result["sufficient"]:
            result["need"] = None
        elif result.get("need") not in ("STRUCTURE", "CONTEXT"):
            result["need"] = "UNCLEAR"
    return result


# ── Metadata-only check after graph expansion ───────────────────────────────
# Replaces assess_sufficiency(phase="graph") in the GAPA_GRAPHMETA / GAPA_META
# modes. Same contract (sufficient, confidence, reason, route, token_usage), but
# the prompt holds facts about the expansion and never any chunk text.

_GRAPH_META_SYSTEM = """\
You are a routing judge for a code-search RAG system.
A graph expansion step has just added neighbouring code units (callers,
callees, imports) to the retrieved chunks. You are given facts about the
expansion, NOT any code text. Decide whether the expanded set is likely
enough to answer the query.

Reply ONLY with a JSON object — no markdown, no explanation outside the JSON.
Schema:
{"sufficient": true|false, "confidence": "high"|"medium"|"low", "reason": "<one sentence>"}"""

_GRAPH_META_USER = """\
query: "{query}"
hops_used: {hops}
seed_chunks: {n_seeds}
neighbours_added: {n_new}
neighbours_kept_after_prune: {n_kept}
neighbour_files: {n_files}
edge_counts: {edge_counts}
top_score: {top_score}

Prefer sufficient=true when neighbours were added and survived pruning;
prefer sufficient=false when none were found or none survived.
State your reasoning in one sentence, then output the JSON verdict."""


def graph_metadata_gate(
    query: str,
    facts: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    """Post-expansion sufficiency check from expansion metadata only.

    facts keys (all optional, missing ones print as 0/unknown): hops,
    n_seeds, n_new, n_kept, n_files, edge_counts, top_score.
    """
    user_msg = _GRAPH_META_USER.format(
        query=query,
        hops=facts.get("hops", 0),
        n_seeds=facts.get("n_seeds", 0),
        n_new=facts.get("n_new", 0),
        n_kept=facts.get("n_kept", 0),
        n_files=facts.get("n_files", 0),
        edge_counts=facts.get("edge_counts") or {},
        top_score=facts.get("top_score", "unknown"),
    )
    payload = {
        "model": (model or "").strip() or _default_gate_model(),
        "messages": [
            {"role": "system", "content": _GRAPH_META_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.0, "num_ctx": 2048},
    }
    try:
        resp = _requests.post(
            f"{_OLLAMA_BASE_URL}/api/chat", json=payload, timeout=_GENERATIVE_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        resp_json = resp.json()
        raw = resp_json.get("message", {}).get("content", "")
        token_usage = {
            "prompt_tokens": resp_json.get("prompt_eval_count"),
            "completion_tokens": resp_json.get("eval_count"),
        }
    except _requests.RequestException as exc:
        logger.warning("graph_metadata_gate: Ollama call failed (%s) -> LC", exc)
        return {
            "sufficient": False, "confidence": "low",
            "reason": f"Graph metadata judge unavailable ({exc}); defaulting to LC.",
            "route": "LC", "stage": "graph_metadata_error",
        }
    try:
        result = _parse_generative_response(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("graph_metadata_gate: unparseable response (%s) -> LC", exc)
        return {
            "sufficient": False, "confidence": "low",
            "reason": "Could not parse graph metadata judge response; defaulting to LC.",
            "route": "LC", "stage": "graph_metadata_error",
        }
    result["stage"] = "graph_metadata"
    result["token_usage"] = token_usage
    return result
