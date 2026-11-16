# Table B — SWE-QA full (Phase 2)

*generator = qwen3.5:9b · judge = qwen-7B:latest (local-judge, single-judge) · n = 720 (Wave 1 144 ∪ rest 576) · stamp = 2026-11-16T12:24:38Z*

| Arm | Overall/100 | tokens mean | token% vs LC | %RAG | %GRAPH | %LC | wall_ms mean |
|-----|-------------|-------------|--------------|------|--------|-----|--------------|
| ALWAYS_LC | 13.65 | 16358.7 | 100.0 | 0.0 | 0.0 | 100.0 | 7619.93 |
| ALWAYS_RAG | 36.33 | 1863.33 | 11.4 | 100.0 | 0.0 | 0.0 | 5420.94 |
| ALWAYS_GRAPH | 31.61 | 1691.76 | 10.3 | 0.0 | 100.0 | 0.0 | 5459.87 |
| SELF_ROUTE | 25.73 | 13514.47 | 82.6 | 20.9 | 0.0 | 79.1 | 8430.46 |
| PREASSESS | 14.45 | 16123.52 | 98.6 | 2.2 | 0.0 | 97.8 | 8438.83 |
| GASR | 29.89 | 6920.09 | 42.3 | 19.4 | 46.2 | 34.4 | 8432.9 |
| GAPA | 30.62 | 2274.48 | 13.9 | 2.1 | 94.1 | 3.8 | 9037.5 |

## Dim means

| Arm | Correctness | Completeness | Relevance | Clarity | Reasoning |
|-----|-------------|--------------|-----------|---------|-----------|
| ALWAYS_LC | 2.87 | 2.65 | 2.72 | 2.73 | 2.69 |
| ALWAYS_RAG | 7.39 | 7.07 | 7.6 | 7.24 | 7.03 |
| ALWAYS_GRAPH | 6.42 | 6.16 | 6.55 | 6.32 | 6.16 |
| SELF_ROUTE | 5.26 | 5.02 | 5.29 | 5.13 | 5.02 |
| PREASSESS | 3.02 | 2.8 | 2.89 | 2.89 | 2.84 |
| GASR | 6.07 | 5.83 | 6.17 | 5.97 | 5.84 |
| GAPA | 6.23 | 5.97 | 6.35 | 6.12 | 5.97 |

## vs SWE-QA paper (anchors only)

Paper Table 3 uses Claude Sonnet 4.5 · 5-vote and stronger scaffolds. This table is **suite-scope** routing cost–quality under our freeze — not absolute paper parity.
- GASR vs Self-Route token cut ≈ **48.8%** (GASR 6920.09 vs Self-Route 13514.47); Overall 29.89 vs 25.73.
- Always-RAG Overall=36.33 (top under this 9B/local-judge setup).

### Split means (Overall/100)

| Arm | Wave 1 (144) | Rest (576) | Full (720) |
|-----|-------------:|-----------:|-----------:|
| ALWAYS_LC | 15.12 | 13.29 | 13.65 |
| ALWAYS_RAG | 45.44 | 34.05 | 36.33 |
| ALWAYS_GRAPH | 37.46 | 30.14 | 31.61 |
| SELF_ROUTE | 28.57 | 25.01 | 25.73 |
| PREASSESS | 16.55 | 13.92 | 14.45 |
| GASR | 38.47 | 27.75 | 29.89 |
| GAPA | 35.67 | 29.36 | 30.62 |
