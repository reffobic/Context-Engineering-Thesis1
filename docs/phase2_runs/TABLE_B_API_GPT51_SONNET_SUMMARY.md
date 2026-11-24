# Paid API results summary — GPT-5.1 gen + Sonnet 4.5 judge

**Stamp:** 2026-11-24  
**Stack:** `generator=openai/gpt-5.1` · `judge=anthropic/claude-sonnet-4.5` · single-judge · freeze `3ac1066`  
**Artifacts:** [`api_gpt51_artifacts/`](api_gpt51_artifacts/)  
**Detail tables:** [W1+RP](TABLE_B_API_GPT51_SONNET_W1_RP.md) · [strat120](TABLE_B_API_GPT51_SONNET_STRAT120.md)

**qpt×1000** = Overall / tokens_mean × 1000 (higher = more quality per injected token).  
Tokens = `inject.est_tokens_div4` (SWE) or `tokens.injected_est_len4` (RP).

---

## 0. Executive scoreboard (absolute Overall)

| Suite | Best absolute | Best qpt | Worst absolute |
|-------|---------------|----------|----------------|
| SWE-QA **W1** n=144 | **SELF_ROUTE 70.31** | **ALWAYS_RAG 34.37** | ALWAYS_GRAPH 56.89 |
| RepoProbe **py8** n=108 | **SELF_ROUTE 36.0%** | **ALWAYS_RAG 14.23** | ALWAYS_GRAPH 26.8% |
| SWE **rest strat2** n=96 | **SELF_ROUTE 59.65** | **ALWAYS_GRAPH 30.98** / RAG 30.15 | ALWAYS_GRAPH 52.45 |
| SWE **strat2 pooled** n=120 | **SELF_ROUTE 60.66** | **ALWAYS_RAG 30.37** / GRAPH 30.71 | ALWAYS_GRAPH 52.20 |

**Stable pattern:** Self-Route / LC lead absolute quality; Always-RAG (and GRAPH) lead efficiency; Always-GRAPH is weakest on absolute score.

---

## 1. Setup (all paid runs)

| Item | Value |
|------|--------|
| Generator + gates | `openai/gpt-5.1` via OpenRouter |
| Judge | `anthropic/claude-sonnet-4.5` (separate key) |
| Pipeline freeze | `3ac1066` |
| Arms | GAPA, GASR, SELF_ROUTE, ALWAYS_RAG, ALWAYS_GRAPH, PREASSESS, ALWAYS_LC |
| Protocol | Judge ≠ generator · single-pass (not paper 5-vote) |

| Run | Sample | Gen out | Scores |
|-----|--------|---------|--------|
| A — W1+RP | W1 full 144 + RP 108 | `api_runs/swe-qa-w1`, `repoprobe-py8` | `api_scores/` |
| B — rest strat2 | 96 (12 repos × 8) | `api_runs/swe-qa-rest` | `api_scores_strat_rest/` |
| Strat120 table | W1 strat24 **reuse** + rest96 | — | filter + pool |

Harness rows still label `model=qwen3.5:9b`; billed path is OpenRouter (`ollama_version=openrouter`).

---

## 2. Spend

| Bucket | Gen $ (logged) | Judge $ | Notes |
|--------|---------------:|--------:|-------|
| Smoke | ~0.01 | — | flask-000 |
| **A: W1+RP** | **31.40** | **~26.60** | User total ~**$58** |
| **B: rest strat2** | **11.48** | ~8–12+ | Plus PREASSESS parse-retry waste before cancel |
| **Combined gen** | **~$42.89** | | |
| **Combined all-in (rough)** | | | **~$78–85** |

### Gen $ by arm

| Arm | W1 | RP | W1+RP | Rest96 | New rest only |
|-----|---:|---:|------:|-------:|--------------:|
| SELF_ROUTE | 2.58 | 2.78 | 5.36 | 2.12 | 2.12 |
| ALWAYS_LC | 2.49 | 2.60 | 5.09 | 2.02 | 2.02 |
| PREASSESS | 2.41 | 2.67 | 5.08 | 1.93 | 1.93 |
| GAPA | 2.44 | 2.58 | 5.02 | 1.72 | 1.72 |
| GASR | 2.07 | 2.35 | 4.42 | 1.67 | 1.67 |
| ALWAYS_RAG | 1.50 | 1.88 | 3.38 | 1.06 | 1.06 |
| ALWAYS_GRAPH | 1.31 | 1.74 | 3.05 | 0.96 | 0.96 |
| **Total** | **14.80** | **16.61** | **31.40** | **11.48** | **11.48** |

W1 strat24 slice of prior W1 bill ≈ **$2.59** (reused, not re-spent). Strat120 “gen $” columns below include that slice for apples-to-apples arm cost.

**Gen failures:** W1 `ALWAYS_GRAPH`/`flask-002` HTTP 520 (1 cell). Rest: **0** failures.

---

## 3. SWE-QA Wave 1 (n=144) — Overall + qpt

| Arm | Overall | tokens | **qpt×1000** | %RAG | %GRAPH | %LC | gen $ |
|-----|--------:|-------:|-------------:|-----:|-------:|----:|------:|
| **SELF_ROUTE** | **70.31** | 8518 | 8.25 | 27.8 | 0.0 | 72.2 | 2.58 |
| **ALWAYS_LC** | **67.86** | 10336 | 6.57 | 0.0 | 0.0 | 100.0 | 2.49 |
| GASR | 65.97 | 3934 | 16.77 | 35.4 | 39.6 | 25.0 | 2.07 |
| PREASSESS | 65.01 | 8441 | 7.70 | 22.2 | 0.0 | 77.8 | 2.41 |
| ALWAYS_RAG | 64.99 | 1891 | **34.37** | 100.0 | 0.0 | 0.0 | 1.50 |
| GAPA | 63.21 | 5799 | 10.90 | 23.6 | 25.0 | 51.4 | 2.44 |
| ALWAYS_GRAPH | 56.89 | 1727 | 32.94 | 0.0 | 100.0 | 0.0 | 1.31 |

### W1 dimensions (1–20)

| Arm | Corr | Comp | Rel | Clar | Reas |
|-----|-----:|-----:|----:|-----:|-----:|
| SELF_ROUTE | 12.8 | 12.1 | 15.2 | 16.1 | 14.1 |
| ALWAYS_LC | 12.0 | 11.3 | 14.7 | 16.1 | 13.7 |
| GASR | 11.3 | 10.6 | 14.4 | 16.3 | 13.4 |
| PREASSESS | 11.3 | 10.5 | 14.2 | 15.8 | 13.1 |
| ALWAYS_RAG | 11.3 | 10.6 | 14.4 | 15.6 | 13.1 |
| GAPA | 10.7 | 10.0 | 13.9 | 15.8 | 12.8 |
| ALWAYS_GRAPH | 9.1 | 8.3 | 12.6 | 15.4 | 11.5 |

---

## 4. RepoProbe Python-8 (n=108) — Overall% + qpt

| Arm | score/10 | overall % | perfect % | tokens | **qpt×1000** | %RAG | %GRAPH | %LC | gen $ |
|-----|--------:|----------:|----------:|-------:|-------------:|-----:|-------:|----:|------:|
| **SELF_ROUTE** | **3.60** | **36.0** | 11.1 | 10927 | 3.30 | 5.6 | 0.0 | 94.4 | 2.78 |
| ALWAYS_LC | 3.57 | 35.7 | 12.0 | 11584 | 3.09 | 0.0 | 0.0 | 100.0 | 2.60 |
| PREASSESS | 3.46 | 34.6 | **14.8** | 9674 | 3.58 | 14.8 | 0.0 | 85.2 | 2.67 |
| GASR | 3.22 | 32.2 | 7.4 | 4832 | 6.67 | 8.3 | 60.2 | 31.5 | 2.35 |
| ALWAYS_RAG | 2.97 | 29.7 | 7.4 | 2088 | **14.23** | 100.0 | 0.0 | 0.0 | 1.88 |
| GAPA | 2.86 | 28.6 | 6.5 | 5504 | 5.20 | 11.1 | 51.9 | 37.0 | 2.58 |
| ALWAYS_GRAPH | 2.68 | 26.8 | 9.3 | 1956 | 13.69 | 0.0 | 100.0 | 0.0 | 1.74 |

qpt here uses overall **percent** / tokens × 1000 (same formula; scale differs from SWE /100).

---

## 5. SWE-QA rest strat2 (n=96) — Overall + qpt

| Arm | Overall | tokens | **qpt×1000** | %RAG | %GRAPH | %LC | gen $ |
|-----|--------:|-------:|-------------:|-----:|-------:|----:|------:|
| **SELF_ROUTE** | **59.65** | 10543 | 5.66 | 17.7 | 0.0 | 82.3 | 2.12 |
| **ALWAYS_LC** | **58.64** | 11894 | 4.93 | 0.0 | 0.0 | 100.0 | 2.02 |
| GASR | 57.55 | 5603 | 10.27 | 14.6 | 49.0 | 36.5 | 1.67 |
| ALWAYS_RAG | 56.32 | 1868 | **30.15** | 100.0 | 0.0 | 0.0 | 1.06 |
| GAPA | 56.00 | 5552 | 10.09 | 25.0 | 35.4 | 39.6 | 1.72 |
| PREASSESS | 54.66 | 9864 | 5.54 | 20.8 | 0.0 | 79.2 | 1.93 |
| ALWAYS_GRAPH | 52.45 | 1693 | **30.98** | 0.0 | 100.0 | 0.0 | 0.96 |

### Rest dimensions (1–20)

| Arm | Corr | Comp | Rel | Clar | Reas |
|-----|-----:|-----:|----:|-----:|-----:|
| SELF_ROUTE | 9.9 | 9.3 | 12.6 | 15.7 | 12.1 |
| ALWAYS_LC | 9.6 | 8.9 | 12.6 | 15.5 | 12.1 |
| GASR | 9.0 | 8.5 | 12.4 | 15.6 | 12.0 |
| ALWAYS_RAG | 8.9 | 8.3 | 12.2 | 15.1 | 11.8 |
| GAPA | 8.6 | 8.2 | 12.1 | 15.4 | 11.7 |
| PREASSESS | 8.6 | 7.9 | 11.7 | 15.1 | 11.4 |
| ALWAYS_GRAPH | 7.9 | 7.2 | 11.3 | 15.1 | 11.0 |

### Rest per-repo Overall (selected arms)

| Repo | SELF_ROUTE | ALWAYS_LC | GASR | ALWAYS_RAG | ALWAYS_GRAPH |
|------|----------:|----------:|-----:|-----------:|-------------:|
| matplotlib | 81.2 | 76.9 | 74.0 | 76.6 | 69.6 |
| django | 73.8 | 72.0 | 68.1 | 59.5 | 52.4 |
| scikit-learn | 71.4 | 69.4 | 65.1 | 55.1 | 45.4 |
| conan | 60.6 | 59.6 | 54.8 | 46.6 | 50.9 |
| sphinx | 57.9 | 59.5 | 54.4 | 42.8 | 46.9 |
| sympy | 57.9 | 47.6 | 45.6 | 44.1 | 44.1 |
| streamlink | 56.4 | 57.5 | 61.2 | 55.4 | 52.9 |
| astropy | 54.0 | 59.2 | 57.2 | 45.9 | 50.0 |
| xarray | 54.0 | 51.4 | 43.1 | 63.4 | 60.9 |
| reflex | 52.0 | 49.5 | 61.0 | 73.6 | 54.1 |
| pylint | 51.0 | 54.6 | 59.0 | 65.4 | 58.9 |
| sqlfluff | 45.6 | 46.4 | 47.0 | 47.5 | 43.4 |

---

## 6. Strat2 pooled n=120 (W1×24 reuse + rest×96) — Overall + qpt

Balanced 15-repo slice (2 per repo × What/Why/Where/How). **Preferred SWE headline** vs W1-only.

| Arm | Overall | tokens | **qpt×1000** | %RAG | %GRAPH | %LC | gen $ |
|-----|--------:|-------:|-------------:|-----:|-------:|----:|------:|
| **SELF_ROUTE** | **60.66** | 10305 | 5.89 | 17.5 | 0.0 | 82.5 | 2.60 |
| **ALWAYS_LC** | **60.12** | 11595 | 5.18 | 0.0 | 0.0 | 100.0 | 2.45 |
| GASR | 57.64 | 5158 | 11.18 | 16.7 | 50.0 | 33.4 | 2.00 |
| GAPA | 56.99 | 5623 | 10.13 | 25.0 | 32.5 | 42.5 | 2.15 |
| ALWAYS_RAG | 56.89 | 1873 | **30.37** | 100.0 | 0.0 | 0.0 | 1.32 |
| PREASSESS | 56.03 | 9622 | 5.82 | 20.8 | 0.0 | 79.2 | 2.36 |
| ALWAYS_GRAPH | 52.20 | 1700 | **30.71** | 0.0 | 100.0 | 0.0 | 1.19 |

### Strat120 dimensions (1–20)

| Arm | Corr | Comp | Rel | Clar | Reas |
|-----|-----:|-----:|----:|-----:|-----:|
| SELF_ROUTE | 10.2 | 9.7 | 12.9 | 15.6 | 12.3 |
| ALWAYS_LC | 10.0 | 9.3 | 12.9 | 15.6 | 12.4 |
| GASR | 9.0 | 8.5 | 12.4 | 15.7 | 12.0 |
| ALWAYS_RAG | 9.1 | 8.5 | 12.3 | 15.2 | 11.9 |
| GAPA | 8.9 | 8.5 | 12.3 | 15.5 | 11.8 |
| PREASSESS | 9.0 | 8.3 | 12.0 | 15.2 | 11.5 |
| ALWAYS_GRAPH | 7.8 | 7.1 | 11.2 | 15.1 | 10.9 |

### W1-only optimism (strat24 vs rest96)

| Arm | W1 strat24 | Rest96 | Δ (W1−rest) |
|-----|----------:|-------:|------------:|
| SELF_ROUTE | 64.71 | 59.65 | +5.1 |
| ALWAYS_LC | 66.08 | 58.64 | +7.4 |
| PREASSESS | 61.54 | 54.66 | +6.9 |
| GAPA | 60.96 | 56.00 | +5.0 |
| ALWAYS_RAG | 59.17 | 56.32 | +2.9 |
| GASR | 58.00 | 57.55 | +0.5 |
| ALWAYS_GRAPH | 51.21 | 52.45 | −1.2 |

---

## 7. Cross-suite ranking (absolute)

Order by Overall / overall% within suite:

| Rank | W1 SWE | RP | Rest96 | Strat120 |
|-----:|--------|----|--------|----------|
| 1 | SELF_ROUTE | SELF_ROUTE | SELF_ROUTE | SELF_ROUTE |
| 2 | ALWAYS_LC | ALWAYS_LC | ALWAYS_LC | ALWAYS_LC |
| 3 | GASR | PREASSESS | GASR | GASR |
| 4 | PREASSESS | GASR | ALWAYS_RAG | GAPA |
| 5 | ALWAYS_RAG | ALWAYS_RAG | GAPA | ALWAYS_RAG |
| 6 | GAPA | GAPA | PREASSESS | PREASSESS |
| 7 | ALWAYS_GRAPH | ALWAYS_GRAPH | ALWAYS_GRAPH | ALWAYS_GRAPH |

**qpt leaders:** Always-RAG (and Always-GRAPH) on every SWE suite; Always-RAG on RP.

---

## 8. Interpretation

1. **Absolute quality:** Self-Route ≥ Always-LC on every paid suite; Always-GRAPH last.
2. **Efficiency:** Always-RAG best (or near-best) **qpt**; gated GASR best mid-token tradeoff among graph-using arms.
3. **Gates + GPT-5.1** push Self-Route / PREASSESS heavily to LC (≈72–94% LC) → high Overall, low qpt.
4. **W1 is optimistic** vs rest/strat120 (~5–7 Overall points on top arms) — use **strat120** for 15-repo claims.
5. **Not paper Table 3 parity** (different judge protocol / scaffolds / n).

---

## 9. Paths

```text
~/haya/api_gen.env / api_judge.env
~/haya/api_runs/{swe-qa-w1,repoprobe-py8,swe-qa-rest}/
~/haya/api_scores/                  # W1 + RP
~/haya/api_scores_strat_rest/       # rest strat2
scripts/run_api_w1_rp.sh
scripts/run_api_judge_stream.sh
scripts/run_api_strat_rest.sh
scripts/run_api_judge_strat_rest.sh
docs/phase2_runs/api_gpt51_artifacts/
```

**Freeze line:**  
`generator=openai/gpt-5.1 · gates=same · judge=anthropic/claude-sonnet-4.5 · single-judge · freeze=3ac1066 · W1+RP + SWE strat2 n=120`
