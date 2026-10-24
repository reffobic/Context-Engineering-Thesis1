"""Tier 2 — API request-validation contract for POST /generate.

FastAPI/pydantic rejects bad bodies with 422 before the handler (and so before any
retrieval or Ollama call), so these tests need no index and no LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
for p in (str(BACKEND), str(BACKEND / "mcp_server")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("fastapi")
pytest.importorskip("chromadb")
pytest.importorskip("sentence_transformers")
from fastapi.testclient import TestClient  # noqa: E402

try:
    from mcp_server.server import app  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover
    from server import app  # type: ignore  # noqa: E402

client = TestClient(app)


def test_health_endpoint_ok():
    r = client.get("/")
    assert r.status_code == 200


def test_generate_rejects_missing_prompt():
    assert client.post("/generate", json={}).status_code == 422


def test_generate_rejects_invalid_mode():
    r = client.post("/generate", json={"prompt": "hi", "mode": "NONEXISTENT"})
    assert r.status_code == 422


def test_generate_rejects_removed_preroute_mode():
    """PREROUTE was renamed to GAPA on main; the old name must no longer validate."""
    r = client.post("/generate", json={"prompt": "hi", "mode": "PREROUTE"})
    assert r.status_code == 422


def test_generate_rejects_invalid_pruning_method():
    r = client.post("/generate", json={"prompt": "hi", "pruning_method": "magic"})
    assert r.status_code == 422


def test_generate_rejects_non_json_body():
    r = client.post("/generate", content="not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
