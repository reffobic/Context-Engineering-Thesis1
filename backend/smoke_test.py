#!/usr/bin/env python3
"""
Quick checks before integration / manual API tests.

Usage (from repo root or from `backend/`):
  python backend/smoke_test.py
  python smoke_test.py

Tier A (always): PromptBuilder + line numbers — no Chroma/Ollama.
Tier B (optional): import MCP server + FastAPI TestClient — needs packages
  installed into the **same** interpreter you use to run this script, e.g.:
    /opt/anaconda3/bin/python3.12 -m pip install chromadb sentence-transformers fastapi uvicorn pydantic
  Avoid bare ``pip install`` when ``which python`` is pyenv 3.7 (wrong env / broken _ctypes).
  See backend/indexing/requirements.txt

End-to-end /generate needs Ollama running + indexed DB; Tier B only hits
GET / and validates POST /generate JSON shape (may 500 if retrieval fails).

Python version:
  Prefer 3.10+ (conda: ``which python`` should not be pyenv 3.7 if that build
  is broken). For 3.7 only: ``pip install 'typing_extensions>=4'`` from repo root
  (see requirements.txt). Chroma / sentence-transformers need a supported 3.10+
  interpreter, not a broken 3.7 missing ``_ctypes``.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def _ensure_python_or_exit() -> None:
    """TypedDict lives in ``typing`` from 3.8+; 3.7 needs ``typing_extensions``."""
    if sys.version_info >= (3, 8):
        return
    try:
        import typing_extensions  # noqa: F401
    except ImportError:
        print(
            "ERROR: Your `python3` is "
            f"{sys.version_info.major}.{sys.version_info.minor} "
            "and `typing_extensions` is not installed.\n\n"
            "Fix ONE of:\n"
            "  1) pip install 'typing_extensions>=4.0'\n"
            "  2) Use Python 3.10+ e.g. conda base:\n"
            "       conda activate base && python backend/smoke_test.py\n"
            "     or:  /opt/anaconda3/bin/python3.12 backend/smoke_test.py\n",
            file=sys.stderr,
        )
        sys.exit(1)


def _tier_a() -> None:
    print("=== Tier A: PromptBuilder (no Chroma) ===")
    sys.path.insert(0, str(BACKEND))
    from injection.prompt_builder import ContextChunk, PromptBuilder

    b = PromptBuilder()
    msg = b.build_user_message(
        "Summarize the snippet.",
        [ContextChunk(text="def foo():\n    return 1\n", path="x.py", start_line=10)],
    )
    assert "<CONTEXT>" in msg and "  10 |" in msg, msg[:400]
    print("PASS: PromptBuilder context + line numbers")

    from injection.chunk_normalize import strip_redundant_line_number_column

    raw = "def x():\n    pass\n"
    assert strip_redundant_line_number_column(raw) == raw
    numbered = "  10 | def a():\n  11 |     return 1\n"
    stripped = strip_redundant_line_number_column(numbered)
    assert "  10 |" not in stripped and "def a()" in stripped
    print("PASS: chunk_normalize strips duplicate editor line column when uniform")


def _tier_b() -> None:
    print("=== Tier B: FastAPI app + JSON body for /generate ===")
    # Same layout as `uvicorn server:app` when cwd is `backend/mcp_server`
    sys.path.insert(0, str(BACKEND / "mcp_server"))
    sys.path.insert(0, str(BACKEND))
    import server as mcp_server  # noqa: E402

    from fastapi.testclient import TestClient  # noqa: E402

    client = TestClient(mcp_server.app)
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert "Running" in str(r.json())

    r2 = client.post("/generate", json={"prompt": "hello"})
    # 200 if pipeline+Ollama OK; 500 if retrieval/index empty — still proves JSON routing works
    assert r2.status_code in (200, 500), r2.text
    if r2.status_code == 200:
        assert "output" in r2.json()
    print(f"PASS: POST /generate accepted JSON (HTTP {r2.status_code})")


def main() -> None:
    _ensure_python_or_exit()
    _tier_a()
    try:
        import chromadb  # noqa: F401
    except ImportError:
        print()
        print("Skip Tier B: chromadb not importable. Install into THIS Python (not bare `pip`):")
        print(f"  {sys.executable} -m pip install chromadb sentence-transformers fastapi uvicorn pydantic")
        if sys.version_info < (3, 10):
            print()
            print("Note: Tier B indexing stack needs Python 3.10+. Your interpreter is older.")
            print("Use e.g. conda:  /opt/anaconda3/bin/python3.12 -m pip install ...")
        return
    _tier_b()


if __name__ == "__main__":
    main()
