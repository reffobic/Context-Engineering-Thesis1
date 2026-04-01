from __future__ import annotations

from pathlib import Path
from .chunk_normalize import strip_redundant_line_number_column
from .prompt_builder import build_coding_prompt, ContextChunk


def inject_context(user_prompt: str, chunks: list[dict]) -> dict[str, str]:
    """
    Build structured prompts for the LLM.

    Returns separate ``system`` and ``user`` strings so ``OllamaAgent`` can call
    ``/api/chat`` with proper roles instead of stuffing both into ``/api/generate``.
    """
    context_chunks = [
        ContextChunk(
            text=strip_redundant_line_number_column(c["code"]),
            path=c["metadata"]["file"],
            symbol=c["metadata"]["function"],
            language=c["metadata"]["language"],
            start_line=c["metadata"]["start_line"],
        )
        for c in chunks
    ]
    system_prompt, user_message = build_coding_prompt(user_prompt, context_chunks)
    return {"system": system_prompt, "user": user_message}