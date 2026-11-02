# Indexed tasks: what they are and where they live

**Path:** `eval_harness/data/cache/indexes/`

It holds 83 folders, one per repo at one commit. They cover 27 SWE-Bench-Lite tasks and 56 Loc-Bench tasks. Several tasks can share a folder.

## Folder name

`<owner>__<repo>__<first 12 chars of base commit>__<key>`

Example: `aio-libs__aiohttp__dd0b6e37339b__13a8b040d16ba58e`

## What is inside each folder

| File or folder | What it is |
|---|---|
| `chroma/` | ChromaDB vector store. Collections: `code_embeddings` and `code_meta_embeddings`. Embedding model: all-MiniLM-L6-v2. |
| `chroma/bm25_index.json` | Keyword (BM25) index. |
| `chroma/call_graph.json` | Call graph. |
| `graphify-out/graph.json` | Code graph (files, classes, functions and their links). |
| `fingerprint.json` | Number of chunks, for a quick check. |

## The source code that was indexed

`eval_harness/data/cache/checkouts/<owner>__<repo>/<base_commit>/`

## Which tasks exist

- `eval_harness/data/samples/swe-bench-lite.sample.json`
- `eval_harness/data/samples/loc-bench.sample.json`

Each task has a `repo` and a `base_commit`. Use them to find its index folder: the folder name starts with the repo (with `/` replaced by `__`) and the first 12 characters of the commit.

## Rules

- **Read only.** Do not write into these folders.
- **Large.** The indexes take about 130 GB and the checkouts about 35 GB. Read them in place on the machine. Do not copy them.
- **Open the vector store** with `chromadb.PersistentClient(path="<index folder>/chroma")`.
