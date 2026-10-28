# Nov 3 analysis (nov3-* jobs only)

Run family: Qwen3.5 9B, harness `7c92a0b`, pipeline `2350c0b`, 27 SWE-Bench-Lite and 56 Loc-Bench tasks, seven arms.
Inputs are read-only: `eval_harness/data/runs/2350c0b/nov3-*/results.jsonl` (task logs) and `eval_harness/data/reports/nov3_83/`
(`per_task.csv`, `answer_scores.csv`, `table_iv/v/vi*.csv`). Oct 23 jobs are never read.

## Regeneration
Run from `eval_harness/` for steps 1-2 (they overwrite `data/reports/nov3_83`; use another `--out` for a dry run and then set
`NOV3_REPORT=<that folder>` for step 3). Pass only the `nov3-*` job directories: the folder also holds the Oct 23 jobs, and `harness.metrics` refuses a mixed set (`MergeError`).

```
PYTHONPATH=src ../.venv/bin/python -m harness.metrics --runs data/runs/2350c0b/nov3-* --data-dir data --out data/reports/nov3_83 --lc-scorer v2 --with-new-arms
PYTHONPATH=src ../.venv/bin/python -m harness.answer_scoring --runs data/runs/2350c0b/nov3-* --data-dir data --out data/reports/nov3_83
```

Then from the repository root:

```
.venv/bin/python docs/analysis/analyze_nov3.py        # tables/*.csv, crosscheck.txt (exit 1 if a cross-check fails)
.venv/bin/python docs/paper_tables/make_tables.py     # tables_nov3.tex (exit 2, nothing written, if it differs from tables/*.csv)
```

`analyze_nov3.py` re-derives cost, route mix, gate error rate and every stage metric from `per_task.csv` and compares them with the report tables
(446 comparisons, see `crosscheck.txt`). The checks cover `data/reports/nov3_83` as it is now, including the copied-in `answer_scores.csv`.

## Definitions (VERIFIED = checked in code or data; INFERRED = reasoning)
- **Primary / sens** (VERIFIED, `harness/metrics.py:per_task_rows`): primary scores a failed task 0; sens keeps its fallback output. Four tasks failed in these jobs, all gate parse failures
  on Loc-Bench (GAPA 1, PREASSESS 1, Self-Route 2). Tokens of failed tasks are kept in both versions, so only quality-based columns differ (the sens files differ from primary in a handful of cells).
- **Does the answer scorer apply the zero rule to failed tasks? No** (VERIFIED, `answer_scoring.py:score_job` scores `line["answer"]` whatever the task status). Its only zeros are for answers without a valid LOCATIONS section.
  The zero rule is therefore applied in `nov3_lib.answer_rows` (primary: a failed task scores 0, adds no listed paths, and is excluded from the conversion base). Counts of unparseable/truncated answers also exclude zeroed tasks in primary.
- **Hallucination rate** = listed paths that are not files of the base-commit checkout / all listed paths (deduplicated per task). **Conversion rate** = of tasks whose final context holds all gold files, the share whose answer lists all gold files.
- **Oracle route** (my definition, INFERRED to be what is wanted): among the three Always arms, the route with the best answer-level score on the stated metric (Function Acc@10; file variant: File Acc@5). Ties go to the cheapest route in the order RAG < GRAPH < LC.
  A gated arm over-escalates when its route is more expensive than the oracle's, under-escalates when cheaper. In primary a failed task counts as wrong and as neither.
  `t4` also gives the same statistics on the *decisive* tasks only (some route scores > 0), because on the others the oracle is RAG by tie-break alone.
  **Caveat (VERIFIED from t4):** because of the tie-break the oracle is RAG for most tasks (RAG/GRAPH/LC oracle counts: SWE 27/0/0 on both answer metrics; Loc-Bench 53/0/3 on Function Acc@10 and 55/0/1 on File Acc@5, so GRAPH is never the oracle), so "routing accuracy" is close to the share of tasks an arm sends to RAG. The `ctx:` rows use the final-context metric instead; that oracle is degenerate too (the context lists of the three Always arms are nearly identical) and is kept for transparency only.
- **Paired tests** (per dataset, per metric, per version): wins/losses/ties of A against B per task, exact two-sided sign test on non-tied tasks, Holm adjustment over the 15 comparisons of that (dataset, metric, version).
  Pairs: each gated arm (GASR, GAPA, Self-Route, PREASSESS) against each Always arm, GAPA-GASR, GAPA-PREASSESS, GASR-Self-Route. Quality metrics: final-context Function Acc@10 and File Acc@5, answer-level Function Acc@10 and File Acc@5.
  Tokens: a win is fewer tokens for A; mean paired difference A minus B with a 95% percentile bootstrap interval (10,000 resamples, `numpy.random.default_rng(20261028)`, reseeded per comparison).
- **GAPA call 2** (`t6`): label read from the `"need"` field of the `gapa_gate_call2` reply (`content_head`); "where it went" is the final route of the task.
  **Break-even** (gate tokens only; post-expansion judges are excluded from both sides): GAPA gate tokens per task = c1 + p * c2, GASR's merged gate = g, so GAPA is cheaper iff p < p* = (g - c1) / c2.
  c1, c2, g are mean (prompt_eval_count + eval_count) over the gate calls; p = call-2 calls / GAPA tasks. Inputs are in `t6_break_even.csv`.

## Files (`tables/`, each in a `_primary` and a `_sens` version except t6)
`t1_cost` (total, routing overhead, injected, quality per cost, route mix, gate error rate, failures), `t2_answer_level` (Acc@k at all levels, hallucination, no-LOCATIONS, truncated, conversion),
`t3_answer_by_route` (answer Function Acc@10 and File Acc@5 by final route, gated arms), `t4_routing_vs_oracle`, `t5_paired`, `t6_gapa_call2_labels`, `t6_break_even`.

## Reading the numbers (what the data supports)
- No quality comparison survives Holm adjustment (`t5`, 0 of 120 quality rows with adjusted p < 0.05 in either version); with 27 and 56 tasks, differences of 1-3 tasks are not results.
- Token differences are large and mostly significant (23 of 30 token rows with adjusted p < 0.05), e.g. Always-LC costs about 5x Always-RAG.
- GAPA's call-2 rate (0.926 SWE, 0.857 Loc-Bench) is above the break-even (0.515, 0.443), so its gate costs more tokens than GASR's merged gate on both datasets (INFERRED to hold generally only for this model and prompts).
