"""
Tests::
  - Language detection
  - API route detection
  - Import extraction (Python + JS/TS)
  - Repository scanning
  - File chunking (Python AST + C/generic)
  - Embedding generation (SentenceTransformer)
  - Vector DB storage (ChromaDB)
  - Full pipeline round-trip: index → query
  - Incremental indexing (hash cache)
  - Hash cache path correctness
  - index_stats() reporting
  - Subversion repo analysis

Run:
    cd backend
    python test_code_indexer.py
"""

import sys
import os
import json
import time
import shutil
import tempfile
from pathlib import Path

from indexing.code_indexer import (
    CodeIndexer,
    scan_repository,
    read_and_chunk_file,
    _detect_language,
    _extract_imports,
    _find_used_imports,
    _is_api_route,
    _sha256,
    LANGUAGE_MAP,
    CHROMADB_AVAILABLE,
    SBERT_AVAILABLE,
)

# ─── paths ────────────────────────────────────────────────────────────────────
REPO_PATH   = Path(__file__).parent.parent / "data" / "subversion"
TEST_DB_PATH = "./test_chroma_db"

# ─── terminal colors ──────────────────────────────────────────────────────────
class Colors:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    BLUE   = "\033[94m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    RESET  = "\033[0m"
    BOLD   = "\033[1m"

def test_header(name: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}TEST: {name}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}")

def test_pass(msg: str):
    print(f"{Colors.GREEN}✓ {msg}{Colors.RESET}")

def test_fail(msg: str, error: str = ""):
    print(f"{Colors.RED}✗ FAIL: {msg}{Colors.RESET}")
    if error:
        print(f"  Error: {error}")

def test_info(msg: str):
    print(f"{Colors.YELLOW}  {msg}{Colors.RESET}")

def test_skip(msg: str):
    print(f"{Colors.CYAN}  SKIP: {msg}{Colors.RESET}")


# ─── helpers ──────────────────────────────────────────────────────────────────
def _cleanup_db(path: str):
    """Remove a test ChromaDB directory if it exists."""
    p = Path(path)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)

def _make_temp_repo(files: dict[str, str]) -> Path:
    """
    Create a temporary directory with given files for testing.
    files = {"filename.py": "source code here", ...}
    Returns the Path to the temp directory.
    """
    tmp = Path(tempfile.mkdtemp())
    for fname, content in files.items():
        fpath = tmp / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return tmp


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — Language Detection
# ══════════════════════════════════════════════════════════════════════════════

def test_language_detection() -> bool:
    test_header("Language Detection")

    cases = [
        ("test.py",   "python"),
        ("test.js",   "javascript"),
        ("test.ts",   "typescript"),
        ("test.tsx",  "typescript"),
        ("test.c",    "c"),
        ("test.h",    "c"),
        ("test.cpp",  "cpp"),
        ("test.go",   "go"),
        ("test.java", "java"),
        ("test.rs",   "rust"),
        ("test.rb",   "ruby"),
        ("test.md",   "markdown"),
        ("test.sh",   "shell"),
        ("test.yaml", "yaml"),
        ("test.json", "json"),
        ("unknown.xyz", None),
    ]

    passed = 0
    for filename, expected in cases:
        detected = _detect_language(Path(filename))
        if detected == expected:
            test_pass(f"{filename} → {detected or 'unknown'}")
            passed += 1
        else:
            test_fail(f"{filename}", f"expected={expected}  got={detected}")

    return passed == len(cases)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — API Route Detection
# ══════════════════════════════════════════════════════════════════════════════

def test_api_route_detection() -> bool:
    test_header("API Route Detection")

    cases = [
        ("@app.get('/users')\ndef list_users(): pass",      True),
        ("@app.post('/users')\ndef create_user(): pass",    True),
        ("@router.get('/items')\ndef list_items(): pass",   True),
        ("@blueprint.put('/resource')\ndef update(): pass", True),
        ("@app.delete('/item')\ndef delete(): pass",        True),
        ("@APP.GET('/upper')\ndef upper(): pass",           True),  # case-insensitive
        ("def regular_function(): pass",                    False),
        ("class MyClass: pass",                             False),
        ("# @app.get this is a comment",                    False),
    ]

    passed = 0
    for code, expected in cases:
        detected = _is_api_route(code)
        if detected == expected:
            label = "route    " if expected else "non-route"
            test_pass(f"[{label}] {code[:45].strip()!r}")
            passed += 1
        else:
            test_fail(f"Wrong detection for: {code[:45]!r}", f"expected={expected} got={detected}")

    return passed == len(cases)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Python Import Extraction
# ══════════════════════════════════════════════════════════════════════════════

def test_import_extraction_python() -> bool:
    test_header("Import Extraction (Python)")

    source = """
import os
import sys
from pathlib import Path
from typing import Optional, List
import numpy as np
"""
    imports = _extract_imports(source, "python")
    found   = set(imports)
    expected = {"os", "sys", "pathlib", "typing", "numpy"}

    if expected.issubset(found):
        test_pass(f"Extracted {len(imports)} imports: {', '.join(sorted(imports))}")
        return True
    else:
        missing = expected - found
        test_fail("Import extraction incomplete", f"Missing: {missing}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — JavaScript / TypeScript Import Extraction  (was MISSING before)
# ══════════════════════════════════════════════════════════════════════════════

def test_import_extraction_js() -> bool:
    test_header("Import Extraction (JavaScript / TypeScript)")

    # ES module style
    js_source = """\
import axios from 'axios'
import { useState, useEffect } from 'react'
import type { FC } from 'react'
const fs = require('fs')
const path = require('path')
"""

    # TypeScript file
    ts_source = """\
import express, { Request, Response } from 'express'
import { PrismaClient } from '@prisma/client'
import bcrypt from 'bcryptjs'
"""

    passed = 0

    js_imports  = set(_extract_imports(js_source,  "javascript"))
    js_expected = {"axios", "react", "fs", "path"}
    if js_expected.issubset(js_imports):
        test_pass(f"JS imports found: {', '.join(sorted(js_imports))}")
        passed += 1
    else:
        test_fail("JS import extraction", f"Missing: {js_expected - js_imports}")

    ts_imports  = set(_extract_imports(ts_source, "typescript"))
    # package names are the last segment of the import path
    ts_expected = {"express", "client", "bcryptjs"}
    if ts_expected.issubset(ts_imports):
        test_pass(f"TS imports found: {', '.join(sorted(ts_imports))}")
        passed += 1
    else:
        test_fail("TS import extraction", f"Missing: {ts_expected - ts_imports}")

    # Verify _find_used_imports works for JS too
    code    = "const res = await axios.get('/api')\nconsole.log(express)"
    used    = _find_used_imports(code, list(js_imports | ts_imports))
    if "axios" in used and "express" in used:
        test_pass(f"_find_used_imports correct: {used}")
        passed += 1
    else:
        test_fail("_find_used_imports wrong", f"got: {used}")

    return passed == 3


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5 — Repository Scanning
# ══════════════════════════════════════════════════════════════════════════════

def test_scan_repository() -> bool:
    test_header("Repository Scanning")

    if not REPO_PATH.exists():
        test_fail(f"Repo not found at {REPO_PATH}")
        return False

    all_files = scan_repository(REPO_PATH)
    test_pass(f"Scanned repository: {len(all_files)} files found")

    lang_counts: dict[str, int] = {}
    for f in all_files:
        lang = _detect_language(f) or "unknown"
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

    print("\n  Files per language:")
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
        print(f"    {lang:15s}: {count:4d} files")

    # Verify excluded dirs are not present
    bad = [f for f in all_files if any(p in f.parts for p in {".git", "node_modules", "__pycache__"})]
    if bad:
        test_fail(f"Excluded dirs leaked into results: {bad[:3]}")
        return False
    test_pass("Excluded directories correctly pruned")

    # Language filter
    py_only = scan_repository(REPO_PATH, languages=["python"])
    if all(_detect_language(f) == "python" for f in py_only):
        test_pass(f"Language filter works: {len(py_only)} python-only files")
    else:
        test_fail("Language filter returned non-python files")
        return False

    test_pass(f"Language detection working ({len(lang_counts)} languages found)")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6 — File Chunking: Python (AST)
# ══════════════════════════════════════════════════════════════════════════════

def test_file_chunking_python() -> bool:
    test_header("File Chunking (Python — AST)")

    py_files = scan_repository(REPO_PATH, languages=["python"])[:3]
    if not py_files:
        test_fail("No Python files found")
        return False

    total, types = 0, set()
    for fpath in py_files:
        chunks = read_and_chunk_file(fpath, REPO_PATH)
        total += len(chunks)
        for c in chunks:
            types.add(c.chunk_type)
            print(f"  [{c.chunk_type:8s}] {fpath.name}:{c.start_line:4d} → {c.name}")

    test_pass(f"Chunked {len(py_files)} Python files: {total} total chunks")
    test_pass(f"Chunk types found: {', '.join(sorted(types))}")

    # Verify chunk fields are populated
    sample_chunks = read_and_chunk_file(py_files[0], REPO_PATH)
    if sample_chunks:
        c = sample_chunks[0]
        assert c.chunk_id,  "chunk_id missing"
        assert c.file_path, "file_path missing"
        assert c.language == "python", f"language wrong: {c.language}"
        assert c.code,      "code missing"
        assert c.file_hash, "file_hash missing"
        assert c.start_line > 0, "start_line invalid"
        assert c.end_line >= c.start_line, "end_line invalid"
        test_pass("Chunk fields all populated correctly")

    return True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7 — File Chunking: C (generic regex)
# ══════════════════════════════════════════════════════════════════════════════

def test_file_chunking_c() -> bool:
    test_header("File Chunking (C — generic regex)")

    c_files = scan_repository(REPO_PATH, languages=["c"])[:3]
    if not c_files:
        test_fail("No C files found")
        return False

    total, types = 0, set()
    for fpath in c_files:
        chunks = read_and_chunk_file(fpath, REPO_PATH)
        total += len(chunks)
        for c in chunks:
            types.add(c.chunk_type)
            print(f"  [{c.chunk_type:8s}] {fpath.name}:{c.start_line:4d} → {c.name}")

    test_pass(f"Chunked {len(c_files)} C files: {total} total chunks")
    test_pass(f"Chunk types found: {', '.join(sorted(types))}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 8 — Embedding Generation  (was MISSING before)
# ══════════════════════════════════════════════════════════════════════════════

def test_embedding_generation() -> bool:
    test_header("Embedding Generation (SentenceTransformer)")

    if not SBERT_AVAILABLE:
        test_fail("sentence-transformers not installed — run: pip install sentence-transformers")
        return False

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")

    # Embed two semantically similar and one dissimilar text
    texts = [
        "def login(username, password): verify credentials",
        "def authenticate(user, pwd): check user password",
        "def calculate_tax(income, rate): return income * rate",
    ]
    vectors = model.encode(texts)

    assert vectors.shape == (3, 384), f"Expected (3,384), got {vectors.shape}"
    test_pass(f"Embedding shape correct: {vectors.shape}")

    # Cosine similarity: similar pair should score higher than dissimilar pair
    import numpy as np
    def cosine(a, b):
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    sim_similar   = cosine(vectors[0], vectors[1])  # both about login/auth
    sim_dissimilar = cosine(vectors[0], vectors[2])  # auth vs tax

    test_info(f"Similarity (login vs authenticate): {sim_similar:.4f}")
    test_info(f"Similarity (login vs calculate_tax): {sim_dissimilar:.4f}")

    if sim_similar > sim_dissimilar:
        test_pass("Semantic similarity ordering correct (auth pair > unrelated pair)")
    else:
        test_fail("Similarity ordering wrong — model may not be loaded correctly")
        return False

    return True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 9 — Full Pipeline Round-Trip: index → store → query  (was MISSING before)
# ══════════════════════════════════════════════════════════════════════════════

def test_full_pipeline_roundtrip() -> bool:
    test_header("Full Pipeline Round-Trip (index → query)")

    if not CHROMADB_AVAILABLE:
        test_fail("chromadb not installed — run: pip install chromadb")
        return False
    if not SBERT_AVAILABLE:
        test_fail("sentence-transformers not installed")
        return False

    db_path = "./test_roundtrip_db"
    _cleanup_db(db_path)

    # Build a small temp repo with known content
    repo = _make_temp_repo({
        "auth.py": """\
import hashlib

def hash_password(password: str) -> str:
    \"\"\"Hash a password using SHA-256.\"\"\"
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    \"\"\"Verify a plaintext password against a stored hash.\"\"\"
    return hash_password(password) == hashed
""",
        "database.py": """\
import sqlite3

def get_connection(db_path: str):
    \"\"\"Open a SQLite database connection.\"\"\"
    return sqlite3.connect(db_path)

def run_query(conn, sql: str):
    \"\"\"Execute a SQL query and return results.\"\"\"
    cursor = conn.cursor()
    cursor.execute(sql)
    return cursor.fetchall()
""",
        "utils.py": """\
def format_date(ts: float) -> str:
    \"\"\"Format a Unix timestamp as a readable date string.\"\"\"
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
""",
    })

    try:
        indexer = CodeIndexer(db_path=db_path, collection_name="roundtrip_test")

        # ── index ────────────────────────────────────────────────────────────
        stats = indexer.index_repository(str(repo), languages=["python"])
        test_info(f"Index stats: {stats}")

        assert stats["files_scanned"]  >= 3, f"Expected 3+ files, got {stats['files_scanned']}"
        assert stats["files_indexed"]  >= 3, "No files were indexed"
        assert stats["chunks_added"]   >= 4, "Too few chunks added"
        assert stats["chunks_skipped"] == 0, "Nothing should be skipped on first run"
        test_pass(f"Indexing: {stats['files_indexed']} files, {stats['chunks_added']} chunks added")

        # ── query: password / auth topic ─────────────────────────────────────
        results = indexer.query("function that hashes or verifies a password", top_k=3)
        assert len(results) > 0, "Query returned no results"

        names   = [r["name"] for r in results]
        top_hit = results[0]
        test_info(f"Top results: {names}")
        test_info(f"Best match: {top_hit['file_path']} :: {top_hit['name']}  dist={top_hit['distance']:.4f}")

        assert any(n in ("hash_password", "verify_password") for n in names), \
            f"Expected hash_password or verify_password in top results, got: {names}"
        test_pass("Query returned correct functions for password/auth query")

        # ── query: database topic ────────────────────────────────────────────
        db_results = indexer.query("database connection and SQL query", top_k=3)
        db_names   = [r["name"] for r in db_results]
        test_info(f"DB query top results: {db_names}")
        assert any(n in ("get_connection", "run_query") for n in db_names), \
            f"Expected DB functions in results, got: {db_names}"
        test_pass("Query returned correct functions for database query")

        # ── result fields present ────────────────────────────────────────────
        required_keys = {"code", "file_path", "language", "chunk_type", "name",
                         "start_line", "end_line", "dependencies", "distance"}
        assert required_keys.issubset(results[0].keys()), \
            f"Missing keys: {required_keys - results[0].keys()}"
        test_pass("All result fields present")

        # ── language filter on query ─────────────────────────────────────────
        py_results = indexer.query("function", top_k=5, language="python")
        assert all(r["language"] == "python" for r in py_results), \
            "Language filter on query not working"
        test_pass("Language filter on query works")

        return True

    except AssertionError as e:
        test_fail("Round-trip assertion failed", str(e))
        return False
    except Exception as e:
        test_fail("Round-trip crashed", str(e))
        return False
    finally:
        _cleanup_db(db_path)
        shutil.rmtree(str(repo), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 10 — Incremental Indexing  (was MISSING before)
# ══════════════════════════════════════════════════════════════════════════════

def test_incremental_indexing() -> bool:
    test_header("Incremental Indexing (hash cache)")

    if not CHROMADB_AVAILABLE or not SBERT_AVAILABLE:
        test_skip("Requires chromadb + sentence-transformers")
        return False

    db_path = "./test_incremental_db"
    _cleanup_db(db_path)

    repo = _make_temp_repo({
        "app.py": "def hello():\n    return 'hello'\n",
        "utils.py": "def add(a, b):\n    return a + b\n",
    })

    try:
        indexer = CodeIndexer(db_path=db_path, collection_name="inc_test")

        # ── run 1: first index ───────────────────────────────────────────────
        stats1 = indexer.index_repository(str(repo), incremental=True)
        test_info(f"Run 1: {stats1}")
        assert stats1["files_indexed"]  == 2, f"Expected 2 indexed, got {stats1['files_indexed']}"
        assert stats1["chunks_skipped"] == 0
        test_pass("Run 1: both files indexed from scratch")

        # ── run 2: no changes — everything should be skipped ─────────────────
        stats2 = indexer.index_repository(str(repo), incremental=True)
        test_info(f"Run 2 (no changes): {stats2}")
        assert stats2["files_indexed"]  == 0, f"Expected 0 re-indexed, got {stats2['files_indexed']}"
        assert stats2["chunks_skipped"] >= 2
        test_pass("Run 2: unchanged files correctly skipped")

        # ── run 3: modify one file — only that file should re-index ──────────
        modified = repo / "app.py"
        modified.write_text("def hello():\n    return 'hello world'\n\ndef goodbye():\n    return 'bye'\n")

        stats3 = indexer.index_repository(str(repo), incremental=True)
        test_info(f"Run 3 (one file changed): {stats3}")
        assert stats3["files_indexed"]  == 1, f"Expected 1 re-indexed, got {stats3['files_indexed']}"
        assert stats3["chunks_skipped"] >= 1
        test_pass("Run 3: only modified file re-indexed")

        # ── run 4: --no-incremental flag forces full re-index ─────────────────
        stats4 = indexer.index_repository(str(repo), incremental=False)
        test_info(f"Run 4 (incremental=False): {stats4}")
        assert stats4["files_indexed"] == 2, f"Expected 2 force-indexed, got {stats4['files_indexed']}"
        test_pass("Run 4: incremental=False forces full re-index")

        return True

    except AssertionError as e:
        test_fail("Incremental indexing assertion", str(e))
        return False
    except Exception as e:
        test_fail("Incremental indexing crashed", str(e))
        return False
    finally:
        _cleanup_db(db_path)
        shutil.rmtree(str(repo), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 11 — index_stats() reporting  (was MISSING before)
# ══════════════════════════════════════════════════════════════════════════════

def test_index_stats() -> bool:
    test_header("index_stats() Reporting")

    if not CHROMADB_AVAILABLE or not SBERT_AVAILABLE:
        test_skip("Requires chromadb + sentence-transformers")
        return False

    db_path = "./test_stats_db"
    _cleanup_db(db_path)

    repo = _make_temp_repo({
        "auth.py": """\
def login(user, pwd): pass
def logout(user): pass
class AuthService:
    def verify(self): pass
""",
        "routes.py": """\
from flask import Flask
app = Flask(__name__)

@app.get('/users')
def list_users(): pass

@app.post('/users')
def create_user(): pass
""",
    })

    try:
        indexer = CodeIndexer(db_path=db_path, collection_name="stats_test")
        indexer.index_repository(str(repo), languages=["python"])

        stats = indexer.index_stats()
        test_info(f"index_stats() returned: {json.dumps(stats, indent=2)}")

        # Validate structure
        assert "total_chunks"   in stats, "Missing total_chunks"
        assert "by_language"    in stats, "Missing by_language"
        assert "by_chunk_type"  in stats, "Missing by_chunk_type"
        assert "model"          in stats, "Missing model"
        test_pass("index_stats() returns all required keys")

        assert stats["total_chunks"] >= 4, f"Expected 4+ chunks, got {stats['total_chunks']}"
        test_pass(f"total_chunks correct: {stats['total_chunks']}")

        assert "python" in stats["by_language"], "python missing from by_language"
        test_pass(f"by_language correct: {stats['by_language']}")

        assert "function" in stats["by_chunk_type"], "function type missing"
        test_pass(f"by_chunk_type correct: {stats['by_chunk_type']}")

        expected_routes = stats["by_chunk_type"].get("route", 0)
        assert expected_routes >= 2, f"Expected 2+ routes, got {expected_routes}"
        test_pass(f"Route chunks detected in stats: {expected_routes}")

        assert stats["model"] == "all-MiniLM-L6-v2"
        test_pass(f"Model name correct: {stats['model']}")

        return True

    except AssertionError as e:
        test_fail("index_stats assertion", str(e))
        return False
    except Exception as e:
        test_fail("index_stats crashed", str(e))
        return False
    finally:
        _cleanup_db(db_path)
        shutil.rmtree(str(repo), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 12 — Hash Cache Path Bug  (was MISSING before)
# ══════════════════════════════════════════════════════════════════════════════

def test_hash_cache_path() -> bool:
    test_header("Hash Cache Path (follows --db flag)")

    if not CHROMADB_AVAILABLE or not SBERT_AVAILABLE:
        test_skip("Requires chromadb + sentence-transformers")
        return False

    custom_db = "./test_custom_db_path"
    _cleanup_db(custom_db)

    repo = _make_temp_repo({"hello.py": "def greet(): return 'hi'\n"})

    try:
        indexer = CodeIndexer(db_path=custom_db, collection_name="cache_path_test")
        indexer.index_repository(str(repo))

        # The cache file should live INSIDE the custom DB directory, not ./chroma_db/
        cache_file = Path(custom_db) / ".index_cache.json"
        wrong_file = Path("./chroma_db") / ".index_cache.json"

        if cache_file.exists():
            test_pass(f"Cache correctly written to custom db path: {cache_file}")
            cache_data = json.loads(cache_file.read_text())
            test_info(f"Cache contents: {list(cache_data.keys())}")
            return True
        elif wrong_file.exists():
            test_fail(
                "Cache written to hardcoded ./chroma_db/ instead of custom --db path",
                f"Fix: change _cache_path() to use self.client._path instead of DEFAULT_DB_PATH"
            )
            return False
        else:
            test_fail("Cache file not found anywhere")
            return False

    except Exception as e:
        test_fail("Hash cache path test crashed", str(e))
        return False
    finally:
        _cleanup_db(custom_db)
        _cleanup_db("./chroma_db")
        shutil.rmtree(str(repo), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 13 — Subversion Repository Full Analysis
# ══════════════════════════════════════════════════════════════════════════════

def test_subversion_analysis() -> bool:
    test_header("Subversion Repository Analysis")

    if not REPO_PATH.exists():
        test_fail(f"Subversion repo not found at {REPO_PATH}")
        return False

    all_files = scan_repository(REPO_PATH)
    test_pass(f"Scanned {len(all_files)} files")

    for lang in ["python", "c", "javascript", "java"]:
        lang_files = scan_repository(REPO_PATH, languages=[lang])
        if lang_files:
            test_pass(f"Found {len(lang_files)} {lang} files")

    print("\n  Sample chunking analysis:")
    for lang in ["python", "c", "javascript"]:
        lang_files = scan_repository(REPO_PATH, languages=[lang])[:2]
        if lang_files:
            total = sum(len(read_and_chunk_file(f, REPO_PATH)) for f in lang_files)
            print(f"    {lang:12s}: {len(lang_files)} files → {total} chunks")

    return True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 14 — SHA-256 Determinism & Chunk ID Uniqueness
# ══════════════════════════════════════════════════════════════════════════════

def test_sha256_and_chunk_ids() -> bool:
    test_header("SHA-256 Determinism & Chunk ID Uniqueness")

    # Same input always produces same hash
    h1 = _sha256("hello world")
    h2 = _sha256("hello world")
    assert h1 == h2, "SHA-256 not deterministic"
    test_pass(f"SHA-256 is deterministic: {h1[:16]}...")

    # Different input produces different hash
    h3 = _sha256("hello world!")
    assert h1 != h3, "SHA-256 collision on trivially different input"
    test_pass("Different inputs produce different hashes")

    # Chunk IDs are unique per (file, line) combination
    repo = _make_temp_repo({
        "a.py": "def foo(): pass\ndef bar(): return 1\n",
        "b.py": "def foo(): pass\n",  # same function name, different file
    })
    try:
        a_chunks = read_and_chunk_file(repo / "a.py", repo)
        b_chunks = read_and_chunk_file(repo / "b.py", repo)
        all_ids  = [c.chunk_id for c in a_chunks + b_chunks]
        assert len(all_ids) == len(set(all_ids)), f"Duplicate chunk IDs found: {all_ids}"
        test_pass(f"All {len(all_ids)} chunk IDs are unique (even across files with same function name)")
        return True
    finally:
        shutil.rmtree(str(repo), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

def run_all_tests():
    print(f"\n{Colors.BOLD}{Colors.BLUE}CODE INDEXER TEST SUITE — FULL COVERAGE{Colors.RESET}")
    print(f"{Colors.BLUE}Testing P2 — Code Indexing & Embeddings{Colors.RESET}")
    print(f"{Colors.BLUE}chromadb available    : {CHROMADB_AVAILABLE}{Colors.RESET}")
    print(f"{Colors.BLUE}sentence-transformers : {SBERT_AVAILABLE}{Colors.RESET}")

    tests = [
        ("Language Detection",                   test_language_detection),
        ("API Route Detection",                  test_api_route_detection),
        ("Import Extraction (Python)",           test_import_extraction_python),
        ("Import Extraction (JS/TS)",            test_import_extraction_js),      # NEW
        ("Repository Scanning",                  test_scan_repository),
        ("File Chunking (Python AST)",           test_file_chunking_python),
        ("File Chunking (C generic)",            test_file_chunking_c),
        ("Embedding Generation",                 test_embedding_generation),       # NEW
        ("Full Pipeline Round-Trip",             test_full_pipeline_roundtrip),    # NEW
        ("Incremental Indexing",                 test_incremental_indexing),       # NEW
        ("index_stats() Reporting",              test_index_stats),                # NEW
        ("Hash Cache Path",                      test_hash_cache_path),            # NEW
        ("SHA-256 & Chunk ID Uniqueness",        test_sha256_and_chunk_ids),       # NEW
        ("Subversion Repository Analysis",       test_subversion_analysis),
    ]

    results: dict[str, bool] = {}
    start = time.time()

    for name, fn in tests:
        try:
            results[name] = fn()
        except Exception as e:
            test_fail(f"Test crashed: {name}", str(e))
            results[name] = False

    elapsed = time.time() - start

    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BOLD}TEST SUMMARY{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}")

    passed = sum(1 for v in results.values() if v)
    total  = len(results)

    for name, ok in results.items():
        tag = f"{Colors.GREEN}✓ PASS{Colors.RESET}" if ok else f"{Colors.RED}✗ FAIL{Colors.RESET}"
        # mark the new tests
        flag = f" {Colors.CYAN}[new]{Colors.RESET}" if name not in {
            "Language Detection", "API Route Detection",
            "Import Extraction (Python)", "Repository Scanning",
            "File Chunking (Python AST)", "File Chunking (C generic)",
            "Subversion Repository Analysis",
        } else ""
        print(f"  {name:45s} {tag}{flag}")

    color = Colors.GREEN if passed == total else Colors.YELLOW
    print(f"\n{Colors.BOLD}{color}Results: {passed}/{total} tests passed ({100*passed//total}%){Colors.RESET}")
    print(f"Elapsed: {elapsed:.2f}s")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)