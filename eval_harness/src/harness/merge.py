"""Merge run folders from several machines into one folder for harness.metrics.

  python -m harness.merge --src DIR [DIR ...] --out DIR

Refuses (exit 2) to combine jobs whose pipeline SHA, harness commit, config hashes, Ollama version, model digest or
sample file differ, or that cover the same (model, dataset, arm, ablation) twice. Warns when jobs come from several
machines (latencies are not comparable). Job folders are copied unchanged; nothing is rewritten.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .metrics import MergeError, check_compat, load_jobs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--src", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    jobs = load_jobs([p.resolve() for p in a.src])
    try:
        warnings = check_compat(jobs)
    except MergeError as exc:
        print(f"[REFUSED] {exc}")
        return 2
    out = a.out.resolve()
    if out.exists() and any(out.iterdir()):
        print(f"[REFUSED] {out} is not empty")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    for j in jobs:
        shutil.copytree(j["dir"], out / j["job_id"], ignore=shutil.ignore_patterns(".lock", "inflight.json"))
    for w in warnings:
        print("WARNING:", w)
    print(f"merged {len(jobs)} jobs into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
