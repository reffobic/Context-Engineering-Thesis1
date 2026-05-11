"""
code_indexer.py — full working version.
"""

import os, re, ast, json, hashlib, logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import chromadb
from sentence_transformers import SentenceTransformer

from .graph_builder import CallGraphBuilder, CallGraph, LANG_EXT
from .graph_embeddings import HybridEmbeddingManager

logger = logging.getLogger("code_indexer")

CHROMA_PATH          = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME      = "code_embeddings"
EMBEDDING_MODEL      = "all-MiniLM-L6-v2"
MAX_CHUNK_TOKENS     = 512
CACHE_FILE           = ".index_cache.json"
EMBEDDING_CACHE_FILE = ".embedding_cache.json"


# ── CodeChunk ────────────────────────────────────────────────────────────────

@dataclass
class CodeChunk:
    chunk_id:     str
    file_path:    str
    language:     str
    chunk_type:   str
    name:         str
    start_line:   int
    end_line:     int
    code:         str
    dependencies: list
    file_hash:    str

    def to_metadata(self) -> dict:
        return {
            "chunk_id":     self.chunk_id,
            "file_path":    self.file_path,
            "language":     self.language,
            "chunk_type":   self.chunk_type,
            "name":         self.name,
            "start_line":   self.start_line,
            "end_line":     self.end_line,
            "dependencies": json.dumps(self.dependencies),
            "file_hash":    self.file_hash,
        }


# ── CodeIndexer ──────────────────────────────────────────────────────────────

class CodeIndexer:

    def __init__(
        self,
        db_path:           str  = CHROMA_PATH,
        collection_name:   str  = COLLECTION_NAME,
        model_name:        str  = EMBEDDING_MODEL,
        use_graphcodebert: bool = False,
    ):
        self.db_path    = db_path
        self.client     = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        self.semantic_model = SentenceTransformer(model_name)

        self.hybrid_manager = HybridEmbeddingManager(
            semantic_model=self.semantic_model,
            use_graphcodebert=use_graphcodebert,
        )

        cache_path = Path(db_path) / CACHE_FILE
        self._cache: dict = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except Exception:
                pass
        self._cache_path = cache_path

        embedding_cache_path = Path(db_path) / EMBEDDING_CACHE_FILE
        self._embedding_cache: dict = {}
        if embedding_cache_path.exists():
            try:
                self._embedding_cache = json.loads(embedding_cache_path.read_text())
            except Exception:
                pass
        self._embedding_cache_path = embedding_cache_path

    # ── public API ───────────────────────────────────────────────────────────

    def index_repository(
        self,
        repo_path:   str,
        languages:   Optional[list] = None,
        incremental: bool = True,
    ) -> dict:
        repo  = Path(repo_path)
        files = scan_repository(repo_path, languages)

        stats = dict(
            files_scanned=len(files),
            files_indexed=0,
            chunks_added=0,
            chunks_skipped=0,
            embeddings_cached=0,
            embeddings_computed=0,
            graph_nodes=0,
            graph_edges=0,
        )

        # Phase 1: chunk
        all_chunks: list[CodeChunk] = []

        for fp in files:
            fhash = _file_hash(fp)
            if incremental and self._cache.get(str(fp)) == fhash:
                stats["chunks_skipped"] += 1
                continue
            chunks = read_and_chunk_file(fp, repo)
            if chunks:
                all_chunks.extend(chunks)
                self._cache[str(fp)] = fhash
                stats["files_indexed"] += 1

        # Phase 2: build call graph
        graph = self._build_call_graph(repo_path)
        self.hybrid_manager.set_call_graph(graph)
        stats["graph_nodes"] = len(graph.nodes)
        stats["graph_edges"] = sum(
            len(n.calls) + len(n.imports) + len(n.inherits_from)
            for n in graph.nodes.values()
        )
        self._save_graph(graph, repo_path)

        # Phase 3: embed + upsert
        if all_chunks:
            added = self._upsert_chunks(all_chunks)
            stats["chunks_added"] = added
            stats["embeddings_cached"]   = len([c for c in all_chunks if c.chunk_id in self._embedding_cache])
            stats["embeddings_computed"] = added - stats["embeddings_cached"]

        self._save_cache()
        return stats

    def query(self, query_text: str, top_k: int = 5, language: Optional[str] = None) -> list[dict]:
        q_emb = self.semantic_model.encode([query_text])[0].tolist()
        where = {"language": language} if language else None
        results = self.collection.query(
            query_embeddings=[q_emb],
            n_results=top_k,
            where=where,
        )
        return _parse_chroma_results(results)

    # ── private helpers ───────────────────────────────────────────────────────

    def _upsert_chunks(self, chunks: list[CodeChunk]) -> int:
        ids, docs, metas, embeddings = [], [], [], []
        cached_count   = 0
        computed_count = 0

        for chunk in chunks:
            if chunk.chunk_id in self._embedding_cache:
                vec = self._embedding_cache[chunk.chunk_id]
                cached_count += 1
            else:
                result  = self.hybrid_manager.embed_code(
                    code=chunk.code,
                    file_path=chunk.file_path,
                    func_name=chunk.name,
                )
                vec_obj = result["combined"]
                if vec_obj is None:
                    vec_obj = self.semantic_model.encode([chunk.code])[0]
                vec = vec_obj.tolist() if hasattr(vec_obj, "tolist") else vec_obj
                self._embedding_cache[chunk.chunk_id] = vec
                computed_count += 1

            ids.append(chunk.chunk_id)
            docs.append(chunk.code)
            metas.append(chunk.to_metadata())
            embeddings.append(vec)

        if cached_count:
            logger.info("Embedding cache hit: %d chunks (computed: %d)", cached_count, computed_count)

        if ids:
            BATCH_SIZE = 5000  # safely under ChromaDB's limit of 5461
            for i in range(0, len(ids), BATCH_SIZE):
                self.collection.upsert(
                    ids=ids[i:i+BATCH_SIZE],
                    documents=docs[i:i+BATCH_SIZE],
                    metadatas=metas[i:i+BATCH_SIZE],
                    embeddings=embeddings[i:i+BATCH_SIZE],
                )
                logger.info("Upserted batch %d-%d of %d", i, min(i+BATCH_SIZE, len(ids)), len(ids))
        return len(ids)

    def _build_call_graph(self, repo_path: str) -> CallGraph:
        files   = scan_repository(repo_path)
        builder = CallGraphBuilder()
        return builder.build_from_files(files, repo_path)

    def _save_graph(self, graph: CallGraph, repo_path: str) -> None:
        out = Path(self.db_path) / "call_graph.json"
        graph.save(str(out))
        logger.info("Call graph saved -> %s (%d nodes)", out, len(graph.nodes))

    def _save_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache, indent=2))
        self._embedding_cache_path.write_text(json.dumps(self._embedding_cache, indent=2))
        logger.info("Caches saved: %d files, %d embeddings", len(self._cache), len(self._embedding_cache))


# ── module-level helpers ──────────────────────────────────────────────────────

def scan_repository(repo_path: str, languages: Optional[list] = None) -> list[Path]:
    SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "dist", "build", ".next"}
    exts = {
        ext for ext, lang in LANG_EXT.items()
        if languages is None or lang in languages
    }
    result = []
    for fp in sorted(Path(repo_path).rglob("*")):
        if any(p in SKIP_DIRS for p in fp.parts):
            continue
        if fp.suffix.lower() in exts:
            result.append(fp)
    return result


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_chroma_results(results: dict) -> list[dict]:
    out   = []
    docs  = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({"code": doc, "metadata": meta, "similarity": 1 - dist})
    return out


# ── read_and_chunk_file ───────────────────────────────────────────────────────

def read_and_chunk_file(file_path: Path, repo_root: Path) -> list[CodeChunk]:
    """
    Chunk a source file into CodeChunk objects.
    - Python: AST-based (functions + classes)
    - Other languages: regex-based
    - Fallback: whole file as single chunk
    """
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning("Cannot read %s: %s", file_path, e)
        return []

    if not source.strip():
        return []

    ext      = file_path.suffix.lower()
    language = LANG_EXT.get(ext, "unknown")
    rel_path = str(file_path.relative_to(repo_root))
    fhash    = _file_hash(file_path)

    if language == "python":
        chunks = _chunk_python(source, rel_path, language, fhash)
    else:
        chunks = _chunk_regex(source, rel_path, language, fhash, ext)

    # Fallback: whole file as one chunk
    if not chunks:
        chunk_id = hashlib.sha256(f"{rel_path}:file".encode()).hexdigest()[:16]
        chunks = [CodeChunk(
            chunk_id=chunk_id,
            file_path=rel_path,
            language=language,
            chunk_type="file",
            name=file_path.name,
            start_line=1,
            end_line=source.count("\n") + 1,
            code=source[:MAX_CHUNK_TOKENS * 4],
            dependencies=[],
            file_hash=fhash,
        )]

    return chunks


def _chunk_python(source: str, rel_path: str, language: str, fhash: str) -> list[CodeChunk]:
    """AST-based chunking for Python — extracts top-level and nested functions/classes."""
    chunks = []
    lines  = source.splitlines()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        start = node.lineno
        end   = getattr(node, "end_lineno", start)
        code  = "\n".join(lines[start - 1:end])

        if len(code) > MAX_CHUNK_TOKENS * 4:
            code = code[:MAX_CHUNK_TOKENS * 4]

        chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
        chunk_id   = hashlib.sha256(f"{rel_path}:{node.name}:{start}".encode()).hexdigest()[:16]

        deps = []
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                deps.extend(alias.name for alias in child.names)
            elif isinstance(child, ast.ImportFrom) and child.module:
                deps.append(child.module)

        chunks.append(CodeChunk(
            chunk_id=chunk_id,
            file_path=rel_path,
            language=language,
            chunk_type=chunk_type,
            name=node.name,
            start_line=start,
            end_line=end,
            code=code,
            dependencies=deps,
            file_hash=fhash,
        ))

    return chunks


# Regex patterns per file extension
_REGEX_PATTERNS: dict[str, re.Pattern] = {
    ".js":    re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),
    ".ts":    re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),
    ".jsx":   re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),
    ".tsx":   re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),
    ".java":  re.compile(r"^\s*(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(", re.MULTILINE),
    ".kt":    re.compile(r"^\s*fun\s+(\w+)\s*\(", re.MULTILINE),
    ".scala": re.compile(r"^\s*def\s+(\w+)\s*[\(\[]", re.MULTILINE),
    ".c":     re.compile(r"^[\w\s\*]+\s+(\w+)\s*\([^;]*\)\s*\{", re.MULTILINE),
    ".cpp":   re.compile(r"^[\w\s\*:~<>]+\s+(\w+)\s*\([^;]*\)\s*\{", re.MULTILINE),
    ".h":     re.compile(r"^[\w\s\*]+\s+(\w+)\s*\([^;]*\)\s*\{", re.MULTILINE),
    ".rb":    re.compile(r"^\s*def\s+(\w+)", re.MULTILINE),
    ".go":    re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", re.MULTILINE),
    ".rs":    re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)\s*[\(<]", re.MULTILINE),
    ".php":   re.compile(r"^\s*(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(", re.MULTILINE),
}
_DEFAULT_PATTERN = re.compile(r"^\s*(?:def|func|function|fn|sub|method)\s+(\w+)", re.MULTILINE)


def _chunk_regex(source: str, rel_path: str, language: str, fhash: str, ext: str) -> list[CodeChunk]:
    """Regex-based chunking for non-Python languages."""
    pattern = _REGEX_PATTERNS.get(ext, _DEFAULT_PATTERN)
    lines   = source.splitlines()
    chunks  = []

    matches = list(pattern.finditer(source))
    for i, match in enumerate(matches):
        name       = match.group(1)
        start_line = source[:match.start()].count("\n") + 1
        end_line   = (
            source[:matches[i + 1].start()].count("\n")
            if i + 1 < len(matches)
            else len(lines)
        )

        code = "\n".join(lines[start_line - 1:end_line])
        if len(code) > MAX_CHUNK_TOKENS * 4:
            code = code[:MAX_CHUNK_TOKENS * 4]

        chunk_id = hashlib.sha256(f"{rel_path}:{name}:{start_line}".encode()).hexdigest()[:16]

        chunks.append(CodeChunk(
            chunk_id=chunk_id,
            file_path=rel_path,
            language=language,
            chunk_type="function",
            name=name,
            start_line=start_line,
            end_line=end_line,
            code=code,
            dependencies=[],
            file_hash=fhash,
        ))

    return chunks