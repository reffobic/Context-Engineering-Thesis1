"""
graph_rerank.py
===============

Graph-aware reranker for the ``graph`` retrieval mode.

Loads the ``call_graph.json`` sidecar produced by ``indexing/graph_builder.py``
during indexing, and rerwrites the retrieval candidates' ``final_score`` by
blending the existing hybrid score with a structural score derived from each
chunk's call-graph node (PageRank + normalized in-degree).

Design notes
------------
- The graph is loaded lazily and cached on first use. Subsequent calls are
  O(N) over the candidate list.
- If ``call_graph.json`` is missing (e.g. an older index, a checkout where
  Phase 2 indexing has not been run yet), this module logs a one-shot warning
  and returns the candidates unchanged so ``graph`` mode degrades gracefully
  to ``vector`` mode.
- Matching a candidate to a graph node uses ``metadata.file`` and one of
  ``metadata.function`` / ``metadata.name`` / ``metadata.qualified_name``.
  Unmatched candidates keep their hybrid score (structural score = 0).
- The blend ratio mirrors the ``graph-embeddings`` branch defaults
  (0.65 hybrid / 0.35 graph). Override with ``alpha``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("graph_rerank")

# Default sidecar location: <repo>/backend/chroma_db/call_graph.json.
# Mirrors backend/retrieval/retrieval_tool.py CHROMA_PATH layout.
DEFAULT_GRAPH_PATH = Path(__file__).resolve().parent.parent / "chroma_db" / "call_graph.json"

# 0.65 hybrid / 0.35 structural matches the graph-embeddings branch.
DEFAULT_ALPHA = float(os.getenv("GRAPH_RERANK_ALPHA", "0.65"))

# Weights inside the structural sub-score. Normalized to sum to 1.0 at call
# time, so users can pass arbitrary positive values and still get sane scores.
DEFAULT_W_PAGERANK = float(os.getenv("GRAPH_RERANK_W_PAGERANK", "0.65"))
DEFAULT_W_INDEGREE = float(os.getenv("GRAPH_RERANK_W_INDEGREE", "0.35"))


# ── lazy graph cache ─────────────────────────────────────────────────────────

# (file_path, function_name) -> {"pagerank": float, "in_degree": float}
_NODE_INDEX: Optional[dict[tuple[str, str], dict[str, float]]] = None
# Secondary index keyed by function name only, used as a fallback when the
# candidate's file path normalization doesn't match the graph's stored path.
_NAME_INDEX: Optional[dict[str, list[dict[str, float]]]] = None
_LOAD_PATH: Optional[Path] = None
_LOAD_FAILED: bool = False


def _normalize_path(value: str) -> str:
    if not value:
        return ""
    return value.replace("\\", "/").lstrip("./")


def _load_graph(graph_path: Path) -> bool:
    """Populate the module-level indexes from ``graph_path``. Returns success."""
    global _NODE_INDEX, _NAME_INDEX, _LOAD_PATH, _LOAD_FAILED

    if not graph_path.exists():
        if not _LOAD_FAILED:
            logger.warning(
                "graph_rerank: %s not found - graph mode will fall back to vector mode "
                "until indexing is re-run.",
                graph_path,
            )
        _LOAD_FAILED = True
        _NODE_INDEX = {}
        _NAME_INDEX = {}
        _LOAD_PATH = graph_path
        return False

    try:
        data = json.loads(graph_path.read_text())
    except Exception as exc:
        logger.warning("graph_rerank: failed to parse %s: %s", graph_path, exc)
        _LOAD_FAILED = True
        _NODE_INDEX = {}
        _NAME_INDEX = {}
        _LOAD_PATH = graph_path
        return False

    nodes = data.get("nodes") or {}
    node_index: dict[tuple[str, str], dict[str, float]] = {}
    name_index: dict[str, list[dict[str, float]]] = {}

    for node_id, payload in nodes.items():
        name = payload.get("name") or ""
        file_path = _normalize_path(payload.get("file_path") or "")
        if not name:
            continue
        score = {
            "pagerank":  float(payload.get("pagerank", 0.0) or 0.0),
            "in_degree": float(payload.get("in_degree", 0.0) or 0.0),
        }
        node_index[(file_path, name)] = score
        name_index.setdefault(name, []).append(score)

    _NODE_INDEX = node_index
    _NAME_INDEX = name_index
    _LOAD_PATH = graph_path
    _LOAD_FAILED = False
    logger.info("graph_rerank: loaded %d graph nodes from %s", len(node_index), graph_path)
    return True


def _ensure_loaded(graph_path: Optional[Path] = None) -> bool:
    """Lazy-load the graph index. Pass ``graph_path`` to override the default."""
    global _NODE_INDEX, _LOAD_PATH
    target = Path(graph_path) if graph_path else DEFAULT_GRAPH_PATH
    if _NODE_INDEX is not None and _LOAD_PATH == target:
        # First attempt may have failed (missing sidecar). Retry if indexing
        # created ``call_graph.json`` while the server stayed up.
        if _LOAD_FAILED and target.exists():
            return _load_graph(target)
        return not _LOAD_FAILED
    return _load_graph(target)


def reset_cache() -> None:
    """Drop the cached graph index. Useful for tests / after re-indexing."""
    global _NODE_INDEX, _NAME_INDEX, _LOAD_PATH, _LOAD_FAILED
    _NODE_INDEX = None
    _NAME_INDEX = None
    _LOAD_PATH = None
    _LOAD_FAILED = False


# ── public API ───────────────────────────────────────────────────────────────

def _lookup(file_path: str, name: str) -> Optional[dict[str, float]]:
    if _NODE_INDEX is None or _NAME_INDEX is None:
        return None
    key = (_normalize_path(file_path), name)
    hit = _NODE_INDEX.get(key)
    if hit is not None:
        return hit
    # Fall back to name-only lookup. If exactly one node has this name, use it;
    # if multiple, average their structural scores so we still nudge ranking
    # without picking the wrong file.
    bucket = _NAME_INDEX.get(name) or []
    if not bucket:
        return None
    if len(bucket) == 1:
        return bucket[0]
    avg_pr = sum(b["pagerank"] for b in bucket) / len(bucket)
    avg_in = sum(b["in_degree"] for b in bucket) / len(bucket)
    return {"pagerank": avg_pr, "in_degree": avg_in}


def _structural_score(meta: dict, w_pagerank: float, w_indegree: float) -> float:
    file_path = meta.get("file") or meta.get("file_path") or ""
    candidates = [
        meta.get("function"),
        meta.get("name"),
        meta.get("qualified_name"),
    ]
    for name in candidates:
        if not name:
            continue
        hit = _lookup(file_path, name)
        if hit is not None:
            return w_pagerank * hit["pagerank"] + w_indegree * hit["in_degree"]
    return 0.0


def _resolve_weights(
    alpha: Optional[float],
    w_pagerank: Optional[float],
    w_indegree: Optional[float],
) -> tuple[float, float, float]:
    """Clamp/normalize the user-supplied weights to safe values.

    - ``alpha`` is clamped to ``[0, 1]``.
    - ``w_pagerank`` / ``w_indegree`` are floored at zero and renormalized so
      they sum to ``1.0``. If both are zero (or omitted), the env-var defaults
      are used.
    """
    a = DEFAULT_ALPHA if alpha is None else float(alpha)
    a = max(0.0, min(1.0, a))

    wp_raw = DEFAULT_W_PAGERANK if w_pagerank is None else float(w_pagerank)
    wi_raw = DEFAULT_W_INDEGREE if w_indegree is None else float(w_indegree)
    wp = max(0.0, wp_raw)
    wi = max(0.0, wi_raw)

    total = wp + wi
    if total <= 0:
        wp, wi = DEFAULT_W_PAGERANK, DEFAULT_W_INDEGREE
        total = wp + wi or 1.0
    return a, wp / total, wi / total


def rerank_with_graph(
    candidates: list[dict],
    alpha: Optional[float] = None,
    w_pagerank: Optional[float] = None,
    w_indegree: Optional[float] = None,
    graph_path: Optional[Path] = None,
) -> list[dict]:
    """Blend each candidate's ``final_score`` with a structural call-graph score.

    Parameters
    ----------
    candidates:
        Ranked candidates from ``rank_chunks``. Each dict must contain
        ``final_score`` and ``metadata`` with ``file`` and one of
        ``function`` / ``name`` / ``qualified_name``.
    alpha:
        Weight for the existing hybrid ``final_score``. ``(1 - alpha)`` is the
        weight applied to the structural score. ``None`` (default) uses the
        env-var default ``GRAPH_RERANK_ALPHA`` (0.65 unless overridden).
    w_pagerank, w_indegree:
        Weights inside the structural score for PageRank and normalized
        in-degree respectively. Auto-normalized so they sum to ``1.0``. ``None``
        for either falls back to ``GRAPH_RERANK_W_PAGERANK`` /
        ``GRAPH_RERANK_W_INDEGREE`` defaults.
    graph_path:
        Optional override of the ``call_graph.json`` location.

    Returns
    -------
    The same list of candidate dicts, mutated in place with a new ``final_score``
    and a ``graph_score`` / ``rerank_weights`` diagnostic, sorted highest-first.
    If the graph sidecar is missing, the candidates are returned unchanged.
    """
    if not candidates:
        return candidates

    a, wp, wi = _resolve_weights(alpha, w_pagerank, w_indegree)
    weights_diag = {
        "alpha": round(a, 4),
        "w_pagerank": round(wp, 4),
        "w_indegree": round(wi, 4),
    }

    if not _ensure_loaded(graph_path):
        for c in candidates:
            c.setdefault("graph_score", 0.0)
            c.setdefault("rerank_weights", weights_diag)
        return candidates

    beta = 1.0 - a

    for item in candidates:
        meta = item.get("metadata") or {}
        struct = _structural_score(meta, wp, wi)
        base = float(item.get("final_score", 0.0) or 0.0)
        blended = a * base + beta * struct
        item["graph_score"] = round(struct, 4)
        item["hybrid_only_score"] = round(base, 4)
        item["final_score"] = round(blended, 4)
        item["rerank_weights"] = weights_diag

    return sorted(candidates, key=lambda x: x.get("final_score", 0.0), reverse=True)


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_W_PAGERANK",
    "DEFAULT_W_INDEGREE",
    "DEFAULT_GRAPH_PATH",
    "rerank_with_graph",
    "reset_cache",
]
