# Paid API artifacts (GPT-5.1 gen · Sonnet 4.5 judge)

Copied from `~/haya/api_runs`, `~/haya/api_scores`, `~/haya/api_scores_strat_rest`, and `~/haya/api_smoke` for analysis in-git.

| Path | Contents |
|------|----------|
| `api_runs/` | Per-arm `results.jsonl`, `job.json`, logs (W1, RP, **swe-qa-rest strat2**) |
| `api_scores/` | SWE W1 + RepoProbe scores, markers (original paid run) |
| `api_scores_strat_rest/` | SWE **rest strat2** (96×7) Sonnet scores + markers |
| `api_smoke/` | Pre-flight smoke outputs |

**Secrets:** any accidental `sk-or-v1-…` / Bearer fragments were redacted to `[REDACTED]` before commit. Live keys remain only in `~/haya/api_gen.env` / `api_judge.env` (gitignored).

**Tables:**
- [`../TABLE_B_API_GPT51_SONNET_SUMMARY.md`](../TABLE_B_API_GPT51_SONNET_SUMMARY.md) — **comprehensive** (W1 + RP + rest + strat120, with qpt)
- [`../TABLE_B_API_GPT51_SONNET_W1_RP.md`](../TABLE_B_API_GPT51_SONNET_W1_RP.md) — full W1 (144) + RP (108)
- [`../TABLE_B_API_GPT51_SONNET_STRAT120.md`](../TABLE_B_API_GPT51_SONNET_STRAT120.md) — strat2 120 (W1×24 filtered + rest×96 new)
