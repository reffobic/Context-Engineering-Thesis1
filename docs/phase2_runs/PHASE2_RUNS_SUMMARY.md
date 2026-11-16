# Phase 2 runs summary — results vs SWE-QA paper

**Stamp (UTC):** 2026-11-16  
**Branch / freeze:** `phase2-eval` · pipeline HEAD `a2cfcec…` (see [`FREEZE.md`](FREEZE.md))  
**SWE-QA:** Wave 1 **144** + rest **576** = **full 720** · pinned SHAs in FREEZE

This note summarizes **everything completed so far**, how the numbers relate to each other, and how they sit against the **SWE-QA paper** ([arXiv:2509.14635](https://arxiv.org/abs/2509.14635)). It is a literature-anchor comparison, not a claim of identical scaffolding.

---

## 1. What we ran

| # | Run | Generator | Judge | Scope | Primary artifact |
|---|-----|-----------|-------|-------|------------------|
| **R1** | 7-arm Wave 1 generation + judge | `qwen3.5:9b` (Ollama) | `qwen-7B:latest` · single-pass | 144 Q × 7 arms | [`TABLE_B_SWEQA_W1.md`](TABLE_B_SWEQA_W1.md) |
| **R2** | Claude cross-judge (robustness) | same R1 answers | Cursor Claude subagent (`claude-sonnet-5-5-high`) · single-pass | **flask only** · 48 Q × 7 arms | `eval_harness/data/reports/phase2_claude_judge_flask/` |
| **R3** | Cursor CLI agent (paper-scaffold) | Cursor `agent` `2026.11.13` · model `auto` | same Claude subagent · single-pass | **flask only** · 48 Q | [`agent_cursor/TABLE_B_AGENT_FLASK.md`](agent_cursor/TABLE_B_AGENT_FLASK.md) |
| **R4** | 7-arm SWE-QA rest + merge full | same as R1 | same qwen-7B · single-pass | **576 Q × 7** → merge **n=720** | [`TABLE_B_SWEQA_FULL.md`](TABLE_B_SWEQA_FULL.md) · rest [`TABLE_B_SWEQA_REST.md`](TABLE_B_SWEQA_REST.md) |

**Also done:** RepoProbe generation (CP8) + checklist score (CP9 full) — compact [`TABLE_C_REPOPROBE_PY8.md`](TABLE_C_REPOPROBE_PY8.md) · full twin of SWE-QA cross-judge doc: [`REPOPROBE_CROSS_JUDGE.md`](REPOPROBE_CROSS_JUDGE.md) (paper Table 3 + our arms; GASR 56.1% top; Always-RAG 40.3%; ≠ SWE-QA ranking).  

**Still open:** CP10–12 write-up polish; budgeted Always-LC re-run; optional `requests` Cursor-agent; paper-style 5-vote Claude API judge. Human verify **Table B full**.

---

## 2. Locked setup (short)

| Knob | Value |
|------|--------|
| Generator (pipeline) | `qwen3.5:9b` · `OLLAMA_THINK=0` · `num_ctx=32768` · RAG `token_budget=1300` |
| Inject ceiling | `MAX_CONTEXT_TOKENS=24000` (Always-LC hit this on 144/144) |
| Main judge (R1) | `qwen-7B:latest` ≠ generator · label `local-judge · single-judge` |
| Cross / agent judge (R2–R3) | Claude via Cursor subagents · label `claude-subagent · single-judge` |
| Hardware | RTX 4080 16 GB · local Ollama |

Paper judge for reference: **Claude Sonnet 4.5** with **5-vote majority**. Our Claude runs are **single-pass** and use Cursor’s Sonnet 5.5-class subagent — same *rubric family*, not the paper protocol.

---

## 3. Run R1 — 7 arms, Wave 1 (main Table B)

**Question:** Among our routes (RAG / GRAPH / LC / gates), what is the **quality–token** trade-off under a fixed small generator?

### Overall (qwen-7B judge, n=144)

| Arm | Overall/100 | tokens mean | token% vs LC | Route mix |
|-----|------------:|------------:|-------------:|-----------|
| **ALWAYS_RAG** | **45.44** | 1892 | 12.7% | 100% RAG |
| GASR | 38.47 | 6433 | 43.1% | 29% RAG / 40% GRAPH / 31% LC |
| ALWAYS_GRAPH | 37.46 | 1655 | 11.1% | 100% GRAPH |
| GAPA | 35.67 | 2408 | 16.1% | ~94% GRAPH |
| SELF_ROUTE | 28.57 | 11751 | 78.7% | 25% RAG / 75% LC |
| PREASSESS | 16.55 | 14791 | 99.1% | ~98% LC |
| ALWAYS_LC | 15.12 | 14928 | 100% | 100% LC (full parent dump → 24k cap) |

### Per-repo Overall (qwen-7B)

| Arm | flask | requests | pytest |
|-----|------:|---------:|-------:|
| ALWAYS_RAG | 31.1 | **59.4** | 45.8 |
| GASR | 30.1 | 48.2 | 37.1 |
| ALWAYS_GRAPH | 25.9 | 51.9 | 34.5 |
| GAPA | 26.3 | 48.2 | 32.5 |
| SELF_ROUTE | 25.9 | 33.9 | 25.9 |
| PREASSESS | 16.5 | 14.8 | 18.4 |
| ALWAYS_LC | 16.5 | 13.3 | 15.6 |

### What R1 shows (internal)

1. **Always-RAG wins quality** under this 9B setup; **Always-LC collapses** (cap + lost-in-the-middle).
2. **PREASSESS ≈ LC** — gate almost always escalates to LC.
3. **SELF_ROUTE** wastes tokens (~79% of LC) by falling back to LC often, with middling quality.
4. **GASR** cuts tokens vs Self-Route by ~**45%** and scores **higher** (38.5 vs 28.6) — main routing win.
5. **GAPA** is cheap (~16% of LC tokens) but quality trails RAG; mostly GRAPH.

Full table + dims: [`TABLE_B_SWEQA_W1.md`](TABLE_B_SWEQA_W1.md).

---

## 4. Run R2 — Claude judge on flask only (7 arms)

Same R1 answers; Claude-subagent scores on **flask n=48** (blind A–G shuffle). Pearson vs qwen-7B on shared pairs ≈ **0.87**.

| Arm | Claude Overall | qwen-7B Overall (flask) |
|-----|---------------:|------------------------:|
| GASR | **35.8** | 30.1 |
| ALWAYS_RAG | 35.7 | **31.1** |
| ALWAYS_GRAPH | 33.3 | 25.9 |
| GAPA | 33.3 | 26.3 |
| SELF_ROUTE | 32.6 | 25.9 |
| ALWAYS_LC | 27.1 | 16.5 |
| PREASSESS | 27.1 | 16.5 |

**Takeaway:** Ranking is stable (RAG/GASR top, LC/PREASSESS bottom). Claude scores higher in absolute terms, especially on LC. The LC failure is **not** a qwen-7B judge artifact.

Detail (prompts, **paper Table 3**, strength gap, APIs for a fairer match): [`CLAUDE_FLASK_CROSS_JUDGE.md`](CLAUDE_FLASK_CROSS_JUDGE.md) §3.

---

## 5. Run R3 — Cursor CLI agent on flask (paper-scaffold reference)

| Metric | Value |
|--------|------:|
| **Overall /100 (mean)** | **69.5** |
| Correctness / Completeness / Relevance / Clarity / Reasoning | 15.1 / 10.2 / 16.6 / 15.6 / 12.0 |
| Min / Max | 51 / 83 |
| Wall | ~27 min · ~33 s/Q mean |

Smoke (flask-000 only) scored **79** under the same Claude judge before the full 48.

Artifacts: [`agent_cursor/TABLE_B_AGENT_FLASK.md`](agent_cursor/TABLE_B_AGENT_FLASK.md), `agent_cursor/output/full/flask.jsonl`.

---

## 6. Comparison to the SWE-QA paper

### What the paper measured

| Paper setting | Role | Approx Overall /100 |
|---------------|------|---------------------:|
| Direct (no repo context) | Lower bound | ~50–66 (model-dependent) |
| Function-chunk / Sliding RAG | Context helps a lot | e.g. Qwen3-Coder-30B Direct→RAG **~51→63** |
| OpenHands + GPT-5.1 | Strong open agent stack | **~70.8** |
| **Cursor-agent** (their CLI) | Commercial agent reference | **~70.7** |
| Tongyi Lingma | Commercial reference | ~69.1 |
| flask repo (avg across their models) | Easier repo | **~75.4** |

Paper **does not** have an Always-LC / parent-file dump arm. Their baselines are Direct, two RAGs, agents, and commercial tools. Our Always-LC is **thesis-owned**.

Paper judge: **Claude Sonnet 4.5**, five dims /100, **5-vote**.

### Side-by-side (honest)

| Our number | Paper anchor | Fair? |
|------------|--------------|-------|
| Always-RAG **45.4** (9B, Wave 1, qwen-7B judge) | FC-RAG ~**63** (Qwen3-Coder-30B, their judge) | **Direction only** — much smaller model + different judge |
| GASR **38.5** vs Self-Route **28.6**, −45% tokens | Agent > RAG narrative in paper | **Internal** routing claim; not a paper arm |
| Cursor CLI agent flask **69.5** (Claude-subagent judge) | Paper Cursor **~70.7** / flask avg **~75.4** | **Closest scaffold match** — still different CLI version, single- vs 5-vote judge, flask-only vs full bench |
| Pipeline flask Claude means **~27–36** | Paper flask **~75** | **Not comparable generators** — 9B pipeline vs strong agents/LLMs |

### What we can claim

1. **Primary thesis (supported by R1):** Under a fixed 9B generator, **routing matters** — RAG beats LC dump; GASR improves on Self-Route on both quality and tokens; PREASSESS fails by collapsing to LC.
2. **Judge robustness (R2):** Claude-subagent ranking agrees with qwen-7B on flask (r≈0.87).
3. **Paper-scaffold reference (R3):** A modern Cursor CLI agent on the **same flask questions / SHA** lands near the paper’s Cursor overall (~70). Use as an **upper-scaffold anchor**, not proof we matched their exact stack.

### What we must not claim

- Beating paper Cursor / OpenHands absolute /100.
- That Always-RAG 45 ≈ paper RAG 63 (different model/judge).
- That Always-LC is a SWE-QA paper baseline (it isn’t).

---

## 7. Diagnostic highlights (why numbers look like this)

| Observation | Likely cause |
|-------------|--------------|
| Always-LC / PREASSESS near floor | ~47 parent-file chunks · ~17k prompt tokens · **hit 24k inject cap** every Wave 1 LC task; 9B “no info in context” answers |
| SELF_ROUTE weak | Escalates to LC ~75% of the time |
| GAPA ≈ Always-GRAPH | Almost always chooses GRAPH |
| Flask harder than requests for our RAG | Repo/question mix; requests flask Overall 59 vs 31 under qwen-7B |
| Cursor-agent completeness ~10/20 | Answers short vs long gold; still high overall via correctness/relevance |

**Planned fix (parked):** replace Always-LC with **budgeted ranked parent-file LC** (`LC_TOKEN_BUDGET≈8000`), labeled as **our** LC — see `.cursor/scratchpad.md`.

---

## 8. Artifact index

| Path | Contents |
|------|----------|
| [`FREEZE.md`](FREEZE.md) | Models, SHAs, hardware |
| [`TABLE_B_SWEQA_W1.md`](TABLE_B_SWEQA_W1.md) | Main 7-arm Wave 1 table (R1) |
| [`TABLE_B_SWEQA_REST.md`](TABLE_B_SWEQA_REST.md) | Rest 12 repos n=576 (R4) |
| [`TABLE_B_SWEQA_FULL.md`](TABLE_B_SWEQA_FULL.md) | Full SWE-QA n=720 merge (R4) |
| [`agent_cursor/TABLE_B_AGENT_FLASK.md`](agent_cursor/TABLE_B_AGENT_FLASK.md) | Cursor-agent flask (R3) |
| [`agent_cursor/SMOKE_CURSOR_AGENT.md`](agent_cursor/SMOKE_CURSOR_AGENT.md) | Agent smoke + J1 score 79 |
| [`agent_cursor/FULL_RUN_CURSOR_AGENT.md`](agent_cursor/FULL_RUN_CURSOR_AGENT.md) | A3 run meta |
| `eval_harness/data/reports/phase2_sweqa_scores/` | R1 score JSONL |
| `eval_harness/data/reports/phase2_claude_judge_flask/` | R2 Claude flask scores |
| `docs/phase2_runs/context_traces/` | Per-arm context traces (LC vs RAG examples) |
| [`../PHASE2_AGENT_EVAL_RUNBOOK.md`](../PHASE2_AGENT_EVAL_RUNBOOK.md) | CP ladder / paper anchors |

---

## 9. One-slide takeaway

> On SWE-QA Wave 1 with **qwen3.5:9b**, **Always-RAG (~45)** beats graph/gates; **Always-LC (~15)** fails under a 24k parent-dump. **GASR** beats Self-Route on quality and cuts ~45% tokens. A **Cursor CLI agent** on flask scores **~69.5**, near the paper’s Cursor ~70 — a scaffold reference, not a head-to-head with their Claude-4.5 5-vote full-benchmark protocol. Thesis claim = **routing cost–quality**, not beating commercial agents.
