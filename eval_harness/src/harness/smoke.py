"""Smoke test of one arm: N sample tasks with a forced timeout and a kill-and-resume, then a report.

  python -m harness.smoke --profile P --dataset D --model M --arm A --sample S --out DIR --n 5 ...configs...

Not a result run (runs with --smoke; record_results stays false). Steps:
  1. start harness.run for the first N sample tasks, force a timeout on task `--timeout-task` (1-based)
  2. SIGKILL the orchestrator while task `--kill-during` (1-based) is in flight
  3. start it again with the same --out (resume), let it finish
  4. check: N lines, no duplicates, the forced timeout is a failed line, orphan worker gone, no scratch left,
     cached indexes unchanged; write smoke_report.json with seconds per task by stage.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

from .common import load_toml
from .run import sqlite_fingerprint


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    for x in ("profile", "sample", "out", "experiment", "pipeline-config", "index-config", "run-config"):
        ap.add_argument(f"--{x}", type=Path, required=True)
    for x in ("dataset", "model", "arm"):
        ap.add_argument(f"--{x}", required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--timeout-task", type=int, default=4)
    ap.add_argument("--kill-during", type=int, default=3)
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()
    ids = json.loads(a.sample.read_text(encoding="utf-8"))["instance_ids"][: a.n]
    data = Path(load_toml(a.profile)["data_dir"])
    cmd = [sys.executable, "-m", "harness.run", "--profile", str(a.profile), "--dataset", a.dataset, "--model", a.model,
           "--arm", a.arm, "--sample", str(a.sample), "--out", str(a.out), "--experiment", str(a.experiment),
           "--pipeline-config", str(a.pipeline_config), "--index-config", str(a.index_config),
           "--run-config", str(a.run_config), "--smoke", "--limit", str(a.n), "--inject-timeout", ids[a.timeout_task - 1]]
    if a.allow_dirty:
        cmd.append("--allow-dirty")
    a.out.mkdir(parents=True, exist_ok=True)
    log1 = open(a.out / "run1.log", "w")
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=log1, stderr=subprocess.STDOUT, text=True)
    res, inflight = a.out / "results.jsonl", a.out / "inflight.json"
    n_lines = lambda: len([x for x in res.read_text().splitlines() if x.strip()]) if res.is_file() else 0
    report: dict = {"killed_during_task": ids[a.kill_during - 1]}
    deadline = time.time() + 3600
    while time.time() < deadline and p.poll() is None:
        if n_lines() == a.kill_during - 1 and inflight.is_file():
            worker_pid = json.loads(inflight.read_text())["pid"]
            p.send_signal(signal.SIGKILL)
            p.wait()
            report["killed_after_lines"] = n_lines()
            report["orphan_worker_alive_after_kill"] = os.path.exists(f"/proc/{worker_pid}")
            report["orphan_pid"] = worker_pid
            break
        time.sleep(0.3)
    else:
        report["note"] = "orchestrator ended before the kill point"
    log2 = open(a.out / "run2.log", "w")
    r2 = subprocess.run(cmd, stdout=log2, stderr=subprocess.STDOUT, text=True)
    report["resume_returncode"] = r2.returncode
    rows = [json.loads(x) for x in res.read_text().splitlines() if x.strip()]
    got = [r["task_id"] for r in rows]
    report["checks"] = {
        "lines": len(rows), "expected": a.n, "no_duplicates": len(got) == len(set(got)), "order_ok": got == ids,
        "forced_timeout_recorded": next((r["status"] for r in rows if r["task_id"] == ids[a.timeout_task - 1]), None),
        "forced_timeout_counted_as_failed": next((r["counted_as_failed"] for r in rows if r["task_id"] == ids[a.timeout_task - 1]), None),
        "orphan_gone": not os.path.exists(f"/proc/{report.get('orphan_pid', 0)}") if "orphan_pid" in report else None,
        "scratch_empty": not any((data / "cache" / "scratch").iterdir()) if (data / "cache" / "scratch").exists() else True,
        "inflight_removed": not inflight.exists(),
        "cached_index_unchanged": all(
            sqlite_fingerprint(Path(d) / "chroma") == json.loads((Path(d) / "fingerprint.json").read_text())
            for d in {r.get("index_dir") for r in rows if r.get("index_dir")}),
    }
    ok = [r for r in rows if r["status"] == "ok"]
    stages = ["retrieve", "rank", "structure_rerank", "prune", "parent_context", "gates_and_judges", "graph_expand",
              "re_prune", "inject", "generate", "task_total"]
    report["seconds_per_task_by_stage"] = {s: {"mean": mean([r["latency_ms"][s] / 1000 for r in ok]),
                                                "median": round(statistics.median([r["latency_ms"][s] / 1000 for r in ok]), 3) if ok else None}
                                           for s in stages}
    report["per_task"] = [{"task": r["task_id"], "status": r["status"], "route": r.get("route"), "hops": r.get("hops_used"),
                           "task_total_s": round(r["latency_ms"]["task_total"] / 1000, 2) if r.get("latency_ms") else None,
                           "gate_call2": (r.get("gate") or {}).get("gapa_call2") is not None if r.get("gate") else None,
                           "seed_tier": (r.get("seed_pool") or {}).get("tier"), "seed_pool_size": (r.get("seed_pool") or {}).get("size"),
                           "answer_prompt_eval_count": (r.get("answer_call") or {}).get("prompt_eval_count"),
                           "prompt_over_num_ctx": (r.get("answer_call") or {}).get("prompt_eval_over_num_ctx"),
                           "hit_max_context_tokens": (r.get("inject") or {}).get("hit_max_context_tokens"),
                           "counted_as_failed": r["counted_as_failed"], "gate_error": r.get("gate_error")} for r in rows]
    g = [r["gate"] for r in ok if r.get("gate") and r["gate"].get("gapa_call1")]
    second = [x for x in g if x["gapa_call2"]]
    tk = lambda c: (c["prompt_eval_count"] or 0) + (c["eval_count"] or 0)
    report["gapa_gate"] = {"tasks_with_gate": len(g), "needing_call2": len(second),
                           "share_needing_call2": round(len(second) / len(g), 3) if g else None,
                           "mean_tokens_sufficient_path": mean([tk(x["gapa_call1"]) for x in g if not x["gapa_call2"]]),
                           "mean_tokens_call2_path": mean([tk(x["gapa_call1"]) + tk(x["gapa_call2"]) for x in second])}
    report["overhead_seconds_mean"] = {k: mean([r["overhead_s"].get(k) for r in rows]) for k in
                                       ("subprocess_startup_s", "spawn_to_ready_s", "index_copy_s", "index_verify_s")}
    report["routes"] = {k: sum(1 for r in ok if r["route"] == k) for k in sorted({r["route"] for r in ok})}
    report["token_reconciliation_mismatches"] = [r["task_id"] for r in ok if r["token_reconciliation"]["match"] is False]
    report["any_truncation_suspected"] = [r["task_id"] for r in ok if r["any_truncation_suspected"]]
    report["structure_rerank_applied_all"] = all(r["structure_rerank"]["applied"] for r in ok)
    report["wall_seconds_total"] = round(time.time() - t0, 1)
    report["job"] = json.loads((a.out / "job.json").read_text())["info"]
    (a.out / "smoke_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("per_task", "job")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
