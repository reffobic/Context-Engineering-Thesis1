"""
GASR graph_expand: seed chunks → hop neighbors → Chroma materialisation.

Returns retrieval-shaped dicts with ``source="graph"`` for post-expand re-prune.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from indexing.graphify_adapter import CodeGraphAdapter, get_adapter
except ImportError:
    from graphify_adapter import CodeGraphAdapter, get_adapter  # type: ignore


def _chroma_collection():
    """Lazy Chroma open — avoid importing retrieval_tool (heavy MiniLM side effects)."""
    if os.environ.get("GASR_OFFLINE", "").strip() in ("1", "true", "yes"):
        return None
    try:
        import chromadb
    except ImportError:
        return None
    chroma_path = os.environ.get(
        "CHROMA_PATH",
        str(Path(__file__).resolve().parent.parent / "chroma_db"),
    )
    collection_name = os.environ.get("CHROMA_COLLECTION", "code_embeddings")
    try:
        client = chromadb.PersistentClient(path=chroma_path)
        return client.get_or_create_collection(collection_name)
    except Exception as exc:
        logger.warning("graph_expand: Chroma unavailable: %s", exc)
        return None


def _chunk_from_chroma_row(
    doc: str,
    meta: dict[str, Any],
    *,
    node_id: str,
    score: float = 0.5,
) -> dict[str, Any]:
    file_path = str(meta.get("file_path") or meta.get("file") or "")
    name = str(meta.get("name") or meta.get("function") or "")
    return {
        "code": doc or "",
        "document": doc or "",
        "metadata": {
            "file": file_path,
            "file_path": file_path,
            "function": name,
            "name": name,
            "start_line": meta.get("start_line", 0),
            "end_line": meta.get("end_line", 0),
            "language": meta.get("language", ""),
            "chunk_id": meta.get("chunk_id") or f"{file_path}:{name}",
            "graph_node_id": node_id,
        },
        "similarity": score,
        "hybrid_score": score,
        "final_score": score,
        "source": "graph",
    }


def _lookup_chunks_for_node(
    collection: Any,
    attrs: dict[str, Any],
    node_id: str,
) -> list[dict[str, Any]]:
    """Resolve a graph node to one or more Chroma chunks."""
    file_path = str(attrs.get("file") or "")
    label = str(attrs.get("label") or "")
    out: list[dict[str, Any]] = []

    if collection is None:
        # Offline / test fallback: synthetic chunk from node attrs
        if file_path or label:
            out.append(
                _chunk_from_chroma_row(
                    f"# graph neighbor\n# {file_path}::{label}\n",
                    {"file_path": file_path, "name": label, "function": label},
                    node_id=node_id,
                    score=0.4,
                )
            )
        return out

    # Try metadata filters (Chroma where)
    attempts: list[dict[str, Any]] = []
    if file_path and label:
        attempts.append({"$and": [{"file_path": {"$eq": file_path}}, {"name": {"$eq": label}}]})
        attempts.append({"$and": [{"file_path": {"$eq": file_path}}, {"function": {"$eq": label}}]})
    if file_path:
        attempts.append({"file_path": {"$eq": file_path}})

    for where in attempts:
        try:
            rows = collection.get(where=where, include=["documents", "metadatas"], limit=5)
        except Exception:
            # older chroma may not support limit
            try:
                rows = collection.get(where=where, include=["documents", "metadatas"])
            except Exception as exc:
                logger.debug("graph_expand lookup failed where=%s: %s", where, exc)
                continue
        docs = rows.get("documents") or []
        metas = rows.get("metadatas") or []
        if not docs:
            continue
        # Prefer name match when we only filtered by file
        paired = list(zip(docs, metas))
        if label:
            named = [
                (d, m)
                for d, m in paired
                if str(m.get("name") or m.get("function") or "") == label
            ]
            if named:
                paired = named
        for d, m in paired[:3]:
            out.append(_chunk_from_chroma_row(d or "", m or {}, node_id=node_id))
        if out:
            return out

    # Last resort: synthetic stub so expand still returns something testable
    if file_path or label:
        out.append(
            _chunk_from_chroma_row(
                f"# unresolved graph neighbor {node_id}\n",
                {"file_path": file_path, "name": label, "function": label},
                node_id=node_id,
                score=0.2,
            )
        )
    return out


def _seed_id(item: dict[str, Any]) -> str:
    meta = item.get("metadata") or {}
    return str(
        meta.get("chunk_id")
        or f"{meta.get('file', meta.get('file_path', ''))}:{meta.get('function', meta.get('name', ''))}"
    )


def graph_expand(
    seed_chunks: list[dict[str, Any]],
    hops: int = 1,
    *,
    adapter: Optional[CodeGraphAdapter] = None,
    extracted_only: bool = True,
    max_neighbors: Optional[int] = None,
) -> dict[str, Any]:
    """
    Expand ``hops`` from seed chunks. Returns new candidate chunks only
    (not including seeds), plus diagnostics.
    """
    hops = max(0, int(hops))
    if max_neighbors is None:
        # Cap keeps GRAPH inject smaller (precision) and Chroma lookups cheaper (time).
        max_neighbors = int(os.environ.get("GASR_MAX_NEIGHBORS", "8"))

    adapter = adapter or get_adapter()
    seed_keys: list[str] = []
    seed_ids = {_seed_id(s) for s in seed_chunks}

    for ch in seed_chunks:
        k = adapter.seed_key_from_chunk(ch)
        if k:
            seed_keys.append(k)

    neighbor_ids = adapter.neighbors(
        seed_keys,
        hops=hops,
        extracted_only=extracted_only,
    )
    if max_neighbors and len(neighbor_ids) > max_neighbors:
        neighbor_ids = neighbor_ids[:max_neighbors]

    collection = _chroma_collection()
    new_chunks: list[dict[str, Any]] = []
    seen_chunk: set[str] = set(seed_ids)

    for nid in neighbor_ids:
        attrs = adapter.node_attrs(nid)
        for chunk in _lookup_chunks_for_node(collection, attrs, nid):
            cid = _seed_id(chunk)
            if cid in seen_chunk:
                continue
            seen_chunk.add(cid)
            new_chunks.append(chunk)

    return {
        "chunks": new_chunks,
        "new_chunk_count": len(new_chunks),
        "neighbor_ids": neighbor_ids,
        "seed_keys": seed_keys,
        "hops": hops,
        "graph_backend": adapter.backend,
        "edge_counts": {"neighbors": len(neighbor_ids)},
    }


def graph_expand_tool(
    seed_chunks: list[dict[str, Any]],
    hops: int = 1,
) -> list[dict[str, Any]]:
    """Registry-friendly: return chunk list only."""
    return graph_expand(seed_chunks, hops=hops)["chunks"]
