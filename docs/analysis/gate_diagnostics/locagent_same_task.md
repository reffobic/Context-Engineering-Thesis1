# Same-task comparison with LocAgent (Claude-3.5) on SWE-Bench-Lite, from LocAgent's shipped outputs (no model run)

Source of LocAgent's predictions: `evaluation/loc_output/locagent/claude_3-5/loc_outputs.jsonl` in the LocAgent repo (commit 4935b55; 300 tasks, found_files / found_modules / found_entities per task). I cloned the repo into the scratchpad and only read the file; nothing from it was executed. The repo ships no Loc-Bench outputs and no outputs for the 7B model, so Loc-Bench and the Qwen rows cannot be compared this way.

## 1. Validation: do I reproduce LocAgent's published Claude-3.5 row?

Scored on all 274 tasks of our `labels.jsonl` (the 274-task SWE-Bench-Lite set), file / module / function accuracy mirrored from `eval_metric.py`:

| | File@1 | File@3 | File@5 | Module@5 | Module@10 | Function@5 | Function@10 |
|---|---|---|---|---|---|---|---|
| Published, paper Table 4 (LocAgent, Claude-3.5) | 77.74 | 91.97 | 94.16 | 86.50 | 87.59 | 73.36 | 77.37 |
| Recomputed from the shipped outputs | 77.74 | 91.97 | 94.16 | 86.50 | 87.59 | 73.36 | 77.37 |
| Difference | -0.00 | +0.00 | +0.00 | -0.00 | +0.00 | -0.00 | +0.00 |

Largest absolute difference: 0.00 points. The scoring and the gold labels reproduce the paper to within rounding of the shipped file.

## 2. Our 27 tasks: LocAgent (Claude-3.5, shipped outputs) vs ours

LocAgent on our 27 sampled tasks vs on the other 247 tasks (is our sample easier or harder than the rest of the set for LocAgent?):

| | n | File@1 | File@3 | File@5 | Module@5 | Module@10 | Function@5 | Function@10 |
|---|---|---|---|---|---|---|---|---|
| LocAgent (Claude-3.5), our 27 tasks | 27 | 74.1 | 92.6 | 92.6 | 81.5 | 85.2 | 59.3 | 70.4 |
| LocAgent (Claude-3.5), other 247 tasks | 247 | 78.1 | 91.9 | 94.3 | 87.0 | 87.9 | 74.9 | 78.1 |

Same 27 tasks, same gold labels, same cap rule:

| | File@1 | File@3 | File@5 | Module@5 | Module@10 | Function@5 | Function@10 |
|---|---|---|---|---|---|---|---|
| **LocAgent (Claude-3.5)** | **74.1** | **92.6** | **92.6** | **81.5** | **85.2** | **59.3** | **70.4** |
| Ours, ranked list (After retrieval) | 29.6 | 51.9 | 66.7 | 48.1 | 55.6 | 25.9 | 37.0 |
| Ours, answer level: Always-GRAPH | 44.4 | 44.4 | 44.4 | 25.9 | 25.9 | 18.5 | 18.5 |
| Ours, answer level: Always-RAG | 48.1 | 48.1 | 48.1 | 33.3 | 33.3 | 25.9 | 25.9 |
| Ours, answer level: Always-LC | 44.4 | 44.4 | 44.4 | 25.9 | 25.9 | 18.5 | 18.5 |
| Ours, answer level: GASR | 44.4 | 44.4 | 44.4 | 25.9 | 25.9 | 18.5 | 18.5 |
| Ours, answer level: GAPA | 48.1 | 48.1 | 48.1 | 29.6 | 29.6 | 22.2 | 22.2 |
| Ours, answer level: Self-Route | 48.1 | 48.1 | 48.1 | 29.6 | 29.6 | 22.2 | 22.2 |
| Ours, answer level: PREASSESS | 48.1 | 48.1 | 48.1 | 29.6 | 29.6 | 22.2 | 22.2 |

Paired per task (one point of the comparison per task). W = tasks where ours is correct and LocAgent is not, L = LocAgent correct and ours not, T = same; p = exact two-sided sign test on W vs L.

| Ours | Metric | W | L | T | Sign-test p |
|---|---|---|---|---|---|
| Ranked list (After retrieval) | File@5 | 0 | 7 | 20 | 0.016 |
| Ranked list (After retrieval) | Module@10 | 0 | 8 | 19 | 0.008 |
| Ranked list (After retrieval) | Function@10 | 1 | 10 | 16 | 0.012 |
| Answer: Always-RAG | File@5 | 0 | 12 | 15 | 0.000 |
| Answer: Always-RAG | Module@10 | 0 | 14 | 13 | 0.000 |
| Answer: Always-RAG | Function@10 | 0 | 12 | 15 | 0.000 |
| Answer: Always-GRAPH | File@5 | 0 | 13 | 14 | 0.000 |
| Answer: Always-GRAPH | Module@10 | 0 | 16 | 11 | 0.000 |
| Answer: Always-GRAPH | Function@10 | 0 | 14 | 13 | 0.000 |
| Answer: Always-LC | File@5 | 0 | 13 | 14 | 0.000 |
| Answer: Always-LC | Module@10 | 0 | 16 | 11 | 0.000 |
| Answer: Always-LC | Function@10 | 0 | 14 | 13 | 0.000 |
| Answer: GASR | File@5 | 0 | 13 | 14 | 0.000 |
| Answer: GASR | Module@10 | 0 | 16 | 11 | 0.000 |
| Answer: GASR | Function@10 | 0 | 14 | 13 | 0.000 |
| Answer: GAPA | File@5 | 0 | 12 | 15 | 0.000 |
| Answer: GAPA | Module@10 | 0 | 15 | 12 | 0.000 |
| Answer: GAPA | Function@10 | 0 | 13 | 14 | 0.000 |

Tasks where LocAgent gets function Acc@10 right and all three of our fixed tiers get it wrong: 12 of 27 (astropy__astropy-14182, django__django-13315, django__django-14608, django__django-14855, django__django-15252, django__django-17051, matplotlib__matplotlib-26011, matplotlib__matplotlib-26020, pylint-dev__pylint-7114, pytest-dev__pytest-7373, sphinx-doc__sphinx-7738, sympy__sympy-20442).

Caveats: LocAgent's Claude-3.5 outputs are as shipped by its authors (not rerun by us); the model differs from ours (Claude-3.5 versus qwen3.5-9b), as does the method (multi-round graph-exploring agent versus single-shot retrieval-augmented answer). The comparison is therefore same-task, not same-model. n = 27, so one task is 3.7 points.

