# Paid API results — GPT-5.1 gen + Sonnet 4.5 judge

**Stamp:** 2026-11-24  
**Label:** `openrouter · gpt-5.1 · claude-sonnet-4.5 · single-judge · W1 n=144 + RepoProbe-py8 n=108 · 7 arms`  
**Artifacts:** `~/haya/api_runs/` (generation) · `~/haya/api_scores/` (scores + `markers/`)

---

## 1. Setup

| Item | Value |
|------|--------|
| Generator + gates/assessors | `openai/gpt-5.1` via OpenRouter (`provider_openrouter.toml` `default`) |
| Judge (SWE-QA 5-dim + RepoProbe checklist) | `anthropic/claude-sonnet-4.5` (separate judge key) |
| Pipeline freeze | `3ac1066` |
| Harness (auth) | `a8824d7` |
| SWE-QA sample | `eval_harness/data/samples/swe-qa-w1.sample.json` — **full Wave 1**, n=144 (flask/requests/pytest × 48) |
| RepoProbe sample | `repoprobe-py8.sample.json` — n=108 |
| Arms | GAPA, GASR, SELF_ROUTE, ALWAYS_RAG, ALWAYS_GRAPH, PREASSESS, ALWAYS_LC |
| Protocol | Judge ≠ generator · single-pass (not paper 5-vote) |

Harness job rows still label `model=qwen3.5:9b`; provider path is OpenRouter (`ollama_version=openrouter`) with billed GPT-5.1 calls.

---

## 2. Completeness & spend

| Suite | Cells | Status |
|-------|------:|--------|
| Gen SWE-QA W1 × 7 | 1008 | 1007 ok · **1 error** |
| Gen RepoProbe × 7 | 756 | 756 ok |
| Judge SWE-QA | 1008 | 1008 scored |
| Judge RepoProbe | 756 | 756 score rows (ALWAYS_GRAPH summary `n_scored=106`) |

**Gen cost (logged `provider_usage.cost`):** **$31.40**  
**User-reported total (gen+judge keys):** **~$58** → implied judge ≈ **$26.60**

| Arm | Gen $ (W1+RP) |
|-----|-------------:|
| SELF_ROUTE | 5.36 |
| ALWAYS_LC | 5.09 |
| PREASSESS | 5.08 |
| GAPA | 5.02 |
| GASR | 4.42 |
| ALWAYS_RAG | 3.38 |
| ALWAYS_GRAPH | 3.05 |

**Sole gen failure:** `ALWAYS_GRAPH` · `flask-002` · OpenRouter/OpenAI **HTTP 520** (transient provider error). Counted failed; not re-tried.

---

## 3. SWE-QA Wave 1 — main table (Sonnet Overall /100)

| Arm | Overall | tokens mean | qpt×1000 | %RAG | %GRAPH | %LC | gen $ |
|-----|--------:|------------:|---------:|-----:|-------:|----:|------:|
| **SELF_ROUTE** | **70.31** | 8518 | 8.25 | 27.8 | 0.0 | 72.2 | 5.36 |
| **ALWAYS_LC** | **67.86** | 10336 | 6.57 | 0.0 | 0.0 | 100.0 | 5.09 |
| GASR | 65.97 | 3934 | 16.77 | 35.4 | 39.6 | 25.0 | 4.42 |
| PREASSESS | 65.01 | 8441 | 7.70 | 22.2 | 0.0 | 77.8 | 5.08 |
| ALWAYS_RAG | 64.99 | 1891 | **34.37** | 100.0 | 0.0 | 0.0 | 3.38 |
| GAPA | 63.21 | 5799 | 10.90 | 23.6 | 25.0 | 51.4 | 5.02 |
| ALWAYS_GRAPH | 56.89 | 1727 | 32.94 | 0.0 | 100.0 | 0.0 | 3.05 |

Tokens = `inject.est_tokens_div4` mean over W1.  
qpt×1000 = Overall / tokens × 1000 (higher = more quality per token).

### Dimension means (1–20)

| Arm | Correctness | Completeness | Relevance | Clarity | Reasoning |
|-----|------------:|-------------:|----------:|--------:|----------:|
| SELF_ROUTE | 12.8 | 12.1 | 15.2 | 16.1 | 14.1 |
| ALWAYS_LC | 12.0 | 11.3 | 14.7 | 16.1 | 13.7 |
| GASR | 11.3 | 10.6 | 14.4 | 16.3 | 13.4 |
| PREASSESS | 11.3 | 10.5 | 14.2 | 15.8 | 13.1 |
| ALWAYS_RAG | 11.3 | 10.6 | 14.4 | 15.6 | 13.1 |
| GAPA | 10.7 | 10.0 | 13.9 | 15.8 | 12.8 |
| ALWAYS_GRAPH | 9.1 | 8.3 | 12.6 | 15.4 | 11.5 |

### Per-repo Overall (selected arms)

| Repo | SELF_ROUTE | ALWAYS_LC | GASR | ALWAYS_RAG | ALWAYS_GRAPH |
|------|----------:|----------:|-----:|-----------:|-------------:|
| flask | 61.1 | 55.2 | 53.8 | 55.6 | 49.0 |
| requests | 75.4 | 74.1 | 75.3 | 71.9 | 63.9 |
| pytest | 74.4 | 74.3 | 68.8 | 67.5 | 57.8 |

---

## 4. RepoProbe Python-8 (Sonnet checklist)

| Arm | score/10 | overall % | perfect % | n_scored |
|-----|--------:|----------:|----------:|---------:|
| **SELF_ROUTE** | **3.60** | **36.0** | 11.1 | 108 |
| ALWAYS_LC | 3.57 | 35.7 | 12.0 | 108 |
| PREASSESS | 3.46 | 34.6 | **14.8** | 108 |
| GASR | 3.22 | 32.2 | 7.4 | 108 |
| ALWAYS_RAG | 2.97 | 29.7 | 7.4 | 108 |
| GAPA | 2.86 | 28.6 | 6.5 | 108 |
| ALWAYS_GRAPH | 2.68 | 26.8 | 9.4 | 106 |

---

## 5. Interpretation

### vs local 9B + qwen-7B Wave 1 (prior Table B)

| Arm | Local Overall | This run (GPT-5.1 / Sonnet) |
|-----|-------------:|----------------------------:|
| ALWAYS_RAG | 45.4 | 65.0 |
| GASR | 38.5 | 66.0 |
| ALWAYS_GRAPH | 37.5 | 56.9 |
| GAPA | 35.7 | 63.2 |
| SELF_ROUTE | 28.6 | **70.3** |
| PREASSESS | 16.6 | 65.0 |
| ALWAYS_LC | 15.1 | **67.9** |

**Ranking flips.** Under 9B, RAG led and LC collapsed (context packing / model limits). Under GPT-5.1, **long-context arms win absolute quality** (Self-Route, LC); **Always-RAG wins efficiency** (best qpt, cheapest gen among strong arms). **Always-GRAPH is weakest** on both suites here.

### Routing / thesis-relevant claims (this stack only)

1. **Absolute quality:** Self-Route ≥ LC > GASR ≈ PRE ≈ RAG > GAPA > GRAPH (SWE); RP agrees on the top/bottom.
2. **Cost–quality:** RAG still best qpt; GASR best *gated* mid-token tradeoff.
3. **Gates with GPT-5.1** bias Self-Route/PREASSESS toward LC (~72–78% LC) — high quality, high tokens.
4. **Do not** treat these Overall numbers as paper Table 3 parity (different judge protocol, W1≠720, our scaffolds).

### Scope limits

- SWE side is **Wave 1 only** (3/15 repos, 20% of questions). Prior local full-720 showed W1 Absolute was optimistic vs rest.
- Single-pass Sonnet, not 5-vote.
- One GRAPH cell missing (520).

---

## 6. Skipping already-done work (if extending to full SWE)

**Yes — if the next job is scoped to rest only**, W1 will not be re-billed:

| Mechanism | Behavior |
|-----------|----------|
| Dataset `swe-qa-rest` (576 Q, 12 repos) | Never includes flask/requests/pytest → **W1 skipped by construction** |
| New out dir (e.g. `~/haya/api_runs_rest`) | No overwrite of `api_runs/swe-qa-w1/*` |
| Resume on an existing rest out dir | Harness skips tasks already present in that dir’s `results.jsonl` |
| Judge markers under `api_scores/markers/` | Stream/full judge **won’t re-score** marked arms |

**Not automatic:** pointing a run at full 720 / W1 sample again **would** re-pay W1 unless you exclude those ids. Pool W1 scores + rest scores for n=720 tables; don’t regenerate W1.

---

## 7. Paths & reproducibility

```text
~/haya/api_gen.env          # gen key (not in git)
~/haya/api_judge.env        # judge key (not in git)
~/haya/api_runs/            # results.jsonl per dataset/arm
~/haya/api_scores/          # *_answers_score.jsonl, repoprobe/, markers/
scripts/run_api_w1_rp.sh
scripts/run_api_judge_stream.sh
eval_harness/configs/provider_openrouter.toml
```

**Freeze labels for prose:**  
`generator=openai/gpt-5.1 · gates=same · judge=anthropic/claude-sonnet-4.5 · single-judge · freeze=3ac1066 · W1+RP-py8`
