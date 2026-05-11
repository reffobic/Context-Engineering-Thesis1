# Evaluation results — Vector retrieval

Log `**POST /evaluate**` response fields here. Match prompts and gold labels to `[TESTCASES.md](../../TESTCASES.md)` §4 for each **TC-ID**.

**Retrieval backend:** **Dense-only path** — fill once exposed in code (same metric names).

Omit unused rows (no gold → skip P/R; judges off → skip relevancy/faithfulness). With `**save_metrics_json: true`** on `**POST /evaluate`** and `**retrieval_mode`: `vector`**, the API also writes `**json/eval-{utc}-{id}.json`** in this directory (same payload as the HTTP response, plus `metrics_json_path` / `metrics_saved_at_utc` / `retrieval_mode` metadata).

## Copy block — reusable template


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

## By test case (`POST /evaluate`)

### TC-01 — RAG — Superset symbol


| Metric / field (`POST /evaluate`)                             | Value                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Run metadata** — date                                       | 10 may 2026                                                                                                                                                                                                                                                                                                              |
| **Run metadata** — git commit / branch                        |                                                                                                                                                                                                                                                                                                                          |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               | eval judge model                                                                                                                                                                                                                                                                                                         |
| `**top_k`**                                                   | 10                                                                                                                                                                                                                                                                                                                       |
| `**token_budget`**                                            | 1800                                                                                                                                                                                                                                                                                                                     |
| `**relevant_files`** (gold; count or paths)                   | superset/app.py                                                                                                                                                                                                                                                                                                          |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) | on                                                                                                                                                                                                                                                                                                                       |
| `pipeline_counts.retrieved_chunks`                            | 10                                                                                                                                                                                                                                                                                                                       |
| `pipeline_counts.pruned_chunks`                               | 8                                                                                                                                                                                                                                                                                                                        |
| `structural.retrieved_top_mean_rank_score`                    | 0.66698                                                                                                                                                                                                                                                                                                                  |
| `structural.retrieved_unique_paths_in_top_k`                  | 3                                                                                                                                                                                                                                                                                                                        |
| `structural.pruned_chunk_count`                               | 8                                                                                                                                                                                                                                                                                                                        |
| `structural.pruned_unique_paths`                              | 3                                                                                                                                                                                                                                                                                                                        |
| `structural.retrieved_top_paths` (summary)                    | "tests/integration_tests/base_[tests.py](http://tests.py)", "superset/[app.py](http://app.py)", "tests/integration_tests/base_[tests.py](http://tests.py)", "tests/integration_tests/base_[tests.py](http://tests.py)", "tests/integration_tests/base_[tests.py](http://tests.py)"                                   |
| `structural.pruned_paths` (summary)                           | `"superset/app.py", "tests/integration_tests/base_tests.py", "tests/integration_tests/base_tests.py", "tests/integration_tests/base_tests.py", "tests/integration_tests/base_tests.py", "tests/integration_tests/base_tests.py", "tests/integration_tests/base_tests.py", "tests/integration_tests/thumbnails_tests.py"` |
| `precision_at_k_retrieval.precision_at_k`                     | 10                                                                                                                                                                                                                                                                                                                       |
| `precision_at_k_retrieval.recall_at_k`                        | 10                                                                                                                                                                                                                                                                                                                       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       | 10                                                                                                                                                                                                                                                                                                                       |
| `precision_at_k_pruned.precision_at_k`                        | 0.1                                                                                                                                                                                                                                                                                                                      |
| `precision_at_k_pruned.recall_at_k`                           | 1                                                                                                                                                                                                                                                                                                                        |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          | 1                                                                                                                                                                                                                                                                                                                        |
| `contextual_relevancy_retrieval.score`                        | 0.85                                                                                                                                                                                                                                                                                                                     |
| `contextual_relevancy_retrieval.success`                      | true                                                                                                                                                                                                                                                                                                                     |
| `contextual_relevancy_retrieval.error`                        | -                                                                                                                                                                                                                                                                                                                        |
| `generation_preview` (note)                                   | -                                                                                                                                                                                                                                                                                                                        |
| `faithfulness.score`                                          | 0.55                                                                                                                                                                                                                                                                                                                     |
| `faithfulness.success`                                        | true                                                                                                                                                                                                                                                                                                                     |
| `faithfulness.error`                                          | -                                                                                                                                                                                                                                                                                                                        |
| **Reviewer notes** (index hash, variance)                     |                                                                                                                                                                                                                                                                                                                          |


---

### TC-02 — RAG — fake symbol


| Metric / field (`POST /evaluate`)                             | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Run metadata** — date                                       | 10 may 2026                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Run metadata** — git commit / branch                        | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               | llama3..2:3b                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `**top_k`**                                                   | 10                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `**token_budget`**                                            | 1800                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `**relevant_files`** (gold; count or paths)                   | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) | on                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `pipeline_counts.retrieved_chunks`                            | 10                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `pipeline_counts.pruned_chunks`                               | 5                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `structural.retrieved_top_mean_rank_score`                    | 0.40778                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `structural.retrieved_unique_paths_in_top_k`                  | 1                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `structural.pruned_chunk_count`                               | 5                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `structural.pruned_unique_paths`                              | 1                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `structural.retrieved_top_paths` (summary)                    | "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)"                                                                                                                                                                                                   |
| `structural.pruned_paths` (summary)                           | "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)", "superset/models/[helpers.py](http://helpers.py)"                                                                                                                                                                                                   |
| `precision_at_k_retrieval.precision_at_k`                     | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `precision_at_k_retrieval.recall_at_k`                        | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `precision_at_k_pruned.precision_at_k`                        | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `precision_at_k_pruned.recall_at_k`                           | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `contextual_relevancy_retrieval.score`                        | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `contextual_relevancy_retrieval.success`                      | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `contextual_relevancy_retrieval.error`                        | RetryError[<Future at 0x7928a9aee480 state=finished raised TimeoutError>]                                                                                                                                                                                                                                                                                                                                                                                           |
| `generation_preview` (note)                                   | The function `totally_fake_superset_api_xyz_999` is not present in the provided code context. The context contains functions like `_apply_series_others_grouping`, `query`, `_normalize_prequery_result_type`, `_process_orderby_expression`, and `is_alias_used_in_orderby`, but none match the requested name. To analyze `totally_fake_superset_api_xyz_999`, additional information or code snippets containing that specific function definition are required. |
| `faithfulness.score`                                          | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `faithfulness.success`                                        | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `faithfulness.error`                                          | RetryError[<Future at 0x79281523bfb0 state=finished raised TimeoutError>]                                                                                                                                                                                                                                                                                                                                                                                           |
| **Reviewer notes** (index hash, variance)                     | the test case counts as a pass because the model generation correctly identified that this is not available in the context. The errors are not                                                                                                                                                                                                                                                                                                                      |


---

### TC-03 — Citation discipline


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-04 — Long-form output


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-05 — Corpus focus (two Superset areas)

*Use one table per `/evaluate` run (e.g. TC-05A / TC-05B).*


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-06 — Empty prompt


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-07 — Invalid JSON

`*/evaluate` N/A unless testing error paths separately.*


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-08 — Index API

`*POST /index` — `/evaluate` is separate.*


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-09 — Smoke script

`*smoke_test.py` — `/evaluate` is optional.*


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-10 — Superset onboarding flow


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-11 — `create_app` deep trace


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-12 — Middleware extraction context


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-13 — Serializer summary behavior


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-14 — Supported vs unsupported claims (Superset)


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-15 — Multi-topic Superset comparison


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-16 — Source-first `Query` vs `SavedQuery`


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-17 — Source-first dashboard/chart/dataset models


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

### TC-18 — Source-first `superset/app.py` config


| Metric / field (`POST /evaluate`)                             | Value |
| ------------------------------------------------------------- | ----- |
| **Run metadata** — date                                       |       |
| **Run metadata** — git commit / branch                        |       |
| **Judge model** (`EVAL_JUDGE_MODEL` or default)               |       |
| `**top_k`**                                                   |       |
| `**token_budget`**                                            |       |
| `**relevant_files`** (gold; count or paths)                   |       |
| `**judge_retrieval_relevancy`** / `**faithfulness`** (on/off) |       |
| `pipeline_counts.retrieved_chunks`                            |       |
| `pipeline_counts.pruned_chunks`                               |       |
| `structural.retrieved_top_mean_rank_score`                    |       |
| `structural.retrieved_unique_paths_in_top_k`                  |       |
| `structural.pruned_chunk_count`                               |       |
| `structural.pruned_unique_paths`                              |       |
| `structural.retrieved_top_paths` (summary)                    |       |
| `structural.pruned_paths` (summary)                           |       |
| `precision_at_k_retrieval.precision_at_k`                     |       |
| `precision_at_k_retrieval.recall_at_k`                        |       |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth`       |       |
| `precision_at_k_pruned.precision_at_k`                        |       |
| `precision_at_k_pruned.recall_at_k`                           |       |
| `precision_at_k_pruned.relevant_chunks_in_top_depth`          |       |
| `contextual_relevancy_retrieval.score`                        |       |
| `contextual_relevancy_retrieval.success`                      |       |
| `contextual_relevancy_retrieval.error`                        |       |
| `generation_preview` (note)                                   |       |
| `faithfulness.score`                                          |       |
| `faithfulness.success`                                        |       |
| `faithfulness.error`                                          |       |
| **Reviewer notes** (index hash, variance)                     |       |


---

