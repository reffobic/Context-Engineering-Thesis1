# Pruning diagnosis (no pruning code changed, no model run)

## (a) Always-RAG function Acc@10: 0.370 → 0.259 (SWE) and 0.429 → 0.250 (Loc)

The paper-table rows 'After retrieval' → 'After pruning' for Always-RAG (`tables_nov3.tex` lines 94–96 SWE and 122–124 Loc; metric column function Acc@10). Recomputed here from the stage lists with `harness.metrics.acc_at`, k = 10:

| Dataset | Tasks | `after_retrieval` (top 10 of the 50 ranked candidates) | `retrieved_top5` (the 5 chunks `retrieve_context` returns) | `after_pruning` | `final_context` |
|---|---|---|---|---|---|
| SWE-Bench-Lite | 27 | 10/27 = 0.370 | 7/27 = 0.259 | 7/27 = 0.259 | 7/27 = 0.259 |
| Loc-Bench | 56 | 24/56 = 0.429 | 15/56 = 0.268 | 14/56 = 0.250 | 14/56 = 0.250 |

- SWE-Bench-Lite: tasks complete in the top-10 list but not in the 5 returned chunks (3): django__django-13315, pytest-dev__pytest-7373, sympy__sympy-12454; complete in the 5 chunks but not after pruning (0): none; complete in the 5 chunks but not in the top-10 list (0): none.
- Loc-Bench: tasks complete in the top-10 list but not in the 5 returned chunks (9): PrefectHQ__prefect-12410, PrefectHQ__prefect-14259, bridgecrewio__checkov-6909, django__django-18653, huggingface__diffusers-10189, jax-ml__jax-25114, pandas-dev__pandas-60415, phidatahq__phidata-1583, py-pdf__pypdf-2934; complete in the 5 chunks but not after pruning (1): getmoto__moto-8316; complete in the 5 chunks but not in the top-10 list (0): none.

## (b) Always-GRAPH: gold added by expansion, traced through the re-prune

'Added by expansion' = a gold function is in `after_expansion` but not in `retrieved_top5`. Replay: `graph_expand` (hops as logged, max 8 neighbours) then pass 1 of `prune_context_graph_aware` (`pruning/file.py:690`), which reserves `int(1800 × 0.30) = 540` estimated tokens for neighbours, sorts them by `_neighbor_structural_score` (descending, stable) and keeps each one whose windowed text still fits.

| Dataset | Tasks | Tasks where a gold function was added | Gold functions added | Tasks where a gold file was added (of which not in the final context) | Replay matches the logged neighbour ids and counts (tasks) |
|---|---|---|---|---|---|
| SWE-Bench-Lite | 27 | 3 | 3 | 1 (1) | 3 of 3 |
| Loc-Bench | 56 | 2 | 2 | 3 (3) | 2 of 2 |

Per added gold function: what happened to it in the re-prune (counts; 'kept' = it is in the logged final context):

| Dataset | Outcome in the replay | In logged final context | Count |
|---|---|---|---|
| Loc-Bench | chunk_larger_than_whole_reserve | False | 1 |
| Loc-Bench | reserve_used_up_by_earlier_neighbours | False | 1 |
| SWE-Bench-Lite | chunk_larger_than_whole_reserve | False | 2 |
| SWE-Bench-Lite | reserve_used_up_by_earlier_neighbours | False | 1 |

Per-function rows (rank, score, token cost, remaining budget, reason): `pruning_trace_gold_added.csv`.

### Code facts that decide the outcome (VERIFIED by reading `pruning/file.py` and `retrieval/graph_expand.py` at the frozen SHA)

- Graph neighbours are built by `_chunk_from_chroma_row` (`graph_expand.py:42`) with no `relation_type` or `edge_type`, so `_neighbor_structural_score` always takes the 'default' priority 0.65 (+0.05 if the function name occurs in its own code): neighbours are only ever ordered by that 0.05 bonus and then by arrival order. The callee/caller/inherits priorities (`file.py:~655`) never apply.
- Neighbour packing is a single pass in that order with a 540-token reserve; a neighbour that does not fit is skipped (`continue`), later smaller ones can still fit. There is no score threshold, no per-file cap and no deduplication inside `prune_context_graph_aware`.
- The only per-file limits are upstream, in expansion: `paired[:3]` chunks per graph node and `limit=5` in the Chroma lookup (`graph_expand.py` `_lookup_chunks_for_node`), and `max_neighbors = 8` nodes per hop. A file-level graph node pulls up to 3 arbitrary chunks of that file.
- Seeds are packed afterwards into the remaining budget (1260 plus whatever the neighbours left unused).

