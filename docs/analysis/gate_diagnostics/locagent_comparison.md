# Our accuracy vs the LocAgent paper (option 2: published full-set numbers, no same-task rerun)

Published numbers: LocAgent paper (arXiv 2503.09089) Table 4 (SWE-Bench-Lite, 274 tasks) and Table 7 (Loc-Bench, 560 tasks), read from the PDF text. VERIFIED that the values below are typed from that text. Ours: qwen3.5-9b, Nov 3 jobs, frozen pipeline 2350c0b, **27 SWE-Bench-Lite and 56 Loc-Bench tasks**. These are a 10% proportional-by-repo sample of the same 274 and 560 tasks (`data/samples/*.sample.json`: seed 42, `n_total` 274 and 560), so the task population matches LocAgent's, but the sample is small: one task moves SWE by 3.7 points and Loc by 1.8 points. Our intervals below are Wilson 95%. Metric definitions are the same cap rule (hits == min(#gold, k)), strict match, as checked earlier in `scoring_lc_and_fix_notes.md`.

Two kinds of our rows: **answer level** = functions/files the model wrote under LOCATIONS, failed tasks scored 0 (the headline numbers); **ranked list** = the 'After retrieval' stage of the table IV/V CSVs, which is our retrieval ranking before any pruning or generation and is the like-for-like row for BM25 and CodeRankEmbed.

## SWE-Bench-Lite (ours n = 27; published n = 274)

| Source | Row | File@1 | File@3 | File@5 | Module@5 | Module@10 | Func@5 | Func@10 |
|---|---|---|---|---|---|---|---|---|
| LocAgent paper | BM25 | 38.7 | 51.8 | 61.7 | 45.3 | 52.9 | 31.8 | 36.9 |
| LocAgent paper | CodeRankEmbed | 52.5 | 77.7 | 84.7 | 71.9 | 78.8 | 51.8 | 58.8 |
| LocAgent paper | Agentless (Claude-3.5) | 72.6 | 79.2 | 79.6 | 69.0 | 69.0 | 58.8 | 58.8 |
| LocAgent paper | MoatlessTools (Claude-3.5) | 72.6 | 85.8 | 86.1 | 76.3 | 76.3 | 64.6 | 65.0 |
| LocAgent paper | SWE-agent (Claude-3.5) | 77.4 | 87.2 | 90.2 | 77.7 | 78.1 | 64.2 | 64.6 |
| LocAgent paper | OpenHands (Claude-3.5) | 76.3 | 89.8 | 90.2 | 83.2 | 83.6 | 68.2 | 70.1 |
| LocAgent paper | LocAgent (Qwen2.5-7B ft) | 70.8 | 84.7 | 88.3 | 81.0 | 82.8 | 64.2 | 71.5 |
| LocAgent paper | LocAgent (Qwen2.5-32B ft) | 75.9 | 90.5 | 92.7 | 85.8 | 87.2 | 71.9 | 77.0 |
| LocAgent paper | LocAgent (Claude-3.5) | 77.7 | 92.0 | 94.2 | 86.5 | 87.6 | 73.4 | 77.4 |
| Ours, ranked list | After retrieval (same list for all arms) | 29.6 [15.9, 48.5] | 51.9 [34.0, 69.3] | 66.7 [47.8, 81.4] | 48.1 [30.7, 66.0] | 55.6 [37.3, 72.4] | 25.9 [13.2, 44.7] | 37.0 [21.5, 55.8] |
| Ours, answer level | Always-GRAPH | 44.4 [27.6, 62.7] | 44.4 [27.6, 62.7] | 44.4 [27.6, 62.7] | 25.9 [13.2, 44.7] | 25.9 [13.2, 44.7] | 18.5 [8.2, 36.7] | 18.5 [8.2, 36.7] |
| Ours, answer level | Always-RAG | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 33.3 [18.6, 52.2] | 33.3 [18.6, 52.2] | 25.9 [13.2, 44.7] | 25.9 [13.2, 44.7] |
| Ours, answer level | Always-LC | 44.4 [27.6, 62.7] | 44.4 [27.6, 62.7] | 44.4 [27.6, 62.7] | 25.9 [13.2, 44.7] | 25.9 [13.2, 44.7] | 18.5 [8.2, 36.7] | 18.5 [8.2, 36.7] |
| Ours, answer level | GASR | 44.4 [27.6, 62.7] | 44.4 [27.6, 62.7] | 44.4 [27.6, 62.7] | 25.9 [13.2, 44.7] | 25.9 [13.2, 44.7] | 18.5 [8.2, 36.7] | 18.5 [8.2, 36.7] |
| Ours, answer level | GAPA | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 29.6 [15.9, 48.5] | 29.6 [15.9, 48.5] | 22.2 [10.6, 40.8] | 22.2 [10.6, 40.8] |
| Ours, answer level | Self-Route | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 29.6 [15.9, 48.5] | 29.6 [15.9, 48.5] | 22.2 [10.6, 40.8] | 22.2 [10.6, 40.8] |
| Ours, answer level | PREASSESS | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 48.1 [30.7, 66.0] | 29.6 [15.9, 48.5] | 29.6 [15.9, 48.5] | 22.2 [10.6, 40.8] | 22.2 [10.6, 40.8] |

Does the published value fall inside our 95% interval? (`in` = yes, `above` = published is higher than the interval's top, `below` = lower)

| Our row | vs published row | File@1 | File@3 | File@5 | Module@5 | Module@10 | Func@5 | Func@10 |
|---|---|---|---|---|---|---|---|---|
| Ours, ranked list: After retrieval | CodeRankEmbed | above | above | above | above | above | above | above |
| Ours, ranked list: After retrieval | LocAgent (Qwen2.5-7B ft) | above | above | above | above | above | above | above |
| Ours, answer: Always-RAG | CodeRankEmbed | in | above | above | above | above | above | above |
| Ours, answer: Always-RAG | LocAgent (Qwen2.5-7B ft) | above | above | above | above | above | above | above |
| Ours, answer: Always-GRAPH | CodeRankEmbed | in | above | above | above | above | above | above |
| Ours, answer: Always-GRAPH | LocAgent (Qwen2.5-7B ft) | above | above | above | above | above | above | above |
| Ours, answer: Always-LC | CodeRankEmbed | in | above | above | above | above | above | above |
| Ours, answer: Always-LC | LocAgent (Qwen2.5-7B ft) | above | above | above | above | above | above | above |
| Ours, answer: GASR | CodeRankEmbed | in | above | above | above | above | above | above |
| Ours, answer: GASR | LocAgent (Qwen2.5-7B ft) | above | above | above | above | above | above | above |
| Ours, answer: GAPA | CodeRankEmbed | in | above | above | above | above | above | above |
| Ours, answer: GAPA | LocAgent (Qwen2.5-7B ft) | above | above | above | above | above | above | above |

## Loc-Bench (ours n = 56; published n = 560)

Loc-Bench: LocAgent reports File@5/10, Module@10/15, Function@10/15; ours are File@1/3/5, Module@5/10, Function@5/10. Only File@5, Module@10 and Function@10 overlap, so only those are shown (`oct23_appendix_source.tex` note, same reasoning).

| Source | Row | File@5 | Module@10 | Func@10 |
|---|---|---|---|---|
| LocAgent paper | CodeRankEmbed | 74.3 | 63.2 | 43.4 |
| LocAgent paper | Agentless (Claude-3.5) | 67.5 | 53.4 | 42.7 |
| LocAgent paper | OpenHands (Claude-3.5) | 79.8 | 68.9 | 59.1 |
| LocAgent paper | SWE-agent (Claude-3.5) | 77.7 | 63.6 | 52.0 |
| LocAgent paper | LocAgent (Qwen2.5-7B ft) | 78.6 | 63.0 | 51.4 |
| LocAgent paper | LocAgent (Claude-3.5) | 83.4 | 70.9 | 59.3 |
| Ours, ranked list | After retrieval (same list for all arms) | 66.1 [53.0, 77.1] | 64.3 [51.2, 75.5] | 42.9 [30.8, 55.9] |
| Ours, answer level | Always-GRAPH | 51.8 [39.0, 64.3] | 30.4 [19.9, 43.3] | 21.4 [12.7, 33.8] |
| Ours, answer level | Always-RAG | 55.4 [42.4, 67.6] | 28.6 [18.4, 41.5] | 23.2 [14.1, 35.8] |
| Ours, answer level | Always-LC | 50.0 [37.3, 62.7] | 30.4 [19.9, 43.3] | 23.2 [14.1, 35.8] |
| Ours, answer level | GASR | 50.0 [37.3, 62.7] | 25.0 [15.5, 37.7] | 21.4 [12.7, 33.8] |
| Ours, answer level | GAPA | 51.8 [39.0, 64.3] | 30.4 [19.9, 43.3] | 23.2 [14.1, 35.8] |
| Ours, answer level | Self-Route | 46.4 [34.0, 59.3] | 25.0 [15.5, 37.7] | 19.6 [11.3, 31.8] |
| Ours, answer level | PREASSESS | 51.8 [39.0, 64.3] | 30.4 [19.9, 43.3] | 25.0 [15.5, 37.7] |

Does the published value fall inside our 95% interval? (`in` = yes, `above` = published is higher than the interval's top, `below` = lower)

| Our row | vs published row | File@5 | Module@10 | Func@10 |
|---|---|---|---|---|
| Ours, ranked list: After retrieval | CodeRankEmbed | in | in | in |
| Ours, ranked list: After retrieval | LocAgent (Qwen2.5-7B ft) | above | in | in |
| Ours, answer: Always-RAG | CodeRankEmbed | above | above | above |
| Ours, answer: Always-RAG | LocAgent (Qwen2.5-7B ft) | above | above | above |
| Ours, answer: Always-GRAPH | CodeRankEmbed | above | above | above |
| Ours, answer: Always-GRAPH | LocAgent (Qwen2.5-7B ft) | above | above | above |
| Ours, answer: Always-LC | CodeRankEmbed | above | above | above |
| Ours, answer: Always-LC | LocAgent (Qwen2.5-7B ft) | above | above | above |
| Ours, answer: GASR | CodeRankEmbed | above | above | above |
| Ours, answer: GASR | LocAgent (Qwen2.5-7B ft) | above | above | above |
| Ours, answer: GAPA | CodeRankEmbed | above | above | above |
| Ours, answer: GAPA | LocAgent (Qwen2.5-7B ft) | above | above | above |

## How to read this

- The ranked-list row is the fair comparison with the retrieval baselines (BM25, CodeRankEmbed): it is our retrieval alone, no generation. The answer-level rows are the end of our pipeline with a 9B model writing LOCATIONS, which has no counterpart in LocAgent's table, where the model is a purpose-built localization agent that explores the code graph over several rounds.
- LocAgent's strongest rows use Claude-3.5 or models fine-tuned on localization trajectories; the 7B row is Qwen2.5-Coder-7B fine-tuned with LoRA on those trajectories (paper Section 3.3). Our generator is the general-purpose qwen3.5-9b with no localization fine-tuning, so the answer-level gap is a model-and-method difference, not just a pipeline one.
- Interval widths: at n = 27 a 95% interval is roughly ±17 points around 50%, at n = 56 roughly ±13. Differences smaller than that between our sample and a published full-set number cannot be separated from sampling noise (INFERRED from the Wilson widths printed above).
- Not comparable from this table: Loc-Bench File@10, Module@15 and Function@15 (we do not score those k).
