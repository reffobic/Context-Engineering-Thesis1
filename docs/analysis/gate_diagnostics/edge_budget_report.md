# Edge types, neighbour scoring and token budgets (offline simulation, nothing run, no pruning code changed)

VERIFIED = read from code/logs or computed by the replay; INFERRED = a reading or an assumption stated next to it. Nothing here changes `eval_harness/data`, the backend or any run.

## 1. Which edge types exist, and what the pruner does with them

Relation labels in the 83 `graphify-out/graph.json` files (all edges, VERIFIED), and whether `load()` keeps them (`_relation_ok` against `DEFAULT_EDGE_TYPES`, `graphify_adapter.py:24, 108`):

| Relation | Edges (83 graphs) | Kept by `load()` | Reaches the pruner as |
|---|---|---|---|
| calls | 1,519,231 | yes | 1.0 if the label were passed on (my mapping `callee`) |
| method | 856,417 | no | - if the label were passed on (my mapping `default`) |
| contains | 761,038 | yes | 0.7 if the label were passed on (my mapping `contains`) |
| imports | 735,337 | yes | 0.75 if the label were passed on (my mapping `import`) |
| imports_from | 454,185 | yes | 0.75 if the label were passed on (my mapping `import`) |
| references | 437,217 | no | - if the label were passed on (my mapping `default`) |
| rationale_for | 375,638 | no | - if the label were passed on (my mapping `default`) |
| uses | 218,626 | no | - if the label were passed on (my mapping `default`) |
| inherits | 140,953 | yes | 0.95 if the label were passed on (my mapping `inherits`) |
| indirect_call | 134,058 | yes | 1.0 if the label were passed on (my mapping `callee`) |
| defines | 26,182 | yes | 0.7 if the label were passed on (my mapping `contains`) |
| re_exports | 17,079 | no | - if the label were passed on (my mapping `default`) |
| case_of | 627 | no | - if the label were passed on (my mapping `default`) |
| implements | 378 | yes | 0.95 if the label were passed on (my mapping `inherits`) |
| dynamic_import | 176 | yes | 0.75 if the label were passed on (my mapping `import`) |
| mixes_in | 61 | no | - if the label were passed on (my mapping `inherits`) |
| exports | 56 | no | - if the label were passed on (my mapping `default`) |
| depends_on | 20 | no | - if the label were passed on (my mapping `default`) |
| extends | 12 | no | - if the label were passed on (my mapping `inherits`) |
| configures | 3 | no | - if the label were passed on (my mapping `default`) |
| embeds | 1 | no | - if the label were passed on (my mapping `default`) |
| cites | 1 | no | - if the label were passed on (my mapping `default`) |

Reverse edges are stored too: `_add_edge` appends `(tgt, rel)` to the source and `(src, 'rev_' + rel)` to the target (`graphify_adapter.py:~215`). `neighbors()` returns node keys only, so the relation is lost there; `graph_expand` then builds each chunk with `_chunk_from_chroma_row` (`graph_expand.py:42`) without `relation_type`/`edge_type`. `_neighbor_structural_score` (`pruning/file.py:667`) therefore always takes `default` (0.65, +0.05 if the function name occurs in its own code = 0.70).

The pruner's table (`_RELATION_PRIORITY`, `file.py:653`): callee/calls 1.00, inherits/inherits_from 0.95, caller 0.85, import/imports 0.75, contains 0.70, default 0.65. Even if the raw label were passed on, `rev_calls`, `rev_imports`, `imports_from`, `indirect_call`, `defines`, `implements` and the other labels would miss the table and fall back to 0.65; only `calls`, `imports`, `inherits`, `inherits_from` and `contains` would hit. The mapping I use in the simulation (`canon` in `build_edge_budget_sim.py`, my choice, not backend code): calls/indirect_call forward → callee 1.00; rev_calls → caller 0.85; imports/imports_from/dynamic_import forward → import 0.75; inherits/extends/mixes_in/implements forward → inherits 0.95; contains/defines either direction → contains 0.70; every other label and the reverse of imports/inherits → default 0.65.

Relation of the discovering edge for every pool chunk of the 83 GRAPH tasks (VERIFIED by replay):

| Relation (discovering edge) | Pool chunks | Of which cover a gold function or gold file | Kept by the current 540 reserve |
|---|---|---|---|
| rev_calls | 568 | 51 | 26 |
| rev_imports | 358 | 3 | 15 |
| calls | 333 | 59 | 22 |
| rev_contains | 129 | 52 | 9 |
| inherits | 17 | 1 | 3 |
| rev_inherits | 16 | 1 | 0 |
| contains | 9 | 5 | 1 |

## 2. Validation of the replay

- Hops whose neighbour ids and new-chunk counts equal the log: 161 of 161. Hops where my relation-tracking BFS returns the same neighbour order as `graph_expand`: 161 of 161.
- Baseline pass 1 (current scoring, 540 reserve) reproduces the logged final context for 83 of 83 tasks, tested as: the replayed neighbour functions that are not seeds are all in the logged final context, and every logged final-context function that is not a retrieved top-5 function was a replayed kept neighbour.

## 3. Scoring variants at the current 540-token reserve

Seeds are assumed unchanged (final context = logged final context, minus the baseline's kept neighbours, plus the variant's kept neighbours). 'Function complete' = all gold functions (cap 10) present; 'file complete' = all gold files (cap 5). INFERRED for anything that depends on the seed pass (see section 5).

| Dataset | Scoring | Tasks whose kept set differs | Mean neighbours kept | Mean neighbour tokens | Function-complete tasks | Gold functions found | File-complete tasks | Gold-bearing share of neighbour tokens |
|---|---|---|---|---|---|---|---|---|
| SWE-Bench-Lite | V0_current_all_default | 0 | 1 | 421.3 | 7/27 | 9 | 14/27 | 0.163 |
| SWE-Bench-Lite | V1_literal_first | 8 | 0.96 | 423.9 | 7/27 | 9 | 14/27 | 0.192 |
| SWE-Bench-Lite | V2_mapped_first | 13 | 0.96 | 419.9 | 7/27 | 9 | 14/27 | 0.185 |
| SWE-Bench-Lite | V3_mapped_best | 13 | 0.96 | 419.9 | 7/27 | 9 | 14/27 | 0.185 |
| SWE-Bench-Lite | V4_posthoc_in_sample | 14 | 0.96 | 407.5 | 8/27 | 10 | 14/27 | 0.269 |
| Loc-Bench | V0_current_all_default | 0 | 0.88 | 379.6 | 13/56 | 22 | 31/56 | 0.242 |
| Loc-Bench | V1_literal_first | 12 | 0.89 | 375.6 | 13/56 | 22 | 31/56 | 0.133 |
| Loc-Bench | V2_mapped_first | 18 | 0.89 | 378.2 | 13/56 | 23 | 31/56 | 0.138 |
| Loc-Bench | V3_mapped_best | 18 | 0.89 | 378.2 | 13/56 | 23 | 31/56 | 0.138 |
| Loc-Bench | V4_posthoc_in_sample | 15 | 0.89 | 380.8 | 13/56 | 23 | 31/56 | 0.302 |

## 4. Reserve size (neighbour tokens), V0 current scoring and V3 edge-aware scoring

| Dataset | Scoring | Reserve | Mean neighbours kept | Mean neighbour tokens | Function-complete tasks | Gold functions found | File-complete tasks | Gold-bearing share of neighbour tokens |
|---|---|---|---|---|---|---|---|---|
| SWE-Bench-Lite | V0 | 540 | 1 | 421.3 | 7/27 | 9 | 14/27 | 0.163 |
| SWE-Bench-Lite | V0 | 720 | 1.19 | 585.7 | 7/27 | 9 | 14/27 | 0.047 |
| SWE-Bench-Lite | V0 | 900 | 1.37 | 722.9 | 8/27 | 10 | 14/27 | 0.066 |
| SWE-Bench-Lite | V0 | 1080 | 1.78 | 915.3 | 7/27 | 9 | 14/27 | 0.114 |
| SWE-Bench-Lite | V0 | 1440 | 2.33 | 1303.1 | 7/27 | 9 | 14/27 | 0.077 |
| SWE-Bench-Lite | V0 | 1800 | 2.93 | 1613.9 | 7/27 | 9 | 14/27 | 0.085 |
| SWE-Bench-Lite | V3 | 540 | 0.96 | 419.9 | 7/27 | 9 | 14/27 | 0.185 |
| SWE-Bench-Lite | V3 | 720 | 1.15 | 570.2 | 8/27 | 10 | 14/27 | 0.106 |
| SWE-Bench-Lite | V3 | 900 | 1.33 | 697.1 | 8/27 | 10 | 14/27 | 0.096 |
| SWE-Bench-Lite | V3 | 1080 | 1.81 | 914.5 | 8/27 | 10 | 14/27 | 0.127 |
| SWE-Bench-Lite | V3 | 1440 | 2.37 | 1287.5 | 8/27 | 10 | 14/27 | 0.133 |
| SWE-Bench-Lite | V3 | 1800 | 2.93 | 1626.6 | 8/27 | 10 | 14/27 | 0.116 |
| Loc-Bench | V0 | 540 | 0.88 | 379.6 | 13/56 | 22 | 31/56 | 0.242 |
| Loc-Bench | V0 | 720 | 1.11 | 513.0 | 14/56 | 23 | 31/56 | 0.231 |
| Loc-Bench | V0 | 900 | 1.43 | 702.7 | 14/56 | 23 | 31/56 | 0.145 |
| Loc-Bench | V0 | 1080 | 1.71 | 863.2 | 14/56 | 23 | 31/56 | 0.181 |
| Loc-Bench | V0 | 1440 | 2.27 | 1171.7 | 14/56 | 23 | 31/56 | 0.194 |
| Loc-Bench | V0 | 1800 | 2.8 | 1487.8 | 14/56 | 23 | 31/56 | 0.21 |
| Loc-Bench | V3 | 540 | 0.89 | 378.2 | 13/56 | 23 | 31/56 | 0.138 |
| Loc-Bench | V3 | 720 | 1.12 | 523.9 | 13/56 | 22 | 32/56 | 0.193 |
| Loc-Bench | V3 | 900 | 1.41 | 701.8 | 13/56 | 22 | 32/56 | 0.149 |
| Loc-Bench | V3 | 1080 | 1.66 | 836.8 | 13/56 | 22 | 32/56 | 0.175 |
| Loc-Bench | V3 | 1440 | 2.21 | 1164.9 | 13/56 | 22 | 32/56 | 0.17 |
| Loc-Bench | V3 | 1800 | 2.7 | 1477.8 | 13/56 | 22 | 32/56 | 0.176 |

Windowed token cost of pool chunks (`estimate_tokens` of the `prune_single_item_heuristic` output; all 1430 non-empty chunks): median 576, p75 705, p90 801, max 1070; share above 540: 57.1%. For chunks that cover a gold function or file (172): median 599, p90 771, share above 540: 60.5%. The window is capped at 40 lines plus radius (`find_best_window(max_lines=40)`, `file.py:306`) but long lines push a chunk above that.

Smallest reserve that admits each gold-function chunk of the pool (`edge_budget_gold_chunks.csv`, columns `min_reserve_to_keep_V0` / `_V3`):

| Dataset | Gold-function chunks in pool | Kept at 540 (V0) | Need ≤ 720 | ≤ 900 | ≤ 1080 | ≤ 1800 | never within 6000 |
|---|---|---|---|---|---|---|---|
| SWE-Bench-Lite V0 | 4 | 0 | 1 | 1 | 1 | 1 | 3 |
| SWE-Bench-Lite V3 | 4 | 0 | 1 | 1 | 1 | 2 | 1 |
| Loc-Bench V0 | 7 | 0 | 3 | 3 | 3 | 3 | 1 |
| Loc-Bench V3 | 7 | 0 | 2 | 2 | 2 | 3 | 1 |

## 5. Seed side: not measurable offline (INFERRED)

`prune_context_graph_aware` gives the seeds `total − neighbour tokens used` (pass 2, `file.py`), so every neighbour token above the baseline displaces a seed token one for one once the 1800 total is full. I tried to estimate the seeds' kept size from the base-commit index (window each logged seed function with `prune_single_item_heuristic`), but the estimate came out at a median of about 2,100 to 2,300 tokens, above the 1800 cap the baseline respected. The seeds the pipeline actually used are the output of the earlier flat prune, whose text I cannot rebuild (retrieval scores are not logged), so I discarded that estimate. Consequence: the 'function-complete' and 'gold functions found' columns above are upper bounds for any reserve larger than 540, because the extra neighbour tokens would come out of the seeds.

## 6. Is 1800 too small? Evidence from the logs and the source (Always-RAG, flat prune)

| Dataset | Tasks | RAG tasks where the prune kept fewer chunks than the 5 retrieved | Gold-complete tasks lost at the prune (top-5 → after pruning) | Median sum of gold-function sizes (est. tokens) | p90 | Tasks whose gold functions together exceed 540 / 1260 / 1800 | Largest single gold function |
|---|---|---|---|---|---|---|---|
| SWE-Bench-Lite | 27 | 13 | 0 | 285 | 719 | 8 / 0 / 0 | 988 |
| Loc-Bench | 56 | 39 | 1 | 817 | 3848 | 34 / 21 / 15 | 14014 |

Sizes are `len(source lines of the def) // 4` from the base-commit checkout; 7 gold functions could not be located as a function def and are excluded.

Model context for reference: `num_ctx` is 32768 in every row; the LC arm injects up to 24000 estimated tokens. The 1800-token pruner budget is a pipeline constant (`DEFAULT_TOKEN_BUDGET`, `pruning/file.py:26`; `arms.py:30, 94`), not a model limit.

## 7. Cases behind the numbers

Gold functions that expansion added and the pass left out (the 5 from `pruning_trace_report.md`), with the smallest reserve that would have admitted each (`edge_budget_gold_chunks.csv`):

| Task | Cost | Discovering edge | Rank V0 / V3 | Min reserve V0 | Min reserve V3 |
|---|---|---|---|---|---|
| pytest-dev__pytest-7373 | 381 | rev_contains | 4 / 11 | 400 | 2500 |
| sphinx-doc__sphinx-8801 | 679 | rev_calls | 20 / 3 | none within 6000 | 1320 |
| sympy__sympy-20442 | 553 | calls | 11 / 1 | none within 6000 | 560 |
| Lightning-AI__pytorch-lightning-20403 | 599 | rev_calls | 1 / 12 | 600 | 1720 |
| huggingface__transformers-29900 | 526 | rev_contains | 6 / 11 | 2000 | 540 |

A sixth case: scikit-learn__scikit-learn-30128 has its gold function `_check_reg_targets` in the retrieved top 5 but not in the final context (lost in the seed pruning), and it re-enters the pool as an 859-token neighbour that needs a reserve of about 2,380 (V3 order); no V0 reserve up to 6000 admits it.

