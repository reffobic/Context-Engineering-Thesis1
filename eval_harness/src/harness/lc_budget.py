"""Budgeted LC selection for harness arms (Always-LC / PREASSESS).

Kept in the harness package so --smoke / --allow-dirty runs do not depend on the
frozen backend snapshot containing ``injection.lc_budget`` yet. Backend
``injection/lc_budget.py`` mirrors this for GASR/SELF/GAPA after a freeze bump.
"""

from __future__ import annotations

import os
from typing import Optional

DEFAULT_LC_TOKEN_BUDGET = 8000


def lc_token_budget() -> int:
    return int(os.environ.get("LC_TOKEN_BUDGET", str(DEFAULT_LC_TOKEN_BUDGET)))


def lc_file_block(path: str, file_chunks: list[dict]) -> str:
    parts = []
    for chunk in file_chunks:
        code = (chunk.get("text") or "").strip()
        if not code:
            continue
        sym = f" (Symbol: {chunk['symbol']})" if chunk.get("symbol") else ""
        lang = f" [lang={chunk['language']}]" if chunk.get("language") else ""
        parts.append(f"--- Snippet{sym}{lang} ---\n{code}")
    body = "\n\n".join(parts) if parts else "(empty)"
    return f"=== File: {path} ===\n{body}"


def select_lc_chunks_by_rank(
    chunks: list[dict],
    max_tokens: Optional[int] = None,
) -> list[dict]:
    if max_tokens is None:
        max_tokens = lc_token_budget()

    grouped: dict[str, list[dict]] = {}
    for chunk in chunks:
        path = chunk.get("path") or "(unknown path)"
        grouped.setdefault(path, []).append(chunk)

    selected_paths: list[str] = []
    remaining = max_tokens

    for path, file_chunks in grouped.items():
        block = lc_file_block(path, file_chunks)
        block_tokens = max(1, len(block) // 4)

        if block_tokens <= remaining:
            selected_paths.append(path)
            remaining -= block_tokens
        elif not selected_paths:
            selected_paths.append(path)
            break

    return [chunk for path in selected_paths for chunk in grouped[path]]
