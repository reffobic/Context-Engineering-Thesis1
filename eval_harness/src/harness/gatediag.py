"""GAPA gate diagnosis over N tasks per model (see gatediag_worker). Writes a JSON report and prints a summary.

  python -m harness.gatediag --profile P --pipeline-config PC --index-config IC --experiment E
        --model M ... --n 20 --show 5 --out F
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from .common import OllamaClient, load_toml
from .envinfo import _graphify_version
from .index_cache import index_dir, make_key
from .pathcheck import sampled_tasks
from .pipeline_src import snapshot
from .pipelineenv import build_env


def pick(tasks: list[dict], n: int) -> list[dict]:
    """n tasks spread evenly over the sample, alternating datasets (deterministic)."""
    by = {}
    for t in tasks:
        by.setdefault(t["dataset"], []).append(t)
    per = n // len(by)
    out = []
    for d in sorted(by):
        step = len(by[d]) / per
        out += [by[d][int(i * step)] for i in range(per)]
    return out[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    for x in ("profile", "pipeline-config", "index-config", "experiment", "out"):
        ap.add_argument(f"--{x}", type=Path, required=True)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--dataset", action="append", default=None)
    a = ap.parse_args()
    for n in ("profile", "pipeline_config", "index_config", "experiment", "out"):
        setattr(a, n, getattr(a, n).resolve())
    pcfg, icfg, exp = load_toml(a.pipeline_config), load_toml(a.index_config), load_toml(a.experiment)
    data = Path(load_toml(a.profile)["data_dir"]).resolve()
    sha = pcfg["pipeline"]["frozen_sha"]
    root = snapshot(sha, data)[1]
    gver = _graphify_version()
    tasks = pick(sampled_tasks(data, a.dataset or ["swe-bench-lite", "loc-bench"]), a.n)
    scratch_root = data / "cache" / "scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    client = OllamaClient("http://127.0.0.1:11434", 300)
    report = {"frozen_sha": sha, "tasks": [t["instance_id"] for t in tasks], "models": {}}
    for model in a.model:
        for m in client.ps():
            client.unload(m["name"])
        rows = []
        for t in tasks:
            idx = index_dir(data, make_key(t["repo"], t["base_commit"], sha, icfg, gver))
            sc = scratch_root / f"gatediag__{t['instance_id']}"
            if sc.exists():
                shutil.rmtree(sc)
            sc.mkdir()
            try:
                shutil.copytree(idx / "chroma", sc / "chroma")
                shutil.copytree(idx / "graphify-out", sc / "graphify-out")
                (sc / "q.txt").write_text(t["query"], encoding="utf-8")
                env = build_env(pcfg, model, exp, extra={"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                                                         "CHROMA_PATH": str(sc / "chroma"), "GRAPHIFY_OUT": str(sc / "graphify-out")})
                r = subprocess.run([sys.executable, "-m", "harness.gatediag_worker", "--pipeline-root", str(root), "--query-file",
                                    str(sc / "q.txt"), "--experiment", str(a.experiment), "--model", model, "--out", str(sc / "out.json")],
                                   capture_output=True, text=True, env=env)
                if r.returncode != 0:
                    rows.append({"task_id": t["instance_id"], "error": r.stderr[-600:]})
                else:
                    rows.append({"task_id": t["instance_id"], "dataset": t["dataset"],
                                 **json.loads((sc / "out.json").read_text(encoding="utf-8"))})
                print(f"[{model}] {t['instance_id']} -> {rows[-1].get('pipeline_decision', rows[-1].get('error'))}", flush=True)
            finally:
                if scratch_root.resolve() in sc.resolve().parents:
                    shutil.rmtree(sc)
        report["models"][model] = rows
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
