"""
Context Pruner — prunes retrieved chunks before injection into the LLM prompt.

Based on:
  - Selective Context (Li et al., 2023): self-information based token filtering.
  - LLMLingua-2 (Pan et al., 2024): distillation-enhanced filtering.
  - "Lost in the Middle" (Liu et al., 2024): place most relevant chunks at
    the START or END of the prompt (U-shaped performance curve).

The pruner is called AFTER the Answerable? diamond decides to escalate,
and BEFORE the final prompt is assembled for the AI Agent.
"""

from __future__ import annotations

import re
from typing import List, Tuple


class ContextPruner:
    """
    Simple context pruner that:
      1. Scores each sentence in each chunk by keyword overlap with the query.
      2. Keeps only the top-p% of sentences by score.
      3. Re-orders chunks so the highest-scoring chunk is first (primacy bias).

    This is a lightweight stand-in for LLMLingua-2.  Replace `_score_sentence`
    with a real token-probability scorer once model access is available.
    """

    def __init__(self, keep_ratio: float = 0.6):
        """
        keep_ratio: fraction of sentences to retain per chunk (default 60%).
        """
        self.keep_ratio = keep_ratio

    def prune(self, query: str, chunks: List[str]) -> List[str]:
        """
        Prune and re-order chunks.  Returns a new list of pruned chunk strings.
        """
        query_tokens = self._tokenize(query)
        scored_chunks: List[Tuple[float, str]] = []

        for chunk in chunks:
            pruned = self._prune_chunk(chunk, query_tokens)
            chunk_score = self._score_text(pruned, query_tokens)
            scored_chunks.append((chunk_score, pruned))

        # Sort by descending score so best chunk is first (primacy bias fix)
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        return [text for _, text in scored_chunks if text.strip()]

    def _prune_chunk(self, chunk: str, query_tokens: set) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", chunk)
        if not sentences:
            return chunk

        scored = [(self._score_text(s, query_tokens), s) for s in sentences]
        scored.sort(key=lambda x: x[0], reverse=True)

        keep_n = max(1, int(len(scored) * self.keep_ratio))
        kept = [s for _, s in scored[:keep_n]]

        # Restore original order for readability
        original_order = [s for s in sentences if s in kept]
        return " ".join(original_order)

    @staticmethod
    def _tokenize(text: str) -> set:
        return set(re.findall(r"\b[a-z]+\b", text.lower()))

    @staticmethod
    def _score_text(text: str, query_tokens: set) -> float:
        if not query_tokens:
            return 0.0
        text_tokens = set(re.findall(r"\b[a-z]+\b", text.lower()))
        return len(text_tokens & query_tokens) / len(query_tokens)
