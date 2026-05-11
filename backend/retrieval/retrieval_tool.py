"""Hybrid retrieval over contextual code chunks."""

import json
import os
import re
import chromadb
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from indexing.sentence_model import get_sentence_transformer

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - fallback keeps demo usable
    BM25Okapi = None

try:
    from sentence_transformers import CrossEncoder
except ImportError:  # pragma: no cover - sentence-transformers is optional at runtime
    CrossEncoder = None

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHROMA_PATH     = str(Path(__file__).resolve().parent.parent / "chroma_db")
COLLECTION_NAME = "code_embeddings"
BM25_PATH       = str(Path(CHROMA_PATH) / "bm25_index.json")
CONTEXT_SEPARATOR = "\n\n--- RAW CODE ---\n"

# Ranking weights
W_HYBRID    = 0.46
W_PROXIMITY = 0.26
W_FUNCTION  = 0.18
W_SYMBOL    = 0.10  # boost when chunk body contains query’s C-style API symbol

# New retrieval stream weights
W_DENSE = float(os.getenv("DENSE_WEIGHT", "0.42"))
W_BM25 = float(os.getenv("BM25_WEIGHT", "0.28"))
W_EXACT = float(os.getenv("EXACT_WEIGHT", "0.22"))
W_KEYWORD = float(os.getenv("KEYWORD_WEIGHT", "0.08"))
W_SOURCE = float(os.getenv("SOURCE_WEIGHT", "0.14"))
W_PATH_MATCH = float(os.getenv("PATH_MATCH_WEIGHT", "0.32"))
W_CLASS_DEF = float(os.getenv("CLASS_DEF_WEIGHT", "0.28"))

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
BM25_TOP_N = int(os.getenv("BM25_TOP_N", "120"))
EXACT_TOP_N = int(os.getenv("EXACT_TOP_N", "400"))
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "80"))
SYMBOL_STOPWORDS = {
    "what", "where", "why", "how", "compare", "difference", "versus", "vs",
    "context", "engineering", "pipeline", "superset", "source", "files", "file",
    "does", "the", "and", "for", "from", "with", "about", "using", "used",
    "requirements", "start", "provide", "add", "not", "none",
}



# ══════════════════════════════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════════════════════════════

client     = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection(COLLECTION_NAME)
_bm25_cache: dict[str, Any] | None = None
_reranker: Any | None = None


# ══════════════════════════════════════════════════
# STEP 1 — vector_retrieve()
# ══════════════════════════════════════════════════

def _raw_code(document: str) -> str:
    if CONTEXT_SEPARATOR in (document or ""):
        return document.split(CONTEXT_SEPARATOR, 1)[1]
    return document or ""


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", (text or "").lower())


def _safe_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if not value:
        return []
    try:
        decoded = json.loads(value)
        return [str(v) for v in decoded] if isinstance(decoded, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return [1.0 if v > 0 else 0.0 for v in values]
    return [(v - lo) / (hi - lo) for v in values]


def _chunk_id(meta: dict[str, Any]) -> str:
    return str(meta.get("chunk_id") or f"{meta.get('file', meta.get('file_path', ''))}:{meta.get('start_line', 0)}")


def _make_chunk(doc: str, meta: dict[str, Any], *, similarity: float = 0.0, source: str = "unknown") -> dict:
    code = _raw_code(doc)
    return {
        "code": code,
        "contextual_document": doc,
        "metadata": {
            "file":         meta.get("file_path", meta.get("file", "")),
            "function":     meta.get("name", meta.get("function", "")),
            "start_line":   meta.get("start_line", 0),
            "end_line":     meta.get("end_line", 0),
            "language":     meta.get("language", ""),
            "chunk_type":   meta.get("chunk_type", ""),
            "dependencies": meta.get("dependencies", "[]"),
            "chunk_id":     meta.get("chunk_id", ""),
            "qualified_name": meta.get("qualified_name", ""),
            "symbols":      meta.get("symbols", "[]"),
            "file_summary": meta.get("file_summary", ""),
        },
        "similarity": round(similarity, 4),
        "dense_score": round(similarity, 4) if source == "vector" else 0.0,
        "bm25_score": 0.0,
        "exact_match_score": 0.0,
        "candidate_sources": [source],
    }


def plan_query(query: str) -> dict[str, Any]:
    """Extract path, symbol, and intent hints before retrieval."""
    path_pattern = r"(?:(?:[\w.-]+/)+[\w.@-]+\.[A-Za-z0-9]+|[\w.@-]+\.[A-Za-z0-9]+)"
    paths = []
    for match in re.findall(path_pattern, query):
        cleaned = match.strip("`'\".,:;()[]{}")
        if "/" in cleaned or cleaned.endswith((".py", ".js", ".ts", ".tsx", ".md", ".json", ".yaml", ".yml")):
            paths.append(cleaned)

    symbol_candidates = re.findall(r"`([^`]+)`", query)
    symbol_candidates += re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b|\b[a-zA-Z_][a-zA-Z0-9_]*_[a-zA-Z0-9_]+\b", query)
    domain_aliases = {
        "dashboard": "Dashboard",
        "dashboards": "Dashboard",
        "chart": "Chart",
        "charts": "Chart",
        "dataset": "Dataset",
        "datasets": "Dataset",
        "query": "Query",
        "queries": "Query",
        "savedquery": "SavedQuery",
        "saved query": "SavedQuery",
        "saved queries": "SavedQuery",
    }
    q_lower = query.lower()
    for phrase, symbol in domain_aliases.items():
        if re.search(r"\b" + re.escape(phrase) + r"\b", q_lower):
            symbol_candidates.append(symbol)
    symbols = []
    for sym in symbol_candidates:
        sym = sym.strip("`'\".,:;()[]{}")
        if (
            sym
            and sym.lower() not in SYMBOL_STOPWORDS
            and sym not in symbols
            and not sym.endswith((".py", ".js", ".ts", ".tsx"))
        ):
            symbols.append(sym)

    q = q_lower
    intents = []
    if any(w in q for w in ["define", "defined", "definition", "model", "class", "where are"]):
        intents.append("definition")
    if any(w in q for w in ["use", "used", "usage", "called", "references"]):
        intents.append("usage")
    if any(w in q for w in ["compare", "difference", "vs", "versus"]):
        intents.append("comparison")
    if any(w in q for w in ["test", "tests", "pytest", "spec"]):
        intents.append("tests")
    if any(w in q for w in ["doc", "docs", "documentation", "readme"]):
        intents.append("docs")

    return {
        "paths": paths,
        "symbols": symbols[:12],
        "intent": intents or ["general"],
    }


def _source_score(file_path: str, query_plan: dict[str, Any]) -> float:
    """Prefer implementation source for definition/comparison questions."""
    path = (file_path or "").lower()
    intents = set(query_plan.get("intent", []))
    source_first = bool(intents & {"definition", "comparison", "usage"})
    if not source_first:
        return 0.5

    if path.startswith(("docs/", "tests/", ".github/")) or "/tests/" in path or "_test." in path or path.endswith("_test.py"):
        return 0.05
    if path.startswith(("superset-core/src/", "superset/")):
        if any(part in path for part in ["/models", "/model", "/dao", "/app.py"]):
            return 1.0
        if "/schemas" in path:
            return 0.55
        if path.endswith("/api.py") or "/api/" in path:
            return 0.65
        if "/commands" in path:
            return 0.72
        return 0.82
    return 0.35


def _class_definition_score(item: dict[str, Any], query_plan: dict[str, Any]) -> float:
    """
    Promote class-definition chunks for definition/comparison prompts that
    mention class-like symbols (e.g., Query, SavedQuery, Dashboard).
    """
    intents = set(query_plan.get("intent", []))
    if not (intents & {"definition", "comparison"}):
        return 0.0

    symbols_raw = [str(s) for s in (query_plan.get("symbols") or [])]
    class_like = [s for s in symbols_raw if s[:1].isupper()]
    if not class_like:
        return 0.0

    meta = item.get("metadata", {}) or {}
    name = str(meta.get("function", ""))
    chunk_type = str(meta.get("chunk_type", "")).lower()
    qualified = str(meta.get("qualified_name", ""))
    symbol_list = _safe_json_list(meta.get("symbols"))
    haystack = f"{name}\n{qualified}\n" + "\n".join(symbol_list)

    exact_hits = sum(1 for s in class_like if re.search(rf"\b{re.escape(s)}\b", haystack))
    if exact_hits <= 0:
        return 0.0

    # Strong preference for class chunks; degrade for function chunks.
    if chunk_type == "class":
        return 1.0
    if chunk_type == "function":
        return 0.25
    return 0.5


def _path_match_score(file_path: str, query_plan: dict[str, Any]) -> float:
    path = (file_path or "").lower()
    for expected in query_plan.get("paths", []) or []:
        expected_lower = str(expected).lower()
        if path == expected_lower or path.endswith(expected_lower) or expected_lower.endswith(path):
            return 1.0
    return 0.0

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
        chunks.append(_make_chunk(doc, meta, similarity=1 - dist, source="vector"))
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


def exact_retrieve(query_plan: dict[str, Any], top_k: int = EXACT_TOP_N) -> list[dict]:
    """Force include chunks from exact path/symbol matches."""
    try:
        rows = collection.get(include=["documents", "metadatas"])
    except Exception as exc:
        print(f"[retrieval] Warning: exact retrieval failed: {exc}")
        return []

    paths = [p.lower() for p in query_plan.get("paths", [])]
    symbols_raw = [str(s) for s in query_plan.get("symbols", [])]
    symbols = [s.lower() for s in symbols_raw]
    docs = rows.get("documents", []) or []
    metas = rows.get("metadatas", []) or []
    candidates = []

    for doc, meta in zip(docs, metas):
        file_path = str(meta.get("file_path", "")).lower()
        name = str(meta.get("name", "")).lower()
        qualified = str(meta.get("qualified_name", "")).lower()
        symbol_list_raw = _safe_json_list(meta.get("symbols"))
        symbol_list = [s.lower() for s in symbol_list_raw]
        text = f"{doc}\n{file_path}\n{name}\n{qualified}".lower()

        score = 0.0
        if paths and any(file_path == p or file_path.endswith(p) or p.endswith(file_path) for p in paths):
            score = max(score, 1.0)
        if symbols:
            exact_symbol_hits = 0
            for raw, lower in zip(symbols_raw, symbols):
                if raw[:1].isupper():
                    exact_symbol_hits += int(raw == str(meta.get("name", "")) or raw in symbol_list_raw)
                else:
                    exact_symbol_hits += int(lower == name or lower in symbol_list)
            contains_hits = sum(1 for s in symbols if s in text)
            if exact_symbol_hits:
                score = max(score, 0.92)
            elif contains_hits:
                class_like_query = any(raw[:1].isupper() for raw in symbols_raw)
                score = max(score, 0.45 if class_like_query else 0.65)

        if score > 0:
            score = min(1.0, score + (0.18 * _source_score(str(meta.get("file_path", "")), query_plan)))
            item = _make_chunk(doc, meta, similarity=0.0, source="exact")
            item["exact_match_score"] = round(score, 4)
            candidates.append(item)

    candidates.sort(key=lambda item: item["exact_match_score"], reverse=True)
    return candidates[:top_k]


def _load_bm25() -> dict[str, Any] | None:
    global _bm25_cache
    path = Path(BM25_PATH)
    if _bm25_cache and _bm25_cache.get("mtime") == path.stat().st_mtime if path.exists() else False:
        return _bm25_cache
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        chunks = payload.get("chunks", []) or []
        corpus = [row.get("tokens") or _tokenize(row.get("document", "")) for row in chunks]
        bm25 = BM25Okapi(corpus) if BM25Okapi is not None and corpus else None
        _bm25_cache = {"mtime": path.stat().st_mtime, "chunks": chunks, "bm25": bm25}
        return _bm25_cache
    except Exception as exc:
        print(f"[retrieval] Warning: BM25 index load failed: {exc}")
        return None


def bm25_retrieve(query: str, top_k: int = BM25_TOP_N) -> list[dict]:
    loaded = _load_bm25()
    if not loaded:
        return []
    chunks = loaded["chunks"]
    tokens = _tokenize(query)
    if not tokens:
        return []

    if loaded.get("bm25") is not None:
        scores = [float(s) for s in loaded["bm25"].get_scores(tokens)]
    else:
        token_set = set(tokens)
        scores = [float(sum(1 for token in row.get("tokens", []) if token in token_set)) for row in chunks]

    normalized = _normalize_scores(scores)
    ranked = sorted(enumerate(normalized), key=lambda pair: pair[1], reverse=True)
    results = []
    for idx, score in ranked[:top_k]:
        if score <= 0:
            continue
        row = chunks[idx]
        meta = row.get("metadata", {}) or {}
        meta["chunk_id"] = row.get("chunk_id", "")
        item = _make_chunk(row.get("document", ""), meta, similarity=0.0, source="bm25")
        item["bm25_score"] = round(score, 4)
        results.append(item)
    return results


# ══════════════════════════════════════════════════
# STEP 2 — keyword_match_score()
# ══════════════════════════════════════════════════

def keyword_match_score(query: str, text: str) -> float:
    query_words = query.lower().split()
    text_lower  = text.lower()
    score = sum(1 for word in query_words if word in text_lower)
    return min(score / max(len(query_words), 1), 1.0)


def _merge_candidates(streams: dict[str, list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for source, items in streams.items():
        for item in items:
            cid = _chunk_id(item.get("metadata", {}))
            if cid not in merged:
                merged[cid] = item
                merged[cid]["candidate_sources"] = []
            target = merged[cid]
            if source not in target["candidate_sources"]:
                target["candidate_sources"].append(source)
            for score_key in ("dense_score", "bm25_score", "exact_match_score", "similarity"):
                target[score_key] = max(float(target.get(score_key, 0.0) or 0.0), float(item.get(score_key, 0.0) or 0.0))
    return list(merged.values())


def _retrieval_diagnostics(query_plan: dict[str, Any], streams: dict[str, list[dict]], merged: list[dict]) -> dict:
    source_counts = {source: len(items) for source, items in streams.items()}
    top_files = []
    seen: set[str] = set()
    for item in sorted(merged, key=lambda x: x.get("hybrid_score", 0), reverse=True):
        file_path = item.get("metadata", {}).get("file", "")
        if file_path and file_path not in seen:
            seen.add(file_path)
            top_files.append(file_path)
        if len(top_files) >= 10:
            break
    expected_paths = query_plan.get("paths", [])
    retrieved_paths = [item.get("metadata", {}).get("file", "") for item in merged]
    return {
        "query_plan": query_plan,
        "candidate_source_counts": source_counts,
        "merged_candidates": len(merged),
        "top_files": top_files,
        "expected_paths": expected_paths,
        "expected_path_retrieved": {
            path: any((rp == path or rp.endswith(path) or path.endswith(rp)) for rp in retrieved_paths)
            for path in expected_paths
        },
        "bm25_available": bool(_load_bm25()),
        "reranker_enabled": RERANKER_ENABLED,
    }


# ══════════════════════════════════════════════════
# STEP 3 — hybrid_retrieve()
# ══════════════════════════════════════════════════

def hybrid_retrieve(query: str, top_k: int = 5) -> list[dict]:
    query_plan = plan_query(query)
    streams = {
        "vector": vector_retrieve_merged(query, top_k=top_k * 2),
        "bm25": bm25_retrieve(query, top_k=BM25_TOP_N),
        "exact": exact_retrieve(query_plan, top_k=EXACT_TOP_N),
    }
    candidates = _merge_candidates(streams)

    scored = []
    for item in candidates:
        doc         = item.get("contextual_document") or item["code"]
        similarity  = float(item.get("dense_score", item.get("similarity", 0.0)) or 0.0)
        bm25_score  = float(item.get("bm25_score", 0.0) or 0.0)
        exact_score = float(item.get("exact_match_score", 0.0) or 0.0)
        file_path = item.get("metadata", {}).get("file", "")
        source_score = _source_score(file_path, query_plan)
        path_match_score = _path_match_score(file_path, query_plan)
        class_def_score = _class_definition_score(item, query_plan)
        k_score     = keyword_match_score(query, doc)

        hybrid_score = (
            W_DENSE * similarity
            + W_BM25 * bm25_score
            + W_EXACT * exact_score
            + W_KEYWORD * k_score
            + W_SOURCE * source_score
            + W_PATH_MATCH * path_match_score
            + W_CLASS_DEF * class_def_score
        )

        scored.append({
            **item,
            "code":         item["code"],
            "source_score": round(source_score, 4),
            "path_match_score": round(path_match_score, 4),
            "class_def_score": round(class_def_score, 4),
            "hybrid_score": round(hybrid_score, 4)
        })

    diagnostics = _retrieval_diagnostics(query_plan, streams, scored)
    scored.sort(key=lambda x: x["hybrid_score"], reverse=True)
    for item in scored:
        item["retrieval_diagnostics"] = diagnostics
    return scored


# ══════════════════════════════════════════════════
# STEP 4 — rank_chunks()
# ══════════════════════════════════════════════════

def _get_reranker():
    global _reranker
    if not RERANKER_ENABLED or CrossEncoder is None:
        return None
    if _reranker is not None:
        return _reranker
    try:
        _reranker = CrossEncoder(RERANKER_MODEL)
        return _reranker
    except Exception as exc:
        print(f"[retrieval] Warning: reranker unavailable, using weighted fusion: {exc}")
        return None


def rerank_candidates(query: str, candidates: list[dict]) -> list[dict]:
    reranker = _get_reranker()
    if reranker is None or not candidates:
        for item in candidates:
            item["rerank_score"] = None
        return candidates

    rerank_pool = candidates[:RERANK_TOP_N]
    pairs = [
        (query, (item.get("contextual_document") or item.get("code") or "")[:6000])
        for item in rerank_pool
    ]
    try:
        raw_scores = [float(s) for s in reranker.predict(pairs)]
    except Exception as exc:
        print(f"[retrieval] Warning: reranking failed, using weighted fusion: {exc}")
        for item in candidates:
            item["rerank_score"] = None
        return candidates

    for item, score in zip(rerank_pool, _normalize_scores(raw_scores)):
        item["rerank_score"] = round(score, 4)
        item["final_score"] = round((0.65 * score) + (0.35 * float(item.get("final_score", item.get("hybrid_score", 0.0)) or 0.0)), 4)

    for item in candidates[RERANK_TOP_N:]:
        item["rerank_score"] = None
    return sorted(candidates, key=lambda x: x.get("final_score", 0.0), reverse=True)

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

        doc_lower = (item.get("contextual_document") or item.get("code") or "").lower()
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

    ranked = sorted(candidates, key=lambda x: x["final_score"], reverse=True)
    return rerank_candidates(query, ranked)


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
                stitched_parts.append(f"{header}\n{_raw_code(doc)}")

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

def retrieve_context(
    query: str,
    top_k: int = 5,
    mode: str = "vector",
    alpha: Optional[float] = None,
    w_pagerank: Optional[float] = None,
    w_indegree: Optional[float] = None,
) -> list[dict]:
    """
    Full retrieval pipeline:
      vector search → hybrid scoring → ranking → (optional graph rerank) → late chunking

    Parameters
    ----------
    query:
        Natural-language or code query.
    top_k:
        Final number of stitched results to return.
    mode:
        ``"vector"`` (default) — the standard hybrid pipeline (dense + BM25 +
        exact-symbol + cross-encoder rerank) without any call-graph signal.
        ``"graph"`` — same candidate set, plus a final structural rerank that
        blends ``final_score`` with PageRank / in-degree from
        ``call_graph.json``. Falls back to ``"vector"`` semantics if the
        sidecar is missing.
    alpha:
        ``graph`` mode only. Weight on the hybrid score in the blend
        ``alpha * hybrid + (1 - alpha) * structural``. ``None`` → env default.
    w_pagerank, w_indegree:
        ``graph`` mode only. Split of the structural score between PageRank and
        normalized in-degree. Auto-normalized to sum to 1.

    Each returned dict contains:
      "code"           — the matched chunk (exact function/block)
      "metadata"       — file_path, function name, start/end line, language
      "similarity"     — raw vector similarity score
      "hybrid_score"   — vector + keyword combined score
      "final_score"    — final ranked score (graph-blended when mode="graph")
      "graph_score"    — structural score (graph mode only)
      "rerank_weights" — {alpha, w_pagerank, w_indegree} actually used (graph)
      "parent_context" — all chunks from the same file stitched in line order
                         Pass this to the LLM for full surrounding context.
    """
    mode_normalized = (mode or "vector").strip().lower()
    if mode_normalized not in {"vector", "graph"}:
        mode_normalized = "vector"

    candidates = hybrid_retrieve(query, top_k=top_k * CHILD_FETCH_MULTIPLIER)
    ranked     = rank_chunks(query, candidates)

    if mode_normalized == "graph":
        from .graph_rerank import rerank_with_graph
        ranked = rerank_with_graph(
            ranked,
            alpha=alpha,
            w_pagerank=w_pagerank,
            w_indegree=w_indegree,
        )

    top        = ranked[:top_k]
    results    = reconstruct_file_context(top)
    for item in results:
        item["retrieval_mode"] = mode_normalized
    return results