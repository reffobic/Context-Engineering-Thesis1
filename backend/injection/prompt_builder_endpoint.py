from __future__ import annotations

from pathlib import Path
from .chunk_normalize import strip_redundant_line_number_column
from .prompt_builder import build_coding_prompt, ContextChunk


import os

DEFAULT_MAX_CONTEXT_TOKENS = int(os.environ.get("MAX_CONTEXT_TOKENS", "24000"))

def inject_context(user_prompt: str, chunks: list[dict]) -> dict[str, str]:
    """
    Build structured prompts for the LLM.

    Returns separate ``system`` and ``user`` strings so ``OllamaAgent`` can call
    ``/api/chat`` with proper roles instead of stuffing both into ``/api/generate``.
    """
    # Supports two upstream shapes:
    # 1) Retrieval items (P3): {"code": str, "metadata": {...}}
    # 2) Pruned ContextChunks (P4): {"text": str, "path": str, ...}
    context_chunks: list[ContextChunk] = []
    for c in chunks:
        if "text" in c:
            # Already PromptBuilder-compatible
            cc = ContextChunk(**c)  # type: ignore[arg-type]
            context_chunks.append(cc)
            continue
        meta = c.get("metadata", {}) or {}
        context_chunks.append(
            ContextChunk(
                text=strip_redundant_line_number_column(c.get("code", "") or ""),
                path=meta.get("file", ""),
                symbol=meta.get("function", ""),
                language=meta.get("language", ""),
                start_line=meta.get("start_line", 0),
            )
        )
    system_prompt, user_message = build_coding_prompt(
        user_prompt,
        context_chunks,
        max_context_tokens=DEFAULT_MAX_CONTEXT_TOKENS,
    )
    return {"system": system_prompt, "user": user_message}