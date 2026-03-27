"""
backend/indexing
================
P2 – Code Indexing & Embeddings subsystem.

Public API:
    from indexing.code_indexer import CodeIndexer, scan_repository, read_and_chunk_file
"""
from .code_indexer import (
    CodeIndexer,
    CodeChunk,
    scan_repository,
    read_and_chunk_file,
)

__all__ = [
    "CodeIndexer",
    "CodeChunk",
    "scan_repository",
    "read_and_chunk_file",
]
