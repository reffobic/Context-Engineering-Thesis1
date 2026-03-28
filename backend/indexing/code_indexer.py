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
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

# ─── third-party ─────────────────────────────────────────────────────────────
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logging.warning("chromadb not installed. Run: pip install chromadb")

try:
    from sentence_transformers import SentenceTransformer
    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False
    logging.warning("sentence-transformers not installed. Run: pip install sentence-transformers")

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
MAX_CHUNK_TOKENS   = 512

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

    def to_metadata(self) -> dict:
        m = asdict(self)
        m.pop("code")
        m["dependencies"] = json.dumps(m["dependencies"])
        return m


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


# ─── repository scanner ───────────────────────────────────────────────────────
def scan_repository(
    repo_path: str | Path,
    languages: Optional[list[str]] = None,
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

    found: list[Path] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for fname in files:
            fpath = Path(root) / fname
            if fpath.suffix.lower() in allowed_exts:
                found.append(fpath)

    found.sort()
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
        model_name:      str = DEFAULT_MODEL,
    ):
        if not CHROMADB_AVAILABLE:
            raise RuntimeError("chromadb is required. Install: pip install chromadb")
        if not SBERT_AVAILABLE:
            raise RuntimeError("sentence-transformers is required. Install: pip install sentence-transformers")

        logger.info("Loading embedding model: %s", model_name)
        self.model      = SentenceTransformer(model_name)
        self.model_name = model_name

        # FIX 4: store db_path on self so _cache_path() can use it instead of
        # the hardcoded DEFAULT_DB_PATH constant.
        self.db_path = db_path

        logger.info("Connecting to ChromaDB at: %s", db_path)
        self.client     = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name     = collection_name,
            metadata = {"hnsw:space": "cosine"},
        )
        logger.info("Collection '%s' ready (%d docs)", collection_name, self.collection.count())

        self._hash_cache: dict[str, str] = self._load_hash_cache()

    # ── index repository ──────────────────────────────────────────────────────
    def index_repository(
        self,
        repo_path:   str | Path,
        languages:   Optional[list[str]] = None,
        incremental: bool = True,
    ) -> dict:
        repo_path = Path(repo_path).resolve()
        files     = scan_repository(repo_path, languages=languages)
        stats = {"files_scanned": len(files), "files_indexed": 0, "chunks_added": 0, "chunks_skipped": 0, "files_skipped": 0}
        
        for fpath in files:
            rel   = str(fpath.relative_to(repo_path))
            fhash = _file_hash(fpath)

            if incremental and self._hash_cache.get(rel) == fhash:
                logger.debug("Skipping unchanged file: %s", rel)
                stats["files_skipped"] += 1
                chunks = read_and_chunk_file(fpath, repo_path)
                stats["chunks_skipped"] += len(chunks)
                continue

            chunks = read_and_chunk_file(fpath, repo_path)
            if not chunks:
                continue

            self._upsert_chunks(chunks)
            self._hash_cache[rel] = fhash
            stats["files_indexed"] += 1
            stats["chunks_added"]  += len(chunks)

        self._save_hash_cache()
        logger.info("Indexing complete: %s", stats)
        return stats

    # ── embed + upsert ────────────────────────────────────────────────────────
    def _upsert_chunks(self, chunks: list[CodeChunk]):
        ids        = [c.chunk_id  for c in chunks]
        documents  = [c.code      for c in chunks]
        metadatas  = [c.to_metadata() for c in chunks]

        logger.info("Embedding %d chunk(s)...", len(chunks))
        embeddings = self.model.encode(documents, show_progress_bar=False).tolist()

        self.collection.upsert(
            ids        = ids,
            documents  = documents,
            embeddings = embeddings,
            metadatas  = metadatas,
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
        
