"""Unit tests for backend.injection.prompt_builder."""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.injection.prompt_builder import (
    CONTEXT_TRUNCATED_TAG,
    PromptBuilder,
    build_coding_prompt,
    default_token_estimate,
)


def test_empty_chunks_placeholder():
    b = PromptBuilder()
    out = b.format_context_body([])
    assert "no context chunks" in out.lower()


def test_xml_structure_and_task():
    b = PromptBuilder()
    chunks = [
        {"path": "a.py", "text": "x = 1"},
    ]
    user = b.build_user_message("Do the thing.", chunks)
    assert "<CONTEXT>" in user
    assert "<FILES_AND_CODE>" in user
    assert "</FILES_AND_CODE>" in user
    assert "</CONTEXT>" in user
    assert "<TASK>" in user
    assert "Do the thing." in user


def test_groups_same_path_together():
    b = PromptBuilder()
    chunks = [
        {"path": "auth.py", "symbol": "login", "text": "def login(): pass"},
        {"path": "db.py", "text": "class DB: pass"},
        {"path": "auth.py", "symbol": "logout", "text": "def logout(): pass"},
    ]
    body = b.format_context_body(chunks)
    # auth.py block should appear once with two snippets
    assert body.count("=== File: auth.py ===") == 1
    assert "login" in body and "logout" in body
    assert "=== File: db.py ===" in body


def test_line_numbers_when_start_line():
    b = PromptBuilder()
    chunks = [
        {"path": "f.py", "text": "def a():\n    return 1", "start_line": 10},
    ]
    body = b.format_context_body(chunks)
    assert "  10 | def a():" in body
    assert "  11 |     return 1" in body


def test_dependencies_in_user_message():
    b = PromptBuilder()
    chunks = [
        {"path": "x.py", "text": "pass", "dependencies": "requires fastapi"},
    ]
    user = b.build_user_message("task", chunks, extra_dependencies="also redis")
    assert "<DEPENDENCIES>" in user
    assert "fastapi" in user
    assert "redis" in user


def test_truncation_inserts_notice():
    def tiny_budget(s: str) -> int:
        return len(s)  # 1 char ≈ 1 token → very tight budget

    b = PromptBuilder(estimate_tokens=tiny_budget)
    chunks = [
        {"path": "a.py", "text": "one"},
        {"path": "b.py", "text": "two" * 50},
    ]
    body = b.format_context_body(chunks, max_context_tokens=20)
    assert CONTEXT_TRUNCATED_TAG in body


def test_build_coding_prompt_returns_tuple():
    sys_p, user = build_coding_prompt("fix bug", [{"path": "m.py", "text": "x"}])
    assert "expert software engineer" in sys_p
    assert "<TASK>" in user


def test_default_token_estimate():
    assert default_token_estimate("") == 0
    assert default_token_estimate("abcd") == 1
