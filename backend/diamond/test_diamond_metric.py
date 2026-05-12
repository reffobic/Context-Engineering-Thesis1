"""
Diamond + contextual relevancy integration test.

Runs a query in RAG, LC, and SELF_ROUTE modes, scores contextual relevancy
for each, and outputs a comparison table.

Usage:
    cd backend
    python diamond/test_diamond_metrics.py -q "What class handles security in Superset?"
    python diamond/test_diamond_metrics.py -q "..." --relevant-file superset/security/manager.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.tool_registry import call_tool
from pruning.file import prune_context as prune_context_with_stats
from diamond.sufficiency_assessor import assess_sufficiency
from context_metrics.run_pipeline_metrics import (
    chunks_as_retrieval_context,
    measure_contextual_relevancy_only,
    precision_recall_at_k,
)


def _build_lc_chunks(raw_retrieved: list[dict]) -> list[dict]:
    lc_chunks = []
    for item in raw_retrieved:
        pc = item.get("parent_context") or item.get("code") or ""
        if not pc.strip():
            continue
        meta = item.get("metadata") or {}
        lc_chunks.append({
            "text":     pc,
            "path":     meta.get("file") or "",
            "symbol":   meta.get("function") or "",
            "language": meta.get("language") or "",
        })
    return lc_chunks


def _row(cols, widths):
    return "│ " + " │ ".join(str(c).ljust(w) for c, w in zip(cols, widths)) + " │"

def _sep(widths, left="├", mid="┼", right="┤"):
    return left + mid.join("─" * (w + 2) for w in widths) + right

def _header(cols, widths):
    top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    return f"{top}\n{_row(cols, widths)}\n{_sep(widths)}"

def _footer(widths):
    return "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"


def run(query: str, top_k: int, token_budget: int, gold_files: list[str]) -> None:
    print(f"\n{'='*60}")
    print(f"  Query: {query[:80]}")
    print(f"  top_k={top_k}  token_budget={token_budget}")
    print(f"{'='*60}\n")

    # ── shared retrieval + pruning ────────────────────────────────────────────
    print("Retrieving...")
    retrieved = call_tool("retrieve_context", query, top_k)

    print("Pruning...")
    pruned, stats = prune_context_with_stats(query, retrieved, token_budget=token_budget)

    # ── SELF_ROUTE decision ───────────────────────────────────────────────────
    print("Assessing sufficiency...")
    decision = assess_sufficiency(query, pruned)
    print(f"  → route={decision['route']} confidence={decision['confidence']}")
    print(f"  → reason: {decision['reason']}\n")

    # ── build context for each mode ───────────────────────────────────────────
    lc_chunks   = _build_lc_chunks(retrieved)
    sr_chunks   = lc_chunks if decision["route"] == "LC" else pruned

    rag_ctx = chunks_as_retrieval_context(pruned)
    lc_ctx  = chunks_as_retrieval_context(lc_chunks)
    sr_ctx  = chunks_as_retrieval_context(sr_chunks)

    modes = [
        ("RAG",       pruned,    rag_ctx),
        ("LC",        lc_chunks, lc_ctx),
        ("SELF_ROUTE", sr_chunks, sr_ctx),
    ]

    # ── contextual relevancy ──────────────────────────────────────────────────
    print("Scoring contextual relevancy (this calls the judge LLM)...")
    relevancy_results = {}
    for mode, chunks, ctx in modes:
        print(f"  Scoring {mode}...")
        result = measure_contextual_relevancy_only(query, ctx)
        relevancy_results[mode] = result

    # ── precision/recall if gold files given ──────────────────────────────────
    pr_results = {}
    if gold_files:
        for mode, chunks, ctx in modes:
            pr = precision_recall_at_k(chunks, len(chunks), gold_files)
            pr_results[mode] = pr

    # ── output tables ─────────────────────────────────────────────────────────
    print("\n━━━ Contextual Relevancy by Mode ━━━")
    cols   = ["Mode", "SR Decision", "Chunks", "Relevancy Score", "Pass", "Reason"]
    widths = [12, 12, 8, 16, 6, 40]
    print(_header(cols, widths))
    for mode, chunks, ctx in modes:
        r      = relevancy_results[mode]
        sr_dec = decision["route"] if mode == "SELF_ROUTE" else "forced"
        print(_row([
            mode,
            sr_dec,
            len(chunks),
            f"{r.get('score', 'N/A')}",
            "✓" if r.get("success") else "✗",
            str(r.get("reason", ""))[:40],
        ], widths))
    print(_footer(widths))

    if pr_results:
        print("\n━━━ Precision & Recall by Mode ━━━")
        cols   = ["Mode", "Chunks", "Precision@k", "Recall@k", "Gold hits"]
        widths = [12, 8, 12, 10, 30]
        print(_header(cols, widths))
        for mode, chunks, ctx in modes:
            pr = pr_results[mode]
            print(_row([
                mode,
                len(chunks),
                f"{pr.get('precision_at_k', 'N/A')}",
                f"{pr.get('recall_at_k', 'N/A')}",
                ", ".join(pr.get("gold_norm_hit", [])),
            ], widths))
        print(_footer(widths))

    # ── summary ───────────────────────────────────────────────────────────────
    print("\n━━━ Summary ━━━")
    scores = {m: relevancy_results[m].get("score") for m, _, __ in modes
              if relevancy_results[m].get("score") is not None}
    if scores:
        best = max(scores, key=scores.get)
        print(f"  Best contextual relevancy: {best} (score={scores[best]})")
        print(f"  SELF_ROUTE chose: {decision['route']}")
        sr_score  = scores.get("SELF_ROUTE")
        rag_score = scores.get("RAG")
        lc_score  = scores.get("LC")
        if sr_score and rag_score:
            diff = round(sr_score - rag_score, 4)
            print(f"  SELF_ROUTE vs RAG: {'+' if diff >= 0 else ''}{diff}")
        if sr_score and lc_score:
            diff = round(sr_score - lc_score, 4)
            print(f"  SELF_ROUTE vs LC:  {'+' if diff >= 0 else ''}{diff}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--query", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--token-budget", type=int, default=1800)
    parser.add_argument("--relevant-file", action="append", default=None, metavar="PATH")
    args = parser.parse_args()

    run(
        query=args.query,
        top_k=args.top_k,
        token_budget=args.token_budget,
        gold_files=[f.strip() for f in (args.relevant_file or []) if f.strip()],
    )


if __name__ == "__main__":
    main()