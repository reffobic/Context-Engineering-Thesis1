#!/usr/bin/env python3
"""Score Phase 2 RepoProbe pipeline answers with upstream SimpleScorer (checklist /10).

Scorer-on-pipeline: join harness results.jsonl + labels.jsonl → RepoProbe scorer.py.
Judge via OPENAI_SCORING_* / SCORING_MODEL (see docs/phase2_runs/judge_ollama.env).

Example (smoke — browser-use × 7 arms):
  set -a && source docs/phase2_runs/judge_ollama.env && set +a
  python3 scripts/phase2_repoprobe_score_pipeline.py \\
    --arms all --repo-filter browser-use \\
    --runs-root eval_harness/data/runs/a2cfcec \\
    --labels eval_harness/data/datasets/repoprobe-py8/labels.jsonl \\
    --out-dir eval_harness/data/reports/phase2_repoprobe_py/smoke_browser_use
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
REPOPROBE = (ROOT.parent / "external" / "RepoProbe").resolve()
if not REPOPROBE.is_dir():
    raise SystemExit(f"RepoProbe not found at {REPOPROBE}")

# Stub Question without importing RepoProbe dataset_loader (avoids pandas dependency).
import types
from dataclasses import dataclass


@dataclass
class Question:
    category: str
    difficulty: str
    description: str
    reference_text: str
    scoring_criteria: str


_dl = types.ModuleType("dataset_loader")
_dl.Question = Question
sys.modules["dataset_loader"] = _dl

sys.path.insert(0, str(REPOPROBE))
from scorer import ModelAnswer, SimpleScorer  # noqa: E402

ALL_ARMS = [
    "ALWAYS_LC",
    "ALWAYS_RAG",
    "ALWAYS_GRAPH",
    "SELF_ROUTE",
    "PREASSESS",
    "GASR",
    "GAPA",
]


def _load_labels(path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        iid = row.get("instance_id") or row.get("task_id")
        if iid:
            out[str(iid)] = row
    return out


def _matches_repo(task_id: str, repo: str, needle: str) -> bool:
    n = needle.lower()
    return n in task_id.lower() or n in (repo or "").lower()


def _question_from_label(lab: Dict[str, Any]) -> Question:
    return Question(
        category=str(lab.get("taxonomy") or "unknown"),
        difficulty=str(lab.get("difficulty") or "medium"),
        description=str(lab.get("question") or ""),
        reference_text=str(lab.get("gold_answer") or lab.get("answer") or ""),
        scoring_criteria=str(lab.get("checklist") or ""),
    )


def _job_dir(runs_root: Path, arm: str) -> Path:
    return runs_root / f"p2-repoprobe-{arm}-py8"


def score_arm(
    arm: str,
    runs_root: Path,
    labels: Dict[str, Dict[str, Any]],
    repo_filter: str,
    scorer: SimpleScorer,
    num_iterations: int,
    scoring_model: str,
) -> List[Dict[str, Any]]:
    results_path = _job_dir(runs_root, arm) / "results.jsonl"
    if not results_path.is_file():
        print(f"[WARN] missing {results_path}", flush=True)
        return []

    records: List[Dict[str, Any]] = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        task_id = str(row.get("task_id") or "")
        repo = str(row.get("repo") or "")
        if repo_filter and not _matches_repo(task_id, repo, repo_filter):
            continue

        lab = labels.get(task_id)
        if lab is None:
            records.append(
                {
                    "arm": arm,
                    "task_id": task_id,
                    "repo": repo,
                    "status": row.get("status"),
                    "success": False,
                    "error_message": "label_missing",
                }
            )
            continue

        status = row.get("status")
        answer_text = str(row.get("answer") or "")
        ok = status == "ok" and bool(answer_text.strip())
        if not ok:
            records.append(
                {
                    "arm": arm,
                    "task_id": task_id,
                    "repo": repo,
                    "status": status,
                    "success": False,
                    "error_message": row.get("failure") or row.get("answer_error") or "not_ok",
                    "score": 0.0,
                    "max_score": 10.0,
                    "overall_pct": 0.0,
                }
            )
            continue

        question = _question_from_label(lab)
        model_answer = ModelAnswer(model_name=arm, answer=answer_text, success=True)
        t0 = time.time()
        scores = scorer.score_answer_multiple(question, model_answer, num_iterations, repo_info=None)
        wall_s = time.time() - t0
        successful = [s for s in scores if s.success]
        if not successful:
            err = scores[0].error_message if scores else "no_score"
            print(f"  [FAIL] {arm} {task_id}: {err}", flush=True)
            records.append(
                {
                    "arm": arm,
                    "task_id": task_id,
                    "repo": repo,
                    "status": status,
                    "success": False,
                    "error_message": err,
                    "wall_s": wall_s,
                    "scoring_model": scoring_model,
                }
            )
            continue

        avg_score = sum(s.score for s in successful) / len(successful)
        avg_k = sum(s.knowledge_score for s in successful) / len(successful)
        avg_c = sum(s.clarity_score for s in successful) / len(successful)
        avg_kmax = sum(s.knowledge_max for s in successful) / len(successful)
        avg_cmax = sum(s.clarity_max for s in successful) / len(successful)
        hall = any(s.hallucination for s in successful)
        reason = successful[0].reason
        print(
            f"  scored {arm} {task_id} total={avg_score:.1f}/10 "
            f"K={avg_k:.1f} C={avg_c:.1f} wall_s={wall_s:.1f}",
            flush=True,
        )
        records.append(
            {
                "arm": arm,
                "task_id": task_id,
                "repo": repo,
                "status": status,
                "route": row.get("route"),
                "tokens": row.get("tokens"),
                "success": True,
                "score": avg_score,
                "max_score": 10.0,
                "overall_pct": avg_score * 10.0,
                "knowledge_score": avg_k,
                "knowledge_max": avg_kmax,
                "clarity_score": avg_c,
                "clarity_max": avg_cmax,
                "hallucination": hall,
                "perfect": avg_score >= 10.0,
                "reason": reason,
                "num_iterations": len(successful),
                "wall_s": wall_s,
                "scoring_model": scoring_model,
            }
        )
    return records


def _summarize(records: List[Dict[str, Any]], arm: str) -> Dict[str, Any]:
    scored = [r for r in records if r.get("success")]
    n = len(records)
    n_ok = len(scored)
    if not scored:
        return {
            "arm": arm,
            "n": n,
            "n_scored": 0,
            "overall_pct_mean": None,
            "score_mean": None,
            "knowledge_score_mean": None,
            "clarity_score_mean": None,
            "perfect_solve_pct": None,
        }
    return {
        "arm": arm,
        "n": n,
        "n_scored": n_ok,
        "score_mean": sum(r["score"] for r in scored) / n_ok,
        "overall_pct_mean": sum(r["overall_pct"] for r in scored) / n_ok,
        "knowledge_score_mean": sum(r["knowledge_score"] for r in scored) / n_ok,
        "clarity_score_mean": sum(r["clarity_score"] for r in scored) / n_ok,
        "perfect_solve_pct": 100.0 * sum(1 for r in scored if r.get("perfect")) / n_ok,
        "wall_s_mean": sum(r.get("wall_s") or 0 for r in scored) / n_ok,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="RepoProbe checklist score on pipeline answers")
    ap.add_argument("--arms", default="all", help="Comma arms or 'all'")
    ap.add_argument("--repo-filter", default="", help="Substring filter on task_id/repo")
    ap.add_argument("--runs-root", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--num-iterations", type=int, default=1)
    ap.add_argument("--scoring-workers", type=int, default=1)
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Scorer cache dir (default: out-dir/cache)",
    )
    args = ap.parse_args()

    scoring_model = os.environ.get("SCORING_MODEL") or os.environ.get("PHASE2_JUDGE_MODEL")
    if not scoring_model:
        print("SCORING_MODEL or PHASE2_JUDGE_MODEL required", file=sys.stderr)
        return 2
    if not (os.environ.get("OPENAI_SCORING_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print("OPENAI_SCORING_API_KEY or OPENAI_API_KEY required", file=sys.stderr)
        return 2

    arms = ALL_ARMS if args.arms.strip().lower() == "all" else [a.strip() for a in args.arms.split(",") if a.strip()]
    labels = _load_labels(args.labels)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = str(args.cache_dir or (out_dir / "cache"))

    scorer = SimpleScorer(
        scoring_model=scoring_model,
        cache_dir=cache_dir,
        scoring_workers=max(args.scoring_workers, 1),
    )

    print(
        f"=== RepoProbe pipeline score start model={scoring_model} "
        f"arms={arms} filter={args.repo_filter!r} iterations={args.num_iterations} ===",
        flush=True,
    )
    t_all = time.time()
    summaries: List[Dict[str, Any]] = []
    all_records: List[Dict[str, Any]] = []

    for arm in arms:
        print(f"--- {arm} ---", flush=True)
        recs = score_arm(
            arm=arm,
            runs_root=args.runs_root,
            labels=labels,
            repo_filter=args.repo_filter,
            scorer=scorer,
            num_iterations=args.num_iterations,
            scoring_model=scoring_model,
        )
        out_path = out_dir / f"{arm}_score.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        summary = _summarize(recs, arm)
        (out_dir / f"{arm}_score.summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[PASS] {arm} wrote {out_path} n_scored={summary.get('n_scored')}", flush=True)
        summaries.append(summary)
        all_records.extend(recs)

    overall = {
        "scoring_model": scoring_model,
        "repo_filter": args.repo_filter,
        "num_iterations": args.num_iterations,
        "arms": summaries,
        "n_total": len(all_records),
        "n_scored": sum(1 for r in all_records if r.get("success")),
        "wall_s_total": time.time() - t_all,
        "label": "local-judge · single-judge · scorer-on-pipeline",
    }
    (out_dir / "summary.json").write_text(json.dumps(overall, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(overall, indent=2), flush=True)
    print(f"=== done in {overall['wall_s_total']:.1f}s ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
