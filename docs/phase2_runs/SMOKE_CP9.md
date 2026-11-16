# CP9 smoke — RepoProbe checklist (browser-use only)

**Stamp:** 2026-11-15  
**Slice:** all **7 arms** × **browser-use** (n=5 per arm → **35** scores)  
**Judge:** `qwen-7B:latest` · `OLLAMA_MODELS=/usr/share/ollama/.ollama/models`  
**Protocol:** RepoProbe `scorer.py` checklist /10 → Overall% (= score×10) · single-pass  
**Scaffolding:** scorer-on-pipeline (host 7-arm answers)  
**Wall:** ~1290 s (~21.5 min)  
**Artifacts:** `eval_harness/data/reports/phase2_repoprobe_py/smoke_browser_use/`

| Arm | Overall% | /10 mean | PerfectSolve% |
|-----|--------:|--------:|-------------:|
| ALWAYS_RAG | **68** | 6.8 | 20 |
| ALWAYS_GRAPH | **68** | 6.8 | — |
| PREASSESS | 64 | 6.4 | — |
| GAPA | 58 | 5.8 | — |
| SELF_ROUTE | 50 | 5.0 | — |
| GASR | 40 | 4.0 | — |
| ALWAYS_LC | 24 | 2.4 | 0 |

**Label:** `local-judge · single-judge · smoke · browser-use · n=5/arm · not full Table C`

---

## Metrics (RepoProbe ≠ SWE-QA)

| Piece | This smoke (RepoProbe) | SWE-QA Wave 1 / flask Claude |
|--------|------------------------|------------------------------|
| Score | Checklist **/10** → **Overall%** (= ×10) | Five dims 1–20 → **Overall /100** |
| What is graded | Knowledge (≤9) + Clarity (≤1) vs CSV checklist + gold | Correctness / Completeness / Relevance / Clarity / Reasoning vs gold |
| Judge | `qwen-7B` · single-pass `scorer.py` | `qwen-7B` or Claude-subagent · SWE-QA rubric |
| Scope here | **browser-use only · n=5** | Wave 1 **n=144** or flask **n=48** |

So you **cannot** read “68%” next to SWE-QA “45/100” as the same scale — only **arm rankings within each experiment**.

---

## How to read this ranking (and why it looks “opposite” to SWE-QA)

### SWE-QA Wave 1 reminder (qwen judge, n=144)

**ALWAYS_RAG ≫ GASR ≫ … ≫ PREASSESS ≈ ALWAYS_LC**  
LC collapses (cap / lost-in-the-middle); GASR loses to Always-RAG mainly by escalating into GRAPH/LC.

### This smoke (RepoProbe checklist, n=5)

**ALWAYS_RAG ≈ ALWAYS_GRAPH > PREASSESS > GAPA > SELF_ROUTE > GASR > ALWAYS_LC**

That is **not** a clean reversal of the SWE-QA story. Breakdown:

1. **n=5 is tiny** — one 0/10 or 10/10 moves the arm mean by ~20 Overall%. Treat as a **wiring check**, not a new ranking law.

2. **Top: RAG and GRAPH tied (68%)**  
   On this small repo, both often retrieve enough checklist-relevant snippets. Not evidence that GRAPH beats RAG on full Python-8.

3. **PREASSESS high (64%) but 100% LC** — looks opposite to SWE-QA, where PREASSESS ≈ LC floor.  
   - Route is still LC (same as Always-LC / Self-Route).  
   - Checklist scores differ because **answers differ** (generation variance) and the judge is strict about *repo-specific* checklist bullets.  
   - Example: Q1 PREASSESS **9**/10 vs Always-LC **0**/10 — PRE hit “separate browser instances”; LC talked about Chrome debug ports and was marked unrelated.  
   - So PREASSESS ≠ “LC is suddenly good”; Always-LC is still **last (24%)**. PRE lucked into better LC generations on this slice.

4. **GASR last among routers (40%)** — same *mechanism* as SWE-QA, worse draw on n=5:  
   routes = LC / GRAPH / GRAPH / RAG / GRAPH with scores **2, 2, 10, 6, 0**.  
   One bad LC + one 0 GRAPH tanks the mean. When GASR stayed RAG (Q3), score **6** — fine. Not “RAG lost to GASR.”

5. **ALWAYS_LC still worst (24%)** — same qualitative failure mode as SWE-QA (big dump, weak checklist match), even though some PREASSESS LC answers scored well.

6. **Different task style** — RepoProbe items are often issue/discussion + checklist points; a long LC answer that happens to name the right API can score. SWE-QA gold answers are long multi-hop architecture writeups where missing the right file usually zeros several dims.

### Thesis framing

| Claim | Smoke supports? |
|-------|-----------------|
| Scorer-on-pipeline + local checklist judge **runs** | **Yes** (35/35) |
| Arm ranking flipped forever vs SWE-QA | **No** — n=5 + different metric |
| Always-LC remains weak | **Yes** (bottom) |
| GASR can underperform Always-RAG when escalations miss | **Yes** (same theme as Wave 1) |
| PREASSESS > RAG on RepoProbe | **Not established** — PRE=LC with lucky answers |

**Next:** full CP9 (7×108) before any RepoProbe ranking claims vs SWE-QA.

**Caveats:** Not paper RepoProbe Table 3 (Claude Sonnet 4.5 judge · Docker agent scaffolding · full Python-8).
