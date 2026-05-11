#!/usr/bin/env python3
"""
One-shot helper: regenerate ``backend/chroma_db/bm25_index.json`` from whatever
documents are already in the Chroma collection. No file scan, no re-embedding.

Run this once after dropping a pre-built Chroma bundle into ``backend/chroma_db/``
(e.g. the graph-embedded index produced by Hoda's indexer), since that bundle
ships the vector store + call_graph.json but not the BM25 sidecar that hybrid
retrieval expects.

Usage::

    cd backend
    python rebuild_bm25.py
"""

from indexing.code_indexer import CodeIndexer


def main() -> None:
    indexer = CodeIndexer()
    n = indexer._rebuild_bm25_index()
    print(f"BM25 rebuilt: {n} chunks -> {indexer._bm25_path()}")


if __name__ == "__main__":
    main()
