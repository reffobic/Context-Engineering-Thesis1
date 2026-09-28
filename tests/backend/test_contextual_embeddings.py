"""Unit tests for contextual + dual-channel embeddings (ContextMeta / α blend)."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from indexing.code_indexer import (  # noqa: E402
    CONTEXT_SEPARATOR,
    INDEX_SCHEMA_VERSION,
    CodeChunk,
    _build_context_header,
    _heuristic_situating_text,
    _infer_chunk_role,
    read_and_chunk_file,
)
from retrieval.retrieval_tool import blend_dense_scores  # noqa: E402


def _raw_code(document: str) -> str:
    if CONTEXT_SEPARATOR in (document or ""):
        return document.split(CONTEXT_SEPARATOR, 1)[1]
    return document or ""


def test_schema_version_is_dual_v1():
    assert INDEX_SCHEMA_VERSION == "contextual-dual-v1"


def test_infer_chunk_role():
    assert _infer_chunk_role(CodeChunk(
        chunk_id="1", file_path="tests/test_app.py", language="python",
        chunk_type="function", name="test_foo", start_line=1, end_line=2, code="def test_foo():\n  pass\n",
    )) == "test"
    assert _infer_chunk_role(CodeChunk(
        chunk_id="2", file_path="superset/api.py", language="python",
        chunk_type="route", name="get_dash", start_line=1, end_line=2, code="def get_dash():\n  pass\n",
    )) == "api"
    assert _infer_chunk_role(CodeChunk(
        chunk_id="3", file_path="superset/models/dashboard.py", language="python",
        chunk_type="class", name="Dashboard", start_line=1, end_line=5, code="class Dashboard:\n  pass\n",
    )) == "definition"


def test_situating_differs_across_chunks_same_file(tmp_path: Path):
    src = tmp_path / "mod.py"
    src.write_text(
        "import os\n\n"
        "class Alpha:\n"
        "    def method_a(self):\n"
        "        return 1\n\n"
        "def beta_helper():\n"
        "    return os.getcwd()\n",
        encoding="utf-8",
    )
    chunks = read_and_chunk_file(src, tmp_path)
    assert len(chunks) >= 2
    texts = [c.situating_text for c in chunks]
    assert all(t.strip() for t in texts)
    assert len(set(texts)) >= 2


def test_meta_document_has_no_raw_code(tmp_path: Path):
    src = tmp_path / "one.py"
    src.write_text("def hello():\n    return 42\n", encoding="utf-8")
    chunks = read_and_chunk_file(src, tmp_path)
    c = chunks[0]
    meta_doc = c.meta_document()
    code_doc = c.code_document()
    contextual = c.contextual_document()
    assert "def hello" in code_doc
    assert "def hello" not in meta_doc
    assert "Situating:" in meta_doc or c.situating_text in meta_doc
    assert CONTEXT_SEPARATOR in contextual
    assert _raw_code(contextual).strip().startswith("def hello")
    assert c.to_metadata()["index_schema_version"] == INDEX_SCHEMA_VERSION


def test_header_uses_shared_fields():
    chunk = CodeChunk(
        chunk_id="x",
        file_path="pkg/a.py",
        language="python",
        chunk_type="function",
        name="run",
        start_line=10,
        end_line=20,
        code="def run():\n    pass\n",
        dependencies=["flask"],
        file_summary="File pkg/a.py is a python source file.",
        symbols=["run", "Other"],
        qualified_name="pkg/a.py::run",
    )
    chunk.chunk_role = _infer_chunk_role(chunk)
    chunk.situating_text = _heuristic_situating_text(chunk, chunk.file_summary)
    chunk.context_header = _build_context_header(chunk)
    assert "Situating:" in chunk.context_header
    assert chunk.situating_text in chunk.context_header
    assert "Role: definition" in chunk.context_header
    assert "Dependencies: flask" in chunk.context_header


def test_blend_dense_scores_alpha():
    assert abs(blend_dense_scores(1.0, 0.0, alpha=0.0) - 1.0) < 1e-6
    assert abs(blend_dense_scores(1.0, 0.0, alpha=1.0) - 0.0) < 1e-6
    assert abs(blend_dense_scores(0.5, 1.0, alpha=0.4) - 0.7) < 1e-6
