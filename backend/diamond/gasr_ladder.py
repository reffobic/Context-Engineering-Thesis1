"""
GASR ladder: RAG → GRAPH expand → LC.

Pure control flow used by ``/generate`` (mode=GASR) and unit tests.
Callers inject assess / expand / LC builders so tests can force a GRAPH route
without Ollama.

``re_prune`` is the post-expand callback: flat-merge seeds+neighbors under
the token budget (``prune_context``). The ladder only needs updated context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


AssessFn = Callable[[], dict[str, Any]]
StructureFn = Callable[[], dict[str, Any]]
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


def run_gasr_ladder(
    *,
    seed_pool: list[Any],
    initial_pruned: Any,
    assess_rag: AssessFn,
    assess_structure: StructureFn,
    expand_hop: ExpandFn,
    re_prune: RePruneFn,
    assess_after_expand: AssessFn,
    build_lc: BuildLcFn,
    hard_cap: int = 2,
) -> GasrLadderResult:
    """
    Execute the GASR controller.

    ``expand_hop(seeds, hops=1)`` must return a dict with at least
    ``chunks`` and ``new_chunk_count`` (and optionally diagnostics).
    """
    hops_used = 0
    assess_trace: list[dict[str, Any]] = []
    expand_trace: list[dict[str, Any]] = []
    current_pruned = initial_pruned
    neighbor_pool: list[Any] = []

    decision = assess_rag()
    assess_trace.append({"phase": "rag", "hops": 0, **decision})

    if decision.get("sufficient"):
        return GasrLadderResult(
            route="RAG",
            hops_used=0,
            context=current_pruned,
            assess_trace=assess_trace,
            expand_trace=expand_trace,
        )

    while True:
        judge = assess_structure()
        need = str(judge.get("need") or "UNCLEAR").upper()
        conf = str(judge.get("confidence") or "low").lower()
        can_expand = need == "STRUCTURE" and hops_used < hard_cap
        assess_trace.append(
            {
                "phase": "structure_vs_context",
                "hops": hops_used,
                **judge,
                "can_expand": can_expand,
            }
        )

        if not can_expand:
            return GasrLadderResult(
                route="LC",
                hops_used=hops_used,
                context=build_lc(),
                assess_trace=assess_trace,
                expand_trace=expand_trace,
            )

        expand_seeds = list(seed_pool) + list(neighbor_pool)
        expand_result = expand_hop(expand_seeds, 1)
        new_chunks = expand_result.get("chunks") or []
        hops_used += 1
        neighbor_ids = list(expand_result.get("neighbor_ids") or [])
        # Cap ids in the trace so live reports stay readable.
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
                "confidence_note": conf,
            }
        )

        if expand_result.get("new_chunk_count", 0) <= 0:
            return GasrLadderResult(
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
            return GasrLadderResult(
                route="GRAPH",
                hops_used=hops_used,
                context=current_pruned,
                assess_trace=assess_trace,
                expand_trace=expand_trace,
            )
        # else: loop for another STRUCTURE hop or LC
