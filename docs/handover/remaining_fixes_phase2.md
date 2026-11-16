# Remaining backend fixes after `phase2-eval`

Written 2026-11-16. Handover for whoever takes the **backend** items left over from
[`pipeline_fixes_handover.md`](pipeline_fixes_handover.md) (2026-11-08).
Compared against `origin/phase2-eval` (tip `4b1d068`, pipeline freeze `a2cfcec`).

**Update 2026-11-16: items B, C and D below were implemented in `ca843a4` (branch `phase2-backend-fixes`, patch `eval_harness/patches/0003-*`), reviewed, and are part of the new freeze on branch `phase2-next`.** The prompt file order is rank order by decision (the earlier alphabetical order was an accidental artefact, not a design choice; no legacy switch is kept). The text below is the original problem statement, kept for the evidence.

Harness-side fixes (answer-format instruction, reporting, queue/config wiring) are handled separately by the harness owner. See section 5.

Marking: **VERIFIED** = read from the code or diff on `origin/phase2-eval`. **INFERRED** = my reading, not run. Nothing here has been executed.

## 1. Backend status of the original fixes

| # | Fix | Status on `phase2-eval` |
|---|---|---|
| 1 | Retrieval hands over only 5 chunks | **Mostly done.** `retrieve_context` default `top_k` is 47 (`backend/retrieval/retrieval_tool.py:978`, `retrieval_tool_endpoint.py`). Token budget went **down** 1800 -> 1300 (`backend/pruning/file.py`), not up as the handover suggested. Untested together. |
| 2 | LC sorts files alphabetically, stops at first file that does not fit | **Not fixed in backend.** Only a harness workaround exists (`ALWAYS_LC_RANKED`). `backend/injection/prompt_builder.py` is unchanged. |
| 4 | File cut to first 10 chunks in line order | **Not done.** `MAX_CONTEXT_CHUNKS_PER_FILE = 10` at `retrieval_tool.py:67`, slice at `:948`. |
| 6 | Graph expansion adds the gold function, then drops it | **Not addressed.** Phase 2 added `backend/retrieval/graph_rerank.py`, a retrieval-side rerank, which is a different thing. |

## 2. Work items, in the order I would do them

### B. Per-file chunk cap (was Fix 4)
- **Problem (VERIFIED on Nov 3 data):** the cap alone removed 5 SWE and 10 Loc gold functions from the LC context.
- **Where:** `backend/retrieval/retrieval_tool.py:67` (`MAX_CONTEXT_CHUNKS_PER_FILE`) and `reconstruct_file_context` (~`:914-948`).
- **Do:** keep the chunks nearest to the retrieved ones, not the first ten in line order.
- **Open question:** with `top_k=47`, more files and chunks reach LC. Re-measure the loss on the new freeze before assuming the Nov 3 numbers hold (INFERRED).

### C. LC truncation in the prompt builder (was Fix 2)
- **Problem (VERIFIED by offline rebuild of the 83 Nov 3 prompts):** the 24,000-token notice fires on 10 of 27 SWE and 30 of 56 Loc LC prompts. Rank order keeps all gold files in more Loc tasks (21 -> 26 of 56) and more gold functions (25 -> 32), but is slightly worse on SWE (files 13 -> 12, functions 9 -> 8).
- **Where:** `backend/injection/prompt_builder.py:129` (the sort) and `:141-165` (the loop that `break`s at the first file that does not fit).
- **Do:** pick files in rank order under the budget and skip, rather than stop at, a file that does not fit. The sort also reorders RAG and GRAPH contexts (nothing is dropped there); whether that matters is not measured (INFERRED).
- **Coordinate with the harness owner:** `eval_harness/src/harness/arms.py` has `_select_lc_chunks_by_rank`, which copies the PromptBuilder token estimate (`len(block) // 4`) and the block format. If you change either in the backend, that copy drifts. Either make the harness call the backend logic, or add a test that fails if the two disagree. Once the backend is fixed, `ALWAYS_LC_RANKED` is redundant.

### D. Graph expansion drops gold (was Fix 6), low priority
- **Evidence (Nov 8, VERIFIED by replay, 161 of 161 hops matched the logs):** 5 of 83 tasks get a gold function from expansion and all 5 are dropped. 3 are larger than the whole 540-token neighbour reserve; 2 find it already used up.
- **Also:** the edge type (calls, contains, ...) is thrown away before scoring: `backend/retrieval/graph_expand.py:42`, and `graphify_adapter.py` `neighbors()` returns node keys only. Every neighbour gets the same default score.
- **New concern (INFERRED):** the reserve is `GASR_NEIGHBOR_BUDGET_FRACTION = 0.30` of the budget. At 1300 tokens that is about 390, down from 540, so the problem is probably worse now. Re-run the replay on the new freeze before spending time here.
- **Expected gain:** small on its own. Simulation on Nov 3 data: passing edge types changed which neighbour was kept in 13 SWE and 18 Loc tasks but found about the same gold (SWE 9 -> 9, Loc 22 -> 23); a larger reserve added at most one gold function per dataset.

## 3. Things to check before or alongside the fixes

1. **Budget vs `top_k`.** 47 chunks into a 1300-token budget means the pruner now does the heavy cutting. The Nov 8 gold-loss funnel (retrieval -> top-k -> pruner) was measured with `top_k=5` and an 1800 budget. Re-run it on one smoke run (`docs/analysis/gate_diagnostics/gold_loss_funnel.md` and `build_funnel_floor.py`, if committed) to see where gold is lost now. The right `top_k` / budget pair may differ from the Pareto defaults for localization.
2. **Index reuse.** `backend/indexing/code_indexer.py` now also writes `call_graph.json`, and `pipeline_phase2.toml` sets `require_call_graph_json = true`. Indexes built under `2350c0b` will not satisfy that. `index_cache rekey` refuses if any file the indexer loads differs between SHAs, so expect to reindex. Run its dry-run first. A change limited to `prompt_builder.py` (item C) should not trip it (INFERRED, run the dry-run to confirm).
3. **Graph mode.** `retrieve_context(mode="graph")` and `graph_rerank.py` are new and off by default. If localization runs should use them, that needs its own freeze decision and weights (`GRAPH_RERANK_ALPHA` default 0.65).

## 4. Shipping a backend change (rules are enforced by `pipelineenv.py` / `run.py`)

A run refuses to start if `git diff --stat <frozen_sha> HEAD -- backend` is non-empty, tracked files are dirty, or the harness commit is on the revoked list.

1. Add a patch file next to `eval_harness/patches/0001-*.patch` and `0002-*.patch`, and make a new pipeline commit.
2. Update `frozen_sha` and `record_results_pipeline_sha` in the pipeline config in use (`pipeline_phase2.toml` for Phase 2). Move the old SHA into the previous/revoked lists.
3. Reuse indexes with `index_cache rekey` if it passes; otherwise reindex.
4. Re-extract the snapshot under `data/cache/pipeline/<sha>/backend` and write a new `data/queue/record_authorisation.json`.
5. Run a small `--smoke` job. Retrieval and expansion do not depend on the model, so ranked lists must match the previous freeze exactly except for the intended change.
6. Batch the backend changes into **one** new SHA if you can. Each bump revokes the previous SHA and forces a full rerun of all 7 arms.

## 5. Out of scope here (harness owner)

Answer-format instruction (`extras.toml`), Loc-Bench @10/@15 reporting, removing the stale `PROPOSED_all-models_3bbc52f.toml` queue, choosing between `pipeline.toml` and `pipeline_phase2.toml` for localization, and cherry-picking `bc7a64b` (relaxed scoring).

## 6. Before you start

- Start from `origin/phase2-eval`.
- Back up `eval_harness/data/` (git ignores it; it holds the Nov 3 logs). SHA-256 hashes are in `docs/handover/baseline_snapshot.json`.
- The Nov 8 evidence scripts and reports are in `docs/analysis/gate_diagnostics/` on the original author's machine and may not be committed. Ask for them before relying on the numbers above.
