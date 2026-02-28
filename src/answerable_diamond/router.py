"""
AnswerabilityRouter — the 'Answerable?' diamond from the block diagram.

Flow (mirrors the diagram exactly):
  1. User prompt arrives at the Online Pipeline.
  2. The router runs the chosen approach against the RAG-retrieved chunks.
  3. If ANSWERABLE  → send prompt + chunks straight to the AI Agent.
  4. If UNANSWERABLE → escalate to Retrieval (LongRAG + FastGen),
                       then Context Pruning & Injection,
                       then re-enter the diamond (up to max_retries times).

The router is approach-agnostic: pass any AnswerabilityApproach instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from .approaches import (
    AnswerabilityApproach,
    AnswerabilityResult,
    Decision,
    KeywordOverlapApproach,
)


@dataclass
class RouterOutput:
    decision: Decision
    final_chunks: List[str]
    result: AnswerabilityResult
    escalation_count: int          # how many times we had to escalate
    route: str                     # "RAG" | "LongRAG"


class AnswerabilityRouter:
    """
    Wraps an AnswerabilityApproach and orchestrates the routing logic.

    Parameters
    ----------
    approach : AnswerabilityApproach
        The decision logic to use (SELF-ROUTE, keyword, semantic, confidence).
    retrieval_fn : callable, optional
        A function (query: str, chunks: List[str]) -> List[str] that performs
        the escalated LongRAG + FastGen retrieval.  If None, a stub is used.
    pruning_fn : callable, optional
        A function (query: str, chunks: List[str]) -> List[str] that prunes
        and injects context before re-checking answerability.
    max_retries : int
        Maximum number of escalation cycles before forcing a decision.
    """

    def __init__(
        self,
        approach: Optional[AnswerabilityApproach] = None,
        retrieval_fn: Optional[Callable] = None,
        pruning_fn: Optional[Callable] = None,
        max_retries: int = 2,
    ):
        self.approach = approach or KeywordOverlapApproach()
        self.retrieval_fn = retrieval_fn or _stub_retrieval
        self.pruning_fn = pruning_fn or _stub_pruning
        self.max_retries = max_retries

    def route(self, query: str, initial_chunks: List[str]) -> RouterOutput:
        """
        Run the full routing pipeline and return a RouterOutput.
        """
        chunks = list(initial_chunks)
        escalation_count = 0

        for attempt in range(self.max_retries + 1):
            result = self.approach.check(query=query, retrieved_chunks=chunks)

            if result.is_answerable:
                route = "RAG" if escalation_count == 0 else "LongRAG"
                return RouterOutput(
                    decision=Decision.ANSWERABLE,
                    final_chunks=chunks,
                    result=result,
                    escalation_count=escalation_count,
                    route=route,
                )

            # UNANSWERABLE — escalate
            if attempt < self.max_retries:
                escalation_count += 1
                chunks = self.retrieval_fn(query, chunks)
                chunks = self.pruning_fn(query, chunks)

        # Exhausted retries — still unanswerable; pass to agent with best chunks
        return RouterOutput(
            decision=Decision.UNANSWERABLE,
            final_chunks=chunks,
            result=result,
            escalation_count=escalation_count,
            route="LongRAG",
        )

    def explain(self, output: RouterOutput) -> str:
        """Return a human-readable summary of the routing decision."""
        lines = [
            f"Approach   : {output.result.approach_name}",
            f"Decision   : {output.decision.value.upper()}",
            f"Confidence : {output.result.confidence:.0%}",
            f"Route      : {output.route}",
            f"Escalations: {output.escalation_count}",
            f"Reasoning  : {output.result.reasoning}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stub implementations (replaced by real ones in integration)
# ---------------------------------------------------------------------------

def _stub_retrieval(query: str, current_chunks: List[str]) -> List[str]:
    """
    Placeholder for LongRAG + FastGen retrieval.
    In the real system this calls the offline-indexed LongRAG corpus.
    """
    return current_chunks + [
        f"[LongRAG escalation stub] Additional context retrieved for: '{query}'"
    ]


def _stub_pruning(query: str, chunks: List[str]) -> List[str]:
    """
    Placeholder for Context Pruning & Injection.
    In the real system this runs LLMLingua-2 or Selective Context.
    """
    return chunks
