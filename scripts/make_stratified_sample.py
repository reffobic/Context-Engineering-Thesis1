"""Build a SWE-QA sample stratified by repository AND question type.

Why: the Phase 2 waves are not a representative sample (W1 is three repos and
scored 8-11 points higher than the rest). This takes the same number of tasks
from every (repo, question type) cell, so repos and What/Why/Where/How
questions are equally represented.

The question type is the first word of the task's ``query`` (the SWE-QA
Level-1 taxonomy). Tasks whose query starts with anything else are left out.
Within a cell, tasks are chosen by lowest sha256(seed:task:instance_id), the
same deterministic rule as ``harness.sampling`` (identical on every OS).

Output has the same schema as ``harness.sampling`` sample files, so
``harness.run --sample <file>`` accepts it. One file is written per dataset.

Usage (from the repo root):
  PYTHONPATH=eval_harness/src python3 scripts/make_stratified_sample.py \
      --data-dir <profile data_dir> --out-dir <where to write> [--per-cell 2] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Reuse the harness's own hashing so this sample is built exactly like theirs
# and passes ``load_sample``'s tasks-file integrity check.
from harness.sampling import _rank, tasks_sha256

DATASETS = ("swe-qa-w1", "swe-qa-rest")        # the two SWE-QA waves
TYPES = ("what", "why", "where", "how")        # SWE-QA Level-1 question types


def question_type(query: str) -> str | None:
    """Return the Level-1 type from the first word of the query, or None."""
    m = re.match(r"\W*(\w+)", query or "")
    word = m.group(1).lower() if m else ""
    return word if word in TYPES else None


def stratified_ids(tasks: list[dict], per_cell: int, seed: int):
    """Pick ``per_cell`` task ids per (repo, type) cell.

    Returns (sorted instance ids, grid of counts per repo and type, number of
    tasks skipped because their query had no recognised type).
    """
    cells: dict[tuple[str, str], list[str]] = defaultdict(list)
    skipped = 0
    for t in tasks:
        qt = question_type(t["query"])
        if qt is None:
            skipped += 1
            continue
        cells[(t["repo"], qt)].append(t["instance_id"])

    chosen: list[str] = []
    grid: dict[str, dict[str, int]] = defaultdict(dict)
    for (repo, qt), ids in sorted(cells.items()):
        ranked = sorted(ids, key=lambda i: _rank(seed, f"task:{i}"))
        take = ranked[:per_cell]
        chosen += take
        grid[repo][qt] = len(take)          # fewer than per_cell if the cell is small
    return sorted(chosen), grid, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data-dir", type=Path, required=True, help="harness data dir (has datasets/<name>/tasks.jsonl)")
    ap.add_argument("--out-dir", type=Path, required=True, help="where to write <dataset>.strat<N>.sample.json")
    ap.add_argument("--per-cell", type=int, default=2, help="tasks per (repo, question type) cell")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for name in DATASETS:
        tasks_path = a.data_dir / "datasets" / name / "tasks.jsonl"
        tasks = [json.loads(l) for l in tasks_path.read_text(encoding="utf-8").splitlines() if l]
        ids, grid, skipped = stratified_ids(tasks, a.per_cell, a.seed)
        short = [c for r in grid.values() for c in r.values() if c < a.per_cell]
        out = a.out_dir / f"{name}.strat{a.per_cell}.sample.json"
        out.write_text(json.dumps({
            "dataset": name, "seed": a.seed,
            "method": f"{a.per_cell} per (repo, question type) cell; within a cell the lowest "
                      "sha256(seed:task:instance_id); type = first word of the query",
            "n_total": len(tasks), "n_sample": len(ids),
            "source_tasks_sha256": tasks_sha256(tasks_path),
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "per_repo_type": grid, "instance_ids": ids,
        }, indent=1), encoding="utf-8", newline="\n")
        total += len(ids)
        print(f"[OK] {name}: {len(ids)}/{len(tasks)} tasks, {len(grid)} repos, "
              f"skipped {skipped} (no type), cells below target: {len(short)} -> {out}")
        for repo in sorted(grid):
            print("   ", f"{repo:28s}", " ".join(f"{t}={grid[repo].get(t, 0)}" for t in TYPES))
    print(f"total sampled: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())