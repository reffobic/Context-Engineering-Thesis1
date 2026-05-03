#!/usr/bin/env python3
"""
Run the MCP tool pipeline and score stages with DeepEval LLM-as-judge metrics.

Judge model defaults to local Ollama (many metric calls per run — budget API usage accordingly).

Environment:
  EVAL_JUDGE_MODEL   Model tag for metrics only (default: llama3.2).
  OLLAMA_BASE_URL    Ollama HTTP API (default: http://localhost:11434).

Examples:
  cd backend && python -m context_metrics.run_pipeline_metrics -q "How does Flask route URLs?"
  cd backend && python -m context_metrics.run_pipeline_metrics -q "..." --skip-generate \\
      --metrics contextual_relevancy
  cd backend && python -m context_metrics.run_pipeline_metrics -q "..." \\
      --relevant-file flask/app.py --relevant-file flask/blueprints.py --skip-generate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent


def _ensure_sys_path() -> None:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def chunk_text(item: dict[str, Any]) -> str:
    """Normalize retrieval or pruned chunk dicts into one string for metrics."""
    meta = item.get("metadata") or {}
    path = item.get("path") or meta.get("file") or ""
    symbol = item.get("symbol") or meta.get("function") or ""
    header_bits = [p for p in (path, symbol) if p]
    header = " — ".join(header_bits) if header_bits else "(chunk)"
    body = item.get("text") if "text" in item else item.get("code") or ""
    body = (body or "").strip()
    return f"{header}\n{body}".strip()


def chunks_as_retrieval_context(items: list[dict[str, Any]]) -> list[str]:
    out = [chunk_text(c) for c in items]
    return [x for x in out if x]


def chunk_file_path(item: dict[str, Any]) -> str:
    meta = item.get("metadata") or {}
    return str(item.get("path") or meta.get("file") or "")


def normalize_path(s: str) -> str:
    return str(s).replace("\\", "/").strip().lower()


def path_matches_gold(chunk_path: str, gold: str) -> bool:
    """True if chunk_path refers to the same file as gold (substring / suffix / basename)."""
    p = normalize_path(chunk_path)
    g = normalize_path(gold)
    if not p or not g:
        return False
    if p.endswith(g) or p.endswith("/" + g.lstrip("/")):
        return True
    if g in p:
        return True
    return Path(p).name == Path(g).name


def precision_recall_at_k(
    ranked: list[dict[str, Any]],
    k: int,
    gold_files: list[str],
) -> dict[str, Any]:
    """
    IR-style metrics with **file-level** gold labels.

    - **Precision@k**: among rank positions ``1..k``, fraction where the chunk's file matches
      at least one gold path (positions beyond ``len(ranked)`` count as non-relevant).
    - **Recall@k**: fraction of distinct gold files that appear in at least one chunk in the
      first ``min(k, len(ranked))`` results (denominator = number of distinct gold paths).
    """
    raw_gold = [g.strip() for g in gold_files if g and str(g).strip()]
    if not raw_gold:
        return {"skipped": True, "reason": "No gold paths provided."}

    seen_norm: set[str] = set()
    gold_ordered: list[str] = []
    for g in raw_gold:
        gn = normalize_path(g)
        if gn not in seen_norm:
            seen_norm.add(gn)
            gold_ordered.append(g)

    n = len(ranked)
    depth = min(k, n)
    hits = 0
    for i in range(depth):
        path = chunk_file_path(ranked[i])
        if any(path_matches_gold(path, g) for g in gold_ordered):
            hits += 1

    precision_at_k = hits / float(k) if k > 0 else 0.0

    hit_gold: set[str] = set()
    for g in gold_ordered:
        for i in range(depth):
            if path_matches_gold(chunk_file_path(ranked[i]), g):
                hit_gold.add(normalize_path(g))
                break

    recall_at_k = len(hit_gold) / float(len(seen_norm)) if seen_norm else 0.0

    missed = sorted(seen_norm - hit_gold)
    return {
        "k": k,
        "n_ranked": n,
        "depth_evaluated": depth,
        "gold_files_distinct": len(seen_norm),
        "precision_at_k": round(precision_at_k, 6),
        "recall_at_k": round(recall_at_k, 6),
        "relevant_chunks_in_top_depth": hits,
        "gold_norm_hit": sorted(hit_gold),
        "gold_norm_missed": missed,
        "definition": {
            "precision_at_k": "relevant_chunks among first depth positions / k (missing ranks count as irrelevant).",
            "recall_at_k": "distinct gold files with at least one chunk in top min(k,n) / |distinct gold|.",
        },
    }


def structural_pipeline_stats(
    retrieved: list[dict[str, Any]],
    pruned: list[dict[str, Any]],
    k_report: int,
) -> dict[str, Any]:
    """
    Cheap, deterministic summaries (no gold labels, no judge LLM).
    Pruned chunks typically omit ``final_score``; means are reported only when present.
    """

    def top_mean_final(items: list[dict[str, Any]], n: int) -> float | None:
        slice_ = items[: min(n, len(items))]
        vals: list[float] = []
        for it in slice_:
            v = it.get("final_score")
            if v is None:
                v = it.get("hybrid_score")
            if v is None:
                v = it.get("similarity")
            if v is not None:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
        if not vals:
            return None
        return round(sum(vals) / len(vals), 6)

    def unique_paths(items: list[dict[str, Any]], n: int) -> int:
        paths = {chunk_file_path(it) for it in items[: min(n, len(items))]}
        return len([p for p in paths if p])

    return {
        "retrieved_top_mean_rank_score": top_mean_final(retrieved, min(5, k_report)),
        "retrieved_unique_paths_in_top_k": unique_paths(retrieved, k_report),
        "retrieved_top_paths": [chunk_file_path(c) for c in retrieved[: min(5, len(retrieved))]],
        "pruned_chunk_count": len(pruned),
        "pruned_unique_paths": unique_paths(pruned, len(pruned)),
        "pruned_paths": [chunk_file_path(c) for c in pruned[:15]],
    }


def measure_contextual_relevancy_only(query: str, retrieve_ctx: list[str]) -> dict[str, Any]:
    """Single DeepEval contextual-relevancy call (judge LLM)."""
    if not retrieve_ctx:
        return {"skipped": True, "reason": "No retrieved chunks to score."}
    from deepeval.metrics import ContextualRelevancyMetric
    from deepeval.test_case import LLMTestCase

    judge = build_judge_model()
    m = ContextualRelevancyMetric(model=judge, async_mode=False)
    m.measure(LLMTestCase(input=query, retrieval_context=retrieve_ctx))
    row = metric_result(m, "after_retrieval")
    row["judge_model"] = judge.get_model_name()
    return row


def metric_result(metric: Any, stage: str) -> dict[str, Any]:
    return {
        "score": getattr(metric, "score", None),
        "success": getattr(metric, "success", None),
        "reason": getattr(metric, "reason", None),
        "stage": stage,
    }


def build_judge_model():
    from deepeval.models import OllamaModel

    model = os.environ.get("EVAL_JUDGE_MODEL", "llama3.2")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    return OllamaModel(model=model, base_url=base_url, temperature=0.0)


_VALID_METRICS = frozenset(
    {"faithfulness", "contextual_relevancy", "answer_relevancy", "hallucination"}
)


def parse_metrics_arg(raw: str) -> list[str]:
    stripped = raw.strip()
    if stripped.lower() in ("", "none", "-"):
        return []
    names = [x.strip().lower() for x in raw.split(",") if x.strip()]
    unknown = [n for n in names if n not in _VALID_METRICS]
    if unknown:
        raise SystemExit(f"Unknown metric(s): {unknown}. Choices: {sorted(_VALID_METRICS)}")
    return names


def run(args: argparse.Namespace) -> dict[str, Any]:
    _ensure_sys_path()
    from mcp_server.tool_registry import call_tool

    query = args.query
    metric_names = parse_metrics_arg(args.metrics)
    gold_files: list[str] = list(args.relevant_file or [])

    judge_holder: list[Any] = []

    def judge():
        if not judge_holder:
            judge_holder.append(build_judge_model())
        return judge_holder[0]

    retrieved = call_tool("retrieve_context", query, args.top_k)
    pruned = call_tool(
        "prune_context",
        query,
        retrieved,
        token_budget=args.token_budget,
    )

    retrieve_ctx = chunks_as_retrieval_context(retrieved)
    prune_ctx = chunks_as_retrieval_context(pruned)

    result: dict[str, Any] = {
        "query": query,
        "pipeline_counts": {
            "retrieved_chunks": len(retrieved),
            "pruned_chunks": len(pruned),
            "retrieve_context_strings": len(retrieve_ctx),
            "prune_context_strings": len(prune_ctx),
        },
        "metrics": {},
    }

    needs_answer = {"faithfulness", "answer_relevancy", "hallucination"}

    if gold_files:
        pr = precision_recall_at_k(retrieved, args.top_k, gold_files)
        pr["stage"] = "after_retrieval"
        result["metrics"]["precision_at_k_retrieval"] = pr

        k_pruned = len(pruned)
        if k_pruned > 0:
            pp = precision_recall_at_k(pruned, k_pruned, gold_files)
            pp["stage"] = "after_prune"
            pp["note"] = (
                "k equals len(pruned); recall is how many distinct gold files "
                "still have at least one chunk in the pruned bundle."
            )
            result["metrics"]["precision_at_k_pruned"] = pp
        else:
            result["metrics"]["precision_at_k_pruned"] = {
                "skipped": True,
                "reason": "No chunks after pruning.",
                "stage": "after_prune",
            }

    if metric_names:
        result["judge_model"] = judge().get_model_name()

    if "contextual_relevancy" in metric_names:
        from deepeval.metrics import ContextualRelevancyMetric
        from deepeval.test_case import LLMTestCase

        if not retrieve_ctx:
            result["metrics"]["contextual_relevancy_retrieval"] = {
                "skipped": True,
                "reason": "No retrieved chunks to score.",
                "stage": "after_retrieval",
            }
        else:
            m = ContextualRelevancyMetric(model=judge(), async_mode=False)
            tc = LLMTestCase(input=query, retrieval_context=retrieve_ctx)
            m.measure(tc)
            result["metrics"]["contextual_relevancy_retrieval"] = metric_result(
                m, "after_retrieval"
            )

    if args.skip_generate:
        skipped = sorted(needs_answer & set(metric_names))
        if skipped:
            result["warnings"] = [
                f"Skipped metrics requiring generation: {skipped} "
                "(omit --skip-generate or drop those metrics)."
            ]
        return result

    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualRelevancyMetric,
        FaithfulnessMetric,
        HallucinationMetric,
    )
    from deepeval.test_case import LLMTestCase

    prompt = call_tool("inject_context", query, pruned)
    answer = call_tool("ollama_generate", prompt)
    if not isinstance(answer, str):
        answer = str(answer)

    result["answer_preview"] = answer[:500] + ("..." if len(answer) > 500 else "")

    if "contextual_relevancy" in metric_names:
        if not prune_ctx:
            result["metrics"]["contextual_relevancy_pruned"] = {
                "skipped": True,
                "reason": "No chunks after pruning.",
                "stage": "after_prune_before_generate",
            }
        else:
            m = ContextualRelevancyMetric(model=judge(), async_mode=False)
            m.measure(LLMTestCase(input=query, retrieval_context=prune_ctx))
            result["metrics"]["contextual_relevancy_pruned"] = metric_result(
                m, "after_prune_before_generate"
            )

    grounded = prune_ctx if prune_ctx else retrieve_ctx

    if "faithfulness" in metric_names:
        if not grounded:
            result["metrics"]["faithfulness"] = {
                "skipped": True,
                "reason": "Empty context bundle.",
                "stage": "after_generate",
            }
        else:
            m = FaithfulnessMetric(model=judge(), async_mode=False)
            m.measure(
                LLMTestCase(
                    input=query,
                    actual_output=answer,
                    retrieval_context=grounded,
                )
            )
            result["metrics"]["faithfulness"] = metric_result(m, "after_generate")

    if "hallucination" in metric_names:
        if not grounded:
            result["metrics"]["hallucination"] = {
                "skipped": True,
                "reason": "Empty context bundle.",
                "stage": "after_generate",
            }
        else:
            m = HallucinationMetric(model=judge(), async_mode=False)
            m.measure(
                LLMTestCase(
                    input=query,
                    actual_output=answer,
                    context=grounded,
                )
            )
            result["metrics"]["hallucination"] = metric_result(m, "after_generate")

    if "answer_relevancy" in metric_names:
        m = AnswerRelevancyMetric(model=judge(), async_mode=False)
        m.measure(LLMTestCase(input=query, actual_output=answer))
        row = metric_result(m, "after_generate")
        row["note"] = "Whether the answer addresses the question (ignores CONTEXT)."
        result["metrics"]["answer_relevancy"] = row

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepEval metrics over the context pipeline.")
    parser.add_argument("-q", "--query", required=True, help="User prompt / question.")
    parser.add_argument("--top-k", type=int, default=10, help="retrieve_context top_k.")
    parser.add_argument("--token-budget", type=int, default=1800, help="prune_context token budget.")
    parser.add_argument(
        "--metrics",
        default="faithfulness,contextual_relevancy,answer_relevancy",
        help=(
            "Comma-separated: faithfulness, contextual_relevancy, answer_relevancy, hallucination. "
            "Use 'none' to skip all DeepEval judge metrics (e.g. only precision/recall with --relevant-file). "
            "contextual_relevancy runs twice (on retrieved vs pruned bundles) when not --skip-generate."
        ),
    )
    parser.add_argument(
        "--relevant-file",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Gold file path (repeatable). Enables precision@k / recall@k vs chunk metadata paths "
            "after retrieval and after prune."
        ),
    )
    parser.add_argument(
        "--skip-generate",
        action="store_true",
        help="Only score retrieval with contextual_relevancy (no Ollama answer or post-generate metrics).",
    )
    parser.add_argument(
        "--compact-json",
        action="store_true",
        help="Single-line JSON (default is indented).",
    )
    args = parser.parse_args()
    if not parse_metrics_arg(args.metrics) and not (args.relevant_file):
        parser.error("Nothing to score: set --metrics or pass at least one --relevant-file.")
    payload = run(args)
    print(json.dumps(payload, indent=None if args.compact_json else 2, default=str))


if __name__ == "__main__":
    main()
