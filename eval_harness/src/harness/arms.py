"""Arms that the pipeline does not provide. ALWAYS_GRAPH (D7) is composed by the harness from the
pipeline's own functions: retrieve -> prune -> GASR seed pool -> graph expand (fixed hops, no judge)
-> graph-aware re-prune -> inject -> generate. Labelled arm_impl="harness" in every log line."""
from __future__ import annotations

import os


def _shaped(chunks: list[dict], source: str) -> list[dict]:
    """Same mapping as the nested helper in server.run_pipeline (_to_retrieve_shaped)."""
    out = []
    for c in chunks:
        if c.get("metadata") and c.get("code") is not None:
            item = dict(c)
            item.setdefault("source", source)
            out.append(item)
            continue
        out.append({
            "code": c.get("text") or c.get("code") or "",
            "metadata": {"file": c.get("path") or "", "function": c.get("symbol") or "",
                         "language": c.get("language") or "", "start_line": c.get("start_line") or 0},
            "final_score": float(c.get("final_score") or c.get("pruning_score") or 0.5),
            "hybrid_score": float(c.get("hybrid_score") or 0.5),
            "source": source,
        })
    return out


def run_always_graph(server, prompt: str, hops: int, pruning_method: str) -> dict:
    from diamond.gasr_seeds import build_gasr_seed_pool
    from retrieval.graph_expand import graph_expand

    token_budget = 1800                                    # same constant as run_pipeline
    max_neighbors = int(os.environ.get("GASR_MAX_NEIGHBORS", "8"))
    fraction = float(os.environ.get("GASR_NEIGHBOR_BUDGET_FRACTION", "0.30"))
    context = server.call_tool("retrieve_context", prompt)
    pruned, _stats = server.prune_context_with_stats(prompt, context, token_budget=token_budget, method=pruning_method)
    seeds = _shaped(build_gasr_seed_pool(pruned, context), "gasr_seed")
    pool: list[dict] = []
    current = pruned
    hops_used = 0
    for _ in range(hops):
        res = graph_expand(list(seeds) + list(pool), hops=1, max_neighbors=max_neighbors)
        hops_used += 1
        new = res.get("chunks") or []
        if res.get("new_chunk_count", 0) <= 0:
            break
        pool.extend(new)
        current, _s = server.prune_context_graph_aware(
            prompt, list(seeds), list(pool), token_budget=token_budget,
            neighbor_budget_fraction=fraction, method=pruning_method)
    final_prompt = server.call_tool("inject_context", prompt, current)
    answer = server.call_tool("ollama_generate", final_prompt)
    return {"output": answer, "route": "GRAPH", "tokens_injected_est": None,
            "pipeline": {"mode": "ALWAYS_GRAPH", "route": "GRAPH", "hops_used": hops_used,
                         "stages": {"inject": {"raw": {"chunks": list(current) if isinstance(current, list) else []}}}}}
