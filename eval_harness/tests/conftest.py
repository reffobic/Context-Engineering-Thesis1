from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent
SRC = HARNESS / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))

from harness.common import load_toml  # noqa: E402
from harness.pipeline_src import snapshot  # noqa: E402
from harness.pipelineenv import build_env  # noqa: E402

PCFG_PATH = HARNESS / "configs" / "pipeline.toml"
EXP_PATH = HARNESS / "configs" / "experiment.toml"
IDX_PATH = HARNESS / "configs" / "index.toml"


def _git_has(sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode == 0


@pytest.fixture(scope="session")
def pcfg():
    return load_toml(PCFG_PATH)


@pytest.fixture(scope="session")
def exp():
    return load_toml(EXP_PATH)


@pytest.fixture(scope="session")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("e2e")


@pytest.fixture(scope="session")
def snapshots(pcfg, work):
    base, aligned = pcfg["pipeline"]["base_commit"], pcfg["pipeline"]["seed_alignment_commit"]
    if not (_git_has(base) and _git_has(aligned)):
        pytest.skip("pipeline commits not available in this repository")
    return {"base": snapshot(base, work)[1], "aligned": snapshot(aligned, work)[1]}


@pytest.fixture(scope="session")
def index_dir(pcfg, snapshots, work):
    """Fixture repo indexed by the pipeline's own indexer (CPU, PYTHONHASHSEED=0), exactly as production does."""
    out = work / "index"
    meta = work / "meta.json"
    meta.write_text(json.dumps({"fixture": True}), encoding="utf-8")
    env = {k: os.environ[k] for k in ("PATH", "HOME", "HF_HOME", "XDG_CACHE_HOME", "HF_HUB_OFFLINE") if k in os.environ}
    env.update({"PYTHONHASHSEED": "0", "PYTHONPATH": str(SRC), "HARNESS_INDEX_DEVICE_OVERRIDE": "cpu"})
    r = subprocess.run([sys.executable, "-m", "harness.index_worker", "--pipeline-root", str(snapshots["aligned"]),
                        "--checkout", str(HERE / "fixtures" / "mini_repo"), "--out", str(out),
                        "--index-config", str(IDX_PATH), "--meta", str(meta)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    return out


@pytest.fixture(scope="session")
def fake():
    from fake_ollama import FakeOllama
    f = FakeOllama().start()
    yield f
    f.stop()


def run_cases(pipeline_root: Path, index_dir: Path, work: Path, fake, cases: list[dict], tag: str,
              pcfg: dict, exp: dict, instrument: bool, extra_env: dict | None = None,
              chroma_mode: str = "env_inplace", patch: Path | None = None) -> dict[str, dict]:
    """Copy the snapshot, put the fixture index where the pipeline expects it, run the cases.

    chroma_mode: env_inplace = index in backend/chroma_db and CHROMA_PATH set to it (default for tests);
                 default     = index in backend/chroma_db, CHROMA_PATH not set at all;
                 external    = index outside backend/, CHROMA_PATH points to it, backend/chroma_db absent.
    patch: a unified diff applied to the copied snapshot before the run.
    """
    root = work / f"run_{tag}"
    shutil.copytree(pipeline_root, root)
    if patch is not None:
        r = subprocess.run(["git", "apply", str(patch)], cwd=root, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    chroma = root / "backend" / "chroma_db"
    if chroma.exists():
        shutil.rmtree(chroma)
    chroma_env = {}
    if chroma_mode == "external":
        ext = work / f"external_{tag}" / "chroma"
        shutil.copytree(index_dir / "chroma", ext)
        chroma_env = {"CHROMA_PATH": str(ext)}
    else:
        shutil.copytree(index_dir / "chroma", chroma)
        if chroma_mode == "env_inplace":
            chroma_env = {"CHROMA_PATH": str(chroma)}
    env = build_env(pcfg, "fake:model", exp, extra={
        "PYTHONPATH": str(SRC), "GRAPHIFY_OUT": str(index_dir / "graphify-out"), **chroma_env, **(extra_env or {})})
    for k in ("HF_HOME", "XDG_CACHE_HOME", "HF_HUB_OFFLINE"):
        if k in os.environ:
            env[k] = os.environ[k]
    cases_path, out_path = work / f"cases_{tag}.json", work / f"out_{tag}.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    cmd = [sys.executable, str(HERE / "runner_pipeline.py"), "--pipeline-root", str(root), "--cases", str(cases_path),
           "--out", str(out_path), "--fake-port", str(fake.port), "--experiment", str(EXP_PATH)]
    from harness.pipelineenv import forbidden_chroma_paths
    for f in forbidden_chroma_paths(HARNESS.parent):
        cmd += ["--forbid", str(f)]
    if instrument:
        cmd.append("--instrument")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-3000:]
    return {o["id"]: o for o in json.loads(out_path.read_text(encoding="utf-8"))}
