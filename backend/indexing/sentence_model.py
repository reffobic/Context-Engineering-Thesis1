"""
Single shared SentenceTransformer for indexing and retrieval.

Importing ``retrieval_tool`` and ``code_indexer`` in the same process used to load
the same model twice (~2× memory and startup time).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger("code_indexer")

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover
    SentenceTransformer = None  # type: ignore[misc, assignment]

DEFAULT_MODEL = "all-MiniLM-L6-v2"

_model: Optional["SentenceTransformer"] = None
_model_name: Optional[str] = None
_lock = threading.Lock()


def get_sentence_transformer(model_name: str = DEFAULT_MODEL) -> "SentenceTransformer":
    """Return a process-wide cached model (thread-safe, lazy)."""
    global _model, _model_name

    if SentenceTransformer is None:
        raise RuntimeError("sentence-transformers is required. Install: pip install sentence-transformers")

    if _model is not None and _model_name == model_name:
        return _model

    with _lock:
        if _model is not None and _model_name == model_name:
            return _model
        logger.info("Loading embedding model: %s", model_name)
        _model = SentenceTransformer(model_name)
        _model_name = model_name
        return _model
