# Phase 2 SWE-QA rest indexes (12 repos)

**Pipeline freeze:** `a2cfcec312626b4b50f743c4af91a6d118c79d0e`  
**Dataset:** `swe-qa-rest` (576 Q)  
**Built:** 2026-11-15

## Notes

- Django graphify hung; finalized with **call_graph → graph.json fallback** (see `index_worker.py` `GRAPHIFY_TIMEOUT_S`, default 900s).
- Full SHAs: [`sweqa_rest_commits.json`](sweqa_rest_commits.json)

## Status

All 12 repos have `DONE` under `eval_harness/data/cache/indexes/`.
