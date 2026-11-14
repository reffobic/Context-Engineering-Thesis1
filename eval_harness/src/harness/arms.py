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


# ── ALWAYS_RAG / ALWAYS_LC / PREASSESS (harness-built, arm_impl="harness") ───────────────────────────────────────
# Composed here from the pipeline's own stage functions; no backend/ file is changed. Server modes "RAG" and "LC" do
# exist in run_pipeline but are not used: the LC branch there runs a pruning stage whose output it discards, and
# PREASSESS exists only on origin/plain_preasses (not taken). Gate input, pruning and injection are the pipeline's.

HARNESS_ARMS = ("ALWAYS_GRAPH", "ALWAYS_RAG", "ALWAYS_LC", "PREASSESS")


def _build_lc_chunks(server, raw_retrieved: list[dict]) -> list[dict]:
    """Same mapping as the nested helper in server.run_pipeline (_build_lc_chunks): parent files of the retrieved chunks."""
    if any("parent_context" not in i for i in raw_retrieved):
        raw_retrieved = server.reconstruct_file_context(raw_retrieved)
    out = []
    for item in raw_retrieved:
        pc = item.get("parent_context") or item.get("code") or ""
        if not pc.strip():
            continue
        meta = item.get("metadata") or {}
        out.append({"text": pc, "path": meta.get("file") or "", "symbol": meta.get("function") or "",
                    "language": meta.get("language") or ""})
    return out


def _finish(server, arm: str, route: str, prompt: str, chunks: list[dict], assess: dict | None = None) -> dict:
    final_prompt = server.call_tool("inject_context", prompt, chunks)
    answer = server.call_tool("ollama_generate", final_prompt)
    stages: dict = {"inject": {"raw": {"chunks": list(chunks) if isinstance(chunks, list) else []}}}
    if assess is not None:
        stages["assess"] = assess
    return {"output": answer, "route": route, "tokens_injected_est": None,
            "pipeline": {"mode": arm, "route": route, "hops_used": 0, "stages": stages}}


def run_always_rag(server, prompt: str, pruning_method: str) -> dict:
    context = server.call_tool("retrieve_context", prompt)
    pruned, _stats = server.prune_context_with_stats(prompt, context, token_budget=1800, method=pruning_method)
    return _finish(server, "ALWAYS_RAG", "RAG", prompt, pruned)


def run_always_lc(server, prompt: str) -> dict:
    context = server.call_tool("retrieve_context", prompt)
    return _finish(server, "ALWAYS_LC", "LC", prompt, _build_lc_chunks(server, context))


def run_preassess(server, prompt: str, pruning_method: str) -> dict:
    """retrieve -> flat prune -> GAPA call-1 metadata gate on the RAW retrieval -> RAG if sufficient, else LC.
    No call 2, no expansion, no judge. Any failed / unparseable / raising gate call is LC."""
    import importlib
    pg = importlib.import_module("diamond.preroute_gate")           # looked up at call time: the instrument wraps this attribute
    context = server.call_tool("retrieve_context", prompt)
    pruned, _stats = server.prune_context_with_stats(prompt, context, token_budget=1800, method=pruning_method)
    try:
        d = dict(pg.preroute_structured_gate(prompt, context))
    except Exception as exc:                                         # the gate catches request / parse errors itself
        d = {"sufficient": False, "confidence": "low", "stage": "preassess_gate_exception",
             "reason": f"Structured judge unavailable ({exc!r}); defaulting to LC.", "route": "LC"}
    route = "RAG" if d.get("sufficient") else "LC"
    chunks = pruned if route == "RAG" else _build_lc_chunks(server, context)
    assess = {"summary": {"route": route, "hops_used": 0, "entry_gate_stage": d.get("stage"),
                          "entry_gate_token_usage": d.get("token_usage"),
                          "trace": [{"phase": "rag_gate", "hops": 0, **d}]}}
    return _finish(server, "PREASSESS", route, prompt, chunks, assess)


def run_harness_arm(server, arm: str, prompt: str, hops: int, pruning_method: str) -> dict:
    if arm == "ALWAYS_GRAPH":
        return run_always_graph(server, prompt, hops, pruning_method)
    if arm == "ALWAYS_RAG":
        return run_always_rag(server, prompt, pruning_method)
    if arm == "ALWAYS_LC":
        return run_always_lc(server, prompt)
    if arm == "PREASSESS":
        return run_preassess(server, prompt, pruning_method)
    raise ValueError(f"unknown harness arm {arm!r}")
