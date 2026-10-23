"""Read-only snapshot of the pipeline code at one commit (no checkout, no merge).

`pipeline_commit` in experiment.toml may be a ref (development: origin/GASR) or a SHA
(frozen). It is resolved to a full SHA, and `git archive` of that commit's backend/ is
unpacked under <data_dir>/cache/pipeline/<sha>/. Nothing in the working tree changes.
"""
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

SUBDIR = "backend"
REPO = Path(__file__).resolve().parents[3]          # git is always run in the harness repository, whatever the cwd


def _git(args: list[str], binary: bool = False):
    return subprocess.run(["git", "-c", "credential.helper=", *args], capture_output=True,
                          text=not binary, check=True, timeout=600, cwd=REPO)


def resolve_sha(ref: str) -> str:
    return _git(["rev-parse", "--verify", f"{ref}^{{commit}}"]).stdout.strip()


def snapshot(ref: str, data_dir: Path) -> tuple[str, Path]:
    """Return (full_sha, root) where root/backend holds the pipeline at that commit."""
    sha = resolve_sha(ref)
    root = data_dir / "cache" / "pipeline" / sha
    if (root / SUBDIR / ".snapshot_ok").is_file():
        return sha, root
    if root.exists():                      # unfinished earlier attempt inside the cache folder
        import shutil
        shutil.rmtree(root)
    blob = _git(["archive", "--format=tar", sha, SUBDIR], binary=True).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        tf.extractall(root, filter="data")
    (root / SUBDIR / ".snapshot_ok").write_text(sha, encoding="utf-8")
    return sha, root
