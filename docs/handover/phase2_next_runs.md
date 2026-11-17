# Phase 2 next runs: new freeze, GAPA/GASR ablations, LocAgent baseline

Written 2026-11-16. Branch **`phase2-next`** (local worktree `/home/g7/Documents/HANA/phase2-next`). Read this before running anything on it.

## 1. What was decided (do not re-litigate; ask the user first)

| Topic | Decision |
|---|---|
| Freeze | **One new pipeline SHA for everything: `cacecedb1862539822e2009e0b17cb5406fee8b4`.** It is `phase2-eval` plus backend fixes B/C/D (patch 0003, commit `ca843a4`) plus the GASR gate ablation switches (patch 0004). Previous freeze `a2cfcec` (first Phase 2: SWE-QA Wave 1, rest, RepoProbe) is kept as `previous_frozen_sha`. Results on different freezes are **not mixed**. |
| Prompt file order | **Rank order**, by decision. The old alphabetical order was an accidental artefact, not a design choice. No switch or legacy mode is kept. |
| Neighbour reserve ("graph reprune") | The `re_prune` stage and oversized-neighbour truncation already exist (`ca843a4`). Only the reserve size changed: `GASR_NEIGHBOR_BUDGET_FRACTION` 0.30 -> **0.42** (about 546 tokens at the 1300 budget; Nov 3 measured 540). Env only, in `pipeline_phase2.toml`: revert there, no new SHA. Evidence is weak (a bigger reserve added at most one gold function per dataset in the Nov 8 replay, `docs/analysis/gate_diagnostics/edge_budget_report.md`) and seed-side cost cannot be measured offline: **check on the smoke run.** |
| Ablations | GAPA ablation is **rerun** on the new freeze; GASR ablation is **new**. Shared rows on both arms: `full`, `no_graph_expansion`, `contains_only`, `truncation`, `no_keyword_channel`. GAPA only: `gate_no_scores`, `gate_no_deps`. GASR only: `gasr_gate_off` (no gate call, always expand), `gasr_gate_no_shortcut` (no top-score shortcut). There is **no `gate_no_deps` row for GASR**: its gate sees only the query and chunk text. |
| LocAgent baseline | **Finish the local fine-tuned Qwen2.5-Coder-7B run** (LocAgent's own pipeline, its own indexes, not pinned to our SHA) and score it with our metrics. |
| Teammates' repo-QA plans | **Job lists are unchanged.** Only `frozen_sha` was updated (see section 6). |

## 2. Where things are

- Code and configs: branch `phase2-next`, worktree `/home/g7/Documents/HANA/phase2-next`. It has **no upstream and has not been pushed**.
- Run in that worktree. `eval_harness/data/{datasets,samples,cache,logs,baselines}` are symlinks to the shared data dir of `/home/g7/Documents/HANA/Context-Engineering-Thesis1/eval_harness/data` (83 indexes there were built at `2350c0b` and are **not reusable**, see 3). `eval_harness/data/reports` is the tracked Phase 2 folder.
- Python: `/home/g7/Documents/HANA/Context-Engineering-Thesis1/.venv/bin/python` (system python has no pytest or tomllib). Prefix commands with `PYTHONPATH=eval_harness/src`.
- LocAgent: stable copy in `/home/g7/Documents/HANA/locagent-baseline/` (see 5). The original scratchpad copy is in `/tmp` and may disappear.

## 3. Before any queue: indexes, smoke, authorisation

1. **Indexes: DONE, nothing to rebuild.** All 83 indexes (SWE-Bench-Lite 27 + Loc-Bench 56 tasks) exist under the new SHA in the shared cache (`index_cache summary ... --pipeline-config eval_harness/configs/pipeline_phase2.toml` reports 83 expected, 83 present, none missing). A full rebuild would have taken about 40 hours. Instead: the only indexer-closure changes are `backend/indexing/code_indexer.py` (adds a best-effort call-graph sidecar after indexing) and `graph_builder.py`; 32 of the 83 indexes (25 repos, the cheap ones) were fresh-built under the new SHA and compared with the `2350c0b` originals: Chroma ids, documents, metadata and embeddings, `bm25_index.json` and `call_graph.json` are byte-identical in 32/32, and the Graphify node and link sets are identical in 32/32 once the build-directory key hash that Graphify embeds in some node ids is normalised. The other 51 were then renamed in place with `index_cache rekey --allow-changed-file backend/indexing/code_indexer.py --allow-changed-file backend/indexing/graph_builder.py --evidence "<this comparison>"` (new option; the evidence and both file hashes are recorded in `data/cache/REKEY_2350c0b_to_caceced.json`). The 32 old originals stay in the cache. Do not run `index_cache build` or `rekey` again unless the backend or index settings change. Large repos can hang in Graphify only on a fresh build (`GRAPHIFY_TIMEOUT_S`).

2. **Smoke** (not a result run): one SWE-Lite task each for GAPA, GASR, and GASR with `--ablation gasr_gate_off`, via `harness.run ... --smoke --limit 1` (arguments as in `Queue.cmd` in `harness/queue.py`). Expect: the retrieval ranked lists equal the `a2cfcec` Phase 2 logs (the Nov 3 logs used different retrieval defaults) except for the intended changes; the gate-off trace shows route STRUCTURE and zero gate tokens. Look at how often GASR neighbours survive at the 0.42 reserve.
3. **Freeze checks:** `git diff --stat cacecedb HEAD -- backend` must print nothing; the worktree must have no uncommitted tracked changes.
4. **`data/queue/record_authorisation.json`** is written with the user's explicit approval, after the final commit of the checkout being run (the user approved on 2026-11-17 that the Phase 2 freeze session writes it): `{"pipeline_sha": "cacecedb1862539822e2009e0b17cb5406fee8b4", "harness_commit": "<git rev-parse HEAD>", "authorised_by": "...", "authorised_utc": "..."}`. Any later commit changes `harness_commit` and invalidates it. Without it only `--smoke` runs.

## 4. Ablation queue (GAPA rerun and GASR)

`eval_harness/queues/phase2-ablations.toml`: 28 jobs (7 settings x 2 arms x SWE-Bench-Lite 27 + Loc-Bench 56), `qwen3.5:9b`, location instruction ON, hard stop 2026-12-01 Cairo, estimates sum to about 7.5 hours (14 x 10 min SWE-Lite + 14 x 22 min Loc-Bench; copied from Nov 3 timings, not timed on the new freeze). Dry-run validated (`--dry-run` accepts all 28 jobs). Launch:
```
PYTHONPATH=eval_harness/src $PY -m harness.queue --profile eval_harness/profiles/ubuntu-4080.toml \
  --queue eval_harness/queues/phase2-ablations.toml --pipeline-config eval_harness/configs/pipeline_phase2.toml \
  --configs eval_harness/configs --cwd .
```
The queue is resume-safe. A `--dry-run` writes empty job dirs and `data/queue/events.jsonl`: delete them (`data/runs/caceced`, `data/queue`) before a real start.
Reports: `python -m harness.metrics ...` writes `table_ix_ablation*.csv`. The GAPA / SWE-Bench-Lite file keeps its old name; other combinations are `table_ix_ablation_<arm>_<dataset>_<model>.csv`. The old Nov 6 GAPA ablation jobs (`/home/g7/Documents/HANA/nov6-queue/`) ran on `2350c0b`: do not merge them with these.

## 5. LocAgent baseline

State of the run (VERIFIED from files): LocAgent `auto_search_main` (repo commit `4935b55`), model `ft-qwen-7B` served by a **custom int8 HuggingFace server on port 8011** (`locagent-baseline/scripts_from_session/hf_server.py`, `load_in_8bit`, checkpoint `locagent_run/model_cl7b`), function calling on, `num_samples=2`, `max_attempt_num=1`, `num_processes=1`, `timeout=600`, local dataset `inputs/ours83.jsonl` (our 27 SWE-Lite + 56 Loc-Bench tasks). Output `locagent_run/full83/loc_outputs.jsonl` has **79 of 83 tasks**. The log stops mid-call (interrupted, not a model error). Missing: `vllm-project__vllm-9390` (was in progress), `yt-dlp__yt-dlp-11438`, `-11711`, `-11782` (never started). Current scores through the adapter (SWE-Lite 27/27, Loc-Bench 52/56 tasks; four missing count as 0): see `eval_harness/data/baselines/locagent_*.json`.

To finish: restart `hf_server.py` (same int8 setup; port 8011, needs about 8 GB VRAM and the GPU free), then re-run `locagent_run/run_locagent_ours.py` with the same arguments as `full83/args.json` (resume skips processed instances; set `GRAPH_INDEX_DIR=locagent_run/index/ours83/graph_index_v2.3`, `BM25_INDEX_DIR=locagent_run/index/ours83/BM25_index`, `OPENAI_API_BASE=http://localhost:8011/v1`). The venv is still in the old `/tmp` scratchpad (`.../a35b3a50.../scratchpad/locagent_run/venv`); `locagent-baseline/venv_requirements.txt` lets you rebuild it. **Do not switch the serving backend** (for example to the Ollama `ft-qwen-7B`) without saying so: it changes the baseline. If `vllm-9390` stalls again, record the failure, do not edit LocAgent.

Scoring: `PYTHONPATH=eval_harness/src $PY -m harness.locagent_baseline --loc-outputs <loc_outputs.jsonl> --data-dir eval_harness/data` merges the two samples with LocAgent's MRR ranking, scores with the harness `acc_at`, and writes `data/baselines/locagent_<dataset>.json`; `harness.metrics` then fills the LocAgent row in Tables VII/VIII. It is **not** an apples-to-apples row: own indexes, own model server, different method (agent vs single pass).

## 6. What changed on this branch (and what did not)

- `backend/`: only patch 0004 (two `ABL_GASR_*` switches, default off) plus docstring-only edits on top of `ca843a4`.
- `eval_harness/configs/pipeline_phase2.toml`: new SHA, reserve 0.42, new env names, patch keys. `ablations.toml`: per-setting `arms`. `extras.toml`: answer-format instruction now has a `path::Class.method` example (used only by jobs with `answer_locations_instruction = true`: localization, not repo-QA). `metrics.py`: Loc-Bench File@10, Module@15, Function@15; Table VII/VIII placeholder rows now have the right width.
- Relaxed scoring (`--relaxed`, commit from `answer-scoring-relaxed`) is included; strict stays the headline.
- **Teammates' queues:** `phase2-sweqa-w1.toml`, `phase2-sweqa-rest.toml` and `phase2-repoprobe-py.toml` have only `frozen_sha` changed to the new SHA (job lists untouched), so every Phase 2 queue and `pipeline_phase2.toml` carry one SHA, `cacecedb`; the Phase 1 queues `nov3-83.toml` and `ubuntu-4080.toml` stay on `2350c0b` as the record of those runs. Those three repo-QA queues already produced their results on `a2cfcec`; rerunning them on the new freeze is a separate decision for the user.
- `ALWAYS_LC_RANKED` is **redundant** now: on synthetic data the backend packing alone gives the same file blocks (`eval_harness/tests/test_lc_ranked_matches_backend.py`). Keep the arm out of the main tables.
- Not done: a full 7-arm localization queue on the new freeze (`nov3-83.toml` targets `2350c0b`), the stale `PROPOSED_all-models_3bbc52f.toml` queue is still present (deletion was blocked), nothing has been pushed.
- Known failing tests, unchanged by this branch: three `test_pruning.py` tests (`test_heuristic_weights_default`, `test_score_candidate_with_custom_weights`, `test_score_candidate_returns_float`) assert pre-Pareto default weights and already fail at `ca843a4`.

## 7. Who runs what, and GPU sharing

One RTX 4080 (16 GB). **One GPU job at a time**: the ablation queue and the LocAgent server must not overlap. Do not push `phase2-next`, change `frozen_sha`, rewrite `record_authorisation.json` or touch `backend/` without asking the user.

Agreed schedule (user, 2026-11-17). Order = priority, then impact, then longest first; overnight takes the longest runs, to be reviewed in the morning.

| Slot | Who | What | GPU | Est. |
|---|---|---|---|---|
| 1 (first) | session `context-engineering-thesis1-7c` | Finish LocAgent: run the 4 missing Loc-Bench tasks, score with `harness.locagent_baseline`, report numbers. A preliminary comparison against the Nov 3 numbers (old freeze, labelled) can be written without the GPU. | int8 server, 8 GB | 1 to 3 h (median 12 min per task earlier; server start extra) |
| 2 | session `Nov6 Table IX ablation queue` | Smoke test, then queue jobs 1 to 2 (SWE-Lite `full` GAPA and GASR). The queue then **holds** (`hold_after`): a person reviews, deletes `data/queue/STOPPED` and reruns. | Ollama qwen3.5:9b | about 20 min |
| 3 (overnight) | same session | Jobs 3 to 28 (Loc-Bench `full` rows, then gate rows, graph rows, `truncation`, `no_keyword_channel`). By morning jobs 1 to 12 (full + all gate rows) should be done and are the first thing to review. | Ollama qwen3.5:9b | about 7 h (whole queue 448 min of estimates, x1.25 safety) |
| 4 (after jobs 1 to 4 exist) | 7c or the user | Final LocAgent comparison against our GAPA and GASR `full` rows on this freeze (Tables VII/VIII). | none | short |

Start slot 2 only after slot 1's server is stopped and the user (or the session holding the user's approval) says go. Job order inside `phase2-ablations.toml`: P0 SWE-Lite full rows (checkpoint), P1 Loc-Bench full rows, P2 gate rows (`gasr_gate_off`, `gate_no_scores`, `gate_no_deps`, `gasr_gate_no_shortcut`), P3 `no_graph_expansion` and `contains_only`, P4 `truncation` and `no_keyword_channel`; Loc-Bench before SWE-Lite inside a tier because it runs about 2.2x longer.

## 8. Lightweight lookup tools (found during the first queue run, 2026-11-17)

`run_pipeline(use_lightweight_tools=True)` is the backend default. `_lightweight_request` matches loose regexes against the prompt and, on a match,
calls `find_symbol` / `find_references` / `list_files` instead of `retrieve_context`. Full issue texts match by accident (7 tasks of the samples:
SWE-Lite django-15252, matplotlib-26011, sympy-18532; Loc-Bench langgraph-2571, pandas-21401, streamlit-6617, vllm-10903). Those tasks fail with
`structure_rerank_not_applied` or a chroma "too many SQL variables" error (`find_symbol` reads the whole collection). It is absent at 2350c0b.

Decision: the backend default is NOT changed (teammates' repo-QA questions can be real lookup requests). Instead the harness has an opt-in per job:
`no_lightweight_tools = true` in the queue job -> `--no-lightweight-tools` -> `run_pipeline(..., use_lightweight_tools=False)` in the worker.
Default off, so every existing queue keeps its behaviour. Harness arms (ALWAYS_RAG/LC/GRAPH etc.) call `retrieve_context` directly and are not affected.
`phase2-ablations.toml` sets it for all jobs. A job may also name `sample_file` (a file under data/samples) to run a subset of tasks.
Separate backend bug for the next backend freeze: `find_symbol` should not read the whole collection on large indexes.
