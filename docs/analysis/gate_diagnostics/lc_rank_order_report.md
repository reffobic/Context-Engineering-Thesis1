# LC context: file order and budget (offline rebuild, 83 tasks)

Rebuilt with the cached backend's own `PromptBuilder` (`_truncate_file_blocks`, 24000-token budget, estimate = chars // 4). No model was run. Per-task rows: `lc_rank_order_tasks.csv`.

## Calibration against the logged prompt sizes

`inject.prompt_chars` (system + user message) is logged for every LC run; the rebuilt prompt is compared with it. 'Repeat' = one snippet per retrieved chunk (the code path); 'no repeat' = one snippet per file.

| Variant | Tasks | Median rel. error | Mean abs. rel. error | Max abs. rel. error | Exact (< 0.5%) | Truncation flag agrees with log |
|---|---|---|---|---|---|---|
| repeat (code path) | 83 | -0.0000 | 0.0000 | 0.0006 | 83 | 83 of 83 |
| no repeat | 83 | -0.2841 | 0.3685 | 2.1512 | 21 | 64 of 83 |

Check of the repeat question on tasks where a file has several retrieved chunks and the budget flag did not fire (untruncated, so prompt size is directly comparable):

| Task | Items per file | Logged chars | Rebuilt (repeat) | Rebuilt (no repeat) |
|---|---|---|---|---|
| sphinx-doc__sphinx-8801 | sphinx/ext/autodoc/__init__.py=5 | 14168 | 14165 | 4527 |
| sympy__sympy-12454 | sympy/matrices/matrices.py=5 | 18257 | 18255 | 5614 |
| dask__dask-10963 | dask/bag/core.py=5 | 42451 | 42448 | 9961 |
| django__django-18653 | django/template/loader_tags.py=5 | 20007 | 20006 | 5391 |
| jax-ml__jax-22897 | jax/_src/numpy/lax_numpy.py=5 | 16471 | 16470 | 5269 |

Token estimate (chars // 4, the quantity the budget uses) against the model's own prompt token count (`prompt_eval_count`), LC runs: estimate / actual median 1.026, range 0.672 to 1.239; the budget therefore over-counts real tokens by about 3% at the median.

## Alphabetical (current) vs retrieval-rank order

| Dataset | Tasks | Budget flag: alphabetical | Budget flag: rank | Tasks whose set of kept files differs | All gold files kept: alpha | rank | Gold functions kept (sum over tasks): alpha | rank | Gold functions in top-5 files | ...after the 10-chunk cap |
|---|---|---|---|---|---|---|---|---|---|---|
| SWE-Bench-Lite | 27 | 10 | 10 | 5 | 13 | 12 | 9 | 8 | 16 | 10 |
| Loc-Bench | 56 | 30 | 30 | 10 | 21 | 26 | 25 | 32 | 54 | 36 |

Tasks where the order changes which gold files or gold functions survive:

- SWE-Bench-Lite (3): django__django-15252 (files 1→0, functions 1→0), scikit-learn__scikit-learn-25747 (files 0→1, functions 0→1), sympy__sympy-18057 (files 1→0, functions 1→0)
- Loc-Bench (8): PrefectHQ__prefect-14259 (files 0→1, functions 0→2), UXARRAY__uxarray-1117 (files 1→0, functions 0→0), huggingface__diffusers-10189 (files 0→1, functions 0→1), huggingface__trl-2433 (files 0→1, functions 0→1), pandas-dev__pandas-60415 (files 0→1, functions 0→1), phidatahq__phidata-1583 (files 0→1, functions 0→1), py-pdf__pypdf-2934 (files 1→0, functions 0→0), ultralytics__ultralytics-17499 (files 0→1, functions 0→1)

