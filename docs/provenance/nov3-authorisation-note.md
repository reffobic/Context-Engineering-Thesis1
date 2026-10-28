# Nov 3 queue: authorisation timestamp and regeneration

Scope: provenance note only. No harness or pipeline code, tag or run header was changed for it.

## 1. Wrong timestamp in the authorisation file
`eval_harness/data/queue/record_authorisation.json` (for harness commit `7c92a0b`) carries
`"authorised_utc": "2026-10-28T12:50:00Z"`. That value was typed in error; it was not measured.
Every Nov 3 run header copies it as `record_authorised_for` (written by `run.py`, which only reads the file).

## 2. Actual queue start
The queue started at **2026-10-28T12:39:38Z**, before the 12:50:00Z in the file.
- `eval_harness/data/runs/2350c0b/nov3-01-qwen3.5-9b-GAPA-swe-bench-lite/queue_status.json`: `started_utc` = `2026-10-28T12:39:38+00:00`.
- `eval_harness/data/queue/events.jsonl` line 27 (`queue_start`) and line 28 (`job_start` nov3-01) show the same instant.
- The first task line in that job's `results.jsonl` has `started_utc` `2026-10-28T12:39:45Z`.

The queue was paused by the `hold_after` checkpoint after job 2 (`queue_end` 13:04:33Z, line 33) and resumed at 13:04:51Z (line 34, second `queue_start`). All 14 jobs completed at 15:12:14Z.

## 3. Where the authorisation came from
The authorisation to run the queue was the user's message in the session (2026-10-28, "start the nov3-83 queue on the same machine as Oct 23"),
not the file's timestamp. The file header was left unedited on purpose, so the run headers and the file stay consistent with each other.
The tag `harness-nov3-runs` stays as is: annotated, on `7c92a0b`, local and pushed.

## 4. Regenerating `data/reports/nov3_83`
The folder holds the metrics tables plus `answer_scores.csv` and `answer_summary.json`, so regenerate both parts into it.
Run from `eval_harness/`:

```
PYTHONPATH=src ../.venv/bin/python -m harness.metrics --runs data/runs/2350c0b/nov3-* --data-dir data --out data/reports/nov3_83 --lc-scorer v2 --with-new-arms && PYTHONPATH=src ../.venv/bin/python -m harness.answer_scoring --runs data/runs/2350c0b/nov3-* --data-dir data --out data/reports/nov3_83
```

Pass only the `nov3-*` job dirs. The whole `data/runs/2350c0b` folder also holds the Oct 23 jobs, and `harness.metrics` stops with a `MergeError` on the duplicate GAPA keys.
Backup with SHA-256 manifest: `../harness-backup-20261028-nov3/MANIFEST.sha256`.
