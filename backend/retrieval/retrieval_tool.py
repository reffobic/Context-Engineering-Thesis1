# retrieval_tool.py
# Person 3 — Full Retrieval Tool

import re
import chromadb
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from indexing.sentence_model import get_sentence_transformer

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHROMA_PATH     = str(Path(__file__).resolve().parent.parent / "chroma_db")
COLLECTION_NAME = "code_embeddings"

# Ranking weights
W_HYBRID    = 0.46
W_PROXIMITY = 0.26
W_FUNCTION  = 0.18
W_SYMBOL    = 0.10  # boost when chunk body contains query’s C-style API symbol

# Long context config
# How many candidates to fetch before re-ranking
CHILD_FETCH_MULTIPLIER = 4
# Minimum vector neighbors before hybrid re-rank (large corpora need wider recall)
MIN_VECTOR_CANDIDATES = 128
# Extra Chroma queries: C-style identifiers (foo_bar) → where_document $contains
MAX_SYMBOL_QUERIES = 5
SYMBOL_QUERY_TOP_K = 45
# Cap on chunks stitched per file — prevents token explosion on huge files
MAX_CONTEXT_CHUNKS_PER_FILE = 10


# ══════════════════════════════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════════════════════════════

client     = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection(COLLECTION_NAME)


# ══════════════════════════════════════════════════
# STEP 1 — vector_retrieve()
# ══════════════════════════════════════════════════

def _c_style_symbols(query: str) -> list[str]:
    """Tokens like svn_fs_commit_txn so we can add Chroma $contains queries."""
    found = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]*_[a-zA-Z0-9_]+\b", query)
    out: list[str] = []
    seen: set[str] = set()
    for w in sorted(set(found), key=len, reverse=True):
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _rows_to_chunks(rows: dict[str, Any]) -> list[dict]:
    chunks: list[dict] = []
    docs = rows["documents"][0] if rows["documents"] else []
    metas = rows["metadatas"][0] if rows["metadatas"] else []
    dists = rows["distances"][0] if rows["distances"] else []
    for doc, meta, dist in zip(docs, metas, dists):
        chunks.append({
            "code": doc,
            "metadata": {
                "file":         meta.get("file_path", ""),
                "function":     meta.get("name", ""),
                "start_line":   meta.get("start_line", 0),
                "end_line":     meta.get("end_line", 0),
                "language":     meta.get("language", ""),
                "chunk_type":   meta.get("chunk_type", ""),
                "dependencies": meta.get("dependencies", "[]"),
                "chunk_id":     meta.get("chunk_id", ""),
            },
            "similarity": round(1 - dist, 4),
        })
    return chunks


def vector_retrieve(
    query: str,
    top_k: int = 5,
    *,
    where_document: Optional[dict] = None,
    query_embedding: Optional[list[float]] = None,
) -> list[dict]:
    model = get_sentence_transformer(EMBEDDING_MODEL)
    qvec = query_embedding if query_embedding is not None else model.encode([query])[0].tolist()

    n_docs = max(collection.count(), 1)
    n_results = min(top_k, n_docs)

    results = collection.query(
        query_embeddings=[qvec],
        n_results=n_results,
        where_document=where_document,
        include=["documents", "metadatas", "distances"],
    )
    return _rows_to_chunks(results)


def vector_retrieve_merged(query: str, top_k: int) -> list[dict]:
    """
    Wide dense retrieval + optional Chroma ``$contains`` passes for underscore
    identifiers (Subversion C APIs, etc.) so symbol-heavy questions are not
    drowned out by unrelated vector neighbors (e.g. tiny Flask samples).
    """
    model = get_sentence_transformer(EMBEDDING_MODEL)
    qvec = model.encode([query])[0].tolist()

    n_docs = max(collection.count(), 1)
    n_vec = min(max(top_k, MIN_VECTOR_CANDIDATES), n_docs)

    candidates = vector_retrieve(query, top_k=n_vec, query_embedding=qvec)
    seen: set[str] = {
        str(c["metadata"].get("chunk_id") or "")
        for c in candidates
        if c["metadata"].get("chunk_id")
    }

    for sym in _c_style_symbols(query)[:MAX_SYMBOL_QUERIES]:
        n_sym = min(SYMBOL_QUERY_TOP_K, n_docs)
        extra = vector_retrieve(
            query,
            top_k=n_sym,
            where_document={"$contains": sym},
            query_embedding=qvec,
        )
        for c in extra:
            cid = str(c["metadata"].get("chunk_id") or "")
            if cid and cid in seen:
                continue
            if cid:
                seen.add(cid)
            candidates.append(c)

    return candidates


# ══════════════════════════════════════════════════
# STEP 2 — keyword_match_score()
# ══════════════════════════════════════════════════

def keyword_match_score(query: str, text: str) -> float:
    query_words = query.lower().split()
    text_lower  = text.lower()
    score = sum(1 for word in query_words if word in text_lower)
    return min(score / max(len(query_words), 1), 1.0)


# ══════════════════════════════════════════════════
# STEP 3 — hybrid_retrieve()
# ══════════════════════════════════════════════════

def hybrid_retrieve(query: str, top_k: int = 5) -> list[dict]:
    candidates = vector_retrieve_merged(query, top_k=top_k * 2)

    scored = []
    for item in candidates:
        doc        = item["code"]
        meta       = item["metadata"]
        similarity = item["similarity"]
        k_score    = keyword_match_score(query, doc)

        hybrid_score = (0.7 * similarity) + (0.3 * k_score)

        scored.append({
            "code":         doc,
            "metadata":     meta,
            "similarity":   similarity,
            "hybrid_score": round(hybrid_score, 4)
        })

    scored.sort(key=lambda x: x["hybrid_score"], reverse=True)
    return scored


# ══════════════════════════════════════════════════
# STEP 4 — rank_chunks()
# ══════════════════════════════════════════════════

def rank_chunks(query: str, candidates: list[dict]) -> list[dict]:
    query_words    = set(query.lower().replace("_", " ").split())
    file_counts    = Counter(item["metadata"]["file"] for item in candidates)
    max_file_count = max(file_counts.values(), default=1)
    query_syms     = [s.lower() for s in _c_style_symbols(query)]

    for item in candidates:
        f1_hybrid    = item["hybrid_score"]
        f2_proximity = file_counts[item["metadata"]["file"]] / max_file_count

        fn_raw      = str(item["metadata"].get("function") or "")
        fn_name     = fn_raw.lower().replace("_", " ")
        fn_words    = set(fn_name.split())
        overlap     = fn_words & query_words
        f3_function = min(len(overlap) / max(len(fn_words), 1), 1.0)

        doc_lower = (item.get("code") or "").lower()
        f_symbol = 0.0
        if query_syms:
            hits = sum(1 for s in query_syms if s in doc_lower)
            f_symbol = min(hits / len(query_syms), 1.0)

        item["final_score"] = round(
            W_HYBRID    * f1_hybrid    +
            W_PROXIMITY * f2_proximity +
            W_FUNCTION  * f3_function +
            W_SYMBOL    * f_symbol,
            4
        )

    return sorted(candidates, key=lambda x: x["final_score"], reverse=True)


# ══════════════════════════════════════════════════════════════════
# STEP 5 — reconstruct_file_context()
# Late Chunking Long Context Retrieval
#
# How it works:
#   1. Take the file_path of each top-ranked matched chunk
#   2. Fetch ALL other chunks from that same file in the DB
#   3. Sort them by start_line — reconstructing file order
#   4. Stitch them together into one long context string
#   5. Attach it to the result as "parent_context"
#
# Why no changes needed from Person 2:
#   Uses file_path (already stored) to group chunks
#   Uses start_line (already stored) to sort them in order
#   Queries the same single collection — no second index needed
#
# What the LLM gets:
#   "code"           → the exact matched snippet (precise)
#   "parent_context" → all chunks from that file stitched in line order
#                      giving the LLM imports, siblings, class structure
# ══════════════════════════════════════════════════════════════════

def reconstruct_file_context(ranked_chunks: list[dict]) -> list[dict]:
    """
    For each matched chunk, fetch all sibling chunks from the same file,
    sort by start_line, and stitch into a single expanded context string.
    Results from the same file share a cached fetch — only one DB call per file.
    """
    file_context_cache = {}  # file_path → stitched string (avoids duplicate fetches)

    for item in ranked_chunks:
        file_path = item["metadata"]["file"]

        if file_path in file_context_cache:
            item["parent_context"] = file_context_cache[file_path]
            continue

        try:
            # Fetch all chunks belonging to this file
            results = collection.get(
                where={"file_path": {"$eq": file_path}},
                include=["documents", "metadatas"]
            )

            if not results["documents"]:
                item["parent_context"] = None
                file_context_cache[file_path] = None
                continue

            # Sort chunks by start_line to reconstruct original file order
            paired = sorted(
                zip(results["documents"], results["metadatas"]),
                key=lambda x: x[1].get("start_line", 0)
            )

            # Cap to avoid token explosion on very large files
            paired = paired[:MAX_CONTEXT_CHUNKS_PER_FILE]

            # Stitch with a header showing chunk type and line range
            stitched_parts = []
            for doc, meta in paired:
                fn_name    = meta.get("name", "block")
                start      = meta.get("start_line", "?")
                end        = meta.get("end_line", "?")
                chunk_type = meta.get("chunk_type", "")
                header     = f"# [{chunk_type}] {fn_name}  (lines {start}–{end})"
                stitched_parts.append(f"{header}\n{doc}")

            stitched = "\n\n".join(stitched_parts)
            item["parent_context"]        = stitched
            file_context_cache[file_path] = stitched

        except Exception as e:
            print(f"[retrieval] Warning: context reconstruction failed for '{file_path}': {e}")
            item["parent_context"] = None
            file_context_cache[file_path] = None

    return ranked_chunks


# ══════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════

def retrieve_context(query: str, top_k: int = 5) -> list[dict]:
    """
    Full retrieval pipeline:
      vector search → hybrid scoring → ranking → late chunking context

    Each returned dict contains:
      "code"           — the matched chunk (exact function/block)
      "metadata"       — file_path, function name, start/end line, language
      "similarity"     — raw vector similarity score
      "hybrid_score"   — vector + keyword combined score
      "final_score"    — final ranked score
      "parent_context" — all chunks from the same file stitched in line order
                         Pass this to the LLM for full surrounding context.
    """
    candidates = hybrid_retrieve(query, top_k=top_k * CHILD_FETCH_MULTIPLIER)
    ranked     = rank_chunks(query, candidates)
    top        = ranked[:top_k]
    results    = reconstruct_file_context(top)
    return results