# retrieval_tool.py
# Person 3 — Full Retrieval Tool

import chromadb
from sentence_transformers import SentenceTransformer
from collections import Counter

# ── Config ─────────────────────────────────────────────────────
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHROMA_PATH     = "./data/chroma_db"
COLLECTION_NAME = "codebase"

W_HYBRID    = 0.50
W_PROXIMITY = 0.30
W_FUNCTION  = 0.20

# ── Connection ──────────────────────────────────────────────────
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
                "file":     meta.get("file", ""),
                "function": meta.get("function", ""),
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
    query_words = set(query.lower().replace("_", " ").split())

    file_counts    = Counter(item["metadata"]["file"] for item in candidates)
    max_file_count = max(file_counts.values(), default=1)

    for item in candidates:
        f1_hybrid   = item["hybrid_score"]
        f2_proximity = file_counts[item["metadata"]["file"]] / max_file_count

        fn_name  = item["metadata"]["function"].lower().replace("_", " ")
        fn_words = set(fn_name.split())
        overlap  = fn_words & query_words
        f3_function = min(len(overlap) / max(len(fn_words), 1), 1.0)

        item["final_score"] = round(
            W_HYBRID    * f1_hybrid    +
            W_PROXIMITY * f2_proximity +
            W_FUNCTION  * f3_function,
            4
        )

    return sorted(candidates, key=lambda x: x["final_score"], reverse=True)


# ══════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════

def retrieve_context(query: str, top_k: int = 5) -> list[dict]:
    candidates = hybrid_retrieve(query, top_k=top_k * 2)
    ranked     = rank_chunks(query, candidates)
    return ranked[:top_k]