#!/usr/bin/env python3
"""
Where does the retriever put the gold file, and does the re-ranking stage help or hurt?
Retrieval only: no judge, no generation, no Ollama.

Run from backend/ with the index you want to probe active (backend/chroma_db) and the same env
exports you use for evaluation (HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 EMBEDDING_DEVICE=cpu):

  python -m context_metrics.probe_retrieval --queries context_metrics/eval_queries_scrapy.json \
      --strip-word Scrapy

For every query it prints, per gold file, its best rank under the FINAL order (what the pipeline uses)
and under the HYBRID-score-only order (before the re-ranking step), plus the gold chunk's score
components, the top-5 chunks, and how concentrated the top 20 is in one file. The summary compares
the two orders: if hybrid-only finds the gold file in the top 5 more often than the final order,
the re-ranking weights (FINAL_W_*) are hurting. With --strip-word every query is repeated with that
word removed. Weights come from the environment (defaults if nothing is exported).
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter

from .run_pipeline_metrics import BACKEND, _ensure_sys_path, chunk_file_path, path_matches_gold


def strip_word(query: str, word: str) -> str:
    q = re.sub(rf"\b{re.escape(word)}\b", "", query, flags=re.I)
    q = re.sub(r"\s+([,?.])", r"\1", q)
    return re.sub(r"\s+", " ", q).strip()


def gold_ranks(items: list[dict], gold: str):
    """(rank under final order, rank under hybrid-only order, first gold chunk) or (None, None, None)."""
    final_rank = next((i for i, it in enumerate(items, 1) if path_matches_gold(chunk_file_path(it), gold)), None)
    if final_rank is None:
        return None, None, None
    by_hybrid = sorted(range(len(items)), key=lambda i: -(items[i].get("hybrid_score") or 0))
    hybrid_rank = next(pos for pos, idx in enumerate(by_hybrid, 1)
                       if path_matches_gold(chunk_file_path(items[idx]), gold))
    return final_rank, hybrid_rank, items[final_rank - 1]


def scores(it: dict) -> str:
    return (f"final={it.get('final_score')} hybrid={it.get('hybrid_score')} dense={it.get('dense_score')} "
            f"bm25={it.get('bm25_score')} exact={it.get('exact_match_score')}")


def probe(call_tool, query: str, gold_files: list[str], top_k: int) -> dict:
    items = call_tool("retrieve_context", query, top_k)
    result = {}
    for g in gold_files:
        rf, rh, chunk = gold_ranks(items, g)
        result[g] = (rf, rh)
        name = g.split("/")[-1]
        if rf is None:
            print(f"  gold {name}: absent from top {top_k}")
        else:
            print(f"  gold {name}: final rank {rf}, hybrid-only rank {rh} | {scores(chunk)}")
    for i, it in enumerate(items[:5], 1):
        m = it.get("metadata") or {}
        print(f"    {i}. {chunk_file_path(it)} | {m.get('function')} | {scores(it)}")
    top20 = Counter(chunk_file_path(it) for it in items[:20])
    if top20:
        f, n = top20.most_common(1)[0]
        print(f"    top-20 concentration: {n}/{min(20, len(items))} chunks from {f}")
    return result


def summarize(results: dict, top_k: int) -> None:
    print("\n=== summary: how often a gold file is found ===")
    for label, rows in results.items():
        n = len(rows)

        def hit(k: int, idx: int) -> int:
            return sum(any(v[idx] is not None and v[idx] <= k for v in ranks.values()) for ranks in rows)

        def rec(k: int, idx: int) -> float:
            return sum(sum(1 for v in ranks.values() if v[idx] is not None and v[idx] <= k) / len(ranks)
                       for ranks in rows) / n

        print(f"[{label}]")
        print(f"  final order (pipeline):  any gold in top5 {hit(5, 0)}/{n}   top20 {hit(20, 0)}/{n}   "
              f"top{top_k} {hit(top_k, 0)}/{n}   recall@5 {rec(5, 0):.2f}")
        print(f"  hybrid-only order:       any gold in top5 {hit(5, 1)}/{n}   top20 {hit(20, 1)}/{n}   "
              f"recall@5 {rec(5, 1):.2f}")
        pairs = [v for ranks in rows for v in ranks.values() if v[0] is not None]
        down = sum(1 for rf, rh in pairs if rf > rh)
        up = sum(1 for rf, rh in pairs if rf < rh)
        print(f"  re-ranking vs hybrid score, per gold file found: pushed down {down}, "
              f"pulled up {up}, unchanged {len(pairs) - down - up}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--strip-word", help="also run every query with this word removed")
    ap.add_argument("--limit", type=int, help="only the first N queries")
    args = ap.parse_args()

    _ensure_sys_path()
    from mcp_server.tool_registry import call_tool

    print("active index:", (BACKEND / "chroma_db").resolve())
    queries = json.load(open(args.queries))
    if args.limit:
        queries = queries[: args.limit]

    variants = [("original", lambda q: q)]
    if args.strip_word:
        variants.append((f"without '{args.strip_word}'", lambda q: strip_word(q, args.strip_word)))
    results = {label: [] for label, _ in variants}

    for qi, tc in enumerate(queries, 1):
        print(f"\nQ{qi}: {tc['query'][:90]}")
        for label, fn in variants:
            print(f" [{label}]")
            results[label].append(probe(call_tool, fn(tc["query"]), tc["gold_files"], args.top_k))
    summarize(results, args.top_k)


if __name__ == "__main__":
    main()