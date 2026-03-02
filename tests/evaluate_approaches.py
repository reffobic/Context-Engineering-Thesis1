"""
End-to-end evaluation of all four Answerable? approaches on the story corpus.

Run:
    python tests/evaluate_approaches.py

Output: per-approach metrics + comparison table.
"""

import sys
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.answerable_diamond.approaches import (
    SelfRouteApproach,
    KeywordOverlapApproach,
    SemanticSimilarityApproach,
    ConfidenceEstimationApproach,
)
from src.answerable_diamond.router import AnswerabilityRouter
from tests.stories.story_corpus import TEST_CASES
from tests.metrics import EvalSample, compute_metrics, compare_approaches


def evaluate_approach(approach, test_cases):
    samples = []
    for tc in test_cases:
        # Build a router with 1 escalation allowed
        router = AnswerabilityRouter(approach=approach, max_retries=1)
        output = router.route(
            query=tc["query"],
            initial_chunks=tc["relevant_chunks"],
        )
        samples.append(EvalSample(
            query=tc["query"],
            predicted=output.decision.value,
            ground_truth=tc["expected"],
            confidence=output.result.confidence,
            escalated=output.escalation_count > 0,
            approach_name=approach.name,
        ))
    return compute_metrics(samples)


def main():
    approaches = [
        KeywordOverlapApproach(threshold=0.4),
        SemanticSimilarityApproach(threshold=0.55),
        SelfRouteApproach(),
        ConfidenceEstimationApproach(confidence_threshold=5),
    ]

    reports = []
    for approach in approaches:
        report = evaluate_approach(approach, TEST_CASES)
        print(report)
        reports.append(report)

    print("\n" + "=" * 70)
    print("  COMPARISON TABLE")
    print("=" * 70)
    print(compare_approaches(reports))
    print()


if __name__ == "__main__":
    main()
