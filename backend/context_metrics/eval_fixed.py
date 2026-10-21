#!/usr/bin/env python3
"""
Re-evaluate fixed weight configs from the Bayesian search (or repo defaults).

Typical uses:
  # Measure judge noise on the baseline (trial 0): 3 repeats × 4 queries ≈ 12 runs
  python -m context_metrics.eval_fixed --trials 0 --repeats 3 --tag superset_noise

  # Compare Pareto candidates
  python -m context_metrics.eval_fixed --trials 0,29,30,31 --repeats 2 --tag front_check
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from context_metrics.bayesian_search_weights import (
    EXTRA_KEYS,
    FINAL_DEFAULTS,
    HYBRID_DEFAULTS,
    JUDGE_MODEL,
    METRIC_NAMES,
    PRUNE_DEFAULTS,
    TEST_QUERIES,
    extract_metrics,
    ollama_preflight,
    run_one_query,
)

PARETO_PATH = Path(__file__).parent / "pareto_v2_results.json"
ALL_KEYS = METRIC_NAMES + EXTRA_KEYS + ["answer_chars"]


def load_trial_params(trial_id: int) -> dict[str, float]:
    """Load weight params for a trial from pareto_v2_results.json, else defaults for 0."""
    if PARETO_PATH.exists():
        rows = json.loads(PARETO_PATH.read_text())
        for row in rows:
            if int(row.get("trial", -1)) == trial_id:
                return {k: float(v) for k, v in row["params"].items()}
    if trial_id == 0:
        return {**FINAL_DEFAULTS, **HYBRID_DEFAULTS, **PRUNE_DEFAULTS}
    sys.exit(
        f"[eval_fixed] trial {trial_id} not found in {PARETO_PATH}. "
        "Run bayesian_search_weights first, or use --trials 0 for defaults."
    )


def params_to_env(params: dict[str, float]) -> dict[str, str]:
    return {k: f"{float(v):.4f}" for k, v in params.items()}


def extract_with_chars(result: dict) -> dict[str, float]:
    row = extract_metrics(result)
    row["answer_chars"] = float(result.get("answer_chars") or 0)
    return row


def load_queries(path: Path | None) -> list[dict]:
    if path is None:
        return list(TEST_QUERIES)
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not data:
        sys.exit(f"[eval_fixed] queries file must be a non-empty JSON list: {path}")
    for i, row in enumerate(data):
        if "query" not in row or "gold_files" not in row:
            sys.exit(f"[eval_fixed] query {i} missing query/gold_files in {path}")
    return data


def run_repeat(
    env_overrides: dict[str, str],
    queries: list[dict],
) -> tuple[dict[str, float], list[dict]]:
    per_query: list[dict] = []
    for tc in queries:
        result = run_one_query(tc["query"], tc["gold_files"], env_overrides)
        if not result:
            continue
        per_query.append({"query": tc["query"][:80], **extract_with_chars(result)})
    if not per_query:
        return {}, []
    means = {k: statistics.mean(q[k] for q in per_query) for k in ALL_KEYS}
    return means, per_query


def summarize_repeats(repeats: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for k in ALL_KEYS:
        vals = [r[k] for r in repeats if k in r]
        if not vals:
            continue
        out[k] = {
            "mean": statistics.mean(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "min": min(vals),
            "max": max(vals),
            "n": len(vals),
        }
    return out


def parse_trials(s: str) -> list[int]:
    ids: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    if not ids:
        sys.exit("[eval_fixed] --trials must list at least one integer (e.g. 0 or 0,31)")
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run fixed Bayesian weight configs.")
    parser.add_argument(
        "--trials",
        default="0",
        help="Comma-separated Optuna trial ids from pareto_v2_results.json (default: 0).",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="How many full 4-query passes per trial (for judge noise).",
    )
    parser.add_argument(
        "--tag",
        default="fixed",
        help="Suffix for the output JSON filename.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip the Ollama warm-up check.",
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=None,
        help="Optional JSON list of {query, gold_files}. Defaults to Superset TEST_QUERIES.",
    )
    args = parser.parse_args()

    if args.repeats < 1:
        sys.exit("[eval_fixed] --repeats must be >= 1")

    queries = load_queries(args.queries)
    trial_ids = parse_trials(args.trials)
    if not args.skip_preflight:
        ollama_preflight()

    print(
        f"[eval_fixed] judge={JUDGE_MODEL} trials={trial_ids} "
        f"repeats={args.repeats} queries={len(queries)} tag={args.tag}"
    )

    payload: dict = {
        "tag": args.tag,
        "judge_model": JUDGE_MODEL,
        "n_queries": len(queries),
        "queries_path": str(args.queries) if args.queries else None,
        "repeats": args.repeats,
        "trials": [],
    }

    for tid in trial_ids:
        params = load_trial_params(tid)
        env = params_to_env(params)
        print(f"\n=== trial {tid} ({args.repeats} repeat(s)) ===")
        repeat_means: list[dict[str, float]] = []
        repeat_details: list[dict] = []
        for r in range(args.repeats):
            print(f"  -- repeat {r + 1}/{args.repeats} --")
            t0 = time.time()
            means, per_query = run_repeat(env, queries)
            elapsed = time.time() - t0
            if not means:
                print(f"  [warn] repeat {r + 1} produced no successful queries")
                continue
            print(
                "  means: "
                + "  ".join(f"{k}={means[k]:.4f}" for k in METRIC_NAMES)
                + f"  answer_chars={means['answer_chars']:.0f}  ({elapsed:.0f}s)"
            )
            repeat_means.append(means)
            repeat_details.append({"repeat": r + 1, "means": means, "per_query": per_query, "elapsed_s": elapsed})

        summary = summarize_repeats(repeat_means)
        if summary:
            print("  summary (mean ± stdev):")
            for k in METRIC_NAMES + ["answer_chars"]:
                if k not in summary:
                    continue
                s = summary[k]
                print(f"    {k}: {s['mean']:.4f} ± {s['stdev']:.4f}  [{s['min']:.4f}, {s['max']:.4f}]")

        payload["trials"].append(
            {
                "trial": tid,
                "params": params,
                "summary": summary,
                "repeats": repeat_details,
            }
        )

    out_path = Path(__file__).parent / f"eval_fixed_{args.tag}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\n[eval_fixed] wrote {out_path}")


if __name__ == "__main__":
    main()
