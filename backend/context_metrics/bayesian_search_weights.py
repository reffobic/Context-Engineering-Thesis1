#!/usr/bin/env python3
"""
Bayesian optimization (Optuna, TPE sampler) over the same 3 weight groups
used in random_search_weights.py. Each trial still runs run_pipeline_metrics.py
in its own subprocess, for the same reason as before: weights are read once
at import time, so a fresh process is the reliable way to apply new values.

Usage:
    cd backend
    python -m context_metrics.bayesian_search_weights --trials 30
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

import optuna

# --- Fill this in with real questions about the repo you're evaluating on ---
TEST_QUERIES = [
    {
        "query": "How does create_app work in this Superset codebase? Name files and functions you rely on.",
        "gold_files": ["superset/app.py"],
    },
    {
        "query": "In superset/mcp_service/middleware.py, explain _extract_context_info precisely: input sources (message, metadata), fallback order, extracted fields (agent_id, user_id, dashboard_id, slice_id, dataset_id), and one likely failure mode. Use only CONTEXT and cite file/function names.",
        "gold_files": ["superset/mcp_service/middleware.py"],
    },
    {
        "query": "Source-first check: What is the difference between Query and SavedQuery models in Superset? Prefer model definition files over tests/docs. The expected source file is superset-core/src/superset_core/queries/models.py. If that file is not in CONTEXT, start with NOT IN CONTEXT: superset-core/src/superset_core/queries/models.py, then summarize only what is visible.",
        "gold_files": ["superset-core/src/superset_core/queries/models.py"],
    },
    {
        "query": "Source-first check: Compare SUPERSET_CONFIG vs SUPERSET_APP_ROOT in superset/app.py: precedence order, fallback behavior, and side effects. Expected path: superset/app.py. If superset/app.py is not in CONTEXT, start with NOT IN CONTEXT: superset/app.py.",
        "gold_files": ["superset/app.py"],
    },
]


# Group A — final ranking weights (retrieval_tool.py, rank_chunks). Must sum to 1.
FINAL_WEIGHT_NAMES = ["FINAL_W_HYBRID", "FINAL_W_PROXIMITY", "FINAL_W_FUNCTION", "FINAL_W_SYMBOL"]
FINAL_DEFAULTS = {"FINAL_W_HYBRID": 0.46, "FINAL_W_PROXIMITY": 0.26, "FINAL_W_FUNCTION": 0.18, "FINAL_W_SYMBOL": 0.10}

# Group B — hybrid retrieval weights (retrieval_tool.py, hybrid_retrieve). Independent, NOT sum-to-1.
HYBRID_WEIGHT_RANGES = {
    "DENSE_WEIGHT": (0.1, 0.6),
    "BM25_WEIGHT": (0.1, 0.4),
    "EXACT_WEIGHT": (0.05, 0.35),
    "KEYWORD_WEIGHT": (0.0, 0.2),
    "SOURCE_WEIGHT": (0.0, 0.3),
    "PATH_MATCH_WEIGHT": (0.0, 0.5),
    "CLASS_DEF_WEIGHT": (0.0, 0.4),
}
HYBRID_DEFAULTS = {
    "DENSE_WEIGHT": 0.42, "BM25_WEIGHT": 0.28, "EXACT_WEIGHT": 0.22, "KEYWORD_WEIGHT": 0.08,
    "SOURCE_WEIGHT": 0.14, "PATH_MATCH_WEIGHT": 0.32, "CLASS_DEF_WEIGHT": 0.28,
}

# Group C — pruning weights (pruning_tool.py, score_candidate). Must sum to 1.
PRUNE_WEIGHT_NAMES = ["PRUNE_W_RETRIEVAL", "PRUNE_W_QUERY_MATCH", "PRUNE_W_FUNCTION", "PRUNE_W_STRUCTURE"]
PRUNE_DEFAULTS = {"PRUNE_W_RETRIEVAL": 0.40, "PRUNE_W_QUERY_MATCH": 0.25, "PRUNE_W_FUNCTION": 0.20, "PRUNE_W_STRUCTURE": 0.15}


def run_one_query(query: str, gold_files: list[str], env_overrides: dict[str, str]) -> dict:
    import os
    env = os.environ.copy()
    env.update(env_overrides)

    cmd = [
        sys.executable, "-m", "context_metrics.run_pipeline_metrics",
        "-q", query,
        "--metrics", "none",
        "--compact-json",
    ]
    for gf in gold_files:
        cmd += ["--relevant-file", gf]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        print("  [warn] query timed out")
        return {}
    if proc.returncode != 0:
        print(f"  [warn] query failed: {proc.stderr[-500:]}")
        return {}
    return json.loads(proc.stdout)


def composite_score(result: dict) -> float:
    metrics = result.get("metrics", {})

    retrieval = metrics.get("precision_at_k_retrieval", {})
    pruned = metrics.get("precision_at_k_pruned", {})

    retrieval_precision = retrieval.get("precision_at_k", 0) or 0
    retrieval_recall = retrieval.get("recall_at_k", 0) or 0

    pruned_precision = pruned.get("precision_at_k", 0) or 0
    pruned_recall = pruned.get("recall_at_k", 0) or 0

    return statistics.mean([
        retrieval_precision,
        retrieval_recall,
        pruned_precision,
        pruned_recall,
    ])


def run_trial(env_overrides: dict[str, str]) -> float:
    scores = []
    for tc in TEST_QUERIES:
        result = run_one_query(tc["query"], tc["gold_files"], env_overrides)
        if result:
            scores.append(composite_score(result))
    return statistics.mean(scores) if scores else 0.0


def objective(trial: optuna.Trial) -> float:
    # Simplex groups: sample each freely, then normalize to sum to 1.
    # (Simplification vs. true Dirichlet sampling — worth noting in your write-up.)
    final_raw = {n: trial.suggest_float(n, 0.01, 1.0) for n in FINAL_WEIGHT_NAMES}
    final_total = sum(final_raw.values())
    final_weights = {n: v / final_total for n, v in final_raw.items()}

    hybrid_weights = {n: trial.suggest_float(n, lo, hi) for n, (lo, hi) in HYBRID_WEIGHT_RANGES.items()}

    prune_raw = {n: trial.suggest_float(n, 0.01, 1.0) for n in PRUNE_WEIGHT_NAMES}
    prune_total = sum(prune_raw.values())
    prune_weights = {n: v / prune_total for n, v in prune_raw.items()}

    env_overrides = {
        **{n: f"{v:.4f}" for n, v in final_weights.items()},
        **{n: f"{v:.4f}" for n, v in hybrid_weights.items()},
        **{n: f"{v:.4f}" for n, v in prune_weights.items()},
    }

    try:
        return run_trial(env_overrides)
    except Exception as exc:
        print(f"  [warn] trial failed entirely: {exc}")
        return 0.0  # worst possible score, don't crash the whole study


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
        storage="sqlite:///bayes_search.db",   # resumable if interrupted/crashes
        study_name="weight_search",
        load_if_exists=True,
    )

    # Seed the very first trial with your repo's current default weights,
    # so trial 0 is a real baseline comparison point.
    study.enqueue_trial({**FINAL_DEFAULTS, **HYBRID_DEFAULTS, **PRUNE_DEFAULTS})

    study.optimize(objective, n_trials=args.trials)

    print(f"\nBest composite score: {study.best_value:.4f}")
    print(f"Best weights: {study.best_params}")

    out_path = Path(__file__).parent / "bayesian_search_results.json"
    out_path.write_text(json.dumps(
        [{"trial": t.number, "value": t.value, "params": t.params} for t in study.trials],
        indent=2,
    ))
    print(f"Full results saved to: {out_path}")


if __name__ == "__main__":
    main()
