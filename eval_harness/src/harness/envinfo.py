"""Versions of the interpreter, key packages and external tools, for log headers."""
from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

KEY_PACKAGES = ["chromadb", "sentence-transformers", "torch", "transformers", "numpy",
                "rank-bm25", "requests", "fastapi", "pydantic", "tokenizers", "huggingface-hub"]
LOCK_PATH = Path(__file__).resolve().parents[2] / "requirements.lock.txt"


def lock_sha256() -> str | None:
    if not LOCK_PATH.is_file():
        return None
    return hashlib.sha256(LOCK_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _graphify_version() -> str | None:
    exe = shutil.which("graphify")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else None
    except (OSError, subprocess.SubprocessError):
        return None


def environment_info() -> dict:
    pk = {}
    for name in KEY_PACKAGES:
        try:
            pk[name] = version(name)
        except PackageNotFoundError:
            pk[name] = None
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": pk, "graphify": _graphify_version(), "lock_sha256": lock_sha256()}


if __name__ == "__main__":
    import json
    json.dump(environment_info(), sys.stdout, indent=1)
    print()
