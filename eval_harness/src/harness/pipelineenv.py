"""Pipeline environment: clean env for subprocesses, unset-assertions, effective-value record."""
from __future__ import annotations

import os
from pathlib import Path

from .common import config_hash, load_toml

# Variables a child process legitimately needs from the parent environment.
PASS_THROUGH = ["PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
                "HF_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "XDG_CACHE_HOME", "LOCALAPPDATA",
                "CUDA_VISIBLE_DEVICES", "VIRTUAL_ENV", "PYTHONPATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"]


class EnvError(RuntimeError):
    pass


def assert_unset(pcfg: dict, environ=None) -> None:
    environ = os.environ if environ is None else environ
    bad = [v for v in pcfg["assert_unset"]["vars"] if v in environ]
    if bad:
        raise EnvError(f"these variables must be unset but are present: {bad}")


def build_env(pcfg: dict, model: str | None, experiment: dict | None = None, extra: dict | None = None,
              environ=None) -> dict[str, str]:
    """Environment for a pipeline subprocess: allowed pass-through + pinned values only."""
    environ = os.environ if environ is None else environ
    assert_unset(pcfg, environ)
    pins = dict(pcfg["env_pinned"])
    if experiment is not None and int(pins["OLLAMA_NUM_CTX"]) != int(experiment["ollama"]["num_ctx"]):
        raise EnvError("pipeline.toml OLLAMA_NUM_CTX differs from experiment.toml [ollama].num_ctx")
    env = {k: environ[k] for k in PASS_THROUGH if k in environ}
    for k, v in pins.items():
        if v == "@model":
            if not model:
                raise EnvError(f"{k} needs the run model but none was given")
            v = model
        env[k] = v
    if extra:
        env.update(extra)
    return env


def effective_record(pcfg: dict, env: dict[str, str]) -> dict[str, dict]:
    """Every pipeline-relevant variable: effective value and where it came from."""
    out = {}
    known = dict(pcfg["known_env"])
    for k in pcfg["env_pinned"]:
        known.setdefault(k, "")
    for k, default in known.items():
        if k in env:
            out[k] = {"value": env[k], "source": "pinned" if k in pcfg["env_pinned"] else "env"}
        else:
            out[k] = {"value": default if default != "" else None, "source": "code_default"}
    return out


def pipeline_identity(pcfg_path: Path) -> dict:
    p = load_toml(pcfg_path)["pipeline"]
    return {"frozen_sha": p["frozen_sha"], "base_commit": p["base_commit"],
            "seed_alignment_commit": p["seed_alignment_commit"], "pipeline_config_hash": config_hash(pcfg_path)}


def _git(args: list[str], cwd: Path) -> str:
    import subprocess
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def git_identity(repo: Path, pcfg: dict) -> dict:
    """Harness commit for log lines, plus the checks a run must pass.

    clean: no uncommitted change to tracked files (untracked files are ignored on purpose).
    backend_matches_frozen: backend/ at HEAD is byte-identical to the frozen pipeline commit.
    revoked: HEAD is a harness commit whose builds must not be used for runs.
    """
    head = _git(["rev-parse", "HEAD"], repo)
    p = pcfg["pipeline"]
    dirty = bool(_git(["status", "--porcelain", "--untracked-files=no"], repo))
    same = _git(["diff", "--stat", p["frozen_sha"], "HEAD", "--", "backend"], repo) == ""
    revoked = (any(head.startswith(r) or r.startswith(head) for r in pcfg["pipeline"]["revoked_harness_commits"])
               or p["frozen_sha"] in pcfg["pipeline"].get("revoked_pipeline_shas", []))
    return {"harness_commit": head, "clean": not dirty, "backend_matches_frozen": same, "revoked": revoked,
            "frozen_sha": p["frozen_sha"], "base_commit": p["base_commit"], "seed_alignment_commit": p["seed_alignment_commit"]}


def require_runnable(identity: dict) -> None:
    if identity["revoked"]:
        raise EnvError(f"harness commit {identity['harness_commit'][:10]} is revoked for runs")
    if not identity["clean"]:
        raise EnvError("tracked files have uncommitted changes; commit before running")
    if not identity["backend_matches_frozen"]:
        raise EnvError("backend/ differs from the frozen pipeline commit")


# ── clean snapshot only ──────────────────────────────────────────────────────

def assert_snapshot_root(pipeline_root: Path, data_dir: Path, pcfg: dict, repo_root: Path) -> None:
    """Runs use the extracted copy of the frozen SHA under <data_dir>/cache/pipeline, never the working tree."""
    root = pipeline_root.resolve()
    snap_base = (data_dir / "cache" / "pipeline").resolve()
    frozen = pcfg["pipeline"]["frozen_sha"]
    if snap_base not in root.parents:
        raise EnvError(f"pipeline root {root} is not under the snapshot cache {snap_base}")
    if root.name != frozen:
        raise EnvError(f"snapshot {root.name} is not the frozen SHA {frozen}")
    marker = root / "backend" / ".snapshot_ok"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != frozen:
        raise EnvError("snapshot marker missing or for another commit")
    if root == repo_root.resolve() or repo_root.resolve() / "backend" == root / "backend":
        raise EnvError("pipeline root is the working tree")


def forbidden_chroma_paths(repo_root: Path) -> list[Path]:
    r = repo_root.resolve()
    return [r / "backend" / "chroma_db", r / "chroma_db", r / "backend" / "superset_chroma_db"]


def install_chroma_guard(forbidden: list[Path]) -> None:
    """Make chromadb.PersistentClient raise for any path inside a forbidden folder (the working-tree databases)."""
    import chromadb
    orig = chromadb.PersistentClient
    bad = [p.resolve() for p in forbidden]

    def guarded(*a, **k):
        path = k.get("path", a[0] if a else None)
        if path is not None:
            p = Path(path).resolve()
            for f in bad:
                if p == f or f in p.parents:
                    raise EnvError(f"refusing to open working-tree Chroma database {p}")
        return orig(*a, **k)

    chromadb.PersistentClient = guarded
