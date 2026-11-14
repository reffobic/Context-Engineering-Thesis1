# Phase 2 Evaluation Plan — Repo-Level Code Explanation (QA)

**Status:** Planner/Executor rewrite (2026-11-08)  
**Branch context:** `baseline-nov3-handover` has **Phase 1** (localization) results; this document specifies **Phase 2** (explanation QA), not yet executed.  
**Sources:** SWE-QA [arXiv:2509.14635v2](https://arxiv.org/abs/2509.14635) · RepoProbe [arXiv:2608.04783v2](https://arxiv.org/abs/2608.04783) · PDFs in repo root.

---

## 0. Two-phase framing (what / why / how)

Our hypothesis tests **across paradigms**. A repository can be queried in (at least) two ways:

| Family | Example | Evaluation phase |
|--------|---------|------------------|
| **Code / location-based** | “Where do I change X?” | **Phase 1 — Localization** |
| **Natural-language explanation** | “How / why does this work?” | **Phase 2 — Repo QA** |

Both phases score **context level** (was the right evidence selected/injected?) and **answer level** (was the output good for that task?).

| | **Phase 1 (done on this branch)** | **Phase 2 (this plan)** |
|--|-----------------------------------|-------------------------|
| Datasets | SWE-Bench-Lite (27) + Loc-Bench (56) | **SWE-QA** + **RepoProbe** |
| Arms | Same **7** (see §2) | Same **7** |
| Context metrics | File/fn Acc@k on final context | Tokens; RAG/GRAPH/LC rates; optional IR |
| Answer metrics | Acc@k on LOCATIONS list | SWE-QA **/100**; RepoProbe **checklist /10** |
| Status | Nov 3 run family complete | **Not run yet** — do not relabel Nov 3 as Phase 2 |

**Why Phase 2 is required:** Self-Route and Pre-Route claim a **token–quality** tradeoff on **QA**, not localization Acc@k. Phase 1 alone cannot ground that narrative. Phase 2 uses public explanation benchmarks with gold answers / checklists.

---

## 1. Benchmark deep read

### 1.1 SWE-QA (Peng et al., ACL Findings 2026)

| Item | Detail |
|------|--------|
| **What** | Repository-level **code QA**: NL question → NL explanation grounded in a Python repo |
| **Scale** | **720** human-validated pairs; **15** repos; **48**/repo |
| **Construction** | Issues-inspired taxonomy → curated Qs + expert gold answers |
| **Taxonomy** | Level-1 **What / Why / Where / How** (How ~35%); Level-2 twelve intentions |
| **Paper systems** | Direct · Sliding-Window RAG · Function-Chunking RAG · SWE-agent · OpenHands · Cursor / Tongyi |
| **Official metrics** | LLM-as-Judge vs gold (Claude Sonnet 4.5): Correctness, Completeness, Relevance, Clarity, Coherence — each **/20** → **/100** (App. B.2); 5× majority vote; judge ≠ candidates; human study |
| **Dimension meanings** | Correctness = factual accuracy; Completeness = covers all aspects of the question; Relevance = on-topic; Clarity = organization/readability; Coherence = logical reasoning (tables often label “Reasoning”) |
| **Headline findings** | No-context worst; RAG large gains; agents often best; Correctness/Completeness lag Relevance/Clarity; hard on constrained What / locational How / multi-hop |
| **Limits** | Python-only; judge bias risk; static snapshots |

**Why LLM judge is justified (SWE-QA):** open-ended explanations have no unique string match or unit-test oracle; scoring is **conditioned on gold answers** with multi-dim rubric and human corroboration.

### 1.2 RepoProbe (Yang et al., ASE 2026)

| Item | Detail |
|------|--------|
| **What** | **Architecture-aware** comprehension: open-ended how/why from **GitHub Discussions** (not bug localization) |
| **Scale** | **500** Qs · **50** repos · **15** languages |
| **Categories** | Project Architecture · Business Logic · Implementation Details |
| **Official metrics** | Checklist **/10** = knowledge ≤9 + clarity 1 → **Overall %**, **Knowledge %**, **Clarity %**, **Perfect Solve %** (full 10/10) |
| **Harness** | Docker agent contract; paper uses fixed agent scaffolding for model comparison |
| **Headline findings (Table 3)** | GPT-5.2 Overall **62.7** / Knowledge **60.3** / Clarity **84.7** / Perfect **26.0**; clarity ≫ knowledge; Perfect Solve remains moderate |
| **Vs SWE-QA (their Table 1)** | Both repo QA + open-ended; RepoProbe adds multilingual + **checklist verification** |

**Why LLM judge is justified (RepoProbe):** same open-ended oracle problem; they **constrain** the judge with weighted checklist items and tiered rubrics (addresses scalar-judge fluency bias — their Limitation 2).

---

## 2. The 7 arms (same matrix as Phase 1 / Nov 3)

Code reference: `docs/analysis/nov3_lib.py` (`ARMS`).

| Arm | Type | Behavior | Role |
|-----|------|----------|------|
| **Always-RAG** | Baseline | Always pruned retrieve snippets | Cheap lower bound |
| **Always-GRAPH** | Baseline | Always graph-expand + re-prune | Always-on graph vs gated |
| **Always-LC** | Baseline | Always long / parent context | Costly upper bound; token% denominator |
| **Self-Route** | Method | Post-retrieve sufficiency → RAG \| LC (no GRAPH) | Li et al. binary |
| **PREASSESS** | Method | Pre-retrieve / metadata gate → RAG \| LC (no GRAPH) | Pre-Route-style early gate |
| **GASR** | Method | Post-retrieve + STRUCTURE\|CONTEXT → RAG → GRAPH → LC | Graph × Self-Route |
| **GAPA** | Method | Pre-assess entry, then same GRAPH→LC cascade as GASR | Graph × PREASSESS (= GAPR in older tex) |

**Lit aliases (first mention in paper):** PREASSESS ↔ Pre-Route; GAPA ↔ GAPR.

**Contrasts these arms enable:**

| Contrast | Arms | Question |
|----------|------|----------|
| Need gated graph? | Self-Route vs GASR; PREASSESS vs GAPA | Does GRAPH help? |
| Gate timing? | GASR vs GAPA; Self-Route vs PREASSESS | Post vs pre |
| Always vs gated graph? | Always-GRAPH vs GASR/GAPA | Is gating better? |
| Bounds | Always-RAG vs Always-LC | Token–quality envelope |

---

## 3. How we integrate

### 3.1 Shared controls
- All **7** arms; fixed generator (e.g. `qwen3.5:9b`, document digest).
- Freeze SHAs: SWE-QA `repo_commit.txt`; RepoProbe `repos_info.json`.
- Graphify required for Always-GRAPH / GASR / GAPA.
- Always log **our** cost/routing: injected tokens, token% vs Always-LC, %RAG/%GRAPH/%LC, wall ms — **plus** each suite’s official quality metrics.
- **Never** average SWE-QA /100 with RepoProbe /10.

### 3.2 SWE-QA protocol
1. Clone pinned repos → index + Graphify.  
2. Run each arm (harness / `/generate` equivalent used for Nov 3, adapted for free-form answers).  
3. Score with SWE-QA 5-dimension protocol (Prompt E / App. B.2); fixed judge ≠ generator.  
4. **Wave 1:** Flask + Requests + Pytest; **Wave 2:** SymPy or Django; full 720 if resources allow.

### 3.3 RepoProbe protocol (default)
- **Scorer-on-pipeline:** our 7 arms produce answers on host → grade with their `scorer.py` checklists (same scaffolding all arms).  
- Optional Docker agent only if reporting under their container contract; state scaffolding explicitly.  
- Start **Python-heavy** CSV subset (5–10 repos) before full 500 / multi-lang.

---

## 4. Comparisons to others in the benchmarks

| Comparison | How we use it |
|------------|----------------|
| **SWE-QA paper systems** | Cite Direct / FC-RAG / SW-RAG / agents on **same subset**; primary claim is **routing cost–quality**, not beating Cursor absolute /100 |
| **RepoProbe Table 3 models** | Optional reference row only (different scaffolding — **not** head-to-head with GPT-5.2) |
| **Self-Route / Pre-Route lit** | Report token% and LC rate in their style; quality = suite official metrics |

---

## 5. Hypotheses (Phase 2)

| ID | Hypothesis | Falsifier |
|----|------------|-----------|
| **H1** | GASR ≥30% mean token cut vs Self-Route at non-inferior SWE-QA Overall/100 (tolerance e.g. −3 pts) | Token Δ &lt; 30% or quality drop beyond tolerance |
| **H2** | GAPA matches/beats GASR on gate-token economy vs PREASSESS (metadata entry cheaper) | Gate cost higher without quality gain |
| **H3** | On RepoProbe, GASR/GAPA Knowledge% ≥ Self-Route/PREASSESS at lower tokens | Knowledge flat/down; only Clarity up |
| **H4** | %LC(GASR) &lt; %LC(Self-Route) on How / Architecture strata | LC rate not lower |
| **H5** | Always-GRAPH does not dominate GASR on tokens | Always-GRAPH cheaper at equal quality |

---

## 6. Expected result tables

### Table A — Literature anchors (filled from papers)

**A1. SWE-QA (illustrative paper numbers; cite Table 3 family)**

| System (paper) | Overall /100 (example) | Note |
|----------------|------------------------|------|
| Direct (no repo context) | ~50–66 depending on model | Lowest; shows need for code context |
| + Function-Chunking RAG | e.g. Qwen3-Coder-30B **50.80 → 63.40** (+12.6) | Large RAG gain |
| + Sliding-Window RAG | Similar large gains | — |
| + SWE-agent / OpenHands | Often highest among open stacks | Agent &gt; RAG |
| Cursor / Tongyi | ~69–71 | Commercial reference |

**A2. RepoProbe (paper Table 3, %)**

| Model | Overall | Knowledge | Clarity | Perfect Solve |
|-------|---------|-----------|---------|---------------|
| GPT-5.2 | **62.7** | **60.3** | **84.7** | **26.0** |
| Claude Opus 4.6 | 62.1 | 60.2 | 79.0 | 27.5 |
| GLM-5 (best open in table) | 54.8 | 52.9 | 72.2 | 21.3 |

Signature to reproduce qualitatively: **Clarity ≫ Knowledge**; Perfect Solve remains moderate.

---

### Table B — Our Phase 2 main (SWE-QA subset) — **to fill**

*n = ___ · generator = ___ · judge = ___ · stamp = ___*

| Arm | Overall/100 | Corr | Comp | Rel | Clar | Coh | tokens mean | token% vs LC | %RAG | %GRAPH | %LC | wall ms |
|-----|-------------|------|------|-----|------|-----|-------------|--------------|------|--------|-----|---------|
| Always-RAG | | | | | | | | | | | | |
| Always-GRAPH | | | | | | | | | | | | |
| Always-LC | | | | | | | | | | | | |
| Self-Route | | | | | | | | | | | | |
| PREASSESS | | | | | | | | | | | | |
| GASR | | | | | | | | | | | | |
| GAPA | | | | | | | | | | | | |

**Expected direction:** GASR/GAPA tokens ≪ Self-Route/PREASSESS; Overall non-inferior; %LC↓ with %GRAPH&gt;0 on How/multi-hop.

---

### Table C — RepoProbe subset — **to fill**

*n = ___ · scaffolding = scorer-on-pipeline · stamp = ___*

| Arm | Overall% | Knowledge% | Clarity% | Perfect% | tokens mean | %LC | %GRAPH |
|-----|----------|------------|----------|----------|-------------|-----|--------|
| Always-RAG | | | | | | | |
| Always-GRAPH | | | | | | | |
| Always-LC | | | | | | | |
| Self-Route | | | | | | | |
| PREASSESS | | | | | | | |
| GASR | | | | | | | |
| GAPA | | | | | | | |

**Expected:** Clarity &gt; Knowledge for all arms; GASR/GAPA raise Knowledge, not only Clarity.

---

### Table D — Stratified quality — **to fill**

**D1. SWE-QA Level-1 × arm (Overall/100)**

| Arm | What | Why | Where | How |
|-----|------|-----|-------|-----|
| Always-RAG | | | | |
| Always-GRAPH | | | | |
| Always-LC | | | | |
| Self-Route | | | | |
| PREASSESS | | | | |
| GASR | | | | |
| GAPA | | | | |

**D2. RepoProbe category × arm (Overall%)**

| Arm | Architecture | Business Logic | Implementation |
|-----|--------------|----------------|----------------|
| … (same 7 rows) | | | |

**Expected:** largest GRAPH absorb on How / Architecture / Implementation multi-file questions.

---

### Table E — Fairness vs SWE-QA paper RAG — **to fill**

| System | Overall/100 | tokens mean | Note |
|--------|-------------|-------------|------|
| Paper Function-Chunking RAG (same model family if possible) | | — | Literature anchor |
| Our Always-RAG | | | Same questions |
| Our GASR | | | Routing + graph |

---

### Table F — Design contrasts (analysis checklist)

| Contrast | Pair | Metric focus | Result (fill after run) |
|----------|------|--------------|-------------------------|
| Gated graph helps? | Self-Route vs GASR | Overall + tokens + %LC | |
| Gated graph helps? | PREASSESS vs GAPA | Overall + tokens + %LC | |
| Gate timing | GASR vs GAPA | Gate tokens + Pareto | |
| Gate timing | Self-Route vs PREASSESS | Overall + LC rate | |
| Always vs gated | Always-GRAPH vs GASR | tokens at similar Overall | |
| Bounds | Always-RAG vs Always-LC | Envelope | |

---

## 7. Execution order

1. Freeze SWE-QA Wave 1 repos + commits; index + Graphify.  
2. Run **7 arms** → fill **Table B**.  
3. RepoProbe Python subset → fill **Table C**.  
4. Stratify → **Table D**; paper RAG compare → **Table E**; write **Table F**.  
5. Paper Evaluation section: Phase 1 = Nov 3 Loc Acc@k; Phase 2 = these tables; cite both arXiv links.

---

## 8. Companion artifacts

| Artifact | Role |
|----------|------|
| [`docs/slides/PHASE2_SWE_QA_RepoProbe_one_v2.pptx`](slides/PHASE2_SWE_QA_RepoProbe_one_v2.pptx) | One-slide summary of the two suites |
| [`docs/analysis/README.md`](analysis/README.md) | Phase 1 Nov 3 analysis (do not mix into Phase 2 tables) |
| [`docs/analysis/nov3_lib.py`](analysis/nov3_lib.py) | Canonical 7-arm labels |

---

## 9. One-paragraph Methods blurb (draft)

> We evaluate repository understanding under two query families. **Phase 1** measures code localization on SWE-Bench-Lite and Loc-Bench (file/function Acc@k at context and answer levels) under seven arms: Always-RAG, Always-GRAPH, Always-LC, Self-Route, PREASSESS, GASR, and GAPA. **Phase 2** measures repository-level code explanation on SWE-QA and RepoProbe with the same seven arms. On SWE-QA we report the official five-dimension LLM-as-Judge score (/100) against gold answers; on RepoProbe we report checklist Overall, Knowledge, Clarity, and Perfect Solve rates. In both Phase 2 suites we additionally report injected tokens, token percentage versus Always-LC, and RAG/GRAPH/LC selection rates, following the cost–quality reporting style of Self-Route and Pre-Route.
