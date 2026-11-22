"""Budgeted LC: LC_TOKEN_BUDGET defaults to 8000 and caps ranked parent-file selection."""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval_harness" / "src"))

from harness.arms import _lc_file_block, run_always_lc  # noqa: E402
from harness.lc_budget import DEFAULT_LC_TOKEN_BUDGET, lc_token_budget, select_lc_chunks_by_rank  # noqa: E402


def _fat_chunks(n_files: int = 20, lines: int = 80) -> list[dict]:
    text = "x = 1\n" * lines
    return [
        {"path": f"pkg/m{i}.py", "text": text, "symbol": f"f{i}", "language": "python"}
        for i in range(n_files)
    ]


def test_lc_token_budget_default_is_8000(monkeypatch):
    monkeypatch.delenv("LC_TOKEN_BUDGET", raising=False)
    assert lc_token_budget() == DEFAULT_LC_TOKEN_BUDGET == 8000
    # select with default max_tokens=None uses LC budget
    selected = select_lc_chunks_by_rank(_fat_chunks(n_files=40, lines=400), max_tokens=None)
    paths = []
    for c in selected:
        if c["path"] not in paths:
            paths.append(c["path"])
    grouped: dict[str, list] = {}
    for c in selected:
        grouped.setdefault(c["path"], []).append(c)
    total = sum(max(1, len(_lc_file_block(p, cs)) // 4) for p, cs in grouped.items())
    assert total <= 8000
    assert len(paths) < 40  # must have dropped some under 8k


def test_lc_token_budget_env_override(monkeypatch):
    monkeypatch.setenv("LC_TOKEN_BUDGET", "1500")
    assert lc_token_budget() == 1500
    selected = select_lc_chunks_by_rank(_fat_chunks(), max_tokens=None)
    grouped: dict[str, list] = {}
    for c in selected:
        grouped.setdefault(c["path"], []).append(c)
    total = sum(max(1, len(_lc_file_block(p, cs)) // 4) for p, cs in grouped.items())
    assert total <= 1500


def test_select_preserves_retrieval_priority():
    chunks = _fat_chunks(10, lines=40)
    selected = select_lc_chunks_by_rank(chunks, max_tokens=2000)
    paths = []
    for c in selected:
        if c["path"] not in paths:
            paths.append(c["path"])
    # First selected path must be the first retrieved file (rank order)
    assert paths[0] == "pkg/m0.py"


def test_run_always_lc_applies_budget(monkeypatch):
    monkeypatch.setenv("LC_TOKEN_BUDGET", "2000")

    class FakeServer:
        def call_tool(self, name, *args, **kwargs):
            if name == "retrieve_context":
                # shaped like retrieve hits with parent_context
                return [
                    {
                        "code": "pass\n",
                        "parent_context": "x = 1\n" * 100,
                        "metadata": {"file": f"a{i}.py", "function": f"f{i}", "language": "python"},
                    }
                    for i in range(15)
                ]
            if name == "inject_context":
                # args: prompt, chunks
                self.last_chunks = args[1]
                return {"system": "s", "user": "u"}
            if name == "ollama_generate":
                return "ok"
            raise AssertionError(name)

        def reconstruct_file_context(self, items):
            return items

    server = FakeServer()
    out = run_always_lc(server, "q")
    assert out["route"] == "LC"
    paths = {c["path"] for c in server.last_chunks}
    assert len(paths) < 15
    # est tokens of selected blocks under budget
    grouped: dict[str, list] = {}
    for c in server.last_chunks:
        grouped.setdefault(c["path"], []).append(c)
    total = sum(max(1, len(_lc_file_block(p, cs)) // 4) for p, cs in grouped.items())
    assert total <= 2000
