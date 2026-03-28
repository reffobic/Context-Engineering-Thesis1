# retrieval_tool.py
# Person 3 — Hybrid Retrieval + Late Chunking Long Context

import chromadb
from sentence_transformers import SentenceTransformer
from collections import Counter

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHROMA_PATH     = r"C:\Uni Stuff\retrieval\retrieval\subversion_db"
COLLECTION_NAME = "code_embeddings"

# Ranking weights
W_HYBRID    = 0.50
W_PROXIMITY = 0.30
W_FUNCTION  = 0.20

# Long context config
# How many candidates to fetch before re-ranking
CHILD_FETCH_MULTIPLIER = 4
# Cap on chunks stitched per file — prevents token explosion on huge files
MAX_CONTEXT_CHUNKS_PER_FILE = 10

# ══════════════════════════════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════════════════════════════

model      = SentenceTransformer(EMBEDDING_MODEL)
client     = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection(COLLECTION_NAME)


# ══════════════════════════════════════════════════
# STEP 1 — vector_retrieve()
# ══════════════════════════════════════════════════

def vector_retrieve(query: str, top_k: int = 5) -> list[dict]:
    query_embedding = model.encode([query])[0].tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0]
    ):
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
            },
            "similarity": round(1 - dist, 4)
        })

    return chunks


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
    candidates = vector_retrieve(query, top_k=top_k * 2)

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

    for item in candidates:
        f1_hybrid    = item["hybrid_score"]
        f2_proximity = file_counts[item["metadata"]["file"]] / max_file_count

        fn_name     = item["metadata"]["function"].lower().replace("_", " ")
        fn_words    = set(fn_name.split())
        overlap     = fn_words & query_words
        f3_function = min(len(overlap) / max(len(fn_words), 1), 1.0)

        item["final_score"] = round(
            W_HYBRID    * f1_hybrid    +
            W_PROXIMITY * f2_proximity +
            W_FUNCTION  * f3_function,
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