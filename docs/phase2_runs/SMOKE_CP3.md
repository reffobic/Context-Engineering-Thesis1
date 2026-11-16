# CP3 smoke — 1 question × 7 arms

**Task:** `flask-000` (SWE-QA Wave 1)  
**Model:** `qwen3.5:9b` · `OLLAMA_THINK=0`  
**Out:** `eval_harness/data/runs/phase2-smoke/<ARM>/`  
**Stamp:** 2026-11-14

| Arm | status | route | hops | wall_s | prompt_tok | eval_tok |
|-----|--------|-------|------|--------|------------|----------|
| ALWAYS_LC | ok | LC | 0 | 11.03 | 21976 | 302 |
| ALWAYS_RAG | ok | RAG | 0 | 3.07 | 2154 | 203 |
| ALWAYS_GRAPH | ok | GRAPH | 2 | 1.93 | 1744 | 102 |
| SELF_ROUTE | ok | RAG | 0 | 4.01 | 2154 | 203 |
| PREASSESS | ok | LC | 0 | 5.04 | 21976 | 302 |
| GASR | ok | RAG | 0 | 3.99 | 2154 | 203 |
| GAPA | ok | GRAPH | 1 | 4.79 | 1744 | 102 |

**Checks:** free-form answers (no LOCATIONS block); GRAPH arms used hops; all status=ok.

## ETA extrapolate (Wave 1 = 144 × 7 = 1008)

Smoke mean ≈ **4.8 s/task** on this easy flask Q (RTX 4080). Harder pytest Qs + gates will be slower.

| Assumed mean | Wave 1 wall |
|--------------|-------------|
| 10 s | ~2.8 h |
| 30 s | ~8.4 h |
| 60 s | ~16.8 h |

Queue `estimated_minutes` (~180–260 per arm) is conservative; hard_stop set to **2026-11-22T08:00 Africa/Cairo**.
