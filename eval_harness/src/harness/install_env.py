"""Create a NEW virtual environment from requirements.lock.txt. Never touches an existing one.

  python -m harness.install_env --venv <new-dir> [--torch-index-url URL]

Refuses if <new-dir> already exists. On Windows pass the CUDA wheel index for torch, e.g.
  --torch-index-url https://download.pytorch.org/whl/cu130   (not verifiable from Ubuntu)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import venv
from pathlib import Path

from .envinfo import LOCK_PATH


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--venv", type=Path, required=True)
    ap.add_argument("--torch-index-url", default=None)
    a = ap.parse_args()
    if sys.version_info[:2] != (3, 12):
        print(f"[FAIL] run this with Python 3.12 (found {sys.version.split()[0]})")
        return 2
    if a.venv.exists():
        print(f"[FAIL] {a.venv} already exists; refusing to modify it. Choose a new directory.")
        return 2
    venv.EnvBuilder(with_pip=True).create(a.venv)
    py = a.venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    lock = LOCK_PATH.read_text(encoding="utf-8").splitlines()
    torch = [x for x in lock if x.startswith("torch==")]
    rest = a.venv / "_lock_without_torch.txt"
    rest.write_text("\n".join(x for x in lock if not x.startswith("torch==")) + "\n", encoding="utf-8")
    if a.torch_index_url and torch:
        subprocess.run([str(py), "-m", "pip", "install", torch[0], "--index-url", a.torch_index_url],
                       check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-r", str(LOCK_PATH if not a.torch_index_url else rest)],
                   check=True)
    print(f"[PASS] created {a.venv}. Verify with:  {py} -m harness.envlock --verify "
          "(with PYTHONPATH=eval_harness/src)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
