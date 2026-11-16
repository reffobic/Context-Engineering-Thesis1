#!/usr/bin/env python3
"""Export Phase 2 SWE-QA Wave 1 results.jsonl → judge candidate JSONL (question, answer).

Also writes a join index (task_id, arm, repo, route, tokens) for Table B.

Usage:
  python3 scripts/phase2_export_sweqa_candidates.py \\
    --runs-root eval_harness/data/runs/a2cfcec \\
    --tasks eval_harness/data/datasets/swe-qa-w1/tasks.jsonl \\
    --out-dir eval_harness/data/reports/phase2_sweqa_candidates
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ARMS = [
    "ALWAYS_LC",
    "ALWAYS_RAG",
    "ALWAYS_GRAPH",
    "SELF_ROUTE",
    "PREASSESS",
    "GASR",
    "GAPA",
]


def load_tasks(path: Path) -> dict[str, dict]:
    return {
        json.loads(l)["instance_id"]: json.loads(l)
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    }


def find_job_dirs(runs_root: Path, job_suffix: str = "w1") -> dict[str, Path]:
    out = {}
    for d in sorted(runs_root.iterdir()):
        if not d.is_dir():
            continue
        # preferred: p2-sweqa-ALWAYS_LC-w1 / p2-sweqa-ALWAYS_LC-rest
        for arm in ARMS:
            if d.name == f"p2-sweqa-{arm}-{job_suffix}" or d.name.endswith(f"-{arm}-{job_suffix}"):
                out[arm] = d
                break
            if d.name.startswith(f"{arm}__"):
                out[arm] = d
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--job-suffix", default="w1", help="Job dir suffix: w1 or rest")
    args = ap.parse_args()
    tasks = load_tasks(args.tasks)
    jobs = find_job_dirs(args.runs_root, job_suffix=args.job_suffix)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"arms": {}, "missing": []}

    join_path = args.out_dir / "join_metrics.jsonl"
    with join_path.open("w", encoding="utf-8", newline="\n") as join_f:
        for arm in ARMS:
            d = jobs.get(arm)
            if not d or not (d / "results.jsonl").is_file():
                meta["missing"].append(arm)
                continue
            rows = [
                json.loads(l)
                for l in (d / "results.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            cand_path = args.out_dir / f"{arm}_answers.jsonl"
            n = 0
            with cand_path.open("w", encoding="utf-8", newline="\n") as cf:
                for r in rows:
                    tid = r.get("task_id")
                    t = tasks.get(tid)
                    if not t:
                        continue
                    answer = r.get("answer") or r.get("output") or ""
                    rec = {
                        "question": t["query"],
                        "answer": answer,
                        "task_id": tid,
                        "arm": arm,
                        "repo": r.get("repo") or t.get("repo"),
                    }
                    cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    ac = r.get("answer_call") or {}
                    inj = r.get("inject") or {}
                    join = {
                        "task_id": tid,
                        "arm": arm,
                        "repo": rec["repo"],
                        "status": r.get("status"),
                        "route": r.get("route"),
                        "hops_used": r.get("hops_used"),
                        "prompt_eval_count": ac.get("prompt_eval_count"),
                        "eval_count": ac.get("eval_count"),
                        "task_total_ms": (r.get("latency_ms") or {}).get("task_total"),
                        "hit_max_context_tokens": inj.get("hit_max_context_tokens"),
                        "est_tokens_div4": inj.get("est_tokens_div4"),
                        "n_chunks": inj.get("n_chunks"),
                    }
                    join_f.write(json.dumps(join, ensure_ascii=False) + "\n")
                    n += 1
            meta["arms"][arm] = {"dir": str(d), "n": n, "candidate": str(cand_path)}
            print(f"[PASS] {arm}: {n} candidates -> {cand_path}")

    # Combined reference for Wave 1 (all three JSONLs)
    (args.out_dir / "export_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if meta["missing"]:
        print(f"[WARN] missing arms: {meta['missing']}")
        return 2
    print(f"[PASS] join metrics -> {join_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
