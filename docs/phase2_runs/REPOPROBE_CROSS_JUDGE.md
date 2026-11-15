# RepoProbe Python-8 cross-judge — metrics, paper Table 3, our 7 arms, vs SWE-QA

**Stamp:** 2026-11-15  
**Label:** `local-judge · single-judge · scorer-on-pipeline · Python-8`  
**Paper:** RepoProbe ([arXiv:2608.04783](https://arxiv.org/abs/2608.04783))  
**Compact scores:** [`TABLE_C_REPOPROBE_PY8.md`](TABLE_C_REPOPROBE_PY8.md)  
**Artifacts:** `eval_harness/data/reports/phase2_repoprobe_py/full/`  
**Smoke (n=5):** [`SMOKE_CP9.md`](SMOKE_CP9.md)  
**SWE-QA twin doc:** [`CLAUDE_FLASK_CROSS_JUDGE.md`](CLAUDE_FLASK_CROSS_JUDGE.md)

---

## 1. What this run is

| Fact | Value |
|------|--------|
| Questions | RepoProbe **Python-8** · **108** Q (8 repos) |
| Answers | Our **7 arms** · generator **`qwen3.5:9b`** · host pipeline |
| Judge | Local **`qwen-7B:latest`** · RepoProbe `scorer.py` checklist · **1** iteration |
| Paper judge (for comparison) | **Claude Sonnet 4.5** (fixed; excluded from evaluated models) |
| Paper generators | Frontier APIs in **Claude Code** agent scaffolding |
| Scored cells | **744** (≤108/arm; gen errors + 3 JSON-parse fails) |
| Wall | ~**20 min** |

This is the RepoProbe analogue of the SWE-QA “paper Table 3 + our rows” write-up — **same thesis question** (routing cost–quality under a fixed small generator), **different** benchmark and score protocol.

---

## 2. Their tasks (what a question looks like)

RepoProbe items are **GitHub discussion / issue–style** open-ended Q&A about a **pinned repo snapshot** — not SWE-QA’s balanced What/How/Why/Where quiz format.

| Source | Path |
|--------|------|
| Upstream CSV | `../external/RepoProbe/dataset/<repo>.csv` (`question`, `answer`, `checklist`, …) |
| Our Phase 2 copy | [`eval_harness/data/datasets/repoprobe-py8/labels.jsonl`](../../eval_harness/data/datasets/repoprobe-py8/labels.jsonl) (**108** Q) |

**Python-8 mix:** FieldStation42 14 · adk-python 30 · agent-framework 9 · browser-use 5 · crawl4ai 18 · docling 10 · sglang 11 · yasb 11.  
**Taxonomy:** Implementation Details **74** · Business Logic **23** · Project Architecture **11** · all **Medium**.

Each task = **question** (user prose) + **gold answer** + **tiered checklist** (judge rubric).

### Examples (from `labels.jsonl`)

**Implementation — `browser-use__browser-use-0`**
```text
Browser Use: Unable to Use Azure OpenAI Despite Valid Credentials
… help me to fix issue why i am unable to connect with Azure open ai …
Checklist starts: (3 points) Problem Root Cause Identification
```

**Implementation — `FieldStation42__FieldStation42-0`**
```text
ffmpeg filters. CRT Scanlines?
… add filters to stations to simulate CRT scanlines? …
Checklist starts: (3 points) Identifying the station_fx Configuration Option
```

**Business logic — `FieldStation42__FieldStation42-3`**
```text
Scheduling longer content on 'normal' channels?
… proper approach to scheduling longer content? …
```

**Architecture — `FieldStation42__FieldStation42-11`**
```text
Catalog … My question is about the catalog. I already have my files sorted…
```

**Other repos (same shape):** MCP background tasks (`adk-python-0`) · customize chat client (`agent-framework-0`) · RegexExtractionStrategy empty (`crawl4ai-0`) · JS-rendered HTML (`docling-0`) · mxfp4 on MI300X (`sglang-0`) · weather location env (`yasb-0`).

### Where their prompts live (upstream)

| What | Path |
|------|------|
| **Judge template** | `../external/RepoProbe/prompt_templates_en.py` — `DIRECT_SCORING_WITH_REPO_TEMPLATE` |
| **Judge call** | `../external/RepoProbe/scorer.py` — `create_scoring_prompt` |
| **Agent user wrapper** | `../external/RepoProbe/evaluator.py` — `_create_agent_prompt` |
| **Agent system + bash tool** | `../external/RepoProbe/agent_configs/example_agent.py` — `SYSTEM_PROMPT` |
| **Container contract** | `../external/RepoProbe/docs/AGENT_INTEGRATION.md` |

**Agent user prompt (paper harness):**
```text
Please answer the following question in English based on the current repository.
No need to interact with me in the middle, just output the complete answer directly:

<question from CSV>
```

**Judge prompt shape** (filled with Q + gold + checklist + candidate answer + optional directory tree):
```text
You are a professional code understanding evaluation expert…
## Question Information / Real Repository Information / Model Answer
## Reference Answer / Scoring Criteria  ← checklist from CSV
… only integer scores from criteria; knowledge ≤9, clarity ≤1 …
… reason BEFORE scores; JSON: knowledge_score, clarity_score, total_score, …
```

**Our CP9:** same **judge template** via `scripts/phase2_repoprobe_score_pipeline.py`; **generation** uses our RAG/GRAPH/LC inject prompts — not their Claude Code / example-agent tool loop.

---

## 3. Metrics explained

| Metric | Definition (paper) | Our column |
|--------|-------------------|------------|
| **/10 total** | Knowledge (≤9) + Clarity (≤1) from checklist | `/10 mean` |
| **Overall%** | mean(total / 10) × 100 | **Overall%** |
| **Knowledge%** | mean(knowledge / 9) × 100 | **Knowledge%** (= K_mean/9×100) |
| **Clarity%** | mean(clarity) with clarity ∈ {0,1} in paper | **Clarity mean** (raw; see caveat) |
| **PerfectSolve%** | fraction with total = 10/10 | **PerfectSolve%** |
| **tokens mean** | (ours) answer prompt+completion proxy from harness | not in paper Table 3 |

**Not the same as SWE-QA:** SWE-QA uses five dims 1–20 → **/100**. RepoProbe uses checklist **/10 → %**. Do not line up “56%” next to “45/100” as one scale.

**Judge caveat:** Local `qwen-7B` sometimes emits totals **>10** or clarity **>1** (protocol drift). Paper Sonnet is discrete-rubric constrained. Treat absolute % as **soft**; ranking among our arms is the main claim.

**Scaffolding caveat:** Paper rows = **agent tools** (navigate/search). Ours = **retrieval arms** (RAG / GRAPH / LC / gates). Not head-to-head with GPT-5.2 / Opus.

---

## 4. Paper Table 3 (RepoProbe) — transcribed

Source: RepoProbe paper Table 3. All systems judged by **Claude Sonnet 4.5** checklist protocol. Values are **%**.

| Category | Model | Overall% | Knowledge% | Clarity% | PerfectSolve% |
|----------|-------|--------:|-----------:|---------:|--------------:|
| **Closed-weight** | **GPT-5.2** | **62.7** | 60.3 | 84.7 | 26.0 |
| | GPT-5.4 | 60.4 | 57.6 | 85.6 | 24.2 |
| | Claude Opus 4.5 | 58.1 | 56.2 | 75.1 | 23.1 |
| | Claude Opus 4.6 | 62.1 | 60.2 | 79.0 | 27.5 |
| | Claude Sonnet 4.6 | 60.2 | 58.3 | 78.2 | 27.2 |
| | Grok 4.2 | 56.8 | 54.8 | 75.0 | 23.4 |
| | Gemini 3 Flash | 55.4 | 53.6 | 71.6 | 21.8 |
| | Gemini 3 Pro | 51.3 | 49.3 | 71.2 | 17.6 |
| | Gemini 3.1 Pro | 54.1 | 52.2 | 70.9 | 21.2 |
| | **Qwen3-Max** | **27.3** | 26.1 | 39.3 | 5.6 |
| | **Qwen3.5-Plus** | **50.0** | 48.1 | 66.9 | 17.8 |
| | Doubao-Seed-1.8 | 43.5 | 42.4 | 55.1 | 12.8 |
| | Doubao-Seed-2.0 | 46.8 | 45.1 | 62.2 | 14.6 |
| **Open-weight** | GLM-4.7 | 50.7 | 49.3 | 64.8 | 18.4 |
| | GLM-5 | 54.8 | 52.9 | 72.2 | 21.3 |
| | DeepSeek-V3.2 | 52.9 | 51.3 | 68.7 | 18.8 |
| | Kimi-K2 | 45.9 | 44.1 | 61.3 | 15.6 |
| | Kimi-K2.5 | 53.7 | 51.8 | 71.1 | 19.9 |
| | MiniMax-M2.1 | 45.0 | 43.6 | 57.8 | 12.8 |
| | MiniMax-M2.5 | 44.6 | 42.8 | 61.2 | 14.5 |

Paper signatures: **Clarity ≫ Knowledge**; perfect solves stay moderate even for frontier; progress across generations is not always monotonic.

---

## 5. Our Python-8 results (same columns + tokens)

**Ours — 7 arms · `qwen3.5:9b` · judge `qwen-7B` · scorer-on-pipeline · Python-8**

Tokens = mean(`answer_prompt` + `answer_completion`) on ok rows. Knowledge% = knowledge_mean/9×100.

| Arm | Overall% | Knowledge% | Clarity mean† | PerfectSolve% | tokens mean | Route mix (n=108 jobs) |
|-----|--------:|-----------:|--------------:|--------------:|------------:|------------------------|
| **GASR** | **56.1** | 43.1 | 1.71 | 20.0 | 9831 | 10% RAG / 40% GRAPH / 50% LC |
| GAPA | 53.0 | 40.1 | 1.62 | 15.4 | 2782 | 3% RAG / 89% GRAPH / 8% LC |
| SELF_ROUTE | 52.6 | 41.6 | 1.68 | 19.2 | 15243 | 10% RAG / 0% GRAPH / 90% LC |
| PREASSESS | 52.0 | 36.0 | 1.74 | 18.5 | 17043 | 3% RAG / 0% GRAPH / 97% LC |
| ALWAYS_LC | 48.9 | 37.8 | 1.52 | 17.6 | 17443 | 0% RAG / 0% GRAPH / 100% LC |
| ALWAYS_GRAPH | 41.9 | 32.4 | 1.29 | 12.1 | 1989 | 0% RAG / 100% GRAPH / 0% LC |
| ALWAYS_RAG | 40.3 | 29.9 | 1.30 | 10.2 | 2088 | 100% RAG / 0% GRAPH / 0% LC |

† Paper clarity is 0/1; our local judge often exceeds 1 — use Overall%/Knowledge% more than Clarity for paper-style reading.

**n scored:** GASR 105 · GAPA 104 · SELF_ROUTE 104 · PREASSESS 108 · ALWAYS_LC 108 · ALWAYS_GRAPH 107 · ALWAYS_RAG 108.

### 5A. Is their Qwen3 stronger than ours?

**Yes — as models.** Not a fair absolute score match.

| System | Model class | Scaffold | Judge | Overall% |
|--------|-------------|----------|-------|--------:|
| Paper **Qwen3-Max** | Strong API Qwen3 | Claude Code agent | Sonnet 4.5 | **27.3** |
| Paper **Qwen3.5-Plus** | Stronger Qwen3.5 API | Claude Code agent | Sonnet 4.5 | **50.0** |
| **Ours (best arm)** | Local **`qwen3.5:9b`** | 7 retrieval arms | `qwen-7B` | GASR **56.1** |

- **Max / Plus ≫ 9B** in capability. Same vendor family only.  
- Our **56% ≰ “beat Plus.”** Different judge + scaffolding. Soft family anchor only.  
- Paper Max at **27.3** is low on *their* ladder; our 9B sitting near Plus-band % under a weak local judge is **not** evidence we match Plus.

### 5B. Why GASR > Always-RAG here (≠ SWE-QA)

On **SWE-QA Wave 1**, Always-RAG won because GASR’s GRAPH/LC escalations hurt a gold-match /100 judge.

On **RepoProbe Python-8** (this checklist judge):

| | ALWAYS_RAG | GASR |
|--|----------:|-----:|
| Overall% | 40.3 | **56.1** |
| Tokens | ~2.1k | ~9.8k |
| Routes | 100% RAG | Often **GRAPH/LC** (~90% of jobs) |

**Reading (hypothesis + evidence, not proven causation):**

1. Checklist items often need **broader evidence** (issue-style / multi-file). Pure short RAG misses bullets → low Knowledge%.  
2. GASR’s escalations to GRAPH/LC **raise** checklist coverage here more often than they destroy it (opposite of SWE-QA lost-in-the-middle pattern on flask gold answers).  
3. Always-LC alone is only mid (48.9) — so “more context” helps **some** questions but is not free; **gated** escalation (GASR) beats always-LC and always-RAG on this judge.  
4. Local judge noise + scores >10 mean **re-judge with Sonnet** before strong claims.

**Thesis framing:** Suite-dependent routing. SWE-QA → prefer focused RAG under 9B. RepoProbe checklist → gated expand (GASR) can win under the same generator. Primary claim stays **routing cost–quality**, not “GASR always best.”

---

## 6. How to compare fairly (thesis)

| Comparison | Align | Claim |
|------------|-------|-------|
| **A. Directional** | Clarity vs knowledge; context helps | Same paper signature family if Clarity stays high |
| **B. Within-ours** | Fixed 9B + fixed local judge | GASR tops this suite; RAG cheapest but lowest Overall% |
| **C. Soft vs paper Qwen** | Family name only | 9B ≪ Max/Plus; do not claim beat Plus |
| **D. Absolute vs Table 3** | Need Sonnet 4.5 judge + preferably agent scaffold | **Not** with current Table C alone |
| **E. vs SWE-QA** | Different metric + questions | Rankings can diverge; both valid for their suite |

**Do compare:** arm rankings and tokens under one freeze.  
**Do not compare:** “GASR 56 vs GPT-5.2 62.7” or “GASR 56 vs Qwen3.5-Plus 50” as equal-footing wins.

---

## 7. APIs for a fairer paper match

| Need | Why |
|------|-----|
| **Anthropic API** · Claude Sonnet 4.5 | Paper checklist judge |
| Optional multi-iteration scoring | Paper-style stability (their harness supports `num_iterations`) |
| Strong generator API **or** Claude Code–like agent | Absolute Overall near Table 3 |
| Keep 7-arm host answers | Isolate **judge** effect first (re-score same `results.jsonl`) |

Cursor-subagent Claude is closer than `qwen-7B` but still **not** paper Sonnet · agent Docker.

---

## 8. Artifact map

| Path | Role |
|------|------|
| [`labels.jsonl`](../../eval_harness/data/datasets/repoprobe-py8/labels.jsonl) | Task text + checklist + gold |
| `../external/RepoProbe/dataset/*.csv` | Upstream tasks |
| `../external/RepoProbe/prompt_templates_en.py` | Judge prompt template |
| `phase2_repoprobe_py/full/*_score.jsonl` | Per-question checklist scores |
| `phase2_repoprobe_py/full/summary.json` | Arm aggregates |
| [`TABLE_C_REPOPROBE_PY8.md`](TABLE_C_REPOPROBE_PY8.md) | Compact Table C |
| [`SMOKE_CP9.md`](SMOKE_CP9.md) | browser-use n=5 wiring check |
| `runs/a2cfcec/p2-repoprobe-*-py8/` | Generations |
| [`CLAUDE_FLASK_CROSS_JUDGE.md`](CLAUDE_FLASK_CROSS_JUDGE.md) | SWE-QA twin ( /100 · paper SWE-QA Table 3) |

---

## 9. One-paragraph takeaway

RepoProbe **Table 3** puts frontier agent+API systems around **~45–63%** Overall (checklist), with **Qwen3-Max 27.3** and **Qwen3.5-Plus 50.0** under Sonnet 4.5. Our **9B** 7-arm host run under local **`qwen-7B`** lands **GASR ~56%** best and **Always-RAG ~40%** worst — **suite-opposite** to SWE-QA Wave 1, where Always-RAG led and LC collapsed. That is expected: different questions, checklist vs 5-dim gold, weak local judge, and retrieval vs paper agents. Use this doc for **routing + soft anchors**; use Anthropic Sonnet (+ optional agent) before any absolute claim against paper Table 3.
