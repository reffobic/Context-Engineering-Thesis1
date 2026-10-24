"""Unit tests for GASR ladder control flow + expand_trace diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from diamond.gasr_ladder import run_gasr_ladder  # noqa: E402


def test_expand_trace_includes_neighbor_diagnostics():
    """STRUCTURE hop must record neighbor_ids, edge_counts, duration_ms."""
    pruned = [{"text": "seed", "path": "a.py", "symbol": "f"}]
    seeds = [
        {
            "code": "def f(): pass",
            "metadata": {"file": "a.py", "function": "f", "chunk_id": "a.py:f"},
        }
    ]
    neighbor = {
        "code": "def g(): pass",
        "metadata": {"file": "b.py", "function": "g", "chunk_id": "b.py:g"},
        "source": "graph",
    }

    def assess_rag():
        return {"sufficient": False, "confidence": "low", "reason": "need more", "route": "LC"}

    def assess_structure():
        return {"need": "STRUCTURE", "confidence": "high", "reason": "need callees"}

    def expand_hop(expand_seeds, hops):
        assert hops == 1
        assert expand_seeds
        return {
            "chunks": [neighbor],
            "new_chunk_count": 1,
            "neighbor_ids": [f"n{i}" for i in range(25)],  # > cap of 20
            "seed_keys": ["a.py:f"],
            "graph_backend": "graphify",
            "edge_counts": {"neighbors": 25},
            "duration_ms": 42,
        }

    def re_prune(seed_pool, neighbors):
        return pruned + neighbors

    def assess_after_expand():
        return {"sufficient": True, "confidence": "high", "reason": "ok", "route": "RAG"}

    def build_lc():
        pytest.fail("should not fall through to LC when expand returns chunks")

    result = run_gasr_ladder(
        seed_pool=seeds,
        initial_pruned=pruned,
        assess_rag=assess_rag,
        assess_structure=assess_structure,
        expand_hop=expand_hop,
        re_prune=re_prune,
        assess_after_expand=assess_after_expand,
        build_lc=build_lc,
        hard_cap=2,
    )

    assert result.route == "GRAPH"
    assert result.hops_used == 1
    assert len(result.expand_trace) == 1
    exp = result.expand_trace[0]
    assert exp["new_chunk_count"] == 1
    assert exp["duration_ms"] == 42
    assert exp["edge_counts"] == {"neighbors": 25}
    assert exp["neighbor_ids_total"] == 25
    assert len(exp["neighbor_ids"]) == 20  # capped
    assert exp["graph_backend"] == "graphify"
    assert exp["seed_keys"] == ["a.py:f"]


def test_zero_new_chunks_records_diagnostics_then_lc():
    pruned = [{"text": "seed", "path": "a.py", "symbol": "f"}]

    result = run_gasr_ladder(
        seed_pool=pruned,
        initial_pruned=pruned,
        assess_rag=lambda: {
            "sufficient": False,
            "confidence": "low",
            "reason": "miss",
            "route": "LC",
        },
        assess_structure=lambda: {
            "need": "STRUCTURE",
            "confidence": "low",
            "reason": "graph",
        },
        expand_hop=lambda seeds, hops: {
            "chunks": [],
            "new_chunk_count": 0,
            "neighbor_ids": [],
            "seed_keys": ["a.py:f"],
            "graph_backend": "graphify",
            "edge_counts": {"neighbors": 0},
            "duration_ms": 7,
        },
        re_prune=lambda s, n: pruned,
        assess_after_expand=lambda: pytest.fail("no post-expand assess on empty expand"),
        build_lc=lambda: [{"text": "lc"}],
        hard_cap=2,
    )

    assert result.route == "LC"
    assert result.hops_used == 1
    exp = result.expand_trace[0]
    assert exp["new_chunk_count"] == 0
    assert exp["duration_ms"] == 7
    assert exp["neighbor_ids_total"] == 0
    assert exp["edge_counts"] == {"neighbors": 0}


def test_expand_then_insufficient_at_cap_stays_graph_not_lc():
    """After successful expand, hop-cap without sufficient → GRAPH (not full LC)."""
    pruned = [{"text": "seed", "path": "a.py", "symbol": "f"}]
    neighbor = {
        "code": "def g(): pass",
        "metadata": {"file": "b.py", "name": "g", "chunk_id": "b.py:g"},
        "source": "graph",
    }
    hops = {"n": 0}

    def expand_hop(seeds, h):
        hops["n"] += 1
        return {
            "chunks": [neighbor],
            "new_chunk_count": 1,
            "neighbor_ids": ["b.py:g"],
            "seed_keys": ["a.py:f"],
            "graph_backend": "graphify",
            "edge_counts": {"neighbors": 1},
            "duration_ms": 1,
        }

    result = run_gasr_ladder(
        seed_pool=[{"metadata": {"file": "a.py", "function": "f"}}],
        initial_pruned=pruned,
        assess_rag=lambda: {
            "sufficient": False,
            "confidence": "low",
            "reason": "miss",
            "route": "LC",
        },
        assess_structure=lambda: {
            "need": "STRUCTURE",
            "confidence": "high",
            "reason": "need callees",
        },
        expand_hop=expand_hop,
        re_prune=lambda s, n: pruned + n,
        assess_after_expand=lambda: {
            "sufficient": False,
            "confidence": "low",
            "reason": "still missing",
            "route": "LC",
        },
        build_lc=lambda: pytest.fail("must not LC after neighbors were added"),
        hard_cap=1,
    )

    assert result.route == "GRAPH"
    assert result.hops_used == 1
    assert hops["n"] == 1
    assert len(result.context) >= 2


def test_context_need_without_neighbors_goes_lc():
    pruned = [{"text": "seed", "path": "a.py", "symbol": "f"}]

    result = run_gasr_ladder(
        seed_pool=pruned,
        initial_pruned=pruned,
        assess_rag=lambda: {
            "sufficient": False,
            "confidence": "high",
            "reason": "need more lines",
            "route": "LC",
        },
        assess_structure=lambda: {
            "need": "CONTEXT",
            "confidence": "high",
            "reason": "same-file surrounding lines",
        },
        expand_hop=lambda seeds, hops: pytest.fail("CONTEXT must not expand"),
        re_prune=lambda s, n: pruned,
        assess_after_expand=lambda: pytest.fail("no graph assess"),
        build_lc=lambda: [{"text": "parent"}],
        hard_cap=2,
    )

    assert result.route == "LC"
    assert result.hops_used == 0
    assert result.context == [{"text": "parent"}]
    assert result.expand_trace == []


def test_missing_graph_falls_through_to_lc_with_diagnostics():
    """Missing graph.json: empty expand is recorded, then ladder falls to LC."""
    pruned = [{"text": "seed", "path": "a.py", "symbol": "f"}]

    result = run_gasr_ladder(
        seed_pool=pruned,
        initial_pruned=pruned,
        assess_rag=lambda: {
            "sufficient": False,
            "confidence": "low",
            "reason": "miss",
            "route": "LC",
        },
        assess_structure=lambda: {
            "need": "STRUCTURE",
            "confidence": "high",
            "reason": "need callees",
        },
        expand_hop=lambda seeds, hops: {
            "chunks": [],
            "new_chunk_count": 0,
            "neighbor_ids": [],
            "seed_keys": [],
            "graph_backend": "empty",
            "edge_counts": {},
            "duration_ms": 0,
        },
        re_prune=lambda s, n: pruned,
        assess_after_expand=lambda: pytest.fail("no post-expand assess on empty expand"),
        build_lc=lambda: [{"text": "lc"}],
        hard_cap=2,
    )

    assert result.route == "LC"
    assert result.expand_trace[0]["new_chunk_count"] == 0
    assert result.expand_trace[0]["graph_backend"] == "empty"


# ── Merged-gate path (assess_rag_gate) — current main behaviour ──────────────


def _gate_kwargs(gate, expand_hop, *, pruned, hard_cap=2, after=None, build_lc=None):
    return dict(
        seed_pool=pruned,
        initial_pruned=pruned,
        assess_rag_gate=gate,
        expand_hop=expand_hop,
        re_prune=lambda s, n: pruned + n,
        assess_after_expand=after or (lambda: {"sufficient": True}),
        build_lc=build_lc or (lambda: [{"text": "lc"}]),
        hard_cap=hard_cap,
    )


def test_gate_sufficient_returns_rag_without_expand():
    pruned = [{"text": "seed"}]
    result = run_gasr_ladder(
        **_gate_kwargs(
            lambda: {"sufficient": True, "confidence": "high"},
            lambda s, h: pytest.fail("must not expand when sufficient"),
            pruned=pruned,
            build_lc=lambda: pytest.fail("must not build LC when sufficient"),
        )
    )
    assert result.route == "RAG"
    assert result.hops_used == 0
    assert result.assess_trace[0]["phase"] == "rag_gate"
    assert result.assess_trace[0]["need"] is None


def test_gate_structure_expands_and_records_trace():
    pruned = [{"text": "seed"}]
    neighbor = {"code": "def g(): pass", "metadata": {"file": "b.py"}, "source": "graph"}
    result = run_gasr_ladder(
        **_gate_kwargs(
            lambda: {"sufficient": False, "need": "STRUCTURE", "confidence": "high"},
            lambda s, h: {"chunks": [neighbor], "new_chunk_count": 1, "graph_backend": "graphify"},
            pruned=pruned,
            build_lc=lambda: pytest.fail("must not LC after neighbors were added"),
        )
    )
    assert result.route == "GRAPH"
    assert result.hops_used == 1
    assert result.assess_trace[0]["can_expand"] is True
    assert result.assess_trace[0]["need"] == "STRUCTURE"


def test_gate_context_need_goes_lc_without_expand():
    result = run_gasr_ladder(
        **_gate_kwargs(
            lambda: {"sufficient": False, "need": "CONTEXT"},
            lambda s, h: pytest.fail("CONTEXT must not expand"),
            pruned=[{"text": "seed"}],
        )
    )
    assert result.route == "LC"
    assert result.hops_used == 0
    assert result.assess_trace[0]["can_expand"] is False


def test_gate_unknown_need_is_normalised_to_unclear_and_goes_lc():
    result = run_gasr_ladder(
        **_gate_kwargs(
            lambda: {"sufficient": False, "need": "banana"},
            lambda s, h: pytest.fail("unknown need must not expand"),
            pruned=[{"text": "seed"}],
        )
    )
    assert result.route == "LC"
    assert result.assess_trace[0]["need"] == "UNCLEAR"


def test_gate_hard_cap_zero_never_expands():
    result = run_gasr_ladder(
        **_gate_kwargs(
            lambda: {"sufficient": False, "need": "STRUCTURE"},
            lambda s, h: pytest.fail("hard_cap=0 must not expand"),
            pruned=[{"text": "seed"}],
            hard_cap=0,
        )
    )
    assert result.route == "LC"
    assert result.assess_trace[0]["can_expand"] is False


def test_gate_structure_need_is_sticky_across_hops():
    """Gate is asked once; later hops must not re-ask STRUCTURE vs CONTEXT."""
    calls = {"gate": 0, "expand": 0}

    def gate():
        calls["gate"] += 1
        return {"sufficient": False, "need": "STRUCTURE"}

    def expand(seeds, h):
        calls["expand"] += 1
        return {"chunks": [{"code": f"n{calls['expand']}"}], "new_chunk_count": 1}

    result = run_gasr_ladder(
        **_gate_kwargs(
            gate,
            expand,
            pruned=[{"text": "seed"}],
            after=lambda: {"sufficient": False},
            hard_cap=2,
        )
    )
    assert calls["gate"] == 1
    assert calls["expand"] == 2
    assert result.route == "GRAPH"
    assert result.hops_used == 2


def test_missing_graph_via_gate_falls_to_lc_with_diagnostics():
    result = run_gasr_ladder(
        **_gate_kwargs(
            lambda: {"sufficient": False, "need": "STRUCTURE"},
            lambda s, h: {"chunks": [], "new_chunk_count": 0, "graph_backend": "empty"},
            pruned=[{"text": "seed"}],
            after=lambda: pytest.fail("no post-expand assess on empty expand"),
        )
    )
    assert result.route == "LC"
    assert result.expand_trace[0]["new_chunk_count"] == 0
    assert result.expand_trace[0]["graph_backend"] == "empty"


def test_ladder_requires_gate_or_both_legacy_callbacks():
    with pytest.raises(TypeError):
        run_gasr_ladder(
            seed_pool=[],
            initial_pruned=[],
            expand_hop=lambda s, h: {},
            re_prune=lambda s, n: [],
            assess_after_expand=lambda: {},
            build_lc=lambda: [],
        )
