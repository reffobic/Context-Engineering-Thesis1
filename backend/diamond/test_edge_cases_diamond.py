"""
Diamond (sufficiency assessor) test suite.

Tests all decision paths and sweeps thresholds.
Outputs results as a formatted table.

Usage:
    cd backend
    python diamond/test_diamond.py                        # mock mode (no Ollama needed)
    python diamond/test_diamond.py --live                 # real Ollama calls
    python diamond/test_diamond.py --live --model qwen3.5:4b
"""

from __future__ import annotations

import argparse
import sys
import unittest.mock as mock
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, ".")

import diamond.sufficiency_assessor as sa


# ── test cases ────────────────────────────────────────────────────────────────

@dataclass
class Case:
    name:        str
    query:       str
    chunks:      list[dict[str, Any]]
    expected:    str          # "RAG" | "LC" | "UNANSWERABLE" | "ANY"
    description: str


CASES: list[Case] = [
    # ── path: empty chunks ────────────────────────────────────────────────────
    Case(
        name="empty_chunks",
        query="How does Superset handle authentication?",
        chunks=[],
        expected="LC",
        description="No chunks retrieved → should route LC without LLM call",
    ),

    # ── path: score shortcut (below threshold) ────────────────────────────────
    Case(
        name="low_score_shortcut",
        query="How does Superset handle authentication?",
        chunks=[{
            "text":       "import os\nfrom flask import Flask",
            "path":       "superset/app.py",
            "symbol":     "",
            "language":   "python",
            "final_score": 0.10,
        }],
        expected="LC",
        description="Top score 0.10 < threshold → LC without LLM call",
    ),

    # ── path: score just below threshold ─────────────────────────────────────
    Case(
        name="score_just_below_threshold",
        query="How does Superset handle authentication?",
        chunks=[{
            "text":       "def authenticate(self, user, password): ...",
            "path":       "superset/security/manager.py",
            "symbol":     "authenticate",
            "language":   "python",
            "final_score": 0.29,
        }],
        expected="LC",
        description="Score 0.29, just below default threshold 0.30 → LC shortcut",
    ),

    # ── path: score just above threshold → goes to LLM ───────────────────────
    Case(
        name="score_just_above_threshold",
        query="How does Superset handle authentication?",
        chunks=[{
            "text":       "def authenticate(self, user, password): ...",
            "path":       "superset/security/manager.py",
            "symbol":     "authenticate",
            "language":   "python",
            "final_score": 0.31,
        }],
        expected="ANY",   # LLM decides; we just check it doesn't crash
        description="Score 0.31, above threshold → LLM judge called",
    ),

    # ── path: clearly sufficient chunks ──────────────────────────────────────
    Case(
        name="clearly_sufficient",
        query="What class handles security and permission checks in Superset?",
        chunks=[{
            "text": (
                "class SupersetSecurityManager(SecurityManager):\n"
                "    \"\"\"\n"
                "    Handles all security and permission checks for Superset.\n"
                "    \"\"\"\n"
                "    def can_access(self, permission, view_name):\n"
                "        return permission in self.get_user_permissions(view_name)\n"
                "    def has_access(self, permission, view_name):\n"
                "        return self.can_access(permission, view_name)"
            ),
            "path":       "superset/security/manager.py",
            "symbol":     "SupersetSecurityManager",
            "language":   "python",
            "final_score": 0.72,
        }],
        expected="RAG",
        description="Chunks directly contain the answer → should route RAG",
    ),

    # ── path: clearly insufficient chunks ────────────────────────────────────
    Case(
        name="clearly_insufficient",
        query="How does Superset warm up the cache for charts?",
        chunks=[{
            "text":       "import logging\nlogger = logging.getLogger(__name__)",
            "path":       "superset/utils/log.py",
            "symbol":     "",
            "language":   "python",
            "final_score": 0.35,
        }],
        expected="LC",
        description="Chunks are unrelated to the query → should route LC",
    ),

    # ── path: unanswerable (out of scope) ────────────────────────────────────
    Case(
        name="out_of_scope",
        query="What is the capital of France?",
        chunks=[{
            "text":       "class SupersetSecurityManager: ...",
            "path":       "superset/security/manager.py",
            "symbol":     "SupersetSecurityManager",
            "language":   "python",
            "final_score": 0.38,
        }],
        expected="UNANSWERABLE",
        description="Query has nothing to do with the codebase → UNANSWERABLE",
    ),

    # ── path: partial answer (ambiguous) ─────────────────────────────────────
    Case(
        name="partial_answer",
        query="How does Superset check CSV download permissions?",
        chunks=[{
            "text": (
                "def can_access(self, permission, view_name):\n"
                "    \"\"\"Check if current user can access the permission.\"\"\"\n"
                "    if not self.is_item_public(permission, view_name):\n"
                "        return self._has_view_access(permission, view_name)\n"
                "    return True"
            ),
            "path":       "superset/security/manager.py",
            "symbol":     "can_access",
            "language":   "python",
            "final_score": 0.48,
        }],
        expected="ANY",
        description="Related but not the exact check → ambiguous, judge decides",
    ),

    # ── stronger UNANSWERABLE cases ───────────────────────────────────────────────
    Case(
        name="nonexistent_feature",
        query="How does Superset handle real-time streaming data ingestion?",
        chunks=[{
            "text": "class DatabaseView(ModelRestApi):\n    datamodel = SQLAInterface(Database)",
            "path": "superset/databases/api.py",
            "symbol": "DatabaseView",
            "language": "python",
            "final_score": 0.38,
        }],
        expected="UNANSWERABLE",
        description="Feature doesn't exist in Superset — should be UNANSWERABLE not LC",
    ),

    Case(
        name="wrong_codebase",
        query="How does Django's ORM handle database migrations?",
        chunks=[{
            "text": "from flask_sqlalchemy import SQLAlchemy\ndb = SQLAlchemy()",
            "path": "superset/extensions.py",
            "symbol": "",
            "language": "python",
            "final_score": 0.41,
        }],
        expected="UNANSWERABLE",
        description="Asks about Django in a Flask/Superset codebase — UNANSWERABLE",
    ),
]


# ── mock responses for each case ──────────────────────────────────────────────

MOCK_RESPONSES: dict[str, str] = {
    "score_just_above_threshold": '{"route": "LC", "confidence": "medium", "reason": "Limited context available."}',
    "clearly_sufficient":         '{"route": "RAG", "confidence": "high", "reason": "Chunk directly defines SupersetSecurityManager and its permission methods."}',
    "clearly_insufficient":       '{"route": "LC", "confidence": "high", "reason": "Chunks contain only logging imports, unrelated to cache warmup."}',
    "out_of_scope":               '{"route": "UNANSWERABLE", "confidence": "high", "reason": "Query asks about geography, unrelated to the codebase."}',
    "partial_answer":             '{"route": "LC", "confidence": "medium", "reason": "General permission check found but specific CSV download check missing."}',
    "nonexistent_feature": '{"route": "UNANSWERABLE", "confidence": "high", "reason": "Superset does not implement real-time streaming ingestion."}',
    "wrong_codebase":      '{"route": "UNANSWERABLE", "confidence": "high", "reason": "Query asks about Django ORM but this is a Flask/SQLAlchemy codebase."}',
}


# ── _parse_response unit tests ────────────────────────────────────────────────

PARSE_CASES = [
    ('{"route": "RAG", "confidence": "high", "reason": "sufficient"}',
     "RAG", "clean JSON with route field"),
    ('{"sufficient": true, "confidence": "high", "reason": "sufficient"}',
     "RAG", "backwards compat: sufficient=true"),
    ('{"sufficient": false, "confidence": "high", "reason": "missing"}',
     "LC", "backwards compat: sufficient=false"),
    ('```json\n{"route": "LC", "confidence": "medium", "reason": "need more"}\n```',
     "LC", "fenced JSON"),
    ('<think>I need to reason</think>\n{"route": "RAG", "confidence": "high", "reason": "yes"}',
     "RAG", "think-tag stripped"),
    ('{"route": "UNANSWERABLE", "confidence": "high", "reason": "out of scope"}',
     "UNANSWERABLE", "unanswerable route"),
    ('{"route": "invalid_value", "confidence": "high", "reason": "bad"}',
     "LC", "invalid route defaults to LC"),
]


# ── threshold sweep ───────────────────────────────────────────────────────────

THRESHOLDS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]

SWEEP_QUERY = "What class handles security and permission checks in Superset?"

SWEEP_CHUNKS = [
    {"name": "score_0.15", "final_score": 0.15,
     "text": "class SupersetSecurityManager: handles all permission checks",
     "path": "superset/security/manager.py", "symbol": "SupersetSecurityManager", "language": "python"},
    {"name": "score_0.25", "final_score": 0.25,
     "text": "class SupersetSecurityManager: handles all permission checks",
     "path": "superset/security/manager.py", "symbol": "SupersetSecurityManager", "language": "python"},
    {"name": "score_0.35", "final_score": 0.35,
     "text": "class SupersetSecurityManager: handles all permission checks",
     "path": "superset/security/manager.py", "symbol": "SupersetSecurityManager", "language": "python"},
    {"name": "score_0.45", "final_score": 0.45,
     "text": "class SupersetSecurityManager: handles all permission checks",
     "path": "superset/security/manager.py", "symbol": "SupersetSecurityManager", "language": "python"},
    {"name": "score_0.55", "final_score": 0.55,
     "text": "class SupersetSecurityManager: handles all permission checks",
     "path": "superset/security/manager.py", "symbol": "SupersetSecurityManager", "language": "python"},
]


# ── table helpers ─────────────────────────────────────────────────────────────

def _row(cols: list[str], widths: list[int]) -> str:
    return "│ " + " │ ".join(str(c).ljust(w) for c, w in zip(cols, widths)) + " │"


def _sep(widths: list[int], left="├", mid="┼", right="┤") -> str:
    return left + mid.join("─" * (w + 2) for w in widths) + right


def _header(cols: list[str], widths: list[int]) -> str:
    top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    hdr = _row(cols, widths)
    div = _sep(widths)
    return f"{top}\n{hdr}\n{div}"


def _footer(widths: list[int]) -> str:
    return "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"


def _pass_fail(expected: str, actual: str) -> str:
    if expected == "ANY":
        return "✓ ANY"
    return "✓ PASS" if actual == expected else f"✗ FAIL(got {actual})"


# ── runners ───────────────────────────────────────────────────────────────────

def run_parse_tests() -> None:
    print("\n━━━ _parse_response unit tests ━━━")
    cols = ["Input description", "Expected", "Got", "Result"]
    widths = [40, 12, 12, 12]
    print(_header(cols, widths))
    for raw, expected_route, desc in PARSE_CASES:
        try:
            result = sa._parse_response(raw)
            got = result["route"]
            status = "✓ PASS" if got == expected_route else f"✗ FAIL"
        except Exception as e:
            got = "ERROR"
            status = f"✗ {e}"
        print(_row([desc, expected_route, got, status], widths))
    print(_footer(widths))


def run_case_tests(live: bool, model: str) -> None:
    print(f"\n━━━ Decision path tests ({'LIVE Ollama' if live else 'MOCK'}) ━━━")
    cols = ["Case", "Expected", "Got", "Confidence", "LLM called", "Result"]
    widths = [28, 12, 12, 10, 10, 16]
    print(_header(cols, widths))

    for case in CASES:
        original_threshold = sa.SCORE_SHORTCUT_THRESHOLD

        if not live:
            mock_json = MOCK_RESPONSES.get(case.name, '{"route": "LC", "confidence": "low", "reason": "mock"}')

            def make_resp(j=mock_json):
                m = mock.MagicMock()
                m.json.return_value = {"message": {"content": j}}
                return m

            with mock.patch("diamond.sufficiency_assessor.requests.post",
                            return_value=make_resp()) as mp:
                result = sa.assess_sufficiency(case.query, case.chunks, model=model)
                llm_called = "yes" if mp.called else "no"
        else:
            call_count = {"n": 0}
            original_post = sa.requests.post

            def counting_post(*a, **kw):
                call_count["n"] += 1
                return original_post(*a, **kw)

            with mock.patch("diamond.sufficiency_assessor.requests.post",
                            side_effect=counting_post):
                result = sa.assess_sufficiency(case.query, case.chunks, model=model)
            llm_called = "yes" if call_count["n"] > 0 else "no"

        status = _pass_fail(case.expected, result["route"])
        print(_row([
            case.name,
            case.expected,
            result["route"],
            result["confidence"],
            llm_called,
            status,
        ], widths))

        sa.SCORE_SHORTCUT_THRESHOLD = original_threshold

    print(_footer(widths))


def run_threshold_sweep(live: bool, model: str) -> None:
    print("\n━━━ Threshold sweep (does score shortcut fire?) ━━━")
    header_cols = ["Chunk score"] + [f"t={t}" for t in THRESHOLDS]
    widths = [12] + [10] * len(THRESHOLDS)
    print(_header(header_cols, widths))

    mock_json = '{"route": "RAG", "confidence": "high", "reason": "mock sufficient"}'

    def make_resp():
        m = mock.MagicMock()
        m.json.return_value = {"message": {"content": mock_json}}
        return m

    for chunk_meta in SWEEP_CHUNKS:
        chunk = {k: v for k, v in chunk_meta.items() if k != "name"}
        row_vals = [chunk_meta["name"]]

        for threshold in THRESHOLDS:
            sa.SCORE_SHORTCUT_THRESHOLD = threshold
            if not live:
                with mock.patch("diamond.sufficiency_assessor.requests.post",
                                return_value=make_resp()) as mp:
                    result = sa.assess_sufficiency("test query", [chunk], model=model)
                    shortcut = "no" if mp.called else "yes"
            else:
                call_count = {"n": 0}
                original_post = sa.requests.post

                def counting_post(*a, **kw):
                    call_count["n"] += 1
                    return original_post(*a, **kw)

                with mock.patch("diamond.sufficiency_assessor.requests.post",
                                side_effect=counting_post):
                    result = sa.assess_sufficiency("test query", [chunk], model=model)
                shortcut = "no" if call_count["n"] > 0 else "yes"

            cell = f"{result['route']}(sc={shortcut})"
            row_vals.append(cell)

        print(_row(row_vals, widths))

    sa.SCORE_SHORTCUT_THRESHOLD = 0.30  # reset
    print(_footer(widths))
    print("  sc=yes → score shortcut fired (no LLM call)")
    print("  sc=no  → LLM judge was called")


def run_model_comparison(models: list[str], query: str, chunks: list[dict]) -> None:
    print(f"\n━━━ Model comparison ━━━")
    print(f"  Query: {query[:80]}")
    cols = ["Model", "Route", "Confidence", "Reason"]
    widths = [20, 12, 10, 50]
    print(_header(cols, widths))
    for model in models:
        try:
            result = sa.assess_sufficiency(query, chunks, model=model)
            print(_row([model, result["route"], result["confidence"],
                        result["reason"][:50]], widths))
        except Exception as e:
            print(_row([model, "ERROR", "", str(e)[:50]], widths))
    print(_footer(widths))


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="Use real Ollama instead of mocks")
    parser.add_argument("--model", default=sa.DEFAULT_MODEL,
                        help="Ollama model tag for live mode")
    parser.add_argument("--compare-models", action="store_true",
                        help="Run model comparison (live only)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Diamond Test Suite")
    print(f"  Mode:  {'LIVE' if args.live else 'MOCK (no Ollama needed)'}")
    print(f"  Model: {args.model}")
    print(f"  Threshold: {sa.SCORE_SHORTCUT_THRESHOLD}")
    print(f"{'='*60}")

    run_parse_tests()
    run_case_tests(live=args.live, model=args.model)
    run_threshold_sweep(live=args.live, model=args.model)

    if args.compare_models and args.live:
        run_model_comparison(
            models=["llama3.2:3b", "qwen3.5:4b", "qwen3.5:9b"],
            query="What class handles security and permission checks in Superset?",
            chunks=CASES[4].chunks,  # clearly_sufficient
        )

    print("\nDone.\n")


if __name__ == "__main__":
    main()