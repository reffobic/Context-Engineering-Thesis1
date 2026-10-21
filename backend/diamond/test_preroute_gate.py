"""Unit tests for the GAPA gate. No live Ollama — mirrors the fake-
callback pattern in test_gasr_ladder.py so this runs the same way theirs
does, and can sit in the same CI job."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from diamond.preroute_gate import (  # noqa: E402
    _stage1_query_only,
    _stage2_rules,
    preroute_gate,
)


# ── Stage 1 ──────────────────────────────────────────────────────────────

def test_stage1_catches_whole_codebase_query():
    decision = _stage1_query_only("What changes across the entire codebase when config reloads?")
    assert decision is not None
    assert decision["route"] == "LC"
    assert decision["stage"] == "preroute_stage1"


def test_stage1_catches_short_local_query():
    decision = _stage1_query_only("What does create_app return?")
    assert decision is not None
    assert decision["route"] == "RAG"


def test_stage1_defers_on_ambiguous_query():
    """A query with no stage-1 keyword should fall through to stage 2, not guess."""
    decision = _stage1_query_only("How does the caching layer interact with session state?")
    assert decision is None


# ── Stage 2 ──────────────────────────────────────────────────────────────

def _chunk(score, file="a.py", dependencies=None, function="f"):
    return {
        "final_score": score,
        "metadata": {
            "file": file,
            "function": function,
            "chunk_type": "function",
            "dependencies": dependencies or [],
        },
    }


def test_stage2_low_score_escalates_to_lc():
    chunks = [_chunk(0.12)]
    decision = _stage2_rules("q", chunks)
    assert decision["sufficient"] is False
    assert decision["route"] == "LC"


def test_stage2_high_score_single_file_few_deps_stays_rag():
    chunks = [_chunk(0.80, dependencies=["helper"]), _chunk(0.40)]
    decision = _stage2_rules("q", chunks)
    assert decision["sufficient"] is True
    assert decision["route"] == "RAG"


def test_stage2_many_dependencies_escalates():
    chunks = [_chunk(0.60, dependencies=["a", "b", "c", "d"])]
    decision = _stage2_rules("q", chunks)
    assert decision["sufficient"] is False
    assert decision["route"] == "LC"


def test_stage2_scattered_across_files_escalates():
    chunks = [_chunk(0.60, file="a.py"), _chunk(0.55, file="b.py"), _chunk(0.50, file="c.py")]
    decision = _stage2_rules("q", chunks)
    assert decision["sufficient"] is False
    assert decision["route"] == "LC"


def test_stage2_no_chunks_escalates():
    decision = _stage2_rules("q", [])
    assert decision["route"] == "LC"
    assert decision["confidence"] == "high"


def test_stage2_ambiguous_signals_defer():
    """Mid-range score, no dependency/file signal strong enough — must
    signal defer (sufficient is None), not guess a route."""
    chunks = [_chunk(0.45, dependencies=["helper"])]
    decision = _stage2_rules("q", chunks)
    assert decision["sufficient"] is None
    assert decision["route"] is None


def test_stage2_dependency_parsing_handles_json_string():
    """CodeChunk.to_metadata() JSON-serializes list fields before storage —
    the gate must handle that shape, not just a raw python list."""
    chunks = [
        {
            "final_score": 0.80,
            "metadata": {"file": "a.py", "dependencies": '["x", "y"]'},
        }
    ]
    decision = _stage2_rules("q", chunks)
    # 2 dependencies > STAGE2_MAX_DEPENDENCIES_FOR_RAG(1) but < ESCALATE(3):
    # should defer, not silently treat the JSON string as zero dependencies.
    assert decision["sufficient"] is None


# ── public entry point / DEFER wiring ─────────────────────────────────────

def test_preroute_gate_defers_to_fallback_when_ambiguous():
    fallback_called = {"n": 0}

    def fallback_assess():
        fallback_called["n"] += 1
        return {"sufficient": True, "confidence": "high", "reason": "llm says so", "route": "RAG"}

    chunks = [_chunk(0.45, dependencies=["helper"])]  # ambiguous, per above
    decision = preroute_gate("how does the caching layer interact with session state?", chunks, fallback_assess)

    assert fallback_called["n"] == 1
    assert decision["route"] == "RAG"
    assert decision["stage"] == "preroute_defer_llm"


def test_preroute_gate_does_not_call_fallback_when_stage1_resolves():
    def fallback_assess():
        pytest.fail("fallback_assess should not be called when stage 1 is confident")

    decision = preroute_gate("What does create_app return?", [], fallback_assess)
    assert decision["route"] == "RAG"
    assert decision["stage"] == "preroute_stage1"


def test_preroute_gate_does_not_call_fallback_when_stage2_resolves():
    def fallback_assess():
        pytest.fail("fallback_assess should not be called when stage 2 is confident")

    chunks = [_chunk(0.12)]  # below score floor
    decision = preroute_gate("ambiguous phrasing with no stage1 keyword match", chunks, fallback_assess)
    assert decision["route"] == "LC"
    assert decision["stage"] == "preroute_stage2"


# ── Stage 2, generative variant ───────────────────────────────────────────

from unittest.mock import patch, MagicMock
from diamond.preroute_gate import (  # noqa: E402
    _stage2_generative,
    _parse_generative_response,
    preroute_gate_generative,
)


def _mock_ollama_response(sufficient: bool, confidence="medium", reason="test reason"):
    body = json.dumps({"sufficient": sufficient, "confidence": confidence, "reason": reason})
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": body}}
    return mock_resp


import json  # noqa: E402


def test_parse_generative_response_plain_json():
    raw = '{"sufficient": true, "confidence": "high", "reason": "concentrated"}'
    result = _parse_generative_response(raw)
    assert result["sufficient"] is True
    assert result["route"] == "RAG"


def test_parse_generative_response_wrapped_in_prose():
    """Model sometimes ignores 'reply ONLY with JSON' — must still parse."""
    raw = 'Sure, here is my answer:\n{"sufficient": false, "confidence": "low", "reason": "scattered"}\nHope that helps!'
    result = _parse_generative_response(raw)
    assert result["sufficient"] is False
    assert result["route"] == "LC"


def test_parse_generative_response_no_json_raises():
    with pytest.raises(ValueError):
        _parse_generative_response("I'm not sure, sorry.")


def test_stage2_generative_no_chunks_skips_llm_call():
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        decision = _stage2_generative("q", [])
        mock_post.assert_not_called()
    assert decision["route"] == "LC"


def test_stage2_generative_calls_ollama_and_parses_result():
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        chunks = [_chunk(0.8, dependencies=["helper"])]
        decision = _stage2_generative("what does create_app return?", chunks)

    assert mock_post.called
    called_payload = mock_post.call_args.kwargs["json"]
    assert called_payload["messages"][1]["content"].count("create_app") == 1
    # metadata-only prompt: must NOT contain raw chunk text/content
    assert "content" not in called_payload["messages"][1]["content"].lower().split("query:")[0]
    assert decision["route"] == "RAG"
    assert decision["stage"] == "preroute_stage2_generative"


def test_stage2_generative_ollama_error_defaults_to_lc():
    import requests as real_requests
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.side_effect = real_requests.RequestException("connection refused")
        decision = _stage2_generative("q", [_chunk(0.8)])
    assert decision["route"] == "LC"
    assert decision["stage"] == "preroute_stage2_generative_error"


def test_preroute_gate_generative_defers_on_ollama_error():
    fallback_called = {"n": 0}

    def fallback_assess():
        fallback_called["n"] += 1
        return {"sufficient": True, "confidence": "high", "reason": "llm says so", "route": "RAG"}

    import requests as real_requests
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.side_effect = real_requests.RequestException("timeout")
        decision = preroute_gate_generative(
            "ambiguous phrasing with no stage1 keyword match", [_chunk(0.8)], fallback_assess
        )

    assert fallback_called["n"] == 1
    assert decision["stage"] == "preroute_defer_llm_after_generative_error"


def test_preroute_gate_generative_stage1_skips_llm_entirely():
    def fallback_assess():
        pytest.fail("should not be called")

    with patch("diamond.preroute_gate._requests.post") as mock_post:
        decision = preroute_gate_generative("What does create_app return?", [], fallback_assess)
        mock_post.assert_not_called()
    assert decision["stage"] == "preroute_stage1"


# ── Single-stage structured gate ──────────────────────────────────────────

from diamond.preroute_gate import preroute_structured_gate  # noqa: E402


def test_structured_gate_no_chunks_skips_llm_call():
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        decision = preroute_structured_gate("q", [])
        mock_post.assert_not_called()
    assert decision["route"] == "LC"


def test_structured_gate_prompt_has_six_steps_and_no_chunk_text():
    chunks = [_chunk(0.8, dependencies=["a", "b"])]
    chunks[0]["metadata"]["context_header"] = "Resolves merge conflicts for working copy edits."
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        preroute_structured_gate("why does resolve_conflict fail?", chunks)

    prompt = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    for step in ("Step 1", "Step 2", "Step 3", "Step 4", "Step 5", "Step 6"):
        assert step in prompt
    assert "Resolves merge conflicts" in prompt  # context_header made it in
    # the raw chunk's placeholder "content" text must NOT appear anywhere
    assert "def f" not in prompt  # sanity: no chunk source text leaked in


def test_structured_gate_includes_sim_fields_only_when_present():
    chunks_without = [_chunk(0.8)]
    chunks_with = [dict(_chunk(0.8), sim_code=0.71, sim_meta=0.89, sim_gap=0.18)]

    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        preroute_structured_gate("q", chunks_without)
        prompt_without = mock_post.call_args.kwargs["json"]["messages"][1]["content"]

        mock_post.return_value = _mock_ollama_response(sufficient=True)
        preroute_structured_gate("q", chunks_with)
        prompt_with = mock_post.call_args.kwargs["json"]["messages"][1]["content"]

    assert "sim_code" not in prompt_without
    assert "sim_code: 0.71" in prompt_with
    assert "sim_gap: 0.18" in prompt_with


def test_structured_gate_error_defaults_to_lc():
    import requests as real_requests
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.side_effect = real_requests.RequestException("down")
        decision = preroute_structured_gate("q", [_chunk(0.8)])
    assert decision["route"] == "LC"
    assert decision["stage"] == "preroute_structured_error"


def test_structured_gate_returns_route_from_llm_verdict():
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=False, reason="scattered")
        decision = preroute_structured_gate("q", [_chunk(0.5, dependencies=["a", "b", "c"])])
    assert decision["route"] == "LC"
    assert decision["stage"] == "preroute_structured"

# ── Optional GAPA variants: compact prompt, emit_need, graph metadata gate ──

from diamond.preroute_gate import graph_metadata_gate  # noqa: E402


def _mock_need_response(sufficient: bool, need):
    body = json.dumps({"sufficient": sufficient, "need": need,
                       "confidence": "high", "reason": "r"})
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": body}}
    return mock_resp


def test_compact_prompt_drops_description_and_keeps_six_steps():
    chunks = [_chunk(0.8, dependencies=["a", "b"])]
    chunks[0]["metadata"]["context_header"] = "LONG HEADER TEXT " * 20
    chunks[0]["metadata"]["function"] = "create_app"
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        preroute_structured_gate("q", chunks, compact=True)
    prompt = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "LONG HEADER TEXT" not in prompt
    assert "symbol: create_app" in prompt
    for step in ("Step 1", "Step 2", "Step 3", "Step 4", "Step 5", "Step 6"):
        assert step in prompt


def test_compact_prompt_is_read_from_env_when_not_passed(monkeypatch):
    chunks = [_chunk(0.8)]
    chunks[0]["metadata"]["context_header"] = "LONG HEADER TEXT"
    monkeypatch.setenv("GAPA_COMPACT_PROMPT", "1")
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        preroute_structured_gate("q", chunks)
    assert "LONG HEADER TEXT" not in mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    monkeypatch.delenv("GAPA_COMPACT_PROMPT")
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        preroute_structured_gate("q", chunks)
    assert "LONG HEADER TEXT" in mock_post.call_args.kwargs["json"]["messages"][1]["content"]


def test_default_prompt_does_not_ask_for_need():
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        decision = preroute_structured_gate("q", [_chunk(0.8)])
    sent = mock_post.call_args.kwargs["json"]["messages"]
    assert '"need"' not in sent[0]["content"]
    assert "STRUCTURE" not in sent[1]["content"]
    assert decision["need"] is None


def test_emit_need_asks_for_label_and_returns_it():
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_need_response(False, "structure")
        decision = preroute_structured_gate(
            "q", [_chunk(0.5, dependencies=["a", "b", "c"])], emit_need=True)
    sent = mock_post.call_args.kwargs["json"]["messages"]
    assert '"need"' in sent[0]["content"]
    assert "STRUCTURE" in sent[1]["content"]
    assert decision["sufficient"] is False
    assert decision["need"] == "STRUCTURE"


def test_emit_need_is_none_when_sufficient_and_unclear_when_label_bad():
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_need_response(True, "STRUCTURE")
        ok = preroute_structured_gate("q", [_chunk(0.9)], emit_need=True)
        mock_post.return_value = _mock_need_response(False, "banana")
        bad = preroute_structured_gate("q", [_chunk(0.4)], emit_need=True)
    assert ok["need"] is None
    assert bad["need"] == "UNCLEAR"


def test_graph_metadata_gate_prompt_has_facts_and_no_chunk_text():
    facts = {"hops": 1, "n_seeds": 5, "n_new": 10, "n_kept": 3,
             "n_files": 2, "edge_counts": {"calls": 4}, "top_score": 0.66}
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_response(sufficient=True)
        decision = graph_metadata_gate("q", facts)
    prompt = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "neighbours_added: 10" in prompt
    assert "chunks_kept_after_prune: 3" in prompt
    assert "def " not in prompt
    assert decision["route"] == "RAG"
    assert decision["stage"] == "graph_metadata"


def test_graph_metadata_gate_error_defaults_to_not_sufficient():
    import requests as real_requests
    with patch("diamond.preroute_gate._requests.post") as mock_post:
        mock_post.side_effect = real_requests.RequestException("down")
        decision = graph_metadata_gate("q", {})
    assert decision["sufficient"] is False
    assert decision["stage"] == "graph_metadata_error"
