"""
Code Indexing & Embeddings
================================
Converts a codebase into a searchable vector database.

Pipeline:
    code chunk → embedding model → vector → ChromaDB


"""

import os
import ast
import re
import json
import hashlib
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

# Keep indexing friendly to laptops by default. These must be set before
# sentence-transformers/torch initializes thread pools.
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("INDEX_CPU_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("INDEX_CPU_THREADS", "2"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ─── third-party ─────────────────────────────────────────────────────────────
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logging.warning("chromadb not installed. Run: pip install chromadb")

try:
    from sentence_transformers import SentenceTransformer  # noqa: F401 — availability probe
    SBERT_AVAILABLE = True
except ImportError:
    SentenceTransformer = None  # type: ignore[misc, assignment]
    SBERT_AVAILABLE = False
    logging.warning("sentence-transformers not installed. Run: pip install sentence-transformers")

from .sentence_model import get_sentence_transformer
from .graph_builder import CallGraphBuilder, CallGraph
from .graph_embeddings import HybridEmbeddingManager

# ─── logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("code_indexer")

# ─── constants ────────────────────────────────────────────────────────────────
DEFAULT_MODEL      = "all-MiniLM-L6-v2"
DEFAULT_DB_PATH    = "./chroma_db"
DEFAULT_COLLECTION = "code_embeddings"
DEFAULT_META_COLLECTION = "code_meta_embeddings"
MAX_CHUNK_TOKENS   = 512
CONTEXT_SEPARATOR  = "\n\n--- RAW CODE ---\n"
DEFAULT_INDEX_MAX_FILES = int(os.getenv("INDEX_MAX_FILES", "600"))
DEFAULT_EMBED_BATCH_SIZE = int(os.getenv("INDEX_EMBED_BATCH_SIZE", "16"))
DEFAULT_CACHE_SAVE_INTERVAL = int(os.getenv("INDEX_CACHE_SAVE_INTERVAL", "50"))
# Bump when embedding recipe changes (invalidates incremental file cache).
INDEX_SCHEMA_VERSION = "contextual-dual-v1"
EMBEDDING_CACHE_FILE = ".embedding_cache.json"
USE_HYBRID_EMBEDDINGS = os.getenv("USE_HYBRID_EMBEDDINGS", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
# Cap Anthropic-style situating text (~50–100 tokens) so MiniLM is not drowned.
SITUATING_MAX_CHARS = int(os.getenv("INDEX_SITUATING_MAX_CHARS", "700"))
CHUNK_ROLES = frozenset({"definition", "api", "config", "test", "docs", "other"})
# Query-time dense blend: score = (1-α)·sim_code + α·sim_meta
DENSE_ALPHA = float(os.getenv("DENSE_ALPHA", "0.40"))

LANGUAGE_MAP: dict[str, list[str]] = {
    "python":     [".py"],
    "javascript": [".js", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "java":       [".java"],
    "c":          [".c", ".h"],
    "cpp":        [".cpp", ".cc", ".hpp"],
    "go":         [".go"],
    "rust":       [".rs"],
    "ruby":       [".rb"],
    "php":        [".php"],
    "swift":      [".swift"],
    "kotlin":     [".kt"],
    "markdown":   [".md"],
    "json":       [".json"],
    "yaml":       [".yaml", ".yml"],
    "shell":      [".sh", ".bash"],
}

EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "env", "dist", "build", ".idea", ".vscode", "coverage",
}


# ─── data model ───────────────────────────────────────────────────────────────
@dataclass
class CodeChunk:
    """
    Indexed unit. Structured fields below are the ContextMeta contract:
    retrieval headers and Chroma metadatas are views of the same data so a
    future RAG/GRAPH/LC prerouter can reuse them without a parallel schema.
    """
    chunk_id:     str
    file_path:    str
    language:     str
    chunk_type:   str   # "function" | "class" | "route" | "block"
    name:         str
    start_line:   int
    end_line:     int
    code:         str
    dependencies: list[str] = field(default_factory=list)
    file_hash:    str = ""
    context_header: str = ""
    file_summary:   str = ""
    situating_text: str = ""  # chunk-specific NL (Anthropic-style)
    chunk_role:     str = "other"  # definition|api|config|test|docs|other
    qualified_name: str = ""
    symbols:        list[str] = field(default_factory=list)

    def to_metadata(self) -> dict:
        m = asdict(self)
        m.pop("code")
        m["dependencies"] = json.dumps(m["dependencies"])
        m["symbols"] = json.dumps(m["symbols"])
        m["index_schema_version"] = INDEX_SCHEMA_VERSION
        return m

    def contextual_document(self) -> str:
        """BM25 / display text: NL header + raw code (Anthropic contextual shape)."""
        header = (self.context_header or "").strip()
        if not header:
            return self.code
        return f"{header}{CONTEXT_SEPARATOR}{self.code}"

    def meta_document(self) -> str:
        """NL-only text embedded as v_meta (no raw code)."""
        header = (self.context_header or "").strip()
        if header:
            return header
        situating = (self.situating_text or "").strip()
        if situating:
            return situating
        return (
            f"Path: {self.file_path}\n"
            f"Chunk: {self.chunk_type} {self.qualified_name or self.name}\n"
            f"Role: {self.chunk_role or 'other'}"
        )

    def code_document(self) -> str:
        """Raw code embedded as v_code."""
        return self.code or ""


# ─── helpers ─────────────────────────────────────────────────────────────────
def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _detect_language(file_path: Path) -> Optional[str]:
    ext = file_path.suffix.lower()
    for lang, exts in LANGUAGE_MAP.items():
        if ext in exts:
            return lang
    return None


def _file_hash(file_path: Path) -> str:
    return _sha256(file_path.read_bytes().decode("utf-8", errors="replace"))


def _extract_symbols(source: str, language: str) -> list[str]:
    if language == "python":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
    else:
        names = []
        for pattern in _FUNC_PATTERNS:
            names.extend(match.group(1) for match in pattern.finditer(source) if match.lastindex)

    seen: set[str] = set()
    ordered = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered[:80]


def _heuristic_file_summary(rel_path: str, language: str, source: str, symbols: list[str]) -> str:
    imports = _extract_imports(source, language)[:12]
    symbol_text = ", ".join(symbols[:20]) if symbols else "no named symbols detected"
    import_text = ", ".join(imports) if imports else "no imports detected"
    return (
        f"File {rel_path} is a {language} source file. "
        f"Key symbols: {symbol_text}. Imports/dependencies: {import_text}."
    )


def _ollama_file_summary(rel_path: str, language: str, source: str, symbols: list[str]) -> Optional[str]:
    if os.getenv("INDEX_CONTEXT_OLLAMA", "0").strip().lower() not in {"1", "true", "yes"}:
        return None

    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("INDEX_CONTEXT_MODEL", os.getenv("OLLAMA_MODEL", "llama3.1"))
    prompt = (
        "Summarize this source file for code retrieval in 2 short sentences. "
        "Mention its purpose, important classes/functions, and whether it defines models, config, APIs, tests, or docs.\n\n"
        f"Path: {rel_path}\nLanguage: {language}\nSymbols: {', '.join(symbols[:30])}\n\n"
        f"Code excerpt:\n{source[:6000]}"
    )
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(os.getenv("INDEX_CONTEXT_TIMEOUT", "20"))) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            summary = str(data.get("response") or "").strip()
            return " ".join(summary.split())[:900] or None
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Ollama context summary failed for %s; falling back to heuristic: %s", rel_path, exc)
        return None


def _infer_chunk_role(chunk: CodeChunk) -> str:
    """Light role tag for retrieval headers and a future mode prerouter."""
    path = (chunk.file_path or "").lower().replace("\\", "/")
    name = (chunk.name or "").lower()
    if (
        path.endswith((".md", ".rst", ".txt"))
        or "/docs/" in f"/{path}/"
        or path.startswith("docs/")
        or "readme" in path
    ):
        return "docs"
    if (
        "/tests/" in f"/{path}/"
        or path.startswith("tests/")
        or path.endswith(("_test.py", ".test.js", ".test.ts", "_spec.py"))
        or name.startswith("test_")
        or name.endswith("_test")
    ):
        return "test"
    if (
        "config" in path
        or "settings" in path
        or path.endswith((".yaml", ".yml", ".toml", ".ini", ".env", ".json"))
        or name in {"config", "settings", "configuration"}
    ):
        return "config"
    if chunk.chunk_type == "route" or "/api/" in f"/{path}/" or path.endswith("/api.py"):
        return "api"
    if chunk.chunk_type in {"function", "class"} and chunk.name:
        return "definition"
    return "other"


def _truncate_situating(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= SITUATING_MAX_CHARS:
        return cleaned
    return cleaned[: SITUATING_MAX_CHARS - 1].rstrip() + "…"


def _heuristic_situating_text(chunk: CodeChunk, file_summary: str) -> str:
    """
    Chunk-specific NL context (Anthropic Contextual Retrieval spirit).
    Differs across chunks in the same file via name / type / deps / span.
    """
    qualified = chunk.qualified_name or chunk.name or "(file block)"
    deps = ", ".join(chunk.dependencies[:8]) if chunk.dependencies else "none listed"
    role = chunk.chunk_role or "other"
    purpose = {
        "definition": "defines a named code unit",
        "api": "exposes an API/route handler",
        "config": "holds configuration or settings",
        "test": "contains tests",
        "docs": "is documentation",
        "other": "is a source block",
    }.get(role, "is a source block")
    base = (
        f"This chunk {purpose}: {chunk.chunk_type} {qualified} "
        f"(lines {chunk.start_line}-{chunk.end_line}) in {chunk.file_path}. "
        f"Role={role}. Uses dependencies: {deps}."
    )
    # Light file backdrop without replacing chunk-specific text.
    backdrop = (file_summary or "").strip()
    if backdrop:
        base = f"{base} File context: {backdrop[:220]}"
    return _truncate_situating(base)


def _ollama_situating_text(chunk: CodeChunk, whole_document: str) -> Optional[str]:
    """Optional Anthropic-style situating prompt (INDEX_CONTEXT_OLLAMA=1)."""
    if os.getenv("INDEX_CONTEXT_OLLAMA", "0").strip().lower() not in {"1", "true", "yes"}:
        return None

    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("INDEX_CONTEXT_MODEL", os.getenv("OLLAMA_MODEL", "llama3.1"))
    prompt = (
        "<document>\n"
        f"{whole_document[:8000]}\n"
        "</document>\n"
        "Here is the chunk we want to situate within the whole document\n"
        "<chunk>\n"
        f"{(chunk.code or '')[:4000]}\n"
        "</chunk>\n"
        "Please give a short succinct context to situate this chunk within the overall "
        "document for improving search retrieval. Answer only with the succinct context."
    )
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(os.getenv("INDEX_CONTEXT_TIMEOUT", "20"))) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            text = str(data.get("response") or "").strip()
            return _truncate_situating(text) or None
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Ollama situating failed for %s:%s; using heuristic: %s",
            chunk.file_path,
            chunk.name,
            exc,
        )
        return None


def _build_context_header(chunk: CodeChunk) -> str:
    """NL header from ContextMeta fields (single source of truth for embed+BM25)."""
    symbol_text = ", ".join(chunk.symbols[:24]) if chunk.symbols else "none"
    qualified = chunk.qualified_name or chunk.name or "(file block)"
    deps_text = ", ".join(chunk.dependencies[:12]) if chunk.dependencies else "none"
    situating = (chunk.situating_text or "").strip() or "(none)"
    return "\n".join(
        [
            f"Path: {chunk.file_path}",
            f"Language: {chunk.language}",
            f"Chunk: {chunk.chunk_type} {qualified} lines {chunk.start_line}-{chunk.end_line}",
            f"Role: {chunk.chunk_role or 'other'}",
            f"Situating: {situating}",
            f"File summary: {chunk.file_summary}",
            f"File symbols: {symbol_text}",
            f"Dependencies: {deps_text}",
        ]
    )


def tokenize_for_bm25(text: str) -> list[str]:
    """Small shared tokenizer for lexical retrieval."""
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", (text or "").lower())


# ─── repository scanner ───────────────────────────────────────────────────────
def scan_repository(
    repo_path: str | Path,
    languages: Optional[list[str]] = None,
    include_dirs: Optional[list[str]] = None,
) -> list[Path]:
    repo_path = Path(repo_path).resolve()
    if not repo_path.exists():
        raise FileNotFoundError(f"Repository path not found: {repo_path}")

    if languages:
        allowed_exts: set[str] = set()
        for lang in languages:
            allowed_exts.update(LANGUAGE_MAP.get(lang.lower(), []))
    else:
        allowed_exts = {ext for exts in LANGUAGE_MAP.values() for ext in exts}

    roots = [repo_path]
    if include_dirs:
        roots = []
        for rel in include_dirs:
            candidate = (repo_path / rel).resolve()
            if candidate.exists() and repo_path in candidate.parents or candidate == repo_path:
                roots.append(candidate)
            else:
                logger.warning("Skipping include_dir outside repo or missing: %s", rel)

    found: list[Path] = []
    for scan_root in roots:
        for root, dirs, files in os.walk(scan_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for fname in files:
                fpath = Path(root) / fname
                if fpath.suffix.lower() in allowed_exts:
                    found.append(fpath)

    found = sorted(set(found))
    logger.info("Scanned %s → %d files found", repo_path, len(found))
    return found


# ─── file reader + chunker ────────────────────────────────────────────────────
def read_and_chunk_file(file_path: Path, repo_root: Path) -> list[CodeChunk]:
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Cannot read %s: %s", file_path, exc)
        return []

    rel_path = str(file_path.relative_to(repo_root))
    language = _detect_language(file_path) or "unknown"
    f_hash   = _sha256(source)
    file_symbols = _extract_symbols(source, language)
    file_summary = (
        _ollama_file_summary(rel_path, language, source, file_symbols)
        or _heuristic_file_summary(rel_path, language, source, file_symbols)
    )

    if language == "python":
        chunks = _chunk_python(source, rel_path, language, f_hash)
    else:
        chunks = _chunk_generic(source, rel_path, language, f_hash)

    if not chunks:
        chunk_id = _sha256(rel_path + ":0")
        chunks = [CodeChunk(
            chunk_id   = chunk_id,
            file_path  = rel_path,
            language   = language,
            chunk_type = "block",
            name       = "",
            start_line = 1,
            end_line   = source.count("\n") + 1,
            code       = source[:MAX_CHUNK_TOKENS * 6],
            file_hash  = f_hash,
        )]

    all_imports = _extract_imports(source, language)
    for chunk in chunks:
        chunk.dependencies = _find_used_imports(chunk.code, all_imports)
        chunk.file_summary = file_summary
        chunk.qualified_name = f"{rel_path}::{chunk.name}" if chunk.name else rel_path
        chunk.symbols = file_symbols
        chunk.chunk_role = _infer_chunk_role(chunk)
        # Prefer optional Ollama situating; always fall back to chunk-specific heuristic.
        chunk.situating_text = (
            _ollama_situating_text(chunk, source)
            or _heuristic_situating_text(chunk, file_summary)
        )
        chunk.context_header = _build_context_header(chunk)

    logger.debug("Chunked %s → %d chunk(s)", rel_path, len(chunks))
    return chunks


# ─── Python AST chunker ───────────────────────────────────────────────────────
def _chunk_python(source: str, rel_path: str, language: str, f_hash: str) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    lines  = source.splitlines(keepends=True)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.warning("Python parse error in %s: %s", rel_path, exc)
        return []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        start = node.lineno
        end   = getattr(node, "end_lineno", start)
        code  = "".join(lines[start - 1: end])

        chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"

        # FIX 3: include decorator lines when checking for API routes.
        # node.lineno points to the "def" line; decorators sit on the lines
        # immediately above it. Collect them so _is_api_route() can see them.
        decorator_start = start
        if hasattr(node, "decorator_list") and node.decorator_list:
            decorator_start = node.decorator_list[0].lineno
        decorator_lines = "".join(lines[decorator_start - 1: start - 1])

        if _is_api_route(decorator_lines):
            chunk_type = "route"

        chunk_id = _sha256(f"{rel_path}:{start}")
        chunks.append(CodeChunk(
            chunk_id   = chunk_id,
            file_path  = rel_path,
            language   = language,
            chunk_type = chunk_type,
            name       = node.name,
            start_line = start,
            end_line   = end,
            code       = code,
            file_hash  = f_hash,
        ))

    return chunks


# ─── generic heuristic chunker ────────────────────────────────────────────────
_FUNC_PATTERNS = [
    re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M),
    re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", re.M),
    re.compile(r"^\s*(?:public|private|protected|static|override)?\s*\w[\w<>\[\]]*\s+(\w+)\s*\(", re.M),
    re.compile(r"^func\s+(\w+)\s*\(", re.M),
    re.compile(r"^(?:export\s+)?(?:abstract\s+)?(?:class|struct|interface|enum)\s+(\w+)", re.M),
]

def _chunk_generic(source: str, rel_path: str, language: str, f_hash: str) -> list[CodeChunk]:
    lines  = source.splitlines(keepends=True)
    n      = len(lines)
    chunks : list[CodeChunk] = []
    starts : list[tuple[int, str, str]] = []

    for pat in _FUNC_PATTERNS:
        for m in pat.finditer(source):
            lineno = source[:m.start()].count("\n")
            name   = m.group(1) if m.lastindex else ""
            ctype  = "route" if _is_api_route(lines[lineno]) else \
                     "class" if re.search(r"\b(class|struct|interface|enum)\b", m.group(0)) else \
                     "function"
            starts.append((lineno, name, ctype))

    if not starts:
        return []

    starts = sorted(set(starts), key=lambda x: x[0])

    for i, (s, name, ctype) in enumerate(starts):
        e = starts[i + 1][0] if i + 1 < len(starts) else n
        e = min(e, s + MAX_CHUNK_TOKENS)

        code     = "".join(lines[s:e])
        chunk_id = _sha256(f"{rel_path}:{s + 1}")
        chunks.append(CodeChunk(
            chunk_id   = chunk_id,
            file_path  = rel_path,
            language   = language,
            chunk_type = ctype,
            name       = name,
            start_line = s + 1,
            end_line   = e,
            code       = code,
            file_hash  = f_hash,
        ))

    return chunks


# ─── dependency / import extraction ──────────────────────────────────────────
def _extract_imports(source: str, language: str) -> list[str]:
    """Return all imported names visible in the file."""
    names: list[str] = []

    if language == "python":
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.extend(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        names.append(node.module.split(".")[0])
                    names.extend(alias.name for alias in node.names)
        except SyntaxError:
            pass

    else:
        # FIX 2: handle all three ES module import styles plus require().
        #
        # Style A — default import:   import axios from 'axios'
        # Style B — named imports:    import { useState, useEffect } from 'react'
        # Style C — side-effect only: import 'some-polyfill'   (no binding, skip)
        # Style D — require:          const x = require('module')
        #
        # The old single regex matched Style C / require but missed A and B
        # because it expected the quote to come directly after import/require.
        # The new approach uses two passes:
        #   Pass 1: "... from 'pkg'" — covers styles A and B
        #   Pass 2: require('pkg')   — unchanged

        # Pass 1: any import statement ending with  from 'package-name'
        for m in re.finditer(r"""\bfrom\s+['"]([^'"]+)['"]""", source):
            pkg = m.group(1).split("/")[-1]   # take last segment of scoped pkg
            # strip @scope prefix if present: "@prisma/client" → "client"
            names.append(pkg)

        # Pass 2: require('package-name')
        for m in re.finditer(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""", source):
            pkg = m.group(1).split("/")[-1]
            names.append(pkg)

    return list(set(names))


def _find_used_imports(code: str, all_imports: list[str]) -> list[str]:
    """Return the subset of imports actually referenced in this chunk."""
    return [imp for imp in all_imports if re.search(r"\b" + re.escape(imp) + r"\b", code)]


def _is_api_route(code: str) -> bool:
    # FIX 1: A line that starts with # is a comment — never a real decorator.
    # Strip out comment-only lines before running the pattern match so that
    # "# @app.get('/users')  ← example" does not produce a false positive.
    non_comment_lines = "\n".join(
        line for line in code.splitlines()
        if not line.lstrip().startswith("#")
    )
    return bool(re.search(
        r"@(?:app|router|blueprint)\.\s*(?:get|post|put|delete|patch|route)"
        r"|app\.(get|post|put|delete|patch)\s*\(",
        non_comment_lines, re.I,
    ))


# ─── CodeIndexer ─────────────────────────────────────────────────────────────
class CodeIndexer:
    """
    Main indexer class.

    Usage:
        indexer = CodeIndexer()
        indexer.index_repository("./my_project", languages=["python", "javascript"])
        results = indexer.query("function that handles authentication", top_k=5)
    """

    def __init__(
        self,
        db_path:         str = DEFAULT_DB_PATH,
        collection_name: str = DEFAULT_COLLECTION,
        meta_collection_name: str = DEFAULT_META_COLLECTION,
        model_name:      str = DEFAULT_MODEL,
        use_graphcodebert: bool = False,
        use_hybrid_embeddings: Optional[bool] = None,
    ):
        if not CHROMADB_AVAILABLE:
            raise RuntimeError("chromadb is required. Install: pip install chromadb")
        if not SBERT_AVAILABLE:
            raise RuntimeError("sentence-transformers is required. Install: pip install sentence-transformers")

        try:
            import torch
            torch.set_num_threads(max(1, int(os.getenv("INDEX_CPU_THREADS", "2"))))
            torch.set_num_interop_threads(1)
        except Exception:
            pass

        self.model = get_sentence_transformer(model_name)
        self.model_name = model_name
        self.use_hybrid_embeddings = (
            USE_HYBRID_EMBEDDINGS if use_hybrid_embeddings is None else bool(use_hybrid_embeddings)
        )

        # FIX 4: store db_path on self so _cache_path() can use it instead of
        # the hardcoded DEFAULT_DB_PATH constant.
        self.db_path = db_path
        self.collection_name = collection_name
        self.meta_collection_name = meta_collection_name

        logger.info("Connecting to ChromaDB at: %s", db_path)
        self.client     = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name     = collection_name,
            metadata = {"hnsw:space": "cosine"},
        )
        self.meta_collection = self.client.get_or_create_collection(
            name     = meta_collection_name,
            metadata = {"hnsw:space": "cosine", "channel": "meta"},
        )
        logger.info(
            "Collections ready: '%s' (%d docs), '%s' (%d docs)",
            collection_name,
            self.collection.count(),
            meta_collection_name,
            self.meta_collection.count(),
        )

        self._hash_cache: dict[str, str] = self._load_hash_cache()
        self._embedding_cache: dict = self._load_embedding_cache()
        self.hybrid_manager: Optional[HybridEmbeddingManager] = None
        if self.use_hybrid_embeddings:
            self.hybrid_manager = HybridEmbeddingManager(
                semantic_model=self.model,
                use_graphcodebert=use_graphcodebert,
            )
            logger.info("Hybrid embeddings enabled (semantic + call-graph structural) on v_code")

    # ── index repository ──────────────────────────────────────────────────────
    def index_repository(
        self,
        repo_path:   str | Path,
        languages:   Optional[list[str]] = None,
        incremental: bool = True,
        max_files:   Optional[int] = DEFAULT_INDEX_MAX_FILES,
        rebuild_bm25: bool = False,
        include_dirs: Optional[list[str]] = None,
    ) -> dict:
        repo_path = Path(repo_path).resolve()
        files     = scan_repository(repo_path, languages=languages, include_dirs=include_dirs)
        stats = {
            "files_scanned": len(files),
            "files_considered": 0,
            "files_indexed": 0,
            "chunks_added": 0,
            "chunks_skipped": 0,
            "files_skipped": 0,
            "max_files": max_files,
            "limited_run": bool(max_files and max_files > 0),
            "include_dirs": include_dirs or [],
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "hybrid_embeddings": bool(self.hybrid_manager),
            "dual_collections": True,
            "meta_collection": self.meta_collection_name,
            "dense_alpha": DENSE_ALPHA,
            "graph_nodes": 0,
            "graph_edges": 0,
            "embeddings_cached": 0,
        }
        bm25_chunks: list[CodeChunk] = []
        pending_chunks: list[CodeChunk] = []

        # Phase 1: chunk (and optionally defer embed until call graph exists)
        for idx, fpath in enumerate(files, 1):
            if max_files and max_files > 0 and stats["files_indexed"] >= max_files:
                break
            stats["files_considered"] += 1
            rel   = str(fpath.relative_to(repo_path))
            fhash = _file_hash(fpath)
            cache_value = f"{INDEX_SCHEMA_VERSION}:{fhash}"

            if incremental and self._hash_cache.get(rel) == cache_value:
                logger.debug("Skipping unchanged file: %s", rel)
                stats["files_skipped"] += 1
                continue

            chunks = read_and_chunk_file(fpath, repo_path)
            if not chunks:
                continue

            if self.hybrid_manager is None:
                self._upsert_chunks(chunks)
            else:
                pending_chunks.extend(chunks)
            bm25_chunks.extend(chunks)
            self._hash_cache[rel] = cache_value
            stats["files_indexed"] += 1
            stats["chunks_added"]  += len(chunks)
            if idx % DEFAULT_CACHE_SAVE_INTERVAL == 0:
                self._save_hash_cache()
                self._save_embedding_cache()

        # Phase 2: call graph + hybrid embed (when enabled)
        if self.hybrid_manager is not None:
            graph = self._build_call_graph(repo_path, languages=languages, include_dirs=include_dirs)
            self.hybrid_manager.set_call_graph(graph)
            stats["graph_nodes"] = len(graph.nodes)
            stats["graph_edges"] = sum(
                len(n.calls) + len(n.imports) + len(n.inherits_from)
                for n in graph.nodes.values()
            )
            self._save_call_graph(graph)
            if pending_chunks:
                self._upsert_chunks(pending_chunks)
                stats["embeddings_cached"] = sum(
                    1 for c in pending_chunks if c.chunk_id in self._embedding_cache
                )

        stats["bm25_chunks"] = (
            self._rebuild_bm25_index()
            if rebuild_bm25
            else self._update_bm25_index(bm25_chunks)
        )
        self._save_hash_cache()
        self._save_embedding_cache()
        logger.info("Indexing complete: %s", stats)
        return stats

    # ── embed + upsert ────────────────────────────────────────────────────────
    def _cache_key(self, chunk_id: str, channel: str) -> str:
        return f"{INDEX_SCHEMA_VERSION}:{channel}:{chunk_id}"

    def _encode_code_vector(self, chunk: CodeChunk) -> list[float]:
        """v_code: raw code (+ optional structural hybrid)."""
        cache_key = self._cache_key(chunk.chunk_id, "code")
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]
        code = chunk.code_document()
        if self.hybrid_manager is not None:
            result = self.hybrid_manager.embed_code(
                code=code,
                file_path=chunk.file_path,
                func_name=chunk.name,
            )
            vec_obj = result.get("combined")
            if vec_obj is None:
                vec_obj = self.model.encode([code])[0]
        else:
            vec_obj = self.model.encode([code])[0]
        vec = vec_obj.tolist() if hasattr(vec_obj, "tolist") else list(vec_obj)
        self._embedding_cache[cache_key] = vec
        return vec

    def _encode_meta_vector(self, chunk: CodeChunk) -> list[float]:
        """v_meta: NL ContextMeta header / situating only."""
        cache_key = self._cache_key(chunk.chunk_id, "meta")
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]
        meta_doc = chunk.meta_document()
        vec_obj = self.model.encode([meta_doc])[0]
        vec = vec_obj.tolist() if hasattr(vec_obj, "tolist") else list(vec_obj)
        self._embedding_cache[cache_key] = vec
        return vec

    def _upsert_chunks(self, chunks: list[CodeChunk]):
        """
        Twin upsert keyed by the same chunk_id:
          code_embeddings      → document=contextual (display/exact), embedding=v_code
          code_meta_embeddings → document=meta NL only, embedding=v_meta
        BM25 still indexes contextual_document separately.
        """
        ids = [c.chunk_id for c in chunks]
        contextual_docs = [c.contextual_document() for c in chunks]
        meta_docs = [c.meta_document() for c in chunks]
        metadatas = [c.to_metadata() for c in chunks]

        logger.info("Dual-embedding %d chunk(s) (v_code + v_meta)...", len(chunks))
        batch_size = max(1, DEFAULT_EMBED_BATCH_SIZE)
        for start in range(0, len(chunks), batch_size):
            end = start + batch_size
            batch_chunks = chunks[start:end]
            code_embeddings = [self._encode_code_vector(c) for c in batch_chunks]
            meta_embeddings = [self._encode_meta_vector(c) for c in batch_chunks]

            self.collection.upsert(
                ids=ids[start:end],
                documents=contextual_docs[start:end],
                embeddings=code_embeddings,
                metadatas=metadatas[start:end],
            )
            self.meta_collection.upsert(
                ids=ids[start:end],
                documents=meta_docs[start:end],
                embeddings=meta_embeddings,
                metadatas=metadatas[start:end],
            )

    # ── query ─────────────────────────────────────────────────────────────────
    def query(
        self,
        query_text: str,
        top_k:      int = 5,
        language:   Optional[str] = None,
    ) -> list[dict]:
        query_vec = self.model.encode([query_text]).tolist()
        where     = {"language": language} if language else None

        results = self.collection.query(
            query_embeddings = query_vec,
            n_results        = top_k,
            where            = where,
        )

        output = []
        if results["documents"]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                output.append({
                    "code":         doc,
                    "file_path":    meta.get("file_path", ""),
                    "language":     meta.get("language",  ""),
                    "chunk_type":   meta.get("chunk_type", ""),
                    "name":         meta.get("name",      ""),
                    "start_line":   meta.get("start_line", 0),
                    "end_line":     meta.get("end_line",   0),
                    "dependencies": json.loads(meta.get("dependencies", "[]")),
                    "distance":     dist,
                })
        return output

    # ── incremental cache ─────────────────────────────────────────────────────
    def _cache_path(self) -> Path:
        # FIX 4: was Path(DEFAULT_DB_PATH) / ".index_cache.json"
        # which always wrote to ./chroma_db/ regardless of --db flag.
        # Now uses self.db_path so the cache lives next to the actual DB.
        return Path(self.db_path) / ".index_cache.json"

    def _bm25_path(self) -> Path:
        return Path(self.db_path) / "bm25_index.json"

    def _load_hash_cache(self) -> dict[str, str]:
        p = self._cache_path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
        return {}

    def _save_hash_cache(self):
        p = self._cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._hash_cache, indent=2))

    def _embedding_cache_path(self) -> Path:
        return Path(self.db_path) / EMBEDDING_CACHE_FILE

    def _load_embedding_cache(self) -> dict:
        p = self._embedding_cache_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not read embedding cache at %s: %s", p, exc)
        return {}

    def _save_embedding_cache(self) -> None:
        if not getattr(self, "_embedding_cache", None):
            return
        p = self._embedding_cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._embedding_cache), encoding="utf-8")

    def _build_call_graph(
        self,
        repo_path: str | Path,
        languages: Optional[list[str]] = None,
        include_dirs: Optional[list[str]] = None,
    ) -> CallGraph:
        files = scan_repository(repo_path, languages=languages, include_dirs=include_dirs)
        builder = CallGraphBuilder()
        logger.info("Building call graph over %d file(s)...", len(files))
        return builder.build_from_files([str(p) for p in files], str(repo_path))

    def _save_call_graph(self, graph: CallGraph) -> None:
        out = Path(self.db_path) / "call_graph.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        graph.save(str(out))
        logger.info("Call graph saved -> %s (%d nodes)", out, len(graph.nodes))

    def _bm25_record(self, chunk: CodeChunk) -> dict:
        doc = chunk.contextual_document()
        return {
            "chunk_id": chunk.chunk_id,
            "document": doc,
            "tokens": tokenize_for_bm25(doc),
            "metadata": {
                "file_path": chunk.file_path,
                "name": chunk.name,
                "language": chunk.language,
                "chunk_type": chunk.chunk_type,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "chunk_id": chunk.chunk_id,
                "qualified_name": chunk.qualified_name,
                "symbols": json.dumps(chunk.symbols),
                "dependencies": json.dumps(chunk.dependencies),
                "file_summary": chunk.file_summary,
                "situating_text": chunk.situating_text,
                "chunk_role": chunk.chunk_role,
                "context_header": chunk.context_header,
            },
        }

    def _load_bm25_payload(self) -> dict:
        p = self._bm25_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not read BM25 index at %s; rebuilding payload: %s", p, exc)
        return {
            "version": 1,
            "model": self.model_name,
            "collection": DEFAULT_COLLECTION,
            "chunks": [],
        }

    def _save_bm25_payload(self, payload: dict) -> int:
        p = self._bm25_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload), encoding="utf-8")
        return len(payload.get("chunks", []) or [])

    def _update_bm25_index(self, chunks: list[CodeChunk]) -> int:
        """Update BM25 only for chunks touched in this run."""
        if not chunks:
            return len(self._load_bm25_payload().get("chunks", []) or [])

        payload = self._load_bm25_payload()
        existing = {
            row.get("chunk_id"): row
            for row in payload.get("chunks", []) or []
            if row.get("chunk_id")
        }
        for chunk in chunks:
            existing[chunk.chunk_id] = self._bm25_record(chunk)

        payload["version"] = 1
        payload["model"] = self.model_name
        payload["collection"] = DEFAULT_COLLECTION
        payload["chunks"] = list(existing.values())
        total = self._save_bm25_payload(payload)
        logger.info("BM25 index updated at %s (%d chunks)", self._bm25_path(), total)
        return total

    def _rebuild_bm25_index(self) -> int:
        """
        Persist a lexical corpus next to Chroma.

        The retriever constructs the BM25 object at query time from this compact
        JSON so indexing owns freshness and retrieval stays dependency-light.
        """
        rows = self.collection.get(include=["documents", "metadatas"])
        docs = rows.get("documents", []) or []
        metas = rows.get("metadatas", []) or []
        records = []
        for doc, meta in zip(docs, metas):
            chunk_id = meta.get("chunk_id") or _sha256(
                f"{meta.get('file_path', '')}:{meta.get('start_line', 0)}"
            )
            records.append({
                "chunk_id": chunk_id,
                "document": doc,
                "tokens": tokenize_for_bm25(doc),
                "metadata": {
                    "file_path": meta.get("file_path", ""),
                    "name": meta.get("name", ""),
                    "language": meta.get("language", ""),
                    "chunk_type": meta.get("chunk_type", ""),
                    "start_line": meta.get("start_line", 0),
                    "end_line": meta.get("end_line", 0),
                },
            })

        payload = {
            "version": 1,
            "model": self.model_name,
            "collection": DEFAULT_COLLECTION,
            "chunks": records,
        }
        self._save_bm25_payload(payload)
        logger.info("BM25 index rebuilt at %s (%d chunks)", self._bm25_path(), len(records))
        return len(records)

    # ── stats ─────────────────────────────────────────────────────────────────
    def index_stats(self) -> dict:
        total   = self.collection.count()
        results = self.collection.get(include=["metadatas"])
        langs, types = {}, {}
        for m in results.get("metadatas", []):
            l = m.get("language",   "?"); langs[l]  = langs.get(l,  0) + 1
            t = m.get("chunk_type", "?"); types[t]  = types.get(t,  0) + 1
        return {
            "total_chunks":  total,
            "by_language":   langs,
            "by_chunk_type": types,
            "model":         self.model_name,
            "bm25_path":     str(self._bm25_path()),
            "bm25_exists":   self._bm25_path().exists(),
        }


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, argparse

    parser = argparse.ArgumentParser(description="P2 – Code Indexer")
    sub    = parser.add_subparsers(dest="command")

    idx = sub.add_parser("index", help="Index a codebase")
    idx.add_argument("repo")
    idx.add_argument("--languages", "-l", nargs="+")
    idx.add_argument("--no-incremental", action="store_true")
    idx.add_argument("--db",         default=DEFAULT_DB_PATH)
    idx.add_argument("--collection", default=DEFAULT_COLLECTION)
    idx.add_argument("--max-files", type=int, default=DEFAULT_INDEX_MAX_FILES,
                     help="Limit files processed in this run; use 0 for all files.")
    idx.add_argument("--rebuild-bm25", action="store_true",
                     help="Rebuild BM25 from all Chroma documents instead of updating touched chunks.")
    idx.add_argument("--include-dir", action="append", dest="include_dirs",
                     help="Repo-relative directory to scan. Repeat for multiple source roots.")

    qry = sub.add_parser("query", help="Query the index")
    qry.add_argument("text")
    qry.add_argument("--top-k", "-k", type=int, default=5)
    qry.add_argument("--language")
    qry.add_argument("--db",         default=DEFAULT_DB_PATH)
    qry.add_argument("--collection", default=DEFAULT_COLLECTION)

    sts = sub.add_parser("stats", help="Show index statistics")
    sts.add_argument("--db",         default=DEFAULT_DB_PATH)
    sts.add_argument("--collection", default=DEFAULT_COLLECTION)

    args = parser.parse_args()

    if args.command == "index":
        indexer = CodeIndexer(db_path=args.db, collection_name=args.collection)
        stats   = indexer.index_repository(
            args.repo,
            languages   = args.languages,
            incremental = not args.no_incremental,
            max_files   = args.max_files or None,
            rebuild_bm25 = args.rebuild_bm25,
            include_dirs = args.include_dirs,
        )
        print(json.dumps(stats, indent=2))

    elif args.command == "query":
        indexer = CodeIndexer(db_path=args.db, collection_name=args.collection)
        results = indexer.query(args.text, top_k=args.top_k, language=args.language)
        for i, r in enumerate(results, 1):
            print(f"\n{'─'*60}")
            print(f"[{i}] {r['file_path']} :: {r['name']} ({r['chunk_type']})  "
                  f"lines {r['start_line']}–{r['end_line']}  dist={r['distance']:.4f}")
            print(r["code"][:400])

    elif args.command == "stats":
        indexer = CodeIndexer(db_path=args.db, collection_name=args.collection)
        print(json.dumps(indexer.index_stats(), indent=2))

    else:
        parser.print_help()
        sys.exit(1)
        
