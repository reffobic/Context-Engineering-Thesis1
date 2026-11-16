"""Tests for the GASR gate ablation switches (ABL_GASR_GATE_OFF, ABL_GASR_GATE_NO_SHORTCUT).

Offline: the Ollama call is replaced, nothing touches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from diamond import sufficiency_assessor as sa  # noqa: E402
from diamond.gasr_ladder import run_gasr_ladder  # noqa: E402


class _Resp:
    def __init__(self, content: str):
        self._c = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._c}, "prompt_eval_count": 7, "eval_count": 3}


def _chunks(score: float):
    return [{"text": "def f(): pass", "path": "a.py", "symbol": "f", "final_score": score}]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ABL_GASR_GATE_OFF", "ABL_GASR_GATE_NO_SHORTCUT"):
        monkeypatch.delenv(k, raising=False)


def _no_call(*a, **k):  # any LLM call in these tests is a failure
    raise AssertionError("gate made an LLM call")


def test_default_low_score_still_takes_shortcut(monkeypatch):
    monkeypatch.setattr(sa.requests, "post", _no_call)
    out = sa.assess_rag_gate("q", _chunks(0.10))
    assert out["sufficient"] is False and out["need"] == "CONTEXT"


def test_gate_off_forces_structure_without_llm_call(monkeypatch):
    monkeypatch.setenv("ABL_GASR_GATE_OFF", "1")
    monkeypatch.setattr(sa.requests, "post", _no_call)
    for score in (0.05, 0.9):
        out = sa.assess_rag_gate("q", _chunks(score))
        assert out["sufficient"] is False
        assert out["need"] == "STRUCTURE"
        assert out["token_usage"] == {"prompt_tokens": 0, "completion_tokens": 0}


def test_gate_off_keeps_empty_retrieval_on_lc(monkeypatch):
    monkeypatch.setenv("ABL_GASR_GATE_OFF", "1")
    monkeypatch.setattr(sa.requests, "post", _no_call)
    assert sa.assess_rag_gate("q", [])["need"] == "CONTEXT"


def test_no_shortcut_sends_low_score_to_the_llm(monkeypatch):
    monkeypatch.setenv("ABL_GASR_GATE_NO_SHORTCUT", "1")
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _Resp('{"sufficient": true, "confidence": "high", "reason": "ok"}')

    monkeypatch.setattr(sa.requests, "post", fake_post)
    out = sa.assess_rag_gate("q", _chunks(0.10))
    assert calls == [1]
    assert out["sufficient"] is True


def test_gate_off_makes_the_ladder_expand():
    gate = lambda: sa.assess_rag_gate("q", _chunks(0.9))  # noqa: E731
    import os

    os.environ["ABL_GASR_GATE_OFF"] = "1"
    try:
        expanded = []

        def expand(seeds, hop):
            expanded.append(hop)
            return {
                "chunks": [{"text": "n", "path": "b.py"}],
                "new_chunk_count": 1,
                "neighbor_ids": ["b"],
                "duration_ms": 0,
            }

        res = run_gasr_ladder(
            seed_pool=[{"text": "s", "path": "a.py"}],
            initial_pruned=[{"text": "s", "path": "a.py"}],
            expand_hop=expand,
            re_prune=lambda seeds, neigh: list(seeds) + list(neigh),
            assess_after_expand=lambda: {"sufficient": True, "confidence": "high", "reason": "ok"},
            build_lc=lambda: [],
            hard_cap=2,
            assess_rag_gate=gate,
        )
    finally:
        os.environ.pop("ABL_GASR_GATE_OFF", None)
    assert expanded, "gate-off must lead to at least one expansion hop"
    assert res.route == "GRAPH"
