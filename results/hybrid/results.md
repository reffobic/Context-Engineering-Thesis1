# Evaluation results — Hybrid retrieval

Log **`POST /evaluate`** response fields here. Match prompts and gold labels to [`TESTCASES.md`](../../TESTCASES.md) §4 for each **TC-ID**.

**Retrieval backend:** `retrieve_context` default (dense + lexical / ranking).

Omit unused rows (no gold → skip P/R; judges off → skip relevancy/faithfulness). With **`save_metrics_json: true`** (default on the dashboard) on **`POST /evaluate`** and **`retrieval_mode`: `hybrid`**, the API writes **`json/eval-{utc}-{id}.json`** here.

## Copy block — reusable template

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

## By test case (`POST /evaluate`)

### TC-01 — RAG — Superset symbol

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-02 — RAG — fake symbol

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-03 — Citation discipline

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-04 — Long-form output

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-05 — Corpus focus (two Superset areas)

*Use one table per `/evaluate` run (e.g. TC-05A / TC-05B).*

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-06 — Empty prompt

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-07 — Invalid JSON

*`/evaluate` N/A unless testing error paths separately.*

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-08 — Index API

*`POST /index` — `/evaluate` is separate.*

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-09 — Smoke script

*`smoke_test.py` — `/evaluate` is optional.*

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-10 — Superset onboarding flow

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-11 — `create_app` deep trace

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-12 — Middleware extraction context

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-13 — Serializer summary behavior

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-14 — Supported vs unsupported claims (Superset)

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-15 — Multi-topic Superset comparison

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-16 — Source-first `Query` vs `SavedQuery`

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-17 — Source-first dashboard/chart/dataset models

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---

### TC-18 — Source-first `superset/app.py` config

| Metric / field (`POST /evaluate`) | Value |
| --- | --- |
| **Run metadata** — date |  |
| **Run metadata** — git commit / branch |  |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) |  |
| **`top_k`** |  |
| **`token_budget`** |  |
| **`relevant_files`** (gold; count or paths) |  |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) |  |
| `pipeline_counts.retrieved_chunks` |  |
| `pipeline_counts.pruned_chunks` |  |
| `structural.retrieved_top_mean_rank_score` |  |
| `structural.retrieved_unique_paths_in_top_k` |  |
| `structural.pruned_chunk_count` |  |
| `structural.pruned_unique_paths` |  |
| `structural.retrieved_top_paths` (summary) |  |
| `structural.pruned_paths` (summary) |  |
| `precision_at_k_retrieval.precision_at_k` |  |
| `precision_at_k_retrieval.recall_at_k` |  |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` |  |
| `precision_at_k_pruned.precision_at_k` |  |
| `precision_at_k_pruned.recall_at_k` |  |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` |  |
| `contextual_relevancy_retrieval.score` |  |
| `contextual_relevancy_retrieval.success` |  |
| `contextual_relevancy_retrieval.error` |  |
| `generation_preview` (note) |  |
| `faithfulness.score` |  |
| `faithfulness.success` |  |
| `faithfulness.error` |  |
| **Reviewer notes** (index hash, variance) |  |

---
