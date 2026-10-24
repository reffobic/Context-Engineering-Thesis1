"""
GASR ladder: RAG → GRAPH expand → LC.

Pure control flow used by ``/generate`` (mode=GASR) and unit tests.
Callers inject assess / expand / LC builders so tests can force a GRAPH route
without Ollama.

``re_prune`` is the post-expand callback: flat-merge seeds+neighbors under
the token budget (``prune_context``). The ladder only needs updated context.

Post-RAG gate is **one** call: sufficiency + STRUCTURE|CONTEXT need.
After STRUCTURE, further hops do not re-ask structure (sticky need).

Policy (after a successful expand that added neighbors):
  prefer GRAPH (re-pruned seeds+neighbors) over dumping full LC when the
  post-expand judge is still unsatisfied or the hop cap is hit.
  LC is for CONTEXT needs (no useful neighbors) or failed expand (0 new chunks
  and empty neighbor pool).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


AssessFn = Callable[[], dict[str, Any]]
ExpandFn = Callable[[list[Any], int], dict[str, Any]]
RePruneFn = Callable[[list[Any], list[Any]], Any]
BuildLcFn = Callable[[], Any]


@dataclass
class GasrLadderResult:
    route: str  # RAG | GRAPH | LC
    hops_used: int
    context: Any
    assess_trace: list[dict[str, Any]] = field(default_factory=list)
    expand_trace: list[dict[str, Any]] = field(default_factory=list)


def _finish(
    *,
    route: str,
    hops_used: int,
    context: Any,
    assess_trace: list[dict[str, Any]],
    expand_trace: list[dict[str, Any]],
) -> GasrLadderResult:
    return GasrLadderResult(
        route=route,
        hops_used=hops_used,
        context=context,
        assess_trace=assess_trace,
        expand_trace=expand_trace,
    )

def _tk(d: dict[str, Any], key: str) -> int:
    return int(((d.get("token_usage") or {}).get(key)) or 0)

def _compose_legacy_gate(assess_rag: AssessFn, assess_structure: AssessFn) -> AssessFn:
    """Two callbacks → one gate dict (for older tests only)."""

    def _gate() -> dict[str, Any]:
        d1 = assess_rag()
        if d1.get("sufficient"):
            return {**d1, "sufficient": True, "need": None}
        d2 = assess_structure()
        return {
            "sufficient": False,
            "need": d2.get("need", "CONTEXT"),
            "confidence": d2.get("confidence", d1.get("confidence", "low")),
            "reason": d2.get("reason") or d1.get("reason", ""),
            "route": "LC",
            "token_usage": {
                "prompt_tokens": _tk(d1, "prompt_tokens") + _tk(d2, "prompt_tokens"),
                "completion_tokens": _tk(d1, "completion_tokens") + _tk(d2, "completion_tokens"),
            },
            "duration_ms": (d1.get("duration_ms") or 0) + (d2.get("duration_ms") or 0),
        }

    return _gate


def run_gasr_ladder(
    *,
    seed_pool: list[Any],
    initial_pruned: Any,
    expand_hop: ExpandFn,
    re_prune: RePruneFn,
    assess_after_expand: AssessFn,
    build_lc: BuildLcFn,
    hard_cap: int = 2,
    assess_rag_gate: Optional[AssessFn] = None,
    assess_rag: Optional[AssessFn] = None,
    assess_structure: Optional[AssessFn] = None,
) -> GasrLadderResult:
    """
    Execute the GASR controller.

    Prefer ``assess_rag_gate`` (single merged judge). Legacy ``assess_rag`` +
    ``assess_structure`` are composed once for older tests.
    """
    if assess_rag_gate is not None:
        gate_fn = assess_rag_gate
    elif assess_rag is not None and assess_structure is not None:
        gate_fn = _compose_legacy_gate(assess_rag, assess_structure)
    else:
        raise TypeError(
            "run_gasr_ladder requires assess_rag_gate=... "
            "or both assess_rag=... and assess_structure=..."
        )

    hops_used = 0
    assess_trace: list[dict[str, Any]] = []
    expand_trace: list[dict[str, Any]] = []
    current_pruned = initial_pruned
    neighbor_pool: list[Any] = []

    gate = gate_fn()
    sufficient = bool(gate.get("sufficient"))
    need = str(gate.get("need") or "UNCLEAR").upper()
    if sufficient:
        need_out: Optional[str] = None
    elif need not in ("STRUCTURE", "CONTEXT", "UNCLEAR"):
        need_out = "UNCLEAR"
        need = "UNCLEAR"
    else:
        need_out = need

    assess_trace.append(
        {
            "phase": "rag_gate",
            "hops": 0,
            **{k: v for k, v in gate.items() if k != "need"},
            "need": need_out,
            "can_expand": (not sufficient) and need == "STRUCTURE" and hard_cap > 0,
        }
    )

    if sufficient:
        return _finish(
            route="RAG",
            hops_used=0,
            context=current_pruned,
            assess_trace=assess_trace,
            expand_trace=expand_trace,
        )

    if need != "STRUCTURE" or hard_cap <= 0:
        return _finish(
            route="LC",
            hops_used=0,
            context=build_lc(),
            assess_trace=assess_trace,
            expand_trace=expand_trace,
        )

    while hops_used < hard_cap:
        expand_seeds = list(seed_pool) + list(neighbor_pool)
        expand_result = expand_hop(expand_seeds, 1)
        new_chunks = expand_result.get("chunks") or []
        hops_used += 1
        neighbor_ids = list(expand_result.get("neighbor_ids") or [])
        neighbor_ids_cap = 20
        expand_trace.append(
            {
                "hops_used": hops_used,
                "new_chunk_count": expand_result.get("new_chunk_count", 0),
                "graph_backend": expand_result.get("graph_backend"),
                "seed_keys": expand_result.get("seed_keys"),
                "neighbor_ids": neighbor_ids[:neighbor_ids_cap],
                "neighbor_ids_total": len(neighbor_ids),
                "edge_counts": expand_result.get("edge_counts"),
                "duration_ms": expand_result.get("duration_ms", 0),
                "confidence_note": str(gate.get("confidence") or "low").lower(),
            }
        )

        if expand_result.get("new_chunk_count", 0) <= 0:
            if neighbor_pool:
                return _finish(
                    route="GRAPH",
                    hops_used=hops_used,
                    context=current_pruned,
                    assess_trace=assess_trace,
                    expand_trace=expand_trace,
                )
            return _finish(
                route="LC",
                hops_used=hops_used,
                context=build_lc(),
                assess_trace=assess_trace,
                expand_trace=expand_trace,
            )

        neighbor_pool.extend(new_chunks)
        current_pruned = re_prune(seed_pool, neighbor_pool)

        decision2 = assess_after_expand()
        assess_trace.append({"phase": "graph", "hops": hops_used, **decision2})
        if decision2.get("sufficient"):
            return _finish(
                route="GRAPH",
                hops_used=hops_used,
                context=current_pruned,
                assess_trace=assess_trace,
                expand_trace=expand_trace,
            )

        if hops_used >= hard_cap:
            return _finish(
                route="GRAPH",
                hops_used=hops_used,
                context=current_pruned,
                assess_trace=assess_trace,
                expand_trace=expand_trace,
            )

    return _finish(
        route="GRAPH" if neighbor_pool else "LC",
        hops_used=hops_used,
        context=current_pruned if neighbor_pool else build_lc(),
        assess_trace=assess_trace,
        expand_trace=expand_trace,
    )
