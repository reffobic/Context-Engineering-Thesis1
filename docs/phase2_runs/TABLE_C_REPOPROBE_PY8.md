# Table C — RepoProbe Python-8 (CP9 full)

**Full write-up (paper Table 3 + metrics + Qwen Max/Plus + vs SWE-QA):** [`REPOPROBE_CROSS_JUDGE.md`](REPOPROBE_CROSS_JUDGE.md)

**Stamp:** 2026-11-15  
**Label:** `local-judge · single-judge · scorer-on-pipeline · Python-8 · n≤108/arm`  
**Generator:** `qwen3.5:9b` (7 arms)  
**Judge:** `qwen-7B:latest` · RepoProbe checklist `/10` → Overall% (= ×10) · 1 iteration  
**Wall:** ~1215 s (~20 min)  
**Artifacts:** `eval_harness/data/reports/phase2_repoprobe_py/full/`  
**Log:** `eval_harness/data/logs/phase2-cp9-full.log`

## Results (ranked by Overall%)

| Arm | Overall% | /10 mean | n scored | PerfectSolve% |
|-----|--------:|--------:|---------:|--------------:|
| **GASR** | **56.1** | 5.61 | 105 | 20.0 |
| GAPA | 53.0 | 5.30 | 104 | 15.4 |
| SELF_ROUTE | 52.6 | 5.26 | 104 | 19.2 |
| PREASSESS | 52.0 | 5.20 | 108 | 18.5 |
| ALWAYS_LC | 48.9 | 4.89 | 108 | 17.6 |
| ALWAYS_GRAPH | 41.9 | 4.19 | 107 | 12.1 |
| ALWAYS_RAG | 40.3 | 4.03 | 108 | 10.2 |

**Coverage:** 744 checklist scores (not 756). Gaps = prior generation `error` rows on some arms + **3** judge JSON-parse fails (`ALWAYS_GRAPH` adk-0, `SELF_ROUTE` adk-9, `GAPA` docling-9).

## Metrics

| Column | Meaning |
|--------|---------|
| Overall% | mean checklist total ×10 (paper-style %) |
| /10 mean | raw mean total_score |
| PerfectSolve% | share with total ≥ 10/10 |
| Judge | local `qwen-7B` ≠ paper Claude Sonnet 4.5 |

## vs SWE-QA Wave 1 (different benchmark + scale)

| | SWE-QA Wave 1 (qwen judge /100) | RepoProbe CP9 (checklist %) |
|--|--------------------------------|-----------------------------|
| Best quality | **ALWAYS_RAG** 45.4 | **GASR** 56.1 |
| Weak | ALWAYS_LC / PREASSESS ~15–17 | ALWAYS_RAG / GRAPH ~40–42 |
| LC | Collapses | Mid-pack (48.9) |

**Do not treat as contradiction of SWE-QA.** Different questions, checklist vs 5-dim gold match, and a small local judge that sometimes emits totals **>10** (parsing/criteria drift). Primary SWE-QA claim (RAG strong, LC weak under 9B) still stands on that suite. RepoProbe needs paper-faithful judge (Sonnet) and/or full-agent scaffolding before absolute Table 3 claims.

## vs smoke (browser-use n=5)

Smoke ranking was unstable; full Python-8 puts **GASR** first and **Always-RAG** last — another reason n=5 was only a wiring check ([`SMOKE_CP9.md`](SMOKE_CP9.md)).

## Caveats

- Not head-to-head with paper RepoProbe Table 3 (Claude Sonnet 4.5 · Claude Code agents · multi-vote).  
- Scorer-on-pipeline only.  
- Re-score JSON fails if needed for exact 108/arm.  
