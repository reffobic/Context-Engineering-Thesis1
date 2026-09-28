#!/usr/bin/env python3
"""
Multi-objective Bayesian optimization (Optuna, TPE) over the 3 weight groups in
retrieval_tool.py and pruning_tool.py. Each query runs run_pipeline_metrics.py in
its own subprocess, because the weight constants are read once at import time.

Objectives (all maximized, kept separate, no composite score):
  precision_at_k  pruned stage: share of pruned chunks that come from a gold file
  recall_at_k     pruned stage: share of gold files that survive pruning
  faithfulness    DeepEval: generated answer grounded in the pruned context
  relevancy       DeepEval: contextual relevancy of the pruned context

Usage:
    cd backend
    python -m context_metrics.bayesian_search_weights --trials 30
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import optuna

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

JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "qwen3.5:9b")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
TOP_K = int(os.environ.get("BAYES_TOP_K", "5"))
STUDY_NAME = "weight_search_v2"
STORAGE = "sqlite:///bayes_search_v2.db"

# Group A: final ranking weights (retrieval_tool.py, rank_chunks). Must sum to 1.
FINAL_WEIGHT_NAMES = ["FINAL_W_HYBRID", "FINAL_W_PROXIMITY", "FINAL_W_FUNCTION", "FINAL_W_SYMBOL"]
FINAL_DEFAULTS = {"FINAL_W_HYBRID": 0.46, "FINAL_W_PROXIMITY": 0.26, "FINAL_W_FUNCTION": 0.18, "FINAL_W_SYMBOL": 0.10}

# Group B: hybrid retrieval weights (retrieval_tool.py, hybrid_retrieve). Independent, NOT sum-to-1.
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

# Group C: pruning weights (pruning_tool.py, score_candidate). Must sum to 1.
PRUNE_WEIGHT_NAMES = ["PRUNE_W_RETRIEVAL", "PRUNE_W_QUERY_MATCH", "PRUNE_W_FUNCTION", "PRUNE_W_STRUCTURE"]
PRUNE_DEFAULTS = {"PRUNE_W_RETRIEVAL": 0.40, "PRUNE_W_QUERY_MATCH": 0.25, "PRUNE_W_FUNCTION": 0.20, "PRUNE_W_STRUCTURE": 0.15}

# Order must match `directions` in main() and the tuple returned by objective().
METRIC_NAMES = ["precision_at_k", "recall_at_k", "faithfulness", "relevancy"]
EXTRA_KEYS = ["retrieval_precision", "retrieval_recall"]  # recorded for the write-up, not optimized


def ollama_preflight() -> None:
    """Fail fast (minutes, not hours) if Ollama is busy or the judge cannot answer."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=10) as r:
            loaded = [m.get("name") for m in json.loads(r.read()).get("models", [])]
    except Exception as exc:
        sys.exit(f"[preflight] Ollama not reachable at {OLLAMA_URL}: {exc}")
    others = [m for m in loaded if m and m != JUDGE_MODEL]
    if others:
        print(f"[preflight] WARNING: other models are loaded: {others}. "
              "Another process may be using Ollama; check `ollama ps` and `nvidia-smi`.")
    body = json.dumps({"model": JUDGE_MODEL, "prompt": "ok", "stream": False,
                       "keep_alive": "30m", "options": {"num_predict": 4}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    start = time.time()
    try:
        urllib.request.urlopen(req, timeout=180).read()
    except Exception as exc:
        sys.exit(f"[preflight] {JUDGE_MODEL} did not answer within 180s ({exc}). "
                 "Something else is probably occupying Ollama/GPU.")
    print(f"[preflight] {JUDGE_MODEL} answered in {time.time() - start:.1f}s")


def run_one_query(query: str, gold_files: list[str], env_overrides: dict[str, str]) -> dict:
    env = os.environ.copy()
    env.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "900")
    env.setdefault("DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE", "3600")
    env.setdefault("OLLAMA_KEEP_ALIVE", "30m")
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("EMBEDDING_DEVICE", "cpu")
    env.setdefault("EVAL_JUDGE_MODEL", JUDGE_MODEL)
    env.setdefault("OLLAMA_MODEL", JUDGE_MODEL)
    env.update(env_overrides)

    cmd = [
        sys.executable, "-m", "context_metrics.run_pipeline_metrics",
        "-q", query,
        "--top-k", str(TOP_K),
        "--metrics", "faithfulness,contextual_relevancy",
        "--skip-retrieval-relevancy",
        "--compact-json",
    ]
    for gf in gold_files:
        cmd += ["--relevant-file", gf]

    proc_timeout = int(os.environ.get("BAYES_QUERY_TIMEOUT_SECONDS", "2400"))
    print(f"    -> running query: {query[:60]}...")
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=proc_timeout)
    except subprocess.TimeoutExpired:
        print(f"  [warn] query timed out after {time.time() - start:.0f}s")
        return {}
    print(f"    <- done in {time.time() - start:.0f}s")
    if proc.returncode != 0:
        print(f"  [warn] query failed: {proc.stderr[-500:]}")
        return {}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        print("  [warn] could not parse pipeline output as JSON")
        return {}


def extract_metrics(result: dict) -> dict[str, float]:
    m = result.get("metrics", {})
    pruned = m.get("precision_at_k_pruned", {})
    retrieval = m.get("precision_at_k_retrieval", {})
    return {
        "precision_at_k": pruned.get("precision_at_k") or 0.0,
        "recall_at_k": pruned.get("recall_at_k") or 0.0,
        "faithfulness": m.get("faithfulness", {}).get("score") or 0.0,
        "relevancy": m.get("contextual_relevancy_pruned", {}).get("score") or 0.0,
        "retrieval_precision": retrieval.get("precision_at_k") or 0.0,
        "retrieval_recall": retrieval.get("recall_at_k") or 0.0,
    }


def run_trial(env_overrides: dict[str, str]) -> tuple[dict[str, float], list[dict]]:
    per_query: list[dict] = []
    for tc in TEST_QUERIES:
        result = run_one_query(tc["query"], tc["gold_files"], env_overrides)
        if result:
            per_query.append({"query": tc["query"][:80], **extract_metrics(result)})
    keys = METRIC_NAMES + EXTRA_KEYS
    means = {k: statistics.mean(q[k] for q in per_query) for k in keys} if per_query else {}
    return means, per_query


def objective(trial: optuna.Trial) -> tuple[float, float, float, float]:
    # Simplex groups: sample each freely, then normalize to sum to 1.
    # (Simplification vs. true Dirichlet sampling; worth noting in the write-up.)
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

    means, per_query = run_trial(env_overrides)
    trial.set_user_attr("n_queries_ok", len(per_query))
    trial.set_user_attr("per_query", per_query)
    if len(per_query) < len(TEST_QUERIES):
        # Failing the trial (instead of averaging fewer queries) keeps trials comparable.
        raise RuntimeError(f"only {len(per_query)}/{len(TEST_QUERIES)} queries succeeded")
    trial.set_user_attr("retrieval_precision", means["retrieval_precision"])
    trial.set_user_attr("retrieval_recall", means["retrieval_recall"])
    return tuple(means[name] for name in METRIC_NAMES)  # type: ignore[return-value]


def make_failure_guard(limit: int = 3):
    state = {"n": 0}

    def guard(study: optuna.Study, trial: optuna.trial.FrozenTrial) -> None:
        if trial.state == optuna.trial.TrialState.FAIL:
            state["n"] += 1
            if state["n"] >= limit:
                print(f"[guard] {limit} failed trials in a row; stopping. Check Ollama and err output above.")
                study.stop()
        else:
            state["n"] = 0

    return guard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()

    ollama_preflight()

    study = optuna.create_study(
        directions=["maximize"] * len(METRIC_NAMES),
        sampler=optuna.samplers.TPESampler(seed=42),
        storage=STORAGE,
        study_name=STUDY_NAME,
        load_if_exists=True,
    )

    # Trial 0 = repo defaults, the baseline. If it fails, delete bayes_search_v2.db and restart.
    if len(study.trials) == 0:
        study.enqueue_trial({**FINAL_DEFAULTS, **HYBRID_DEFAULTS, **PRUNE_DEFAULTS})

    study.optimize(
        objective,
        n_trials=args.trials,
        catch=(RuntimeError,),
        callbacks=[make_failure_guard(3)],
    )

    complete = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    front = study.best_trials if complete else []
    print(f"\n{len(front)} trials on the Pareto front ({len(complete)} complete of {len(study.trials)} total):\n")
    for t in sorted(front, key=lambda t: t.number):
        vals = dict(zip(METRIC_NAMES, t.values))
        print(f"trial {t.number}: " + "  ".join(f"{k}={v:.4f}" for k, v in vals.items()))

    out_path = Path(__file__).parent / "pareto_v2_results.json"
    out_path.write_text(json.dumps(
        [
            {
                "trial": t.number,
                "values": dict(zip(METRIC_NAMES, t.values)),
                "params": t.params,
                "user_attrs": t.user_attrs,
            }
            for t in complete
        ],
        indent=2,
    ))
    print(f"\nAll completed trials saved to: {out_path}")


if __name__ == "__main__":
    main()
