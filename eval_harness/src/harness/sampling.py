"""Seeded sample stratified by repository; one task-ID file reused by every arm and model.

Reads ONLY tasks.jsonl (never labels). Selection is hash-ranked, so it is identical on
Ubuntu and Windows and independent of Python's `random` implementation.

Usage:
  PYTHONPATH=eval_harness/src python -m harness.sampling \
      --profile eval_harness/profiles/ubuntu-4080.toml \
      --config eval_harness/configs/experiment.toml [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.common import config_hash, load_toml


def _rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()


def allocate(counts: dict[str, int], fraction: float, seed: int) -> dict[str, int]:
    """Largest-remainder proportional allocation; total = round-half-up(fraction * N)."""
    total_n = sum(counts.values())
    k = int(fraction * total_n + 0.5)
    quota = {r: fraction * n for r, n in counts.items()}
    alloc = {r: min(counts[r], math.floor(q)) for r, q in quota.items()}
    remaining = k - sum(alloc.values())
    order = sorted(
        (r for r in counts if alloc[r] < counts[r]),
        key=lambda r: (-(quota[r] - math.floor(quota[r])), _rank(seed, f"alloc:{r}")),
    )
    for r in order[:remaining]:
        alloc[r] += 1
    assert sum(alloc.values()) == k, (sum(alloc.values()), k)
    return alloc


def sample_tasks(tasks: list[dict], fraction: float, seed: int) -> tuple[list[str], dict[str, Any]]:
    by_repo: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        by_repo[t["repo"]].append(t["instance_id"])
    counts = {r: len(v) for r, v in by_repo.items()}
    alloc = allocate(counts, fraction, seed)
    chosen: list[str] = []
    for repo, ids in by_repo.items():
        ranked = sorted(ids, key=lambda i: _rank(seed, f"task:{i}"))
        chosen += ranked[: alloc[repo]]
    stats = {r: {"tasks": counts[r], "sampled": alloc[r]} for r in sorted(counts)}
    return sorted(chosen), stats


def tasks_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sample(sample_path: Path, tasks_path: Path) -> list[str]:
    """Used by every arm/model run: returns the shared ID list, refusing stale samples."""
    s = json.loads(Path(sample_path).read_text(encoding="utf-8"))
    if s["source_tasks_sha256"] != tasks_sha256(tasks_path):
        raise RuntimeError(f"{sample_path}: tasks file changed since sampling; re-sample deliberately")
    return list(s["instance_ids"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--force", action="store_true", help="overwrite existing sample files")
    args = ap.parse_args()
    profile, cfg = load_toml(args.profile), load_toml(args.config)
    seed, frac = cfg["seed"]["value"], cfg["sampling"]["fraction"]
    data_dir = Path(profile["data_dir"])
    out_dir = data_dir / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in cfg["sampling"]["datasets"]:
        tasks_path = data_dir / "datasets" / name / "tasks.jsonl"
        tasks = [json.loads(l) for l in tasks_path.read_text(encoding="utf-8").splitlines() if l]
        out = out_dir / f"{name}.sample.json"
        if out.exists() and not args.force:
            print(f"[SKIP] {name}: {out.as_posix()} exists (sample is reused, not regenerated)")
            continue
        ids, stats = sample_tasks(tasks, frac, seed)
        payload = {
            "dataset": name, "fraction": frac, "seed": seed,
            "method": "largest-remainder proportional allocation per repo; within-repo "
                      "selection by lowest sha256(seed:task:instance_id)",
            "n_total": len(tasks), "n_sample": len(ids),
            "source_tasks_sha256": tasks_sha256(tasks_path),
            "config_hash": config_hash(args.config),
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "per_repo": stats, "instance_ids": ids,
        }
        out.write_text(json.dumps(payload, indent=1), encoding="utf-8", newline="\n")
        covered = sum(1 for v in stats.values() if v["sampled"])
        print(f"[PASS] {name}: {len(ids)}/{len(tasks)} tasks (target {frac:.0%}), "
              f"{covered}/{len(stats)} repos represented -> {out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
