# Pipeline fixes: handover for the next person

Written 2026-11-08. Plain version: **what we ran, how to get back to it, and what is still wrong with the pipeline, in the order I would fix it.**
Every claim is marked VERIFIED (read from code or logs, or computed) or INFERRED (my reading or a prediction).

---

## 1. The go-back point (do not lose this)

The Nov 3 results (83 tasks x 7 arms, model `qwen3.5:9b`) were made with exactly this code:

| What | Commit | Where on GitHub |
|---|---|---|
| Harness that ran Nov 3 | `7c92a0b55a5c` | tag `harness-nov3-runs` (VERIFIED, on `origin`) |
| Same code plus docs only | `a75f180` | `origin/integration-oct28` tip (VERIFIED: no file outside `docs/` differs from `7c92a0b`) |
| Frozen pipeline (the `backend/` folder) | `2350c0b2e064` | on `origin/integration-oct28` (VERIFIED). `backend/` is byte-identical in `2350c0b`, `7c92a0b` and `a75f180` (same tree hash `54ed09065939`) |

How the pipeline commit was built, oldest first (all on `origin/integration-oct28`):

1. `166dbd01` : `origin/main` as frozen by the team (the base)
2. `50cf149e` : GAPA expands from the same seeds as GASR
3. `cd84fad1` : merge of that branch into `eval-harness` (revoked as a run SHA)
4. `3bbc52f2` : `CHROMA_PATH` can be set from the environment (revoked as a run SHA)
5. `2350c0b2` : Graphify path-prefix switch and six ablation switches, all off by default (**the frozen SHA**)

Older tags if you ever need them: `harness-oct23-runs` (9c8ff12), `harness-oct23-scorer-v2` (9208675).

The full record (config hashes, model digest, GPU, file hashes of every Nov 3 log) is in `docs/handover/baseline_snapshot.json` in this same commit. **The Nov 3 logs and reports live in `eval_harness/data/`, which git ignores.** Back that folder up before you change anything. The snapshot file has SHA-256 hashes so you can check a restored copy.

To go back at any time:

```
git switch --detach harness-nov3-runs
git diff --stat 2350c0b2e064e4fd808e941731366e6cc71a9551 HEAD -- backend    # must print nothing
```

## 2. Which commits to start from

- **Start from `origin/integration-oct28` (`a75f180`).** It holds the harness, the frozen pipeline and the Nov 3 queue.
- **Do not take `origin/main` yet.** It moved after we froze: it is now `9630285`, and `166dbd0..9630285` changes `backend/retrieval/retrieval_tool.py` (+126 lines), `backend/mcp_server/server.py` (+152) and `tool_registry.py` (+4) ("lightweight lookup tools"). VERIFIED by `git diff --stat`. Taking it would change retrieval, so it needs its own decision and a new frozen SHA.
- **Do not use** `queues/PROPOSED_all-models_3bbc52f.toml`: it is for a SHA that is now revoked (VERIFIED in `configs/pipeline.toml`).
- **Optional, not on GitHub yet:** branch `answer-scoring-relaxed` (commit `bc7a64b`) adds a secondary scoring mode (`--relaxed`). It only touches `eval_harness/src/harness/answer_scoring.py` and its tests, and strict scoring stays the default. It exists on one machine only; `git cherry-pick bc7a64b` if you want it.

## 3. The fixes, in the order I would do them

Each one says what is wrong, the evidence, where it is, and what to do. "New SHA" means the change is inside `backend/` and so needs a new frozen pipeline commit (see section 4).

### Fix 1. Retrieval hands over only 5 chunks (biggest loss, new SHA or call argument)
- **Evidence (VERIFIED):** Always-RAG gold-complete tasks (function Acc@10): SWE 10 of 27 in the top-10 list, 7 after the cut to 5 chunks; Loc 24 of 56 down to 15. The pruner then loses only 0 (SWE) and 1 (Loc) more.
- **Where:** `backend/retrieval/retrieval_tool.py:976, 992` (`top_k=5`, `ranked[:5]`).
- **Do:** return more chunks (try 10 to 15). Then the 1800-token budget starts to bind (INFERRED): a pool chunk costs a median 576 tokens, so 10 chunks is about 5,800. Raise the budget together with `top_k`, and test them together.
- **Check first:** whether `arms.py` can pass `top_k` without touching `backend/` (I did not check).

### Fix 2. The long-context (LC) prompt sorts files alphabetically and stops at the first file that does not fit (new SHA, or a harness workaround)
- **Evidence (VERIFIED by offline rebuild of all 83 prompts):** the 24,000-token notice fires on 10 of 27 SWE and 30 of 56 Loc LC prompts. Switching to retrieval-rank order keeps all gold files in more Loc tasks (21 to 26 of 56) and more gold functions (25 to 32), but is slightly worse on SWE (files 13 to 12, functions 9 to 8). 3 SWE and 8 Loc tasks change.
- **Where:** `backend/injection/prompt_builder.py:129` (the sort) and `:141-165` (the loop that `break`s at the first file that does not fit).
- **Do:** either patch the backend (new SHA), or in `eval_harness/src/harness/arms.py` pick the files in rank order under the budget *before* calling `inject_context`, so the backend never truncates (no SHA change, but the harness commit changes). The sort also reorders RAG and GRAPH contexts (nothing is dropped there). Whether that matters is not measured (INFERRED).

### Fix 3. LC repeats the same file once per retrieved chunk (harness only, easiest)
- **Evidence (VERIFIED):** rebuilding the prompt with repeats matches the logged prompt size within 0.06% on 83 of 83 tasks; with one copy per file it is off by 37% on average.
- **Where:** `eval_harness/src/harness/arms.py:67` (`_build_lc_chunks`).
- **Do:** one entry per file. This frees budget and is the safest first change. Make it its own arm or flag so its effect is not mixed with Fix 2.

### Fix 4. A file is cut to its first 10 chunks, in line order (new SHA)
- **Evidence (VERIFIED):** this cap alone removes 5 SWE and 10 Loc gold functions from the LC context.
- **Where:** `backend/retrieval/retrieval_tool.py:67` (`MAX_CONTEXT_CHUNKS_PER_FILE`) and `reconstruct_file_context` (line 914).
- **Do:** keep the chunks nearest to the retrieved ones, not the first ten.

### Fix 5. The answer format loses easy points (prompt only, no pipeline change)
- **Evidence (VERIFIED):** the instruction (`eval_harness/configs/extras.toml:17`) asks for `path::Class.method`, but models often write `path::method`. Strict scoring marks that wrong. Leaf-name matching flips 1 to 2 SWE and 4 to 5 Loc tasks per tier from 0 to 1. For Loc, 8 of 20 leaf-only matches are ambiguous (the same name is defined more than once in the file), so treat the relaxed number as an upper bound.
- **Do:** make the instruction more explicit, with one example that includes the class name. Keep strict scoring as the headline number.

### Fix 6. Graph expansion adds the right function and then throws it away (low priority, new SHA)
- **Evidence (VERIFIED by replay, 161 of 161 hops match the logs):** only 5 of 83 tasks get a gold function from expansion, and all 5 are dropped: 3 are larger than the whole 540-token neighbour reserve, 2 find it already used up. A pool chunk costs a median 576 tokens, so the reserve holds about one chunk.
- **Also (VERIFIED):** the graph's edge type (calls, contains, ...) is thrown away before scoring (`backend/retrieval/graph_expand.py:42`; `graphify_adapter.py` `neighbors()` returns node keys only). Every neighbour scores the same default.
- **Simulation:** passing the edge types on changes which neighbour is kept in 13 SWE and 18 Loc tasks but finds about the same gold (SWE 9 to 9, Loc 22 to 23 gold functions). A bigger reserve (up to the whole 1800) adds at most one gold function per dataset. So do Fix 1 first; this one is worth little on its own.

### Fix 7. Reporting and housekeeping
- Loc-Bench: we score File@5, Module@10, Function@10 only. LocAgent's Table 7 also has File@10, Module@15 and Function@15. Add them if we want a like-for-like table.
- Remove or mark the stale 3bbc52f queue file. Decide about `origin/main` (section 2).

## 4. How to ship a fix without breaking the run rules (VERIFIED from `eval_harness/src/harness/pipelineenv.py` and `run.py`)

- A run refuses to start if `git diff --stat <frozen_sha> HEAD -- backend` is not empty, if tracked files are dirty, or if the harness commit is on the revoked list.
- **Change only the harness (Fix 3, Fix 5, and the Fix 2 workaround):** commit it, then write a new `data/queue/record_authorisation.json` whose `pipeline_sha` and `harness_commit` match what you run. No new SHA.
- **Change `backend/` (Fixes 1, 2 patch, 4, 6):**
  1. Add a patch file next to `eval_harness/patches/0001-*.patch` and `0002-*.patch`, and make a new pipeline commit.
  2. Update `frozen_sha` and `record_results_pipeline_sha` in `eval_harness/configs/pipeline.toml`; move `2350c0b` into the previous/revoked lists.
  3. Reuse the indexes with `index_cache rekey` (`index_cache.py:375`). It refuses if any file the indexer loads differs between the two SHAs. `prompt_builder.py` is not part of the indexer, so for Fix 2 it should pass (INFERRED; run the dry-run option first).
  4. Re-extract the snapshot under `data/cache/pipeline/<sha>/backend` and write the new authorisation file.
  5. Run a small `--smoke` job. Retrieval and expansion do not depend on the model, so the ranked lists must match the Nov 3 logs exactly. If they do, only the intended change moved.
- Run the baseline on any extra model **before** you bump the SHA; after a bump the old SHA is revoked.

## 5. What to compare after the fixes

- Same model (`qwen3.5:9b`, digest `6488c96fa5fa...`), same 83 tasks, same instruction text. All 7 arms are affected by the prompt builder, so rerun all of them.
- Predictions to check against (from the offline simulation): LC order change touches 3 SWE and 8 Loc tasks; Fix 1 should lift the share of gold-complete contexts above 7/27 and 15/56 (INFERRED: it removes the largest measured loss, but nothing has been run).
- For context, on the same 27 SWE tasks LocAgent (Claude-3.5, from the outputs shipped in its repo) gets function Acc@10 70.4; our fixed tiers get 18.5 to 25.9. That gap is mostly model and method, so do not expect these fixes to close it.

## 6. Where the evidence is

All scripts and reports are in `docs/analysis/gate_diagnostics/` on the author's machine. **They are not committed yet**, so ask for them or have them committed before relying on them: `gold_loss_funnel.md`, `all_tiers_zero_artefact.md`, `tier_contexts_summary.md`, `relaxed_scoring_report.md`, `lc_rank_order_report.md`, `pruning_trace_report.md`, `edge_budget_report.md`, `scoring_lc_and_fix_notes.md`, `locagent_comparison.md`, `locagent_same_task.md`.
