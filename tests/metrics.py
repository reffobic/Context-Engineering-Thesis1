"""
Evaluation metrics for the Answerable? diamond.

Hana's task: define and compute the metrics we use to compare approaches.

Metrics implemented:
  1. Routing Accuracy       — % of queries correctly classified as answerable/unanswerable
  2. Precision / Recall / F1 for ANSWERABLE class
  3. Escalation Rate        — % of queries that had to escalate to LongRAG
  4. False Escalation Rate  — % of answerable queries incorrectly escalated (wasted cost)
  5. Missed Escalation Rate — % of unanswerable queries not escalated (hallucination risk)
  6. Average Confidence     — mean confidence score across all decisions
  7. Cost Proxy             — relative token cost (RAG=1x, LongRAG=10x)

Reference:
  - Li et al. (2024) SELF-ROUTE: routes >50% of queries to cheaper RAG pipeline.
  - Leng et al. (2024): uses GPT-4o judge calibrated to 88.1% human agreement.
  - Jiang et al. (2024) LongRAG: uses Exact Match (EM) and token-level F1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class EvalSample:
    """One labelled test case."""
    query: str
    predicted: str          # "answerable" | "unanswerable"
    ground_truth: str       # "answerable" | "unanswerable"
    confidence: float
    escalated: bool         # did the router escalate to LongRAG?
    approach_name: str


@dataclass
class MetricsReport:
    approach_name: str
    n_samples: int

    # Classification
    routing_accuracy: float
    precision: float
    recall: float
    f1: float

    # Routing efficiency
    escalation_rate: float
    false_escalation_rate: float
    missed_escalation_rate: float

    # Quality
    avg_confidence: float
    cost_proxy: float          # weighted average token cost

    # Raw counts
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    def __str__(self) -> str:
        return (
            f"\n{'─' * 55}\n"
            f"  Approach : {self.approach_name}\n"
            f"  Samples  : {self.n_samples}\n"
            f"  ─── Classification ───────────────────────────\n"
            f"  Accuracy : {self.routing_accuracy:.1%}\n"
            f"  Precision: {self.precision:.1%}   "
            f"Recall: {self.recall:.1%}   F1: {self.f1:.1%}\n"
            f"  ─── Routing Efficiency ───────────────────────\n"
            f"  Escalation rate        : {self.escalation_rate:.1%}\n"
            f"  False escalation rate  : {self.false_escalation_rate:.1%}  "
            f"(wasted LongRAG calls)\n"
            f"  Missed escalation rate : {self.missed_escalation_rate:.1%}  "
            f"(hallucination risk)\n"
            f"  ─── Quality ──────────────────────────────────\n"
            f"  Avg confidence : {self.avg_confidence:.1%}\n"
            f"  Cost proxy     : {self.cost_proxy:.2f}x  "
            f"(1.0 = pure RAG, 10.0 = pure LongRAG)\n"
        )


def compute_metrics(samples: List[EvalSample]) -> MetricsReport:
    """
    Compute all metrics from a list of EvalSample objects.

    Convention: ANSWERABLE = positive class.
    """
    if not samples:
        raise ValueError("No samples provided.")

    approach_name = samples[0].approach_name
    n = len(samples)

    tp = fp = tn = fn = 0
    escalated_count = 0
    false_escalations = 0
    missed_escalations = 0
    confidence_sum = 0.0
    cost_sum = 0.0

    RAG_COST = 1.0
    LONGRAG_COST = 10.0  # LongRAG uses ~10x more tokens than RAG (Li et al.)

    for s in samples:
        pred_pos = s.predicted == "answerable"
        true_pos = s.ground_truth == "answerable"

        if pred_pos and true_pos:
            tp += 1
        elif pred_pos and not true_pos:
            fp += 1
        elif not pred_pos and not true_pos:
            tn += 1
        else:
            fn += 1

        if s.escalated:
            escalated_count += 1
            if true_pos:
                false_escalations += 1   # answerable but we escalated anyway
        else:
            if not true_pos:
                missed_escalations += 1  # unanswerable but we didn't escalate

        confidence_sum += s.confidence
        cost_sum += LONGRAG_COST if s.escalated else RAG_COST

    accuracy = (tp + tn) / n
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    n_answerable = sum(1 for s in samples if s.ground_truth == "answerable")
    n_unanswerable = n - n_answerable

    return MetricsReport(
        approach_name=approach_name,
        n_samples=n,
        routing_accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
        escalation_rate=escalated_count / n,
        false_escalation_rate=false_escalations / n_answerable if n_answerable else 0.0,
        missed_escalation_rate=missed_escalations / n_unanswerable if n_unanswerable else 0.0,
        avg_confidence=confidence_sum / n,
        cost_proxy=cost_sum / n,
        tp=tp, fp=fp, tn=tn, fn=fn,
    )


def compare_approaches(reports: List[MetricsReport]) -> str:
    """Print a side-by-side comparison table."""
    header = (
        f"\n{'Approach':<44} {'Acc':>6} {'F1':>6} {'EscRate':>8} "
        f"{'FalseEsc':>9} {'MissEsc':>8} {'Cost':>6}"
    )
    sep = "─" * len(header)
    rows = [header, sep]
    for r in reports:
        rows.append(
            f"{r.approach_name:<44} "
            f"{r.routing_accuracy:>6.1%} "
            f"{r.f1:>6.1%} "
            f"{r.escalation_rate:>8.1%} "
            f"{r.false_escalation_rate:>9.1%} "
            f"{r.missed_escalation_rate:>8.1%} "
            f"{r.cost_proxy:>6.2f}x"
        )
    return "\n".join(rows)
