"""Tier 1 robustness tests: chunker fallback on syntax errors and pruning budget."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.indexing.code_indexer as code_indexer  # noqa: E402
from backend.indexing.code_indexer import _chunk_python, read_and_chunk_file  # noqa: E402
from backend.pruning.file import prune_context  # noqa: E402

MALFORMED = "def broken_func(:\n    return 1\n\ndef other(x):\n    return x\n"


def test_python_syntax_error_falls_back_to_generic():
    chunks = _chunk_python(MALFORMED, "broken.py", "python", "hash")
    assert chunks, "syntax-error file must still yield chunks"
    assert all(c.file_path == "broken.py" for c in chunks)


def test_read_and_chunk_file_survives_syntax_error(tmp_path, monkeypatch):
    # Avoid any live Ollama call for the file summary.
    monkeypatch.setattr(code_indexer, "_ollama_file_summary", lambda *a, **k: None)
    f = tmp_path / "broken.py"
    f.write_text(MALFORMED, encoding="utf-8")
    chunks = read_and_chunk_file(f, tmp_path)
    assert len(chunks) >= 1


def test_token_budget_invariant():
    items = [
        {
            "code": "\n".join(
                f"def func_{i}_{j}(a, b):\n    value = a + b + {j}\n    return value"
                for j in range(150)
            ),
            "metadata": {"file": f"mod_{i}.py", "function": f"func_{i}_0"},
        }
        for i in range(12)
    ]
    chunks, stats = prune_context("func value", items, token_budget=1800)
    assert stats.kept_est_tokens <= 1800
    assert stats.original_est_tokens > 1800  # budget actually constrained


def test_empty_and_whitespace_file():
    for src in ("", "   \n\t\n"):
        assert _chunk_python(src, "e.py", "python", "h") == []
    assert code_indexer._chunk_generic("", "e.py", "python", "h") == []


def test_empty_file_read_and_chunk_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(code_indexer, "_ollama_file_summary", lambda *a, **k: None)
    f = tmp_path / "empty.py"
    f.write_text("", encoding="utf-8")
    read_and_chunk_file(f, tmp_path)  # must not raise
