"""
Tests for the Answerable? diamond — all four approaches, on the story corpus.

Run with:
    python -m pytest tests/test_answerable_diamond.py -v

Or run the demo directly:
    python tests/test_answerable_diamond.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.answerable_diamond.approaches import (
    SelfRouteApproach,
    KeywordOverlapApproach,
    SemanticSimilarityApproach,
    ConfidenceEstimationApproach,
    Decision,
)
from src.answerable_diamond.router import AnswerabilityRouter
from tests.stories.story_corpus import TEST_CASES, STORY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_approaches():
    return [
        KeywordOverlapApproach(threshold=0.4),
        SemanticSimilarityApproach(threshold=0.55),
        SelfRouteApproach(),
        ConfidenceEstimationApproach(confidence_threshold=5),
    ]


def _run_case(approach, tc):
    result = approach.check(
        query=tc["query"],
        retrieved_chunks=tc["relevant_chunks"],
    )
    return result


# ---------------------------------------------------------------------------
# pytest tests
# ---------------------------------------------------------------------------

def test_keyword_answerable_cases():
    approach = KeywordOverlapApproach(threshold=0.4)
    answerable_cases = [tc for tc in TEST_CASES if tc["expected"] == "answerable"]
    for tc in answerable_cases:
        result = _run_case(approach, tc)
        assert result.decision == Decision.ANSWERABLE, (
            f"[{tc['id']}] Expected ANSWERABLE but got {result.decision}. "
            f"Reasoning: {result.reasoning}"
        )


def test_keyword_unanswerable_cases():
    approach = KeywordOverlapApproach(threshold=0.4)
    unanswerable_cases = [
        tc for tc in TEST_CASES
        if tc["expected"] == "unanswerable"
        and approach.name not in tc.get("known_failure_approaches", [])
    ]
    for tc in unanswerable_cases:
        result = _run_case(approach, tc)
        assert result.decision == Decision.UNANSWERABLE, (
            f"[{tc['id']}] Expected UNANSWERABLE but got {result.decision}. "
            f"Reasoning: {result.reasoning}"
        )


def test_semantic_similarity_runs():
    approach = SemanticSimilarityApproach(threshold=0.55)
    for tc in TEST_CASES:
        result = _run_case(approach, tc)
        assert result.decision in (Decision.ANSWERABLE, Decision.UNANSWERABLE)
        assert 0.0 <= result.confidence <= 1.0


def test_self_route_runs():
    approach = SelfRouteApproach()
    for tc in TEST_CASES:
        result = _run_case(approach, tc)
        assert result.decision in (Decision.ANSWERABLE, Decision.UNANSWERABLE)


def test_confidence_estimation_runs():
    approach = ConfidenceEstimationApproach(confidence_threshold=5)
    for tc in TEST_CASES:
        result = _run_case(approach, tc)
        assert result.decision in (Decision.ANSWERABLE, Decision.UNANSWERABLE)


def test_router_escalation():
    """TC-06: starts unanswerable with initial chunks, becomes answerable after escalation."""
    tc = next(t for t in TEST_CASES if t["id"] == "TC-06")

    escalated_chunks = tc["escalated_chunks"]

    def mock_retrieval(query, chunks):
        return escalated_chunks

    router = AnswerabilityRouter(
        approach=KeywordOverlapApproach(threshold=0.4),
        retrieval_fn=mock_retrieval,
        max_retries=1,
    )
    output = router.route(query=tc["query"], initial_chunks=tc["relevant_chunks"])

    assert output.decision == Decision.ANSWERABLE
    assert output.escalation_count == 1
    assert output.route == "LongRAG"


def test_router_no_escalation_needed():
    """TC-01: directly answerable, no escalation."""
    tc = next(t for t in TEST_CASES if t["id"] == "TC-01")
    router = AnswerabilityRouter(approach=KeywordOverlapApproach(threshold=0.4))
    output = router.route(query=tc["query"], initial_chunks=tc["relevant_chunks"])

    assert output.decision == Decision.ANSWERABLE
    assert output.escalation_count == 0
    assert output.route == "RAG"


# ---------------------------------------------------------------------------
# Demo runner (no pytest needed)
# ---------------------------------------------------------------------------

def run_demo():
    approaches = _make_approaches()

    print("=" * 70)
    print("  CONTEXT ENGINEERING OPTIMIZATION ENGINE")
    print("  Answerable? Diamond — Approach Comparison Demo")
    print(f"  Story: {STORY['title']}")
    print("=" * 70)

    for tc in TEST_CASES:
        print(f"\n{'─' * 70}")
        print(f"  [{tc['id']}] {tc['description']}")
        print(f"  Query   : {tc['query']}")
        print(f"  Expected: {tc['expected'].upper()}")
        print(f"  Answer  : {tc['answer']}")
        print()

        for approach in approaches:
            result = approach.check(
                query=tc["query"],
                retrieved_chunks=tc["relevant_chunks"],
            )
            match = "✓" if result.decision.value == tc["expected"] else "✗"
            print(
                f"  {match} [{approach.name:<42}] "
                f"{result.decision.value.upper():<14} "
                f"conf={result.confidence:.0%}"
            )
            print(f"    → {result.reasoning[:100]}")

    print(f"\n{'─' * 70}")
    print("\n  ROUTER DEMO (TC-06 — escalation test)")
    tc = next(t for t in TEST_CASES if t["id"] == "TC-06")

    def mock_retrieval(query, chunks):
        return tc["escalated_chunks"]

    router = AnswerabilityRouter(
        approach=KeywordOverlapApproach(threshold=0.4),
        retrieval_fn=mock_retrieval,
        max_retries=1,
    )
    output = router.route(query=tc["query"], initial_chunks=tc["relevant_chunks"])
    print(router.explain(output))
    print()


if __name__ == "__main__":
    run_demo()
