# Paid API results — GPT-5.1 gen + Sonnet 4.5 judge (strat2 n=120)

**Stamp:** 2026-11-24  
**Label:** `openrouter · gpt-5.1 · claude-sonnet-4.5 · single-judge · SWE-QA strat2 n=120 · freeze=3ac1066`  
**Artifacts:** [`api_gpt51_artifacts/`](api_gpt51_artifacts/) — `api_runs/swe-qa-rest/`, `api_scores_strat_rest/` (+ existing W1 under `api_scores/` / `api_runs/swe-qa-w1/`).  
**Local mirrors:** `~/haya/api_runs/swe-qa-rest/`, `~/haya/api_scores_strat_rest/`.

---

## 1. What was billed vs reused

| Piece | n/arm | Action |
|-------|------:|--------|
| W1 strat2 | 24 | **Reused** from paid full W1 (filter strat2 ids) — no rebill |
| RepoProbe | 108 | **Not part of this table** (already paid earlier) |
| Rest strat2 | 96 | **New** gen + Sonnet judge |

Sample construction: `2` tasks per `(repo × What/Why/Where/How)` cell · seed `42` · files `eval_harness/samples_strat/swe-qa-{w1,rest}.strat2.sample.json`.

Scripts: `scripts/run_api_strat_rest.sh`, `scripts/run_api_judge_strat_rest.sh` (rest-only; separate score tree so W1/RP markers cannot be overwritten).

---

## 2. Completeness & spend (rest only)

| Suite | Cells | Status |
|-------|------:|--------|
| Gen rest strat2 × 7 | 672 | **672 ok** |
| Judge rest | 672 | **672 scored** |

**Rest gen cost** (`ollama_calls[*].provider_usage.cost`): **$11.48**  
**Judge:** Sonnet stream + one oneshot repair for `PREASSESS`/`reflex-040` (model repeatedly omitted `clarity`; stream retries cancelled to stop rebill, then one repair call completed the cell).

| Arm | Rest gen $ |
|-----|----------:|
| SELF_ROUTE | 2.12 |
| ALWAYS_LC | 2.02 |
| PREASSESS | 1.93 |
| GAPA | 1.72 |
| GASR | 1.67 |
| ALWAYS_RAG | 1.06 |
| ALWAYS_GRAPH | 0.96 |

Harness auth for this rest run: `harness_commit=db16582` · pipeline freeze `3ac1066`.

---

## 3. Rest strat2 only (n=96) — Sonnet Overall /100

| Arm | Overall | tokens mean | qpt×1000 | %RAG | %GRAPH | %LC | gen $ |
|-----|--------:|------------:|---------:|-----:|-------:|----:|------:|
| **SELF_ROUTE** | **59.65** | 10543 | 5.66 | 17.7 | 0.0 | 82.3 | 2.12 |
| **ALWAYS_LC** | **58.64** | 11894 | 4.93 | 0.0 | 0.0 | 100.0 | 2.02 |
| GASR | 57.55 | 5603 | 10.27 | 14.6 | 49.0 | 36.5 | 1.67 |
| ALWAYS_RAG | 56.32 | 1868 | **30.15** | 100.0 | 0.0 | 0.0 | 1.06 |
| GAPA | 56.00 | 5552 | 10.09 | 25.0 | 35.4 | 39.6 | 1.72 |
| PREASSESS | 54.66 | 9864 | 5.54 | 20.8 | 0.0 | 79.2 | 1.93 |
| ALWAYS_GRAPH | 52.45 | 1693 | **30.98** | 0.0 | 100.0 | 0.0 | 0.96 |

### Dimension means (1–20) — rest

| Arm | Correctness | Completeness | Relevance | Clarity | Reasoning |
|-----|------------:|-------------:|----------:|--------:|----------:|
| SELF_ROUTE | 9.9 | 9.3 | 12.6 | 15.7 | 12.1 |
| ALWAYS_LC | 9.6 | 8.9 | 12.6 | 15.5 | 12.1 |
| GASR | 9.0 | 8.5 | 12.4 | 15.6 | 12.0 |
| ALWAYS_RAG | 8.9 | 8.3 | 12.2 | 15.1 | 11.8 |
| GAPA | 8.6 | 8.2 | 12.1 | 15.4 | 11.7 |
| PREASSESS | 8.6 | 7.9 | 11.7 | 15.1 | 11.4 |
| ALWAYS_GRAPH | 7.9 | 7.2 | 11.3 | 15.1 | 11.0 |

---

## 4. Pooled strat2 n=120 (W1×24 reused + rest×96)

| Arm | Overall | tokens mean | qpt×1000 | %RAG | %GRAPH | %LC | gen $ (24+96) |
|-----|--------:|------------:|---------:|-----:|-------:|----:|--------------:|
| **SELF_ROUTE** | **60.66** | 10305 | 5.89 | — | — | — | 2.60 |
| **ALWAYS_LC** | **60.12** | 11595 | 5.18 | — | — | — | 2.45 |
| GASR | 57.64 | 5158 | 11.18 | — | — | — | 2.00 |
| GAPA | 56.99 | 5623 | 10.13 | — | — | — | 2.15 |
| ALWAYS_RAG | 56.89 | 1873 | **30.37** | — | — | — | 1.32 |
| PREASSESS | 56.03 | 9622 | 5.82 | — | — | — | 2.36 |
| ALWAYS_GRAPH | 52.20 | 1700 | **30.71** | — | — | — | 1.19 |

**Strat120 gen $ total (all arms):** **~$14.07** (of which ~$11.48 is new rest; W1×24 slice of prior W1 bill).

W1-strat24 alone is optimistic vs rest (same pattern as local full-720): e.g. SELF_ROUTE W1-strat **64.71** vs rest **59.65**.

---

## 5. Takeaways (this stack)

1. Ranking matches full paid W1: Self-Route ≥ LC > gated mid > RAG > GRAPH on absolute Overall; RAG/GRAPH best qpt.
2. Rest pulls means down vs W1-only / W1-strat — strat120 is the fairer 15-repo slice.
3. Do **not** compare absolute /100 to paper Table 3 (different judge protocol / scaffold).

**Freeze line:**  
`generator=openai/gpt-5.1 · gates=same · judge=anthropic/claude-sonnet-4.5 · single-judge · freeze=3ac1066 · SWE-QA strat2 n=120`
