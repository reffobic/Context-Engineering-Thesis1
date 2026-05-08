"""
code_indexer.py — key changes only (show as replacements in your file).

CHANGES:
  1. index_repository() now does TWO passes:
       pass 1 → chunk all files, collect in memory
       pass 2 → build call graph
       pass 3 → embed (combined) + upsert
  2. _upsert_chunks() uses HybridEmbeddingManager instead of raw sentence model
  3. query() encodes with semantic model only (ChromaDB still does vector search;
     structural signal enters at re-rank time via enhance_ranking_with_structure)
  4. build_call_graph() returns the CallGraph object so indexer can pass it to
     HybridEmbeddingManager before embedding starts.
"""

import os, json, hashlib, logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import chromadb
from sentence_transformers import SentenceTransformer

from .graph_builder import CallGraphBuilder, CallGraph, LANG_EXT
from .graph_embeddings import HybridEmbeddingManager

logger = logging.getLogger("code_indexer")

CHROMA_PATH           = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME       = "code_embeddings"
EMBEDDING_MODEL       = "all-MiniLM-L6-v2"
MAX_CHUNK_TOKENS      = 512
CACHE_FILE            = ".index_cache.json"
EMBEDDING_CACHE_FILE  = ".embedding_cache.json"


# ── CodeChunk (unchanged) ────────────────────────────────────────────────────

@dataclass
class CodeChunk:
    chunk_id: str
    file_path: str
    language: str
    chunk_type: str
    name: str
    start_line: int
    end_line: int
    code: str
    dependencies: list
    file_hash: str

    def to_metadata(self) -> dict:
        return {
            "chunk_id":    self.chunk_id,
            "file_path":   self.file_path,
            "language":    self.language,
            "chunk_type":  self.chunk_type,
            "name":        self.name,
            "start_line":  self.start_line,
            "end_line":    self.end_line,
            "dependencies": json.dumps(self.dependencies),
            "file_hash":   self.file_hash,
        }


# ── CodeIndexer ──────────────────────────────────────────────────────────────

class CodeIndexer:

    def __init__(
        self,
        db_path:         str  = CHROMA_PATH,
        collection_name: str  = COLLECTION_NAME,
        model_name:      str  = EMBEDDING_MODEL,
        use_graphcodebert: bool = False,   # set True if you have GPU + transformers
    ):
        self.db_path    = db_path
        self.client     = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        # Semantic model (shared with HybridEmbeddingManager)
        self.semantic_model = SentenceTransformer(model_name)

        # Hybrid manager — call graph set AFTER graph is built
        self.hybrid_manager = HybridEmbeddingManager(
            semantic_model=self.semantic_model,
            use_graphcodebert=use_graphcodebert,
        )

        # File-level cache (chunk state tracking)
        cache_path = Path(db_path) / CACHE_FILE
        self._cache: dict = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except Exception:
                pass
        self._cache_path = cache_path

        # Embedding-level cache (precomputed 384D vectors)
        embedding_cache_path = Path(db_path) / EMBEDDING_CACHE_FILE
        self._embedding_cache: dict = {}  # chunk_id -> [384D vector as list]
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
        """
        Three-phase indexing:
          1. Chunk all files → memory
          2. Build call graph (graph metrics now available)
          3. Compute combined embeddings → upsert ChromaDB
        """
        repo = Path(repo_path)
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

        # ── Phase 1: chunk ────────────────────────────────────────────────
        all_chunks:  list[CodeChunk] = []
        skip_hashes: set[str]        = set()

        for fp in files:
            fhash = _file_hash(fp)
            if incremental and self._cache.get(str(fp)) == fhash:
                stats["chunks_skipped"] += 1
                skip_hashes.add(str(fp))
                continue
            chunks = read_and_chunk_file(fp, repo)
            if chunks:
                all_chunks.extend(chunks)
                self._cache[str(fp)] = fhash
                stats["files_indexed"] += 1

        # ── Phase 2: build call graph (ALL languages now) ─────────────────
        graph = self._build_call_graph(repo_path)
        self.hybrid_manager.set_call_graph(graph)          # ← wire it in
        stats["graph_nodes"] = len(graph.nodes)
        stats["graph_edges"] = sum(
            len(n.calls) + len(n.imports) + len(n.inherits_from)
            for n in graph.nodes.values()
        )
        self._save_graph(graph, repo_path)

        # ── Phase 3: embed + upsert ───────────────────────────────────────
        if all_chunks:
            added = self._upsert_chunks(all_chunks)
            stats["chunks_added"] = added
            stats["embeddings_cached"] = len([c for c in all_chunks if c.chunk_id in self._embedding_cache])
            stats["embeddings_computed"] = added - stats["embeddings_cached"]

        self._save_cache()
        return stats

    def query(self, query_text: str, top_k: int = 5, language: Optional[str] = None) -> list[dict]:
        """Vector search — semantic only (structural enters at re-rank in P3)."""
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
        """Embed each chunk with COMBINED (semantic + structural) vector."""
        ids, docs, metas, embeddings = [], [], [], []
        cached_count = 0
        computed_count = 0

        for chunk in chunks:
            # Check embedding cache first
            if chunk.chunk_id in self._embedding_cache:
                vec = self._embedding_cache[chunk.chunk_id]
                cached_count += 1
            else:
                # Compute embedding if not cached
                result = self.hybrid_manager.embed_code(
                    code=chunk.code,
                    file_path=chunk.file_path,
                    func_name=chunk.name,
                )
                vec_obj = result["combined"]
                if vec_obj is None:
                    # Fallback: pure semantic if structural failed
                    vec_obj = self.semantic_model.encode([chunk.code])[0]
                vec = vec_obj.tolist() if hasattr(vec_obj, 'tolist') else vec_obj
                
                # Store in cache for future use
                self._embedding_cache[chunk.chunk_id] = vec
                computed_count += 1

            ids.append(chunk.chunk_id)
            docs.append(chunk.code)
            metas.append(chunk.to_metadata())
            embeddings.append(vec)

        if cached_count > 0:
            logger.info(f"Embedding cache hit: {cached_count} chunks (computed: {computed_count})")

        if ids:
            self.collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=metas,
                embeddings=embeddings,
            )
        return len(ids)

    def _build_call_graph(self, repo_path: str) -> CallGraph:
        """Build call graph for ALL supported languages (not Python-only)."""
        files = scan_repository(repo_path)   # all languages
        builder = CallGraphBuilder()
        graph = builder.build_from_files(files, repo_path)
        return graph

    def _save_graph(self, graph: CallGraph, repo_path: str) -> None:
        out = Path(self.db_path) / "call_graph.json"
        graph.save(str(out))
        logger.info(f"Call graph saved → {out} ({len(graph.nodes)} nodes)")

    def _save_cache(self) -> None:
        """Save both file-level and embedding-level caches."""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save file-level cache (chunk state)
        self._cache_path.write_text(json.dumps(self._cache, indent=2))
        
        # Save embedding-level cache (precomputed vectors)
        self._embedding_cache_path.write_text(json.dumps(self._embedding_cache, indent=2))
        logger.info(f"Caches saved: {len(self._cache)} files, {len(self._embedding_cache)} embeddings")


# ── module-level helpers (unchanged signatures) ───────────────────────────────

def scan_repository(repo_path: str, languages: Optional[list] = None) -> list[Path]:
    SKIP_DIRS = {".git","__pycache__","node_modules",".venv","dist","build",".next"}
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
    out = []
    docs   = results.get("documents",  [[]])[0]
    metas  = results.get("metadatas",  [[]])[0]
    dists  = results.get("distances",  [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({
            "code":       doc,
            "metadata":   meta,
            "similarity": 1 - dist,
        })
    return out


def read_and_chunk_file(file_path: Path, repo_root: Path) -> list[CodeChunk]:
    """Dispatch to language-specific chunker — import from your existing file."""
    # This function is unchanged from your original code_indexer.py.
    # It already handles Python (AST) and generic (regex) chunking.
    # Just make sure it uses LANG_EXT from graph_builder for language detection.
    from .code_indexer import read_and_chunk_file as _orig
    return _orig(file_path, repo_root)