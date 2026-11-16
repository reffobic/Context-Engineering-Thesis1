# Relaxed scoring (secondary) on the stored Nov 3 answers

Strict (exact `path:Class.method`, LocAgent cap rule) stays primary. Variants: **(a) leaf** = same file and same final name component, class prefix ignored; **(b) recall@10** = gold functions found / min(#gold, 10). Zero rule applied (no fixed-tier task failed, so it changes nothing here). Per-task rows: `relaxed_scoring_tasks.csv`.

## Mean function metrics and tasks that change

| Dataset | Tier | Tasks | Strict Acc@10 | Leaf Acc@10 | Tasks flipping 0→1 | Strict recall@10 | Leaf recall@10 | Tasks with partial recall but strict Acc 0 |
|---|---|---|---|---|---|---|---|---|
| SWE-Bench-Lite | RAG | 27 | 0.259 | 0.296 | 1 | 0.278 | 0.333 | 1 |
| SWE-Bench-Lite | GRAPH | 27 | 0.185 | 0.259 | 2 | 0.204 | 0.296 | 1 |
| SWE-Bench-Lite | LC | 27 | 0.185 | 0.222 | 1 | 0.204 | 0.259 | 1 |
| Loc-Bench | RAG | 56 | 0.232 | 0.321 | 5 | 0.265 | 0.377 | 5 |
| Loc-Bench | GRAPH | 56 | 0.214 | 0.286 | 4 | 0.256 | 0.333 | 6 |
| Loc-Bench | LC | 56 | 0.232 | 0.304 | 4 | 0.258 | 0.332 | 4 |

## Tasks that flip 0→1 under leaf matching (IDs)

- SWE-Bench-Lite RAG (1): django__django-14855
- SWE-Bench-Lite GRAPH (2): django__django-14855, pytest-dev__pytest-11143
- SWE-Bench-Lite LC (1): django__django-14855
- Loc-Bench RAG (5): bridgecrewio__checkov-6909, huggingface__transformers-29511, huggingface__trl-2433, pandas-dev__pandas-60415, yt-dlp__yt-dlp-11711
- Loc-Bench GRAPH (4): bridgecrewio__checkov-6909, huggingface__trl-2433, pandas-dev__pandas-60415, yt-dlp__yt-dlp-11711
- Loc-Bench LC (4): bridgecrewio__checkov-6909, dask__dask-10963, pandas-dev__pandas-60415, yt-dlp__yt-dlp-11711

Tasks that flip in at least one tier, and whether they were all-tiers-zero under strict scoring:

- SWE-Bench-Lite: 2 tasks flip in some tier (django__django-14855, pytest-dev__pytest-11143); all-tiers-zero count strict -> leaf: 20 -> 19.
- Loc-Bench: 6 tasks flip in some tier (bridgecrewio__checkov-6909, dask__dask-10963, huggingface__transformers-29511, huggingface__trl-2433, pandas-dev__pandas-60415, yt-dlp__yt-dlp-11711); all-tiers-zero count strict -> leaf: 40 -> 35.

## Ambiguity of leaf-only matches

A gold function found only by leaf matching is *ambiguous* when the gold file at the base commit defines more than one function with that leaf name (Class A.run and Class B.run), so the leaf match may point at the wrong definition. Counted over (task, tier) answers.

| Dataset | Tier | Gold functions found only by leaf | Ambiguous | File not parsable | Flips whose every leaf-only match is unambiguous |
|---|---|---|---|---|---|
| SWE-Bench-Lite | RAG | 2 | 0 | 0 | 1 of 1 |
| SWE-Bench-Lite | GRAPH | 3 | 0 | 0 | 2 of 2 |
| SWE-Bench-Lite | LC | 2 | 0 | 0 | 1 of 1 |
| Loc-Bench | RAG | 9 | 3 | 0 | 4 of 5 |
| Loc-Bench | GRAPH | 6 | 3 | 0 | 3 of 4 |
| Loc-Bench | LC | 5 | 2 | 0 | 2 of 4 |

