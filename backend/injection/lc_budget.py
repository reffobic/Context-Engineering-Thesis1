"""Budgeted long-context (LC) parent-file selection.

``LC_TOKEN_BUDGET`` (default 8000) caps how many ranked parent-file blocks we
hand to inject. ``MAX_CONTEXT_TOKENS`` (24000) stays the inject safety ceiling;
pre-selecting under the LC budget avoids relying on that ceiling to drop files
(and keeps ``hit_max_context_tokens`` near zero when selection already fits).
"""

from __future__ import annotations

import os
from typing import Optional

DEFAULT_LC_TOKEN_BUDGET = 8000


def lc_token_budget() -> int:
    """Effective LC packing budget (est. tokens ≈ chars/4)."""
    return int(os.environ.get("LC_TOKEN_BUDGET", str(DEFAULT_LC_TOKEN_BUDGET)))


def _lc_file_block(path: str, file_chunks: list[dict]) -> str:
    """Render a file block the same way PromptBuilder / harness do."""
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
    """
    Keep complete LC file blocks in retrieval-priority (first-seen path) order
    until ``max_tokens`` is exhausted. Files that do not fit are skipped so a
    later smaller file may still be kept. If the first file alone exceeds the
    budget it is kept and left for inject/PromptBuilder to hard-truncate.
    """
    if max_tokens is None:
        max_tokens = lc_token_budget()

    grouped: dict[str, list[dict]] = {}
    for chunk in chunks:
        path = chunk.get("path") or "(unknown path)"
        grouped.setdefault(path, []).append(chunk)

    selected_paths: list[str] = []
    remaining = max_tokens

    for path, file_chunks in grouped.items():
        block = _lc_file_block(path, file_chunks)
        block_tokens = max(1, len(block) // 4)

        if block_tokens <= remaining:
            selected_paths.append(path)
            remaining -= block_tokens
        elif not selected_paths:
            # Oversized first file — keep it; inject may truncate inside the block.
            selected_paths.append(path)
            break
        # else: skip and consider lower-ranked files

    return [chunk for path in selected_paths for chunk in grouped[path]]
