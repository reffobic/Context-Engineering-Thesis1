"""
Endpoint adapter for P4 pruning (tool_registry integration).

Tool signature for the MCP pipeline:
  prune_context(query: str, retrieved_items: list[dict], token_budget: int = 1800) -> list[dict]

Returns a list of PromptBuilder-compatible ContextChunk dicts (keys like: text, path, symbol, language, start_line).
"""

from __future__ import annotations

from typing import Any

from .file import prune_context as _prune


def prune_context(query: str, retrieved_items: list[dict], token_budget: int = 1800) -> list[dict[str, Any]]:
    pruned_chunks, _stats = _prune(query, retrieved_items, token_budget=token_budget)
    return pruned_chunks

