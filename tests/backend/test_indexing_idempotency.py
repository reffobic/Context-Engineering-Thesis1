"""Tier 2 — Indexing idempotency, incremental cache, and max_files cap tests.

Uses a temporary repository and a throwaway ChromaDB directory under `tmp_path`
so the developer's real `backend/chroma_db` is never touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("chromadb")

import backend.indexing.code_indexer as code_indexer  # noqa: E402
from backend.indexing.code_indexer import CodeIndexer  # noqa: E402


class _FakeSentenceTransformer:
    """Fast deterministic 384-d encoder so idempotency tests run offline in <0.2s."""

    def encode(self, texts, *args, **kwargs):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for idx, t in enumerate(texts):
            vec = np.full(384, 0.01, dtype=np.float32)
            vec[len(t) % 384] = 0.99
            vec[(idx + 17) % 384] = 0.5
            out.append(vec)
        return np.asarray(out, dtype=np.float32)


@pytest.fixture(autouse=True)
def _stub_external_models(monkeypatch):
    """Stub SentenceTransformer and Ollama helpers so tests never hit network or GPU."""
    monkeypatch.setattr(code_indexer, "SBERT_AVAILABLE", True)
    monkeypatch.setattr(
        code_indexer, "get_sentence_transformer", lambda *a, **k: _FakeSentenceTransformer()
    )
    monkeypatch.setattr(code_indexer, "_ollama_file_summary", lambda *a, **k: None)
    monkeypatch.setattr(code_indexer, "_ollama_situating_text", lambda *a, **k: "")


def _make_sample_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "a.py").write_text(
        "def alpha(x: int) -> int:\n    return x + 1\n\ndef beta(y: int) -> int:\n    return alpha(y) * 2\n",
        encoding="utf-8",
    )
    (repo_dir / "b.py").write_text(
        "class Calculator:\n    def add(self, a, b):\n        return a + b\n",
        encoding="utf-8",
    )
    (repo_dir / "c.js").write_text(
        "export function formatCurrency(amount) {\n  return '$' + amount.toFixed(2);\n}\n",
        encoding="utf-8",
    )
    return repo_dir


def test_index_idempotency_second_run_skips_unchanged(tmp_path):
    repo_dir = _make_sample_repo(tmp_path / "repo")
    db_dir = tmp_path / "chroma_db"

    indexer = CodeIndexer(db_path=str(db_dir), use_hybrid_embeddings=False)
    stats1 = indexer.index_repository(repo_dir)

    assert stats1["files_scanned"] == 3
    assert stats1["files_indexed"] == 3
    assert stats1["files_skipped"] == 0
    assert stats1["chunks_added"] >= 3

    code_count_after_first = indexer.collection.count()
    meta_count_after_first = indexer.meta_collection.count()
    assert code_count_after_first == stats1["chunks_added"]
    assert meta_count_after_first == stats1["chunks_added"]

    # Re-instantiate from disk to prove `.index_cache.json` persists across runs
    indexer2 = CodeIndexer(db_path=str(db_dir), use_hybrid_embeddings=False)
    stats2 = indexer2.index_repository(repo_dir)

    assert stats2["files_scanned"] == 3
    assert stats2["files_indexed"] == 0
    assert stats2["files_skipped"] == 3
    assert stats2["chunks_added"] == 0
    assert indexer2.collection.count() == code_count_after_first
    assert indexer2.meta_collection.count() == meta_count_after_first


def test_incremental_reindex_only_updates_modified_file(tmp_path):
    repo_dir = _make_sample_repo(tmp_path / "repo")
    db_dir = tmp_path / "chroma_db"

    indexer = CodeIndexer(db_path=str(db_dir), use_hybrid_embeddings=False)
    stats1 = indexer.index_repository(repo_dir)
    assert stats1["files_indexed"] == 3

    # Modify only `a.py`
    (repo_dir / "a.py").write_text(
        "def alpha(x: int) -> int:\n    return x + 100\n\ndef gamma(z: int) -> int:\n    return z - 1\n",
        encoding="utf-8",
    )

    stats2 = indexer.index_repository(repo_dir)
    assert stats2["files_scanned"] == 3
    assert stats2["files_indexed"] == 1
    assert stats2["files_skipped"] == 2
    assert stats2["chunks_added"] >= 1


def test_non_incremental_reindex_forces_full_rescan(tmp_path):
    repo_dir = _make_sample_repo(tmp_path / "repo")
    db_dir = tmp_path / "chroma_db"

    indexer = CodeIndexer(db_path=str(db_dir), use_hybrid_embeddings=False)
    indexer.index_repository(repo_dir)

    stats_forced = indexer.index_repository(repo_dir, incremental=False)
    assert stats_forced["files_indexed"] == 3
    assert stats_forced["files_skipped"] == 0


def test_max_files_cap_and_unlimited_override(tmp_path):
    repo_dir = tmp_path / "repo_five"
    repo_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (repo_dir / f"mod_{i}.py").write_text(
            f"def compute_{i}(val):\n    return val + {i}\n",
            encoding="utf-8",
        )
    db_dir = tmp_path / "chroma_db"

    indexer = CodeIndexer(db_path=str(db_dir), use_hybrid_embeddings=False)

    # First run capped at max_files=2
    stats_capped = indexer.index_repository(repo_dir, max_files=2)
    assert stats_capped["files_scanned"] == 5
    assert stats_capped["limited_run"] is True
    assert stats_capped["max_files"] == 2
    assert stats_capped["files_indexed"] == 2

    # Second run with max_files=None indexes the remaining 3 and skips the first 2
    stats_full = indexer.index_repository(repo_dir, max_files=None)
    assert stats_full["files_scanned"] == 5
    assert stats_full["limited_run"] is False
    assert stats_full["max_files"] is None
    assert stats_full["files_skipped"] == 2
    assert stats_full["files_indexed"] == 3
