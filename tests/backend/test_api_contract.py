"""Tier 2 — API request-validation contract, mocked end-to-end 200 flows, and empty-DB fallback."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for p in (str(ROOT), str(BACKEND), str(BACKEND / "mcp_server")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("fastapi")
pytest.importorskip("chromadb")
pytest.importorskip("sentence_transformers")
import chromadb  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import mcp_server.server as server_mod  # noqa: E402
import retrieval.retrieval_tool as retrieval_tool  # noqa: E402

app = server_mod.app
client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Request validation contract (422s & health endpoints)
# ---------------------------------------------------------------------------


def test_health_endpoint_ok():
    r = client.get("/")
    assert r.status_code == 200
    assert "Running" in (r.json().get("message") or r.json().get("status") or "")


def test_ui_endpoint_serves_html():
    r = client.get("/ui")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_models_endpoint_graceful_when_ollama_unreachable(monkeypatch):
    import urllib.error
    import urllib.request

    def _fail_urlopen(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _fail_urlopen)
    r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == []
    assert "error" in body


def test_index_summary_endpoint_returns_200(monkeypatch):
    monkeypatch.setattr(
        server_mod,
        "get_index_summary",
        lambda *a, **k: {"chunks_indexed": 42, "collection": "code_chunks", "files_indexed": 7},
    )
    r = client.get("/index/summary")
    assert r.status_code == 200
    assert r.json()["chunks_indexed"] == 42


def test_generate_rejects_missing_prompt():
    assert client.post("/generate", json={}).status_code == 422


def test_generate_rejects_invalid_mode():
    r = client.post("/generate", json={"prompt": "hi", "mode": "NONEXISTENT"})
    assert r.status_code == 422


def test_generate_rejects_removed_preroute_mode():
    """PREROUTE was renamed to GAPA on main; the old name must no longer validate."""
    r = client.post("/generate", json={"prompt": "hi", "mode": "PREROUTE"})
    assert r.status_code == 422


def test_generate_stream_rejects_removed_preroute_mode():
    r = client.post("/generate/stream", json={"prompt": "hi", "mode": "PREROUTE"})
    assert r.status_code == 422


def test_generate_rejects_invalid_pruning_method():
    r = client.post("/generate", json={"prompt": "hi", "pruning_method": "magic"})
    assert r.status_code == 422


def test_generate_rejects_non_json_body():
    r = client.post("/generate", content="not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 2. Mocked End-to-End 200 Pipeline Flows (RAG, LC, SELF_ROUTE, GASR, GAPA)
# ---------------------------------------------------------------------------


SAMPLE_RETRIEVED = [
    {
        "code": "def authenticate(user, password):\n    return check_hash(user, password)\n",
        "metadata": {
            "file": "backend/auth.py",
            "function": "authenticate",
            "language": "python",
            "start_line": 10,
            "end_line": 12,
        },
        "similarity": 0.88,
        "hybrid_score": 0.85,
        "final_score": 0.86,
        "parent_context": "# [function] authenticate (lines 10-12)\ndef authenticate(user, password):\n    return check_hash(user, password)\n",
    }
]


@pytest.fixture
def _mock_pipeline_dependencies(monkeypatch):
    """Stub retrieval, prompt injection, and Ollama generation for deterministic 200 flows."""

    def _fake_call_tool(name: str, *args, **kwargs):
        if name == "retrieve_context":
            return [dict(item) for item in SAMPLE_RETRIEVED]
        if name == "inject_context":
            user_prompt = args[0] if args else ""
            chunks = args[1] if len(args) > 1 else []
            return {
                "system": "You are a helpful coding assistant.",
                "user": f"Prompt: {user_prompt}\nChunks: {len(chunks)}",
            }
        if name == "ollama_generate":
            return "Mocked LLM synthesis: `authenticate` verifies credentials."
        raise AssertionError(f"Unexpected tool call: {name}")

    monkeypatch.setattr(server_mod, "call_tool", _fake_call_tool)
    monkeypatch.setattr(server_mod, "reconstruct_file_context", lambda items: items)


@pytest.mark.parametrize("mode,expected_route", [("RAG", "RAG"), ("LC", "LC")])
def test_generate_200_direct_modes(mode, expected_route, _mock_pipeline_dependencies):
    r = client.post("/generate", json={"prompt": "How does authenticate work?", "mode": mode})
    assert r.status_code == 200
    data = r.json()
    assert "authenticate" in data["output"]
    pipeline = data["pipeline"]
    assert pipeline["mode"] == mode
    assert pipeline["route"] == expected_route
    assert pipeline["tokens_injected_est"] > 0
    for stage in ("retrieve", "prune", "inject", "generate"):
        assert stage in pipeline["stages"]
        assert pipeline["stages"][stage]["status"] == "complete"


def test_generate_200_self_route_escalates_to_lc(monkeypatch, _mock_pipeline_dependencies):
    monkeypatch.setattr(
        server_mod,
        "assess_sufficiency",
        lambda *a, **k: {
            "route": "LC",
            "sufficient": False,
            "confidence": "high",
            "reason": "Need full file context.",
        },
    )
    r = client.post("/generate", json={"prompt": "Explain auth flow", "mode": "SELF_ROUTE"})
    assert r.status_code == 200
    pipeline = r.json()["pipeline"]
    assert pipeline["route"] == "LC"
    assert pipeline["stages"]["assess"]["summary"]["route"] == "LC"


def test_generate_200_gasr_one_hop_flow(monkeypatch, _mock_pipeline_dependencies):
    import retrieval.graph_expand as graph_expand_mod

    monkeypatch.setattr(
        server_mod,
        "assess_rag_gate",
        lambda *a, **k: {
            "route": "LC",
            "sufficient": False,
            "confidence": "high",
            "need": "STRUCTURE",
            "reason": "Missing callee check_hash.",
        },
    )
    monkeypatch.setattr(
        graph_expand_mod,
        "graph_expand",
        lambda seeds, hops=1, max_neighbors=8: {
            "chunks": [
                {
                    "code": "def check_hash(u, p):\n    return True\n",
                    "metadata": {
                        "file": "backend/crypto.py",
                        "function": "check_hash",
                        "language": "python",
                        "start_line": 1,
                        "end_line": 2,
                    },
                    "final_score": 0.75,
                    "hybrid_score": 0.75,
                    "source": "graph_hop",
                }
            ],
            "new_chunk_count": 1,
            "graph_backend": "call_graph",
            "seed_keys": ["backend/auth.py::authenticate"],
            "neighbor_ids": ["backend/crypto.py::check_hash"],
            "edge_counts": {"calls": 1},
        },
    )
    monkeypatch.setattr(
        server_mod,
        "assess_sufficiency",
        lambda *a, **k: {
            "route": "RAG",
            "sufficient": True,
            "confidence": "high",
            "reason": "Callee check_hash found after 1 hop.",
        },
    )

    r = client.post(
        "/generate",
        json={"prompt": "How does authenticate verify hashes?", "mode": "GASR", "gasr_hard_cap": 2},
    )
    assert r.status_code == 200
    pipeline = r.json()["pipeline"]
    assert pipeline["route"] == "GRAPH"
    assert pipeline["hops_used"] == 1
    assert "graph_expand" in pipeline["stages"]
    assert "re_prune" in pipeline["stages"]


def test_generate_200_gapa_entry_sufficient(monkeypatch, _mock_pipeline_dependencies):
    import diamond.preroute_gate as preroute_gate_mod

    monkeypatch.setattr(
        preroute_gate_mod,
        "preroute_structured_gate",
        lambda *a, **k: {
            "route": "RAG",
            "sufficient": True,
            "confidence": "high",
            "reason": "Entry metadata covers authenticate.",
        },
    )

    r = client.post("/generate", json={"prompt": "Where is authenticate?", "mode": "GAPA"})
    assert r.status_code == 200
    pipeline = r.json()["pipeline"]
    assert pipeline["mode"] == "GAPA"
    assert pipeline["route"] == "RAG"
    assert pipeline["hops_used"] == 0


# ---------------------------------------------------------------------------
# 3. Empty ChromaDB collection fallback
# ---------------------------------------------------------------------------


def test_retrieve_context_on_empty_chroma_collection_returns_empty_list(tmp_path, monkeypatch):
    """Querying an empty ChromaDB collection must return [] gracefully without crashing."""
    empty_db = tmp_path / "empty_chroma"
    chroma_client = chromadb.PersistentClient(path=str(empty_db))
    empty_code_coll = chroma_client.get_or_create_collection("empty_code")
    empty_meta_coll = chroma_client.get_or_create_collection("empty_meta")

    class _DummyEncoder:
        def encode(self, texts, *a, **k):
            return np.zeros((len(texts), 384), dtype=np.float32)

    monkeypatch.setattr(retrieval_tool, "collection", empty_code_coll)
    monkeypatch.setattr(retrieval_tool, "meta_collection", empty_meta_coll)
    monkeypatch.setattr(retrieval_tool, "BM25_PATH", str(empty_db / "nonexistent_bm25.json"))
    monkeypatch.setattr(retrieval_tool, "_bm25_cache", None)
    monkeypatch.setattr(retrieval_tool, "get_sentence_transformer", lambda *a, **k: _DummyEncoder())

    results = retrieval_tool.retrieve_context("where is Dashboard defined?", top_k=5)
    assert results == []
