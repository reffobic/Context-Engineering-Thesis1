# Table B — SWE-QA Wave 1 (Phase 2)

*n = 144 · generator = qwen3.5:9b · judge = qwen-7B:latest (local-judge, single-judge) · stamp = 2026-11-15T05:08:35Z*

| Arm | Overall/100 | tokens mean | token% vs LC | %RAG | %GRAPH | %LC | wall_ms mean |
|-----|-------------|-------------|--------------|------|--------|-----|--------------|
| ALWAYS_LC | 15.12 | 14927.94 | 100.0 | 0.0 | 0.0 | 100.0 | 5811.76 |
| ALWAYS_RAG | 45.44 | 1891.83 | 12.7 | 100.0 | 0.0 | 0.0 | 4609.48 |
| ALWAYS_GRAPH | 37.46 | 1655.43 | 11.1 | 0.0 | 100.0 | 0.0 | 4077.2 |
| SELF_ROUTE | 28.57 | 11750.5 | 78.7 | 25.0 | 0.0 | 75.0 | 6725.8 |
| PREASSESS | 16.55 | 14791.15 | 99.1 | 2.1 | 0.0 | 97.9 | 6787.74 |
| GASR | 38.47 | 6433.25 | 43.1 | 29.2 | 40.3 | 30.6 | 7260.58 |
| GAPA | 35.67 | 2408.19 | 16.1 | 2.1 | 93.8 | 4.2 | 7532.59 |

## Dim means

| Arm | Correctness | Completeness | Relevance | Clarity | Reasoning |
|-----|-------------|--------------|-----------|---------|-----------|
| ALWAYS_LC | 3.28 | 2.9 | 2.94 | 3.03 | 2.97 |
| ALWAYS_RAG | 9.25 | 8.9 | 9.44 | 9.06 | 8.81 |
| ALWAYS_GRAPH | 7.6 | 7.33 | 7.73 | 7.48 | 7.32 |
| SELF_ROUTE | 5.86 | 5.58 | 5.78 | 5.73 | 5.62 |
| PREASSESS | 3.53 | 3.2 | 3.24 | 3.31 | 3.26 |
| GASR | 7.78 | 7.58 | 7.87 | 7.68 | 7.56 |
| GAPA | 7.24 | 6.98 | 7.34 | 7.13 | 6.98 |

## CP7 — vs SWE-QA paper (anchors only)

Paper systems (Direct / FC-RAG / agents) use different scaffolding and often larger models; primary claim is **routing cost–quality** among our 7 arms, not beating Cursor absolute /100.
- GASR vs Self-Route token cut ≈ **45.3%** (GASR 6433.25 vs Self-Route 11750.5); Overall 38.47 vs 28.57.
- GASR %GRAPH=40.3 %LC=30.6; Self-Route %RAG=25.0 %LC=75.0.
- Our Always-RAG Overall=45.44 (paper FC-RAG example ~63 with Qwen3-Coder-30B — different model/scaffold).
