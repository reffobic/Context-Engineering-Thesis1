# Table B — SWE-QA rest (12 repos beyond Wave 1)

*generator = qwen3.5:9b · judge = qwen-7B:latest (local-judge, single-judge) · n = 576 (12 repos) · stamp = 2026-11-16T12:24:38Z*

| Arm | Overall/100 | tokens mean | token% vs LC | %RAG | %GRAPH | %LC | wall_ms mean |
|-----|-------------|-------------|--------------|------|--------|-----|--------------|
| ALWAYS_LC | 13.29 | 16716.39 | 100.0 | 0.0 | 0.0 | 100.0 | 8071.97 |
| ALWAYS_RAG | 34.05 | 1856.2 | 11.1 | 100.0 | 0.0 | 0.0 | 5623.81 |
| ALWAYS_GRAPH | 30.14 | 1700.85 | 10.2 | 0.0 | 100.0 | 0.0 | 5805.54 |
| SELF_ROUTE | 25.01 | 13955.46 | 83.5 | 19.9 | 0.0 | 80.1 | 8856.63 |
| PREASSESS | 13.92 | 16456.61 | 98.4 | 2.3 | 0.0 | 97.7 | 8851.61 |
| GASR | 27.75 | 7041.8 | 42.1 | 16.9 | 47.6 | 35.4 | 8725.98 |
| GAPA | 29.36 | 2241.06 | 13.4 | 2.1 | 94.2 | 3.7 | 9413.73 |

## Dim means

| Arm | Correctness | Completeness | Relevance | Clarity | Reasoning |
|-----|-------------|--------------|-----------|---------|-----------|
| ALWAYS_LC | 2.76 | 2.58 | 2.67 | 2.66 | 2.62 |
| ALWAYS_RAG | 6.93 | 6.61 | 7.14 | 6.78 | 6.59 |
| ALWAYS_GRAPH | 6.12 | 5.87 | 6.26 | 6.02 | 5.86 |
| SELF_ROUTE | 5.11 | 4.88 | 5.17 | 4.99 | 4.87 |
| PREASSESS | 2.89 | 2.7 | 2.8 | 2.79 | 2.74 |
| GASR | 5.64 | 5.4 | 5.75 | 5.54 | 5.41 |
| GAPA | 5.97 | 5.71 | 6.1 | 5.86 | 5.71 |

## vs SWE-QA paper (anchors only)

Paper Table 3 uses Claude Sonnet 4.5 · 5-vote and stronger scaffolds. This table is **suite-scope** routing cost–quality under our freeze — not absolute paper parity.
- GASR vs Self-Route token cut ≈ **49.5%** (GASR 7041.8 vs Self-Route 13955.46); Overall 27.75 vs 25.01.
- Always-RAG Overall=34.05 (top under this 9B/local-judge setup).
