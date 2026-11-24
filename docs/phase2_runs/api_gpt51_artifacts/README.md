# Paid API artifacts (GPT-5.1 gen · Sonnet 4.5 judge)

Copied from `~/haya/api_runs`, `~/haya/api_scores`, and `~/haya/api_smoke` for analysis in-git.

| Path | Contents |
|------|----------|
| `api_runs/` | Per-arm `results.jsonl`, `job.json`, `run.log`, judge stream logs |
| `api_scores/` | SWE `*_answers_score.jsonl`, RepoProbe scores, markers |
| `api_smoke/` | Pre-flight smoke outputs |

**Secrets:** any accidental `sk-or-v1-…` / Bearer fragments were redacted to `[REDACTED]` before commit. Live keys remain only in `~/haya/api_gen.env` / `api_judge.env` (gitignored).

**Tables:** see [`../TABLE_B_API_GPT51_SONNET_W1_RP.md`](../TABLE_B_API_GPT51_SONNET_W1_RP.md).
