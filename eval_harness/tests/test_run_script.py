"""End-to-end tests of harness.run against the fake Ollama server and the fixture index."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import EXP_PATH, HARNESS, IDX_PATH, PCFG_PATH, SRC
from harness.common import load_toml
from harness.envinfo import _graphify_version
from harness.index_cache import index_dir as key_dir, make_key
from harness.pipeline_src import snapshot
from harness.run import classify, sqlite_fingerprint
from test_pipeline_e2e import Q

pytestmark = pytest.mark.e2e
RUN_TOML = HARNESS / "configs" / "run.toml"
COMMIT = "c" * 40
MODEL = "fake:model"


@pytest.fixture(scope="module")
def mini(tmp_path_factory, pcfg, index_dir, fake):
    """A tiny data dir: 3 tasks on the fixture repo, its checkout, its index under the real key, a sample."""
    root = tmp_path_factory.mktemp("rundata")
    data = root / "data"
    repo_dir = data / "cache" / "checkouts" / "shop__shop" / COMMIT
    shutil.copytree(HARNESS / "tests" / "fixtures" / "mini_repo", repo_dir)
    tasks = [{"instance_id": f"shop__shop-{i}", "repo": "shop/shop", "base_commit": COMMIT, "query": Q} for i in range(3)]
    (data / "datasets" / "mini").mkdir(parents=True)
    tf = data / "datasets" / "mini" / "tasks.jsonl"
    tf.write_text("".join(json.dumps(t) + "\n" for t in tasks), encoding="utf-8")
    import hashlib
    (data / "samples").mkdir()
    sample = data / "samples" / "mini.sample.json"
    sample.write_text(json.dumps({"seed": 42, "instance_ids": [t["instance_id"] for t in tasks],
                                  "source_tasks_sha256": hashlib.sha256(tf.read_bytes()).hexdigest()}), encoding="utf-8")
    sha = pcfg["pipeline"]["frozen_sha"]
    snapshot(sha, data)
    icfg = load_toml(IDX_PATH)
    parts = make_key("shop/shop", COMMIT, sha, icfg, _graphify_version())
    idx = key_dir(data, parts)
    shutil.copytree(index_dir, idx)
    (idx / "key.json").write_text(json.dumps(parts), encoding="utf-8")
    (idx / "DONE").write_text(parts["key"], encoding="utf-8")
    profile = root / "profile.toml"
    profile.write_text(f'machine_id = "test-machine"\ndata_dir = "{data.as_posix()}"\nqueue_file = "q.toml"\nntfy_topic_env = "X"\n', encoding="utf-8")
    rc = root / "run.toml"
    rc.write_text(RUN_TOML.read_text(encoding="utf-8").replace("http://127.0.0.1:11434", f"http://127.0.0.1:{fake.port}")
                  .replace("worker_ready_timeout_s = 180", "worker_ready_timeout_s = 180"), encoding="utf-8")
    return {"data": data, "profile": profile, "run_config": rc, "sample": sample, "idx": idx, "root": root, "tasks": tasks}


def cmd(mini, out, arm="GAPA", extra=()):
    return [sys.executable, "-m", "harness.run", "--profile", str(mini["profile"]), "--dataset", "mini", "--model", MODEL,
            "--arm", arm, "--sample", str(mini["sample"]), "--out", str(out), "--experiment", str(EXP_PATH),
            "--pipeline-config", str(PCFG_PATH), "--index-config", str(IDX_PATH), "--run-config", str(mini["run_config"]),
            "--smoke", "--allow-dirty", *extra]


def env_for(fake):
    e = {k: os.environ[k] for k in ("PATH", "HOME", "HF_HOME", "XDG_CACHE_HOME") if k in os.environ}
    e.update({"PYTHONPATH": str(SRC), "HARNESS_OLLAMA_REDIRECT_PORT": str(fake.port)})
    return e


def scenario(fake, sc):
    import urllib.request
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{fake.port}/__scenario", data=json.dumps(sc).encode(), method="POST")).read()


def lines(out):
    return [json.loads(x) for x in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


EXPAND = {"gapa1": False, "structure": "STRUCTURE", "post_expand": [True]}


def test_gapa_job_writes_complete_lines(mini, fake):
    out = mini["root"] / "out_gapa"
    scenario(fake, EXPAND)
    r = subprocess.run(cmd(mini, out), capture_output=True, text=True, env=env_for(fake))
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    rows = lines(out)
    assert [x["task_id"] for x in rows] == [t["instance_id"] for t in mini["tasks"]]
    x = rows[0]
    assert x["status"] == "ok" and x["route"] == "GRAPH" and x["counted_as_failed"] is False and x["arm_impl"] == "pipeline"
    for k in ("machine_id", "gpu_name", "gpu_driver", "ollama_version", "model_digest", "config_hash", "harness_commit",
              "pipeline_frozen_sha", "pipeline_base_commit", "pipeline_seed_alignment_commit", "env", "num_ctx"):
        assert x[k] not in (None, ""), k
    assert x["model_digest"] == "sha256:fake" and x["pipeline_base_commit"].startswith("166dbd0") and x["env"]["PYTHONHASHSEED"]["value"] == "0"
    assert x["env"]["MAX_CONTEXT_TOKENS"]["value"] == "24000" and x["env"]["HF_HUB_OFFLINE"]["value"] == "1"
    assert {"subprocess_startup_s", "index_copy_s", "index_verify_s"} <= set(x["overhead_s"])
    lat = x["latency_ms"]
    assert lat["retrieve"] > 0 and lat["task_total"] >= lat["retrieve"] and "subprocess" not in json.dumps(lat)
    assert x["gate"]["gapa_call1"] and x["gate"]["gapa_call2"] and x["seed_pool"]["tier"] == "pruned_non_test"
    assert x["token_reconciliation"]["match"] is True
    rk = x["ranked"]
    assert rk["after_retrieval"]["files"] and rk["after_expansion"]["files"] and rk["after_pruning"]["files"]
    assert any(f.startswith("shop/") for f in rk["after_retrieval"]["functions"][0].split(":")[:1])
    assert x["structure_rerank"]["applied"] is True and x["ollama_ps"] == {"size": 100, "size_vram": 100}
    assert x["answer_call"]["num_ctx"] == 32768 and x["inject"]["hit_max_context_tokens"] is False
    # scratch copies deleted, cached original untouched
    assert not list((mini["data"] / "cache" / "scratch").iterdir())
    fp = json.loads((mini["idx"] / "fingerprint.json").read_text())
    assert sqlite_fingerprint(mini["idx"] / "chroma") == fp
    assert (out / "summary.json").is_file()


def test_forced_timeout_is_recorded_as_failure_and_job_continues(mini, fake):
    out = mini["root"] / "out_timeout"
    scenario(fake, {"gapa1": True})
    r = subprocess.run(cmd(mini, out, extra=["--inject-timeout", "shop__shop-1"]), capture_output=True, text=True, env=env_for(fake))
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    st = {x["task_id"]: x for x in lines(out)}
    assert st["shop__shop-1"]["status"] == "timeout" and st["shop__shop-1"]["counted_as_failed"] is True
    assert st["shop__shop-1"]["failure"]["type"] == "timeout" and st["shop__shop-1"]["forced_timeout"] is True
    assert st["shop__shop-0"]["status"] == "ok" and st["shop__shop-2"]["status"] == "ok"
    assert not list((mini["data"] / "cache" / "scratch").iterdir())
    s = json.loads((out / "summary.json").read_text())
    assert s["status_counts"] == {"ok": 2, "timeout": 1} and s["counted_as_failed"] == 1


def test_kill_and_resume(mini, fake):
    out = mini["root"] / "out_resume"
    scenario(fake, {"gapa1": True, "answer_delay_s": 4})
    p = subprocess.Popen(cmd(mini, out), env=env_for(fake), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 240
    while time.time() < deadline and not ((out / "results.jsonl").is_file() and len(lines(out)) >= 1):
        time.sleep(0.5)
    while time.time() < deadline and not (out / "inflight.json").is_file():          # second task now in flight
        time.sleep(0.2)
    assert (out / "inflight.json").is_file()
    worker_pid = json.loads((out / "inflight.json").read_text())["pid"]
    p.send_signal(signal.SIGKILL)
    p.wait()
    assert len(lines(out)) == 1 and os.path.exists(f"/proc/{worker_pid}")             # orphan still alive
    scenario(fake, {"gapa1": True})
    r = subprocess.run(cmd(mini, out), capture_output=True, text=True, env=env_for(fake))
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    ids = [x["task_id"] for x in lines(out)]
    assert ids == [t["instance_id"] for t in mini["tasks"]] and len(set(ids)) == 3  # no duplicates, nothing lost
    assert "1 done, 2 to run" in r.stdout
    assert not (out / "inflight.json").exists() and not list((mini["data"] / "cache" / "scratch").iterdir())
    time.sleep(0.5)
    assert not os.path.exists(f"/proc/{worker_pid}") or Path(f"/proc/{worker_pid}/status").read_text().find("zombie") >= 0


def test_resume_refuses_different_settings_and_torn_last_line(mini, fake):
    out = mini["root"] / "out_resume"                                             # job finished in the previous test
    scenario(fake, {"gapa1": True})
    r = subprocess.run(cmd(mini, out, arm="GASR"), capture_output=True, text=True, env=env_for(fake))
    assert r.returncode == 2 and "different settings" in r.stdout
    with (out / "results.jsonl").open("a") as f:
        f.write('{"task_id": "torn')
    r = subprocess.run(cmd(mini, out), capture_output=True, text=True, env=env_for(fake))
    assert r.returncode == 0 and "3 done, 0 to run" in r.stdout
    assert len(lines(out)) == 3


def test_non_smoke_run_refused_while_record_results_false(mini, fake):
    c = [x for x in cmd(mini, mini["root"] / "out_refused") if x != "--smoke"]
    r = subprocess.run(c, capture_output=True, text=True, env=env_for(fake))
    assert r.returncode == 2 and "record_results" in r.stdout


def test_missing_call_graph_fails_the_task_not_silently(mini, fake):
    cg = mini["idx"] / "chroma" / "call_graph.json"
    keep = cg.read_text()
    cg.unlink()
    try:
        out = mini["root"] / "out_nocg"
        r = subprocess.run(cmd(mini, out, extra=["--limit", "1"]), capture_output=True, text=True, env=env_for(fake))
        x = lines(out)[0]
        assert x["status"] == "preflight_failed" and "call_graph.json missing" in x["failure"]["detail"] and x["counted_as_failed"]
    finally:
        cg.write_text(keep)


def test_always_graph_arm_is_harness_composed(mini, fake):
    out = mini["root"] / "out_ag"
    scenario(fake, {})
    r = subprocess.run(cmd(mini, out, arm="ALWAYS_GRAPH", extra=["--limit", "1"]), capture_output=True, text=True, env=env_for(fake))
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    x = lines(out)[0]
    assert x["arm_impl"] == "harness" and x["route"] == "GRAPH" and x["status"] == "ok" and x["hops_used"] == 2
    assert x["gate"]["gapa_call1"] is None and x["tokens"]["router_prompt"] == 0          # no judge calls at all
    assert x["ranked"]["after_expansion"] and x["seed_pool"]["tier"] == "pruned_non_test"


def test_self_route_and_gasr_arms_run(mini, fake):
    for arm, sc in (("SELF_ROUTE", {"selfroute": False}), ("GASR", {"gasr": {"sufficient": False, "need": "STRUCTURE"}, "post_expand": [True]})):
        out = mini["root"] / f"out_{arm}"
        scenario(fake, sc)
        r = subprocess.run(cmd(mini, out, arm=arm, extra=["--limit", "1"]), capture_output=True, text=True, env=env_for(fake))
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
        x = lines(out)[0]
        assert x["status"] == "ok" and x["route"] == ("LC" if arm == "SELF_ROUTE" else "GRAPH")


def test_oom_in_ollama_response_is_classified(mini, fake):
    out = mini["root"] / "out_oom"
    scenario(fake, {"gapa1": True, "http_error": "llama runner process has terminated: CUDA error: out of memory"})
    r = subprocess.run(cmd(mini, out, extra=["--limit", "2"]), capture_output=True, text=True, env=env_for(fake))
    assert (out / "results.jsonl").is_file(), r.stdout[-1500:] + r.stderr[-1500:]
    rows = lines(out)
    assert [x["status"] for x in rows] == ["oom", "oom"] and all(x["counted_as_failed"] for x in rows), rows[0].get("failure")
    assert "out of memory" in rows[0]["failure"]["detail"].lower()


def test_classify_unit():
    assert classify(None, -9, False, "")[0] == "oom"
    assert classify(None, 1, False, "boom")[0] == "error"
    assert classify(None, None, True, "")[0] == "timeout"


def test_run_scripts_never_open_labels():
    forbidden = ("labels.jsonl", "harness.datasets", "from .datasets", "import datasets", "derive_labels(")
    for f in ("run.py", "task_worker.py", "arms.py", "instrument.py", "locmap.py", "ollama_shim.py", "pipelineenv.py"):
        text = (SRC / "harness" / f).read_text(encoding="utf-8")
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        assert not [x for x in forbidden if x in code.replace('"""', "")], f
