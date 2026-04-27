# Retrieval Upgrade Resources

These are the main references behind the retrieval upgrade and the implementation choices in this project.

## Contextual Indexing

- Anthropic, "Contextual Retrieval": https://www.anthropic.com/news/contextual-retrieval
  - Used for the idea of adding file/chunk-specific context before embedding and before lexical search.

## Lexical Search / BM25

- `rank-bm25` on PyPI: https://pypi.org/project/rank-bm25/
  - Used for the lightweight `BM25Okapi` lexical retrieval stream.
- `rank-bm25` source: https://github.com/dorianbrown/rank_bm25
  - Useful for understanding scoring behavior and dependency footprint.
- Okapi BM25 overview: https://en.wikipedia.org/wiki/Okapi_BM25
  - Background on why BM25 helps exact terms like class names, config variables, and file-specific symbols.

## Reranking

- Sentence-Transformers CrossEncoder docs: https://www.sbert.net/examples/applications/cross-encoder/README.html
  - Used for the optional second-stage reranker interface.
- Hugging Face model `cross-encoder/ms-marco-MiniLM-L-6-v2`: https://huggingface.co/cross-encoder/ms-marco-MiniLM-L-6-v2
  - Compact reranker option controlled by `RERANKER_ENABLED` and `RERANKER_MODEL`.

## Vector Store

- Chroma documentation: https://docs.trychroma.com/
  - Used for persistent vector storage and metadata/document querying.

## Resource-Friendly Indexing Notes

- Large repositories should be indexed incrementally with a bounded `max_files` value instead of forcing a full re-index.
- Defaults are intentionally conservative for laptop demos:
  - `INDEX_CPU_THREADS=2`
  - `INDEX_EMBED_BATCH_SIZE=16`
  - `/index?...&max_files=600`
- Run repeated incremental indexing calls to continue through a large repo without a single long CPU-heavy process.
