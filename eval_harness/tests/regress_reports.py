"""Regression check: regenerate both Oct 23 report folders and compare them byte-for-byte.

  python tests/regress_reports.py [--python PY] [--data-dir D] [--runs DIR]

Runs `python -m harness.metrics` for the default scorer and for `--lc-scorer v2` into temporary folders and
compares every file with data/reports/final_2350c0b and data/reports/final_2350c0b_scorer_v2. Missing and extra
files are differences. For summary.json a failed byte comparison is followed by a comparison of the parsed JSON,
listing the differing keys; that detail is added to the report, the byte difference itself is still counted.
Also asserts gate_no_usage == 0 over the ten Oct 23 jobs. Exit code 0 only if every file is byte-identical and that holds. Logs and references are only read.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
CASES = [("default", [], "final_2350c0b"), ("lc-scorer v2", ["--lc-scorer", "v2"], "final_2350c0b_scorer_v2")]


def files_of(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*")) if p.is_file()}


def json_diff(a, b, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                out.append(f"{p}: only in regenerated")
            elif k not in b:
                out.append(f"{p}: only in reference")
            else:
                out += json_diff(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in json_diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: regenerated={json.dumps(a)[:80]} reference={json.dumps(b)[:80]}"]


def compare(new: Path, ref: Path) -> list[str]:
    n, r = files_of(new), files_of(ref)
    diffs = [f"missing in regenerated: {k}" for k in sorted(set(r) - set(n))]
    diffs += [f"extra in regenerated: {k}" for k in sorted(set(n) - set(r))]
    for k in sorted(set(n) & set(r)):
        if n[k].read_bytes() == r[k].read_bytes():
            continue
        diffs.append(f"bytes differ: {k}")
        if k == "summary.json":
            try:
                jd = json_diff(json.loads(n[k].read_text("utf-8")), json.loads(r[k].read_text("utf-8")))
                diffs += [f"  summary.json key {d}" for d in jd] or ["  summary.json: parsed JSON is equal (formatting-only byte difference)"]
            except ValueError as exc:
                diffs.append(f"  summary.json: not parseable ({exc})")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--data-dir", type=Path, default=HARNESS / "data")
    ap.add_argument("--runs", type=Path, default=None)
    a = ap.parse_args()
    data = a.data_dir.resolve()
    runs_root = (a.runs or data / "runs" / "2350c0b").resolve()
    # only the Oct 23 jobs (NN-<name>, with a job.json); later jobs (nov3-*) share this folder and must not enter the comparison
    runs = [d for d in sorted(runs_root.glob("[0-9][0-9]-*")) if (d / "job.json").is_file()]
    env = dict(os.environ, PYTHONPATH=str(HARNESS / "src"))
    failed = 0
    for label, extra, ref_name in CASES:
        ref = data / "reports" / ref_name
        with tempfile.TemporaryDirectory(prefix="regress_reports_") as tmp:
            out = Path(tmp) / "out"
            cmd = [a.python, "-m", "harness.metrics", "--runs", *map(str, runs), "--data-dir", str(data), "--out", str(out), *extra]
            p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=HARNESS)
            print(f"[{label}] {' '.join(cmd[2:])} -> exit {p.returncode}")
            if p.returncode != 0 or not out.is_dir():
                print(p.stdout[-1500:], p.stderr[-1500:])
                failed += 1
                continue
            diffs = compare(out, ref)
            n = len(files_of(ref))
            print(f"[{label}] {n} reference files in {ref_name}: " + ("ALL BYTE-IDENTICAL" if not diffs else f"{len(diffs)} difference line(s)"))
            for d in diffs:
                print("   ", d)
            failed += bool(diffs)
    sys.path.insert(0, str(HARNESS / "src"))
    from harness.metrics import load_jobs, no_usage_calls                       # gate_no_usage must stay 0 on the Oct 23 jobs
    jobs = load_jobs(runs)
    n_nu = sum(len(no_usage_calls(r)) for j in jobs for r in j["rows"])
    n_tasks = sum(len(j["rows"]) for j in jobs)
    print(f"[gate_no_usage] {len(jobs)} jobs, {n_tasks} tasks, calls with missing or zero usage: {n_nu}" + ("" if n_nu == 0 else "  <-- must be 0"))
    failed += n_nu != 0 or len(jobs) != 10
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
