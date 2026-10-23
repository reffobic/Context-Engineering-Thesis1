"""On the real smoke tasks (first 5 of the SWE-Bench-Lite sample, real cached indexes, fake Ollama), every
Table VIII ablation must produce a different pipeline output than full GAPA, and the full run must contain
no prefix-mismatch stub neighbours. Needs the cached indexes; skipped when they are absent.

The fake Ollama forces the expansion path (gate says insufficient, structure need, post-expansion sufficient),
so what differs between settings is exactly what the switches change.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import EXP_PATH, HARNESS, IDX_PATH, PCFG_PATH, SRC
from harness.common import load_toml
from harness.index_cache import index_dir, make_key
from harness.envinfo import _graphify_version

pytestmark = pytest.mark.e2e
ABL = load_toml(HARNESS / "configs" / "ablations.toml")
DATA = HARNESS / "data"
DATASET = "swe-bench-lite"
SCENARIO = {"gapa1": False, "structure": "STRUCTURE", "post_expand": [True]}
N = 5
TABLE_VIII = [k for k, v in ABL["settings"].items() if k != "full" and not v.get("control")]


def _real_data_available() -> bool:
    try:
        p = load_toml(PCFG_PATH)["pipeline"]
        sample = json.loads((DATA / "samples" / f"{DATASET}.sample.json").read_text(encoding="utf-8"))
        tasks = {t["instance_id"]: t for t in (json.loads(x) for x in (DATA / "datasets" / DATASET / "tasks.jsonl").read_text(encoding="utf-8").splitlines())}
        icfg, gver = load_toml(IDX_PATH), _graphify_version()
        return all((index_dir(DATA, make_key(tasks[i]["repo"], tasks[i]["base_commit"], p["frozen_sha"], icfg, gver)) / "DONE").is_file()
                   for i in sample["instance_ids"][:N])
    except Exception:
        return False


@pytest.fixture(scope="module")
def smoke_runs(tmp_path_factory, fake):
    if not _real_data_available():
        pytest.skip("cached indexes for the smoke tasks are not available")
    root = tmp_path_factory.mktemp("abl_smoke")
    profile = root / "profile.toml"
    profile.write_text(f'machine_id = "test-machine"\ndata_dir = "{DATA.as_posix()}"\nqueue_file = "q.toml"\nntfy_topic_env = "X"\n', encoding="utf-8")
    rc = root / "run.toml"
    rc.write_text((HARNESS / "configs" / "run.toml").read_text(encoding="utf-8").replace("http://127.0.0.1:11434", f"http://127.0.0.1:{fake.port}"), encoding="utf-8")
    import urllib.request
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{fake.port}/__scenario", data=json.dumps(SCENARIO).encode(), method="POST")).read()
    env = {k: os.environ[k] for k in ("PATH", "HOME", "HF_HOME", "XDG_CACHE_HOME") if k in os.environ}
    env.update({"PYTHONPATH": str(SRC), "HARNESS_OLLAMA_REDIRECT_PORT": str(fake.port)})
    runs = {}
    for key in ["full", *TABLE_VIII]:
        out = root / f"out_{key}"
        cmd = [sys.executable, "-m", "harness.run", "--profile", str(profile), "--dataset", DATASET, "--model", "fake:model", "--arm", "GAPA",
               "--sample", str(DATA / "samples" / f"{DATASET}.sample.json"), "--out", str(out), "--experiment", str(EXP_PATH),
               "--pipeline-config", str(PCFG_PATH), "--index-config", str(IDX_PATH), "--run-config", str(rc), "--smoke", "--allow-dirty",
               "--limit", str(N), "--ablation", key]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=root)
        assert r.returncode == 0, (key, r.stdout[-1500:], r.stderr[-800:])
        runs[key] = {x["task_id"]: x for x in (json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    return runs


def signature(line: dict) -> str:
    g1 = next((c for c in line["ollama_calls"] if c["role"] == "gapa_gate_call1"), {})
    r = line["ranked"]
    return hashlib.sha256(json.dumps({
        "route": line["route"], "retrieved": r["retrieved_top5"]["functions"], "final": r["final_context"]["functions"],
        "after_expansion": (r["after_expansion"] or {}).get("functions"),
        "gate_request": g1.get("request"), "n_expansion_neighbors": [e["new_chunk_count"] for e in line["expansions"]],
    }, sort_keys=True, default=str).encode()).hexdigest()


def test_every_task_ran_in_every_setting(smoke_runs):
    for key, rows in smoke_runs.items():
        assert len(rows) == N and all(x["status"] == "ok" for x in rows.values()), (key, {t: x["status"] for t, x in rows.items()})


def test_full_gapa_has_no_prefix_mismatch_stub_neighbours(smoke_runs):
    for tid, x in smoke_runs["full"].items():
        assert x["stub_neighbors"]["by_category"]["prefix_mismatch"] == 0, (tid, x["stub_neighbors"])


def test_every_ablation_differs_from_full_gapa_on_the_smoke_tasks(smoke_runs):
    full = {t: signature(x) for t, x in smoke_runs["full"].items()}
    differs = {k: [t for t, x in smoke_runs[k].items() if signature(x) != full[t]] for k in TABLE_VIII}
    print("\nablation differs from full GAPA on:", json.dumps(differs, indent=1))
    assert all(differs.values()), differs
