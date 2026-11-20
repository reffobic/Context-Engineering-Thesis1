"""Gate sweep driver: run the GAPA call-1 gate under every sweep setting over a sample.

For each task in the sample files it starts ``gate_sweep_worker.py`` (one process per
task) against a scratch copy of that repo's index, and stores one JSON result per task.
Nothing is written to the shared data folder: indexes are only read, copies and results
go under ``--out-dir``. Unlike ``harness.gatediag`` it never unloads models from Ollama,
so other people's loaded models are left alone.

The run is resumable: a task whose result file already exists (and has no error) is
skipped, so re-running the same command after a crash continues where it stopped.

Usage (from the repo root):
  python3 scripts/gate_sweep.py --data-dir <data_dir> [--limit 3] [--out-dir eval_harness/sweep_out]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]                  # repo root (scripts/ sits directly under it)
sys.path.insert(0, str(REPO / "eval_harness" / "src"))      # lets ``import harness`` work without PYTHONPATH

from harness.common import load_toml                        # noqa: E402
from harness.envinfo import _graphify_version               # noqa: E402
from harness.index_cache import index_dir, make_key, safe_name  # noqa: E402
from harness.pipelineenv import EnvError, build_env         # noqa: E402
from harness.sampling import load_sample                    # noqa: E402


def load_tasks(data_dir: Path, samples_dir: Path) -> list[dict]:
    """Tasks named by every ``*.sample.json`` in ``samples_dir``, tagged with their dataset.

    ``load_sample`` refuses a sample whose ``tasks.jsonl`` hash no longer matches, so a stale
    sample cannot be used by accident.
    """
    tasks: list[dict] = []
    for sample in sorted(samples_dir.glob("*.sample.json")):
        dataset = json.loads(sample.read_text(encoding="utf-8"))["dataset"]
        tasks_path = data_dir / "datasets" / dataset / "tasks.jsonl"
        ids = set(load_sample(sample, tasks_path))
        for line in tasks_path.read_text(encoding="utf-8").splitlines():
            if line:
                t = json.loads(line)
                if t["instance_id"] in ids:
                    tasks.append({**t, "dataset": dataset})
    return tasks


def pick_spread(tasks: list[dict], limit: int) -> list[dict]:
    """``limit`` tasks spread evenly over the list (deterministic), for quick smoke runs."""
    if limit <= 0 or limit >= len(tasks):
        return tasks
    step = len(tasks) / limit
    return [tasks[int(i * step)] for i in range(limit)]


def find_index(data_dir: Path, task: dict, sha: str, icfg: dict, gver: str | None) -> tuple[Path | None, str]:
    """Locate the index for the task's repo and commit; returns (path or None, note).

    First the exact cache key. If that misses (for example the graphify version differs on
    this machine), fall back to the repo's index when exactly one exists for this commit.
    """
    exact = index_dir(data_dir, make_key(task["repo"], task["base_commit"], sha, icfg, gver))
    if (exact / "chroma").is_dir():
        return exact, "exact key"
    prefix = f"{safe_name(task['repo'])}__{task['base_commit'][:12]}__"
    found = [p for p in (data_dir / "cache" / "indexes").glob(prefix + "*") if (p / "chroma").is_dir()]
    if len(found) == 1:
        return found[0], f"fallback (key differs, single match: {found[0].name})"
    return None, f"no usable index ({len(found)} candidates for {prefix}*)"


def run_task(task: dict, idx_copy: Path, args, pcfg: dict, exp: dict) -> dict:
    """Run the worker for one task against the scratch index copy; returns the result row."""
    qfile = idx_copy / f"q_{task['instance_id']}.txt"
    wout = idx_copy / f"out_{task['instance_id']}.json"
    qfile.write_text(task["query"], encoding="utf-8")
    env = build_env(pcfg, args.model, exp, extra={
        "PYTHONPATH": str(REPO / "eval_harness" / "src"),
        "CHROMA_PATH": str(idx_copy / "chroma"),
        "GRAPHIFY_OUT": str(idx_copy / "graphify-out"),
    })
    cmd = [sys.executable, str(REPO / "scripts" / "gate_sweep_worker.py"),
           "--pipeline-root", str(args.pipeline_root), "--query-file", str(qfile),
           "--experiment", str(args.experiment), "--out", str(wout)]
    head = {"task_id": task["instance_id"], "dataset": task["dataset"], "repo": task["repo"],
            "query_head": task["query"][:200]}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=args.task_timeout)
    except subprocess.TimeoutExpired:
        return {**head, "error": f"worker timed out after {args.task_timeout}s"}
    if r.returncode != 0:
        return {**head, "error": r.stderr[-800:]}
    return {**head, **json.loads(wout.read_text(encoding="utf-8"))}


def flags(row: dict) -> str:
    """Compact verdict string: T sufficient, F insufficient, E gate error; two groups (old prompt | reason first)."""
    s = ["E" if r["result"].get("stage", "").endswith("error") else ("T" if r["result"].get("sufficient") else "F")
         for r in row["settings"]]
    return "".join(s[:4]) + " " + "".join(s[4:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data-dir", type=Path, required=True, help="harness data dir (datasets/, cache/indexes/); only read")
    ap.add_argument("--samples-dir", type=Path, default=REPO / "eval_harness" / "samples_strat")
    ap.add_argument("--out-dir", type=Path, default=REPO / "eval_harness" / "sweep_out")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--limit", type=int, default=0, help="only this many tasks, spread over the sample (0 = all)")
    ap.add_argument("--task-timeout", type=int, default=900, help="seconds allowed per task")
    ap.add_argument("--pipeline-root", type=Path, default=REPO, help="checkout whose backend/ is loaded")
    ap.add_argument("--experiment", type=Path, default=REPO / "eval_harness" / "configs" / "experiment.toml")
    ap.add_argument("--pipeline-config", type=Path, default=REPO / "eval_harness" / "configs" / "pipeline_phase2.toml")
    ap.add_argument("--index-config", type=Path, default=REPO / "eval_harness" / "configs" / "index.toml")
    args = ap.parse_args()
    for name in ("data_dir", "samples_dir", "out_dir", "pipeline_root", "experiment", "pipeline_config", "index_config"):
        setattr(args, name, getattr(args, name).resolve())

    pcfg, icfg, exp = load_toml(args.pipeline_config), load_toml(args.index_config), load_toml(args.experiment)
    sha, gver = pcfg["pipeline"]["frozen_sha"], _graphify_version()    # both are part of the index cache key
    tasks = pick_spread(load_tasks(args.data_dir, args.samples_dir), args.limit)
    tasks.sort(key=lambda t: (t["repo"], t["instance_id"]))             # same repo together: one index copy each
    res_dir, scratch = args.out_dir / "tasks", args.out_dir / "_scratch"
    res_dir.mkdir(parents=True, exist_ok=True)
    print(f"{len(tasks)} tasks, model {args.model}, results -> {res_dir}", flush=True)

    done = skipped = 0
    failed: list[str] = []
    t0 = time.time()
    repos = sorted({t["repo"] for t in tasks})
    for repo in repos:
        group = [t for t in tasks if t["repo"] == repo]
        todo = [t for t in group if not _finished(res_dir / f"{t['instance_id']}.json")]
        skipped += len(group) - len(todo)
        if not todo:
            continue
        idx, note = find_index(args.data_dir, todo[0], sha, icfg, gver)
        if idx is None:
            print(f"[SKIP] {repo}: {note}", flush=True)
            failed += [t["instance_id"] for t in todo]
            continue
        print(f"[{repo}] index: {note}", flush=True)
        copy = scratch / safe_name(repo)
        if copy.exists():
            shutil.rmtree(copy)
        try:
            copy.mkdir(parents=True)
            shutil.copytree(idx / "chroma", copy / "chroma")            # scratch copy: the shared index is never opened
            if (idx / "graphify-out").is_dir():
                shutil.copytree(idx / "graphify-out", copy / "graphify-out")
            for t in todo:
                try:
                    row = run_task(t, copy, args, pcfg, exp)
                except EnvError as exc:
                    print(f"[FAIL] environment check: {exc}", flush=True)
                    return 2
                (res_dir / f"{t['instance_id']}.json").write_text(json.dumps(row, indent=1, default=str), encoding="utf-8")
                done += 1
                if "error" in row:
                    failed.append(t["instance_id"])
                    print(f"[{done + skipped}/{len(tasks)}] {t['instance_id']} ERROR {row['error'][-200:]!r}", flush=True)
                else:
                    print(f"[{done + skipped}/{len(tasks)}] {t['instance_id']} {flags(row)}", flush=True)
        finally:
            if copy.exists():
                shutil.rmtree(copy)                                     # always remove the copy, even after a crash
    print(f"done {done}, already done {skipped}, failed {len(failed)} in {(time.time() - t0) / 60:.1f} min", flush=True)
    if failed:
        print("failed:", ", ".join(failed))
    return 1 if failed else 0


def _finished(path: Path) -> bool:
    """True when a result file exists and holds no error (so the task can be skipped on resume)."""
    if not path.is_file():
        return False
    try:
        return "error" not in json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False


if __name__ == "__main__":
    sys.exit(main())