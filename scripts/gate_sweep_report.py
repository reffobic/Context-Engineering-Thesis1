"""Implied quality of each gate-sweep setting, from the per-task scores that already exist.

The sweep ran only the first gate call, so no new answers were generated. For each setting
and each task we take the score of the route that setting would have used:
  * gate says sufficient (T)  -> the task goes to RAG: use its Always-RAG score.
  * gate says insufficient    -> unchanged GAPA path: use the existing GAPA score.
  * the old gate (prompt 0, K=0) sent the task to RAG but this setting does not -> the task
    would now continue to the graph: use its Always-GRAPH score (an approximation).
The sweep's own setting (prompt 0, K=0) is the old gate, so it serves as the baseline.

Usage (from the repo root):
  python3 scripts/gate_sweep_report.py [--sweep-dir eval_harness/sweep_out/tasks] [--reports-dir eval_harness/data/reports]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ARMS = ("ALWAYS_RAG", "ALWAYS_GRAPH", "GAPA")
BIG = 10          # score difference (out of 100) counted as a clear loss or gain on a task


def load_scores(reports_dir: Path) -> dict[str, dict[str, float]]:
    """Overall score per task for each arm, from every ``phase2_sweqa_scores*`` folder."""
    scores: dict[str, dict[str, float]] = {a: {} for a in ARMS}
    for folder in sorted(reports_dir.glob("phase2_sweqa_scores*")):
        for arm in ARMS:
            f = folder / f"{arm}_answers_score.jsonl"
            if f.is_file():
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        r = json.loads(line)
                        scores[arm][r["task_id"]] = r["overall"]
    return scores


def verdict(row: dict) -> str:
    """T sufficient, F insufficient, E gate error (the gate then falls back to the escalation path)."""
    res = row["result"]
    if res.get("stage", "").endswith("error"):
        return "E"
    return "T" if res.get("sufficient") else "F"


def load_sweep(sweep_dir: Path) -> dict[str, dict[tuple[int, int], str]]:
    """Verdict per task and setting ``(reason_first, topk)``; tasks whose run failed are left out."""
    out: dict[str, dict[tuple[int, int], str]] = {}
    for p in sorted(sweep_dir.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if "error" in d:
            continue
        out[d["task_id"]] = {(int(r["reason_first"]), r["gate_topk"]): verdict(r) for r in d["settings"]}
    return out


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--sweep-dir", type=Path, default=Path("eval_harness/sweep_out/tasks"))
    ap.add_argument("--reports-dir", type=Path, default=Path("eval_harness/data/reports"))
    a = ap.parse_args()

    scores, sweep = load_scores(a.reports_dir), load_sweep(a.sweep_dir)
    # Only tasks that have a sweep result and a score under all three arms can be compared.
    ids = sorted(t for t in sweep if all(t in scores[arm] for arm in ARMS))
    missing = len(sweep) - len(ids)
    print(f"{len(ids)} tasks compared" + (f" ({missing} skipped: missing score)" if missing else ""))
    for arm in ARMS:
        print(f"  mean {arm:13s} {mean([scores[arm][t] for t in ids]):6.2f}   (same tasks)")
    gapa_mean = mean([scores["GAPA"][t] for t in ids])

    print("\nsetting      sent   implied   vs GAPA | on the sent tasks:  RAG   GAPA  clear-loss  clear-gain")
    print("(rf, k)      to RAG  score     score   |                      score  score")
    for setting in sorted(next(iter(sweep.values()))):
        implied, sent = [], []
        for t in ids:
            v, old = sweep[t][setting], sweep[t][(0, 0)]
            if v == "T":
                implied.append(scores["ALWAYS_RAG"][t])
                sent.append(t)
            elif old == "T":                                   # old gate used RAG, this setting escalates
                implied.append(scores["ALWAYS_GRAPH"][t])
            else:                                              # unchanged escalation path
                implied.append(scores["GAPA"][t])
        rag = [scores["ALWAYS_RAG"][t] for t in sent]
        gap = [scores["GAPA"][t] for t in sent]
        lost = sum(r <= g - BIG for r, g in zip(rag, gap))
        gain = sum(r >= g + BIG for r, g in zip(rag, gap))
        print(f"({setting[0]}, {setting[1]:2})     {len(sent):5}  {mean(implied):8.2f}  {mean(implied) - gapa_mean:+8.2f} |"
              f"{mean(rag):26.1f}  {mean(gap):5.1f}  {lost:8}  {gain:10}")
    print(f"\n'clear' = a task's score differs by {BIG}+ points. With about 120 tasks, differences of a few points "
          "between settings are within noise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())