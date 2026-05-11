# Endpoint for tool_registry.py
from pathlib import Path
from typing import Optional

from .retrieval_tool import retrieve_context as _retrieve


def retrieve_context(
    query: str,
    top_k: int = 5,
    mode: str = "vector",
    alpha: Optional[float] = None,
    w_pagerank: Optional[float] = None,
    w_indegree: Optional[float] = None,
) -> list[dict]:
    """Tool-registry-facing wrapper.

    ``mode`` selects the retrieval strategy:
      - ``"vector"``: hybrid dense + BM25 + exact + rerank (no graph).
      - ``"graph"``: same candidates + call-graph structural rerank.

    When ``mode == "graph"``, ``alpha`` / ``w_pagerank`` / ``w_indegree`` are
    forwarded to the reranker. ``None`` keeps the env-default value.
    """
    return _retrieve(
        query,
        top_k=top_k,
        mode=mode,
        alpha=alpha,
        w_pagerank=w_pagerank,
        w_indegree=w_indegree,
    )
