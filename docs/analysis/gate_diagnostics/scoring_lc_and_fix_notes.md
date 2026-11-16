# Scoring rules, LC mechanics, candidate fixes, and what an LC-v2 run would need

Text notes for the follow-up request. Nothing here was implemented. VERIFIED = read from code/logs/computed; INFERRED = a reading or a prediction.

## 1. LOCATIONS instruction (exact text) and the class prefix

Source: `eval_harness/configs/extras.toml:17`; it is what the Nov 3 jobs used (logged in every results row as `extras.answer_suffix`, VERIFIED), appended to the task as `user_prompt + "\n\n" + suffix` (`eval_harness/src/harness/task_worker.py:37-40`).

> Begin your answer with a section titled LOCATIONS. List, one per line and most likely first, each file that should be edited to resolve this issue, written as path, or as path::Class.method where a function applies. Then write the line END_LOCATIONS and continue with a short explanation of the fix.

It does ask for the class prefix where a function applies (`path::Class.method`) (VERIFIED). It does not say what to write for a top-level function or a nested one. The scorer then turns `path::qual` into `path:qual` (`answer_scoring.py:89-100`, `normalise`). Models often write `path::method` for methods; strict scoring counts that as a miss (VERIFIED in the relaxed-scoring report: 8 task flips).

## 2. Acc@k definitions

**LocAgent evaluation code** (`evaluation/eval_metric.py`, master as fetched 2026-11-07, not pinned to a commit; line numbers are for that copy):

- `acc_at_k` (lines 66-74): `ideal_target = ideal_target[:, :k]` (line 68), `total_relevant = (ideal_target == 1).sum(dim=-1)` (71), `comparison = relevant == total_relevant` (73), returned as the mean over queries. So a query is correct when hits in the top k equal the number of gold items inside the first k gold slots, i.e. min(#gold, k).
- Gold labels (lines 228-231 / 354-357): the first `max_k` gold items get label 1. A prediction is a hit when `l in gt_dict[instance_id]` (233 / 359): exact string membership.
- File level: gold is the file path; predictions are reduced to `.py` paths by `extract_file_path` (129).
- Module level (lines 300-302): `mname = func.split(':')[-1].split('.')[0]`, `mid = f'{fn}:{mname}'`, i.e. the module is `path:Class` (first dotted component).
- Function level (lines 307-311): `mname = func.split(':')[-1]`, only a trailing `.__init__` is stripped, `mid = f'{fn}:{mname}'`. Predictions get the same stripping (189-190, 204-205, 327-328). There is no leniency on the class prefix: `Class.method` and `method` are different strings (VERIFIED from code).
- `recall_at_k` (58-63) slices the prediction to k but not the gold, so its denominator is min(#gold, max_k) rather than min(#gold, k) (VERIFIED). Our `metrics.recall_at` uses min(#gold, k); for the k = 10 used here with max_k = 10 they coincide.

**LocAgent / Loc-Bench paper** (arXiv 2503.09089, via a page fetch and summary, not read in full by me; INFERRED until you check the wording): Acc@k is "a modified accuracy inspired by R-Precision"; a localization attempt succeeds "only if all relevant locations are correctly identified" in the top k; module localization only requires any function in the patched class; Loc-Bench uses file Acc@5/10 and function/module Acc@10/15.

**Our scorer** (VERIFIED): exact `path:Class.method` match after normalisation (`answer_scoring.py:89-100`), then `metrics.acc_at` = `hits_at(pred, gold, k) == min(len(set(gold)), k)` (`metrics.py:61-62`). That is the same cap rule as LocAgent. "All gold within the top 10" is therefore literally true only when #gold ≤ 10; no task among the 83 has more than 9 gold functions (max 9, VERIFIED), so for these tasks it is all gold.

## 3. LC parent files: the remaining checks

- **Repeat per retrieved chunk (VERIFIED).** `_build_lc_chunks` (`arms.py:67`) emits one item per retrieved chunk, each carrying the whole stitched parent text, and `PromptBuilder` groups items by path without removing duplicates (`prompt_builder.py:100-112`). Rebuilding the LC prompt from the index reproduces the logged `inject.prompt_chars` to within 0.06% on all 83 tasks (median error 0.0000) when the parent text is repeated once per retrieved chunk; with one copy per file the mean error is 37% and 19 tasks have a different truncation flag. Example: sphinx-doc__sphinx-8801 (5 chunks in one file): logged 14,168 chars, rebuilt 14,165 with repeats vs 4,527 without. Five such tasks are in `lc_rank_order_report.md`. The prompt text itself is not logged, so this is a size reconstruction, not a text comparison.
- **Collection name (VERIFIED).** `reconstruct_file_context` reads the module-level `collection` (`retrieval_tool.py:87-90`), which is `get_or_create_collection(name=COLLECTION_NAME)`, `COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "code_embeddings")` (`retrieval_tool.py:36`). The logged environment of every row shows `CHROMA_COLLECTION = code_embeddings` (source: code default).
- **Order and drop rule (VERIFIED, `injection/prompt_builder.py` at the frozen SHA).** `format_context_body` groups chunks by path and orders paths with `sorted(grouped.keys(), key=lambda p: (p == "(unknown path)", p))` (`prompt_builder.py:129`), i.e. alphabetically, not by retrieval rank. `_truncate_file_blocks` (line 141) walks the blocks in that order: the first block is always kept (if it alone exceeds the budget its lines are cut from the bottom, `_truncate_single_block`, line 178); each later block is kept only while `total + tokens <= 24000`, and the loop `break`s (line 165) at the first block that does not fit, so every later block is dropped even if it is small. When anything was dropped or the first block was cut, a `<CONTEXT_TRUNCATED>` notice is prepended. Token estimate is `len(text) // 4` (`default_token_estimate`, line 57). The budget is `MAX_CONTEXT_TOKENS`, default 24000 (`prompt_builder_endpoint.py`).
- **Within a file** the cap is the first 10 chunks in start-line order (`retrieval_tool.py:67`, `reconstruct_file_context` line 914), so large files lose their later functions before the budget is even reached.

## 4. Offline LC order simulation (summary; details in `lc_rank_order_report.md`)

Calibration: rebuilt prompt size vs logged `prompt_chars`, 83 tasks, median error 0.0000, max 0.06%; truncation flag agrees on 83 of 83. The budget's `chars // 4` estimate versus the model's real prompt tokens: median ratio 1.026 (range 0.67 to 1.24), so the estimate is close at the median but off by up to about +24% / -33% per task.

Order effect on gold survival, alphabetical versus retrieval-rank order (VERIFIED, simulation):

| | SWE (27) | Loc (56) |
|---|---|---|
| tasks with the budget notice | 10 | 30 |
| all gold files kept, alphabetical | 13 | 21 |
| all gold files kept, rank order | 12 | 26 |
| gold functions kept (sum), alphabetical | 9 | 25 |
| gold functions kept (sum), rank order | 8 | 32 |
| tasks where a gold file or function survival changes | 3 | 8 |

Reading (INFERRED): rank order helps most on Loc, and costs a little on SWE (two SWE tasks lose a gold file, one gains). Rank order changes only which files survive the budget, not the 10-chunk cap, which removes 5 SWE and 10 Loc gold functions on its own.

## 5. What a real LC-v2 run would need, and its cost (not launched)

- **Code:** the order is set inside the frozen backend (`prompt_builder.py`). Options: (a) a backend patch, which creates a new frozen SHA and breaks the "frozen pipeline" provenance for that arm; or (b) a harness-side wrapper in the style of `task_worker.py:37-40` that replaces `PromptBuilder.format_context_body` (or pre-renders the context) for a new arm, plus a new arm name in `arms.py` (`HARNESS_ARMS`, `run_harness_arm`), the arm list in `configs/extras.toml`, and a queue entry. Option (b) keeps the backend untouched.
- **Optional separate levers** (should be separate arms so effects are not confounded): drop the per-chunk repetition, raise the 10-chunk cap.
- **Cost (INFERRED from the Nov 3 logs):** the two Always-LC jobs took 594 s (SWE) and 805 s (Loc) from first task start to last task end, 0.39 h in total, with the same machine, model digest and indexes; add a few minutes of preflight. An LC-v2 arm alone is therefore about 0.4 to 0.5 h. Re-running the gated arms (GASR, GAPA, Self-Route, PREASSESS) would be needed to see the effect on their LC-routed tasks; I have not estimated that, since each of those runs has its own gate and expansion calls.
- It needs the Ollama model and GPU, so it is outside the "no model runs" rule of this request; nothing was started.

## 6. Candidate fixes, text only (nothing implemented)

Pruning and expansion (the evidence is in `pruning_trace_report.md`):

1. **The largest loss is the count cut, not the pruner.** `retrieve_context(top_k=5)` returns 5 chunks (`retrieval_tool.py:976, 992`) and the pruner only chooses among those. For Always-RAG, function-complete tasks fall from 10 to 7 of 27 (SWE) and 24 to 15 of 56 (Loc) at that cut, versus 0 and 1 at pruning. Candidate: pass a larger `top_k` (for example 10 to 15) into the pruner so that its 1800-token budget, not a chunk count, decides. (Expected effect INFERRED; it would also change the shared retrieval for all arms.)
2. **Neighbour reserve is smaller than one chunk.** The reserve is `int(1800 × 0.30) = 540` tokens; windowed neighbour chunks cost 381 to 679 tokens, so one neighbour fits and the rest are skipped. All 5 gold functions that expansion added in Always-GRAPH were dropped for this reason (3 SWE, 2 Loc): 3 had a chunk larger than the entire reserve, 2 found the reserve used up. Candidates: cap the window per neighbour (currently up to 40 lines), raise the fraction, let unused seed budget roll over to neighbours (only the reverse roll-over exists), or let neighbours and seeds compete in one pool.
3. **Edge-type priorities are dead code.** `_chunk_from_chroma_row` creates neighbours without `relation_type` or `edge_type`, so every neighbour scores 0.65 (0.70 with the name bonus). Candidate: have `graph_expand` copy the graph edge type onto each chunk, or score neighbours with query overlap as well.
4. **File-level graph nodes** pull up to 3 arbitrary chunks of the file (`paired[:3]`, `limit=5`). Candidate: choose the chunk that overlaps the seed or the query terms.
5. **Class prefix in answers:** keep strict as primary; report the leaf-name variant as secondary (done on the branch `answer-scoring-relaxed`), and consider asking for `Class.method` more explicitly in the instruction for a future run.
