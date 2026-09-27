#!/usr/bin/env python3
"""
Multi-objective Bayesian optimization (Optuna, TPE sampler) over the 3 weight
groups used across retrieval_tool.py and pruning_tool.py. Each trial runs
run_pipeline_metrics.py in its own subprocess, because the weight constants
are read once at import time — a fresh process is the reliable way to apply
new env-var values each trial.

This optimizes the 4 thesis metrics SEPARATELY and simultaneously — Precision@k,
Recall@k, Faithfulness, Contextual Relevancy — rather than averaging them into
one composite score. The result is a Pareto front: the set of trials where no
other trial beats it on every metric at once. There is no single "best" trial;
study.best_trials gives you the whole non-dominated set to choose from.

Prerequisites before running:
  - Ollama running with your judge model warmed up (`ollama run <model>` once,
    wait for a real response) — this script re-enables generation calls
    (faithfulness/relevancy both need a real LLM answer), which are the slow,
    timeout-prone part of the pipeline.
  - Consider `export OLLAMA_KEEP_ALIVE=30m` so the model doesn't unload mid-run.

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

# The 4 thesis metrics, kept SEPARATE — order here must match `directions` in
# main() and the tuple returned by objective(). Precision/Recall are the
# non-LLM metrics (cheap, from retrieval metadata); Faithfulness/Relevancy are
# the LLM-as-judge metrics (require a real Ollama generation call per query).
METRIC_NAMES = ["precision_at_k", "recall_at_k", "faithfulness", "relevancy"]


def run_one_query(query: str, gold_files: list[str], env_overrides: dict[str, str]) -> dict:
    import os, time
    env = os.environ.copy()
    env.update(env_overrides)

    cmd = [
        sys.executable, "-m", "context_metrics.run_pipeline_metrics",
        "-q", query,
        "--metrics", "faithfulness,contextual_relevancy",
        "--compact-json",
    ]
    for gf in gold_files:
        cmd += ["--relevant-file", gf]

    print(f"    -> running query: {query[:60]}...")
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        print(f"  [warn] query timed out after {time.time()-start:.0f}s")
        return {}
    print(f"    <- done in {time.time()-start:.0f}s")
    if proc.returncode != 0:
        print(f"  [warn] query failed: {proc.stderr[-500:]}")
        return {}
    return json.loads(proc.stdout)


def extract_metrics(result: dict) -> dict[str, float]:
    """Pull out the 4 thesis metrics individually — no averaging here."""
    metrics = result.get("metrics", {})
    retrieval = metrics.get("precision_at_k_retrieval", {})
    return {
        "precision_at_k": retrieval.get("precision_at_k", 0) or 0,
        "recall_at_k": retrieval.get("recall_at_k", 0) or 0,
        "faithfulness": metrics.get("faithfulness", {}).get("score", 0) or 0,
        "relevancy": metrics.get("contextual_relevancy_pruned", {}).get("score", 0) or 0,
    }


def run_trial(env_overrides: dict[str, str]) -> dict[str, float]:
    """Run every test query; average EACH metric across queries separately (still no combining across metrics)."""
    per_metric: dict[str, list[float]] = {name: [] for name in METRIC_NAMES}

    for tc in TEST_QUERIES:
        result = run_one_query(tc["query"], tc["gold_files"], env_overrides)
        if not result:
            continue
        m = extract_metrics(result)
        for name in METRIC_NAMES:
            per_metric[name].append(m[name])

    return {name: (statistics.mean(values) if values else 0.0) for name, values in per_metric.items()}


def objective(trial: optuna.Trial) -> tuple[float, float, float, float]:
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
        breakdown = run_trial(env_overrides)
    except Exception as exc:
        print(f"  [warn] trial failed entirely: {exc}")
        return 0.0, 0.0, 0.0, 0.0

    # Order MUST match METRIC_NAMES / directions
    return (
        breakdown["precision_at_k"],
        breakdown["recall_at_k"],
        breakdown["faithfulness"],
        breakdown["relevancy"],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()

    study = optuna.create_study(
        directions=["maximize", "maximize", "maximize", "maximize"],  # one per METRIC_NAMES entry
        sampler=optuna.samplers.TPESampler(seed=42),  # TPESampler handles multi-objective automatically
        storage="sqlite:///bayes_search_multiobj.db",
        study_name="weight_search_multiobj",
        load_if_exists=True,
    )

    if len(study.trials) == 0:
        study.enqueue_trial({**FINAL_DEFAULTS, **HYBRID_DEFAULTS, **PRUNE_DEFAULTS})

    study.optimize(objective, n_trials=args.trials)

    front = study.best_trials  # the Pareto front — no single "best" anymore
    print(f"\n{len(front)} trials on the Pareto front (out of {len(study.trials)} total):\n")
    for t in sorted(front, key=lambda t: t.number):
        vals = dict(zip(METRIC_NAMES, t.values))
        print(f"trial {t.number}: " + "  ".join(f"{k}={v:.4f}" for k, v in vals.items()))

    out_path = Path(__file__).parent / "pareto_front_results.json"
    out_path.write_text(json.dumps(
        [
            {"trial": t.number, "values": dict(zip(METRIC_NAMES, t.values)), "params": t.params}
            for t in study.trials
        ],
        indent=2,
    ))
    print(f"\nAll trials (front + dominated) saved to: {out_path}")


if __name__ == "__main__":
    main()
