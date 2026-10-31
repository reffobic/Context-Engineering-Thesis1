"""Tier 1 robustness tests: parser/chunker edge cases, encodings, multi-language, and pruning budgets."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.indexing.code_indexer as code_indexer  # noqa: E402
from backend.indexing.code_indexer import (  # noqa: E402
    MAX_CHUNK_TOKENS,
    _chunk_generic,
    _chunk_python,
    read_and_chunk_file,
)
from backend.pruning import file as pruning_mod  # noqa: E402
from backend.pruning.file import prune_context  # noqa: E402

MALFORMED = "def broken_func(:\n    return 1\n\ndef other(x):\n    return x\n"


@pytest.fixture(autouse=True)
def _no_live_ollama(monkeypatch):
    """Prevent any live HTTP calls to Ollama during Tier 1 unit tests."""
    monkeypatch.setattr(code_indexer, "_ollama_file_summary", lambda *a, **k: None)
    monkeypatch.setattr(code_indexer, "_ollama_situating_text", lambda *a, **k: "")


# ---------------------------------------------------------------------------
# Syntax error fallback & chunk quality
# ---------------------------------------------------------------------------


def test_python_syntax_error_falls_back_to_generic():
    chunks = _chunk_python(MALFORMED, "broken.py", "python", "hash")
    assert chunks, "syntax-error file must still yield chunks"
    assert all(c.file_path == "broken.py" for c in chunks)
    assert all(c.language == "python" for c in chunks)
    assert all(1 <= c.start_line <= c.end_line for c in chunks)
    assert all(c.code.strip() for c in chunks)
    assert any(c.name == "other" for c in chunks), "valid function after syntax error should be extracted"


def test_read_and_chunk_file_survives_syntax_error(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text(MALFORMED, encoding="utf-8")
    chunks = read_and_chunk_file(f, tmp_path)
    assert len(chunks) >= 1
    assert all(c.file_path == "broken.py" for c in chunks)
    assert all(1 <= c.start_line <= c.end_line for c in chunks)


# ---------------------------------------------------------------------------
# Empty, whitespace, and binary files
# ---------------------------------------------------------------------------


def test_empty_and_whitespace_file():
    for src in ("", "   \n\t\n"):
        assert _chunk_python(src, "e.py", "python", "h") == []
    assert _chunk_generic("", "e.py", "python", "h") == []


def test_empty_file_read_and_chunk_does_not_raise(tmp_path):
    f = tmp_path / "empty.py"
    f.write_text("", encoding="utf-8")
    chunks = read_and_chunk_file(f, tmp_path)
    assert isinstance(chunks, list)


def test_binary_bytes_in_source_file_does_not_crash(tmp_path):
    """Random binary bytes (e.g., PNG header masquerading as .py) must not crash the indexer."""
    f = tmp_path / "corrupt_binary.py"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xfd\x80def foo():\n    return 42\n")
    chunks = read_and_chunk_file(f, tmp_path)
    assert len(chunks) >= 1
    assert all(c.file_path == "corrupt_binary.py" for c in chunks)


# ---------------------------------------------------------------------------
# Encoding & line-ending resilience
# ---------------------------------------------------------------------------


def test_latin1_encoded_file_does_not_crash(tmp_path):
    """Files saved with Latin-1 high bytes (e.g. 0xe9 for é) must be decoded safely via errors='replace'."""
    f = tmp_path / "latin1_mod.py"
    latin1_src = "# Author: Ren\xe9 Descartes\ndef compute_caf\xe9():\n    return 'caf\xe9'\n".encode("latin-1")
    f.write_bytes(latin1_src)
    chunks = read_and_chunk_file(f, tmp_path)
    assert len(chunks) >= 1
    assert any("return" in c.code for c in chunks)


def test_utf8_bom_and_crlf_line_endings(tmp_path):
    """UTF-8 BOM and Windows CRLF line endings must parse cleanly."""
    f = tmp_path / "windows_bom.py"
    raw = "\ufeffdef greet(name):\r\n    msg = f'hello {name}'\r\n    return msg\r\n".encode("utf-8")
    f.write_bytes(raw)
    chunks = read_and_chunk_file(f, tmp_path)
    assert len(chunks) >= 1
    assert all(1 <= c.start_line <= c.end_line for c in chunks)


def test_utf16_encoded_file_survives_without_exception(tmp_path):
    """UTF-16 files contain null bytes; read_and_chunk_file must not raise UnicodeDecodeError or ValueError."""
    f = tmp_path / "utf16_script.py"
    f.write_bytes("def hello():\n    return 1\n".encode("utf-16"))
    chunks = read_and_chunk_file(f, tmp_path)
    assert isinstance(chunks, list)
    assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# Minified code & giant single-line files
# ---------------------------------------------------------------------------


def test_minified_giant_single_line_js_is_bounded(tmp_path):
    """A minified JS file with 18,000 chars on a single line must not hang or blow past chunk caps."""
    f = tmp_path / "bundle.min.js"
    giant_line = "function a(){return 1;}" + ("var x=1234567890;" * 1000)
    f.write_text(giant_line, encoding="utf-8")
    chunks = read_and_chunk_file(f, tmp_path)
    assert len(chunks) >= 1
    max_allowed_chars = MAX_CHUNK_TOKENS * 6
    assert all(len(c.code) <= max_allowed_chars for c in chunks)


# ---------------------------------------------------------------------------
# Multi-language generic chunking (.ts, .js, .java, .go)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,source,expected_lang,expected_symbol",
    [
        (
            "service.ts",
            "export class UserService {\n  async fetchUser(id: string) {\n    return id;\n  }\n}\n",
            "typescript",
            "UserService",
        ),
        (
            "utils.js",
            "export async function calculateTotal(items) {\n  let sum = 0;\n  return sum;\n}\n",
            "javascript",
            "calculateTotal",
        ),
        (
            "Handler.java",
            "public class RequestHandler {\n    public void handle() {\n        System.out.println(\"ok\");\n    }\n}\n",
            "java",
            "RequestHandler",
        ),
        (
            "main.go",
            "package main\n\nfunc ProcessOrder(orderID int) error {\n\treturn nil\n}\n",
            "go",
            "ProcessOrder",
        ),
    ],
)
def test_multilanguage_generic_chunker_extracts_symbols_and_valid_lines(
    tmp_path, filename, source, expected_lang, expected_symbol
):
    f = tmp_path / filename
    f.write_text(source, encoding="utf-8")
    chunks = read_and_chunk_file(f, tmp_path)
    assert len(chunks) >= 1
    assert all(c.language == expected_lang for c in chunks)
    assert all(c.file_path == filename for c in chunks)
    assert all(1 <= c.start_line <= c.end_line for c in chunks)
    assert any(c.name == expected_symbol for c in chunks)


# ---------------------------------------------------------------------------
# Pruning token budget invariants & edge cases
# ---------------------------------------------------------------------------


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


def test_token_budget_zero_returns_no_tokens():
    items = [
        {
            "code": "def add(a, b):\n    return a + b\n",
            "metadata": {"file": "math.py", "function": "add"},
        }
    ]
    chunks, stats = prune_context("add numbers", items, token_budget=0)
    assert chunks == []
    assert stats.kept_est_tokens == 0
    assert stats.kept_chunks == 0


def test_prune_context_empty_input():
    chunks, stats = prune_context("any query", [], token_budget=1800)
    assert chunks == []
    assert stats.original_chunks == 0
    assert stats.kept_chunks == 0
    assert stats.kept_est_tokens == 0


def test_single_monster_chunk_exceeding_budget_is_truncated_within_budget():
    """A single chunk of ~3,000 tokens against a 500-token budget must be windowed/truncated to <= 500 est tokens."""
    monster_code = "\n".join(
        f"def step_{k}(x):\n    y = x * {k} + {k + 1}\n    return y" for k in range(300)
    )
    items = [{"code": monster_code, "metadata": {"file": "giant.py", "function": "step_0"}}]
    chunks, stats = prune_context("step calculation", items, token_budget=500)
    assert stats.original_est_tokens > 1500
    assert stats.kept_est_tokens <= 500
    assert len(chunks) == 1
    assert len(chunks[0]["text"]) < len(monster_code)


def test_selective_method_falls_back_to_heuristic_when_model_fails(monkeypatch):
    """If SelectiveContextPruner fails to load (e.g. offline/no weights), prune_context falls back to heuristic."""
    monkeypatch.setattr(
        pruning_mod,
        "_load_selective_pruner",
        lambda *a, **k: (None, "RuntimeError: simulated model load failure"),
    )
    items = [
        {
            "code": "def authenticate(user, token):\n    return user is not None and bool(token)\n",
            "metadata": {"file": "auth.py", "function": "authenticate"},
        }
    ]
    chunks, stats = prune_context("authenticate user", items, token_budget=500, method="selective")
    assert len(chunks) == 1
    assert stats.method == "heuristic"
    assert stats.requested_method == "selective"
    assert stats.kept_est_tokens <= 500
    assert stats.fallback_reason is not None
    assert "RuntimeError" in stats.fallback_reason
