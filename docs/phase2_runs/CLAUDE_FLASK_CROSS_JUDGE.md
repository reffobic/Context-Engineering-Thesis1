# Claude flask cross-judge (7 arms) — what it is, prompts, vs paper, APIs for a fair match

**Stamp:** 2026-11-15  
**Label:** `claude-subagent · single-judge · flask-only · n=48 × 7 arms`  
**Data dir:** [`eval_harness/data/reports/phase2_claude_judge_flask/`](../../eval_harness/data/reports/phase2_claude_judge_flask/)

---

## 1. What `claude_overall_flask.json` means

File: `eval_harness/data/reports/phase2_claude_judge_flask/claude_overall_flask.json`

| Fact | Value |
|------|--------|
| Entries | **336** = **7 arms × 48** flask questions |
| Key format | `"ARM\|task_id"` e.g. `"ALWAYS_RAG\|flask-000"` |
| Value | **Overall /100 only** = sum of five dims (each 1–20) |
| Who generated the answers? | Our pipeline · **`qwen3.5:9b`** (Wave 1 7-arm run) |
| Who scored them? | Cursor **Claude subagent** (`claude-sonnet-5-5-high`) — **judge only** |

Per-dimension scores (correctness, completeness, relevance, clarity, reasoning) are in:

- `scores_batch1.json` … `scores_batch6.json`  
- Blind prompts + A–G mapping: `batch1.txt` … `batch6.txt`, `mapping.json`

### Arm means (Overall /100, flask n=48)

| Arm | Mean Overall | Min | Max |
|-----|-------------:|----:|----:|
| **GASR** | **35.8** | 20 | 82 |
| **ALWAYS_RAG** | **35.7** | 18 | 82 |
| GAPA | 33.3 | 18 | 77 |
| ALWAYS_GRAPH | 33.3 | 18 | 73 |
| SELF_ROUTE | 32.6 | 20 | 80 |
| ALWAYS_LC | 27.1 | 19 | 71 |
| PREASSESS | 27.1 | 19 | 71 |

Agreement with the main Wave 1 judge (`qwen-7B:latest` on the same flask answers): Pearson **≈ 0.87**. Ranking is the same family (RAG/GASR top, LC/PREASSESS bottom).

---

## 2. Types of prompts used (with flask examples)

There are **three layers**: (1) SWE-QA **questions** in the flask benchmark, (2) our **generation** prompts (system + context + question), (3) **judge** prompts in this folder.

### 2A. Flask benchmark questions (SWE-QA taxonomy)

Wave 1 flask has **48** questions, balanced **12 What / 12 How / 12 Why / 12 Where** (paper Level-1 types). Wording is repo-grounded and often long / multi-hop.

| Type | Count | Example (flask) |
|------|------:|-----------------|
| **What** | 12 | *What is the relationship between the ASCII-only encoding control parameter in the framework's JSON serialization provider and Unicode character representation in serialized output?* (`flask-000`) |
| **How** | 12 | *How does the subclass initialization hook in the HTTP method-dispatching view class resolve conflicting methods attributes when inheriting from multiple parent classes?* (`flask-023`) |
| **Why** | 12 | *Why does the class that handles UUID serialization in the tagged JSON serialization system enable bidirectional conversion between UUID instances and their hexadecimal string representations?* |
| **Where** | 12 | *Where does the application root path get transformed during initialization when instance-relative configuration mode is enabled before reaching the configuration loading stage?* |

Other **What** examples (harder / architectural):

- *What architectural decisions are reflected by the dual inheritance from both AssertionError and UnicodeError in the debug-mode exception class…?* (`flask-003`)
- *What is the architectural pattern of the test class that isolates request context lifecycle management from streaming response generation logic?* (`flask-010`)

Gold answers are long, cite paths/lines (e.g. `src/flask/json/provider.py`), and are the **Reference Answer** in the judge prompt.

---

### 2B. Generation prompts (7-arm pipeline → `qwen3.5:9b`)

**Not stored in this folder.** Same system wrapper for all arms; **context block** changes by arm.

**System prompt** (from `backend/injection/prompt_builder.py`):

```text
You are an expert software engineer assisting with code changes.
Rules:
- Use only the information in the CONTEXT sections when discussing the codebase.
- If context is insufficient, state what is missing instead of inventing files or APIs.
- Prefer concise, correct code and explain non-obvious choices briefly.
- Citation discipline: each technical claim must be supported by the code lines shown…
- Do not mention concrete artifacts … unless they appear verbatim in CONTEXT.
- Always finish with complete sentences…
```

**User message shape** (simplified):

```text
=== File: src/flask/json/provider.py ===
--- Snippet (Symbol: DefaultJSONProvider) [lang=python] ---
<retrieved / parent-file code>

=== File: … ===
…

<user question from SWE-QA>
```

| Arm | Context the model saw (flask-000 example) |
|-----|---------------------------------------------|
| ALWAYS_RAG | ~**6** chunks · ~2k prompt tokens · includes `provider.py` |
| ALWAYS_GRAPH | Graph-expanded prune · fewer files |
| ALWAYS_LC | ~**47** chunks / many parent files · ~22k prompt tokens · **hit 24k cap** |
| SELF_ROUTE | RAG or LC after sufficiency check |
| PREASSESS | Almost always same as LC on flask |
| GASR | Ladder; may stay RAG/GRAPH or fall to LC |
| GAPA | Almost always GRAPH |

**Same question, different answers (flask-000 heads):**

*ALWAYS_RAG* (good — context had `ensure_ascii`):
```text
The relationship … is defined by the `ensure_ascii` attribute in the
`DefaultJSONProvider` class. According to the code in src/flask/json/provider.py: …
```

*ALWAYS_LC* (failed — cap / lost-in-the-middle; `provider.py` present but model misses it):
```text
Based on the provided context, I cannot determine the relationship between an
"ASCII-only encoding control parameter" and Unicode character representation…
```

Claude scored those candidates (among all 7) into `claude_overall_flask.json`. Full traces: [`context_traces/flask-000.md`](context_traces/flask-000.md).

---

### 2C. Judge prompts (this experiment) — `batch*.txt`

Copied from our port of SWE-QA `llm-as-a-judge.py` (`scripts/phase2_sweqa_ollama_judge.py`).

**Rubric head (excerpt):**

```text
You are a STRICT and RIGOROUS evaluator. You must rate the candidate answer
STRICTLY against the reference answer. Be CONSERVATIVE with high scores…

Evaluation Criteria … (each scored 1 to 20, total score 100):
  1. Correctness …
  2. Completeness …
  3. Relevance …
  4. Clarity …
  5. Reasoning …
```

**Per-item input shape** (after blinding, one of A–G is e.g. ALWAYS_RAG):

```text
===== ITEM flask-000 =====
Question: What is the relationship between the ASCII-only encoding control
parameter in the framework's JSON serialization provider and Unicode …

Reference Answer:
The `ensure_ascii` attribute on `DefaultJSONProvider` (defaults to `True`)
controls whether non-ASCII characters are escaped as Unicode escape sequences…

--- Candidate A ---
<one arm’s answer>

--- Candidate B ---
…
```

**Required judge output:**

```json
{
  "correctness": 17,
  "completeness": 12,
  "relevance": 19,
  "clarity": 18,
  "reasoning": 13
}
```

Overall = sum → stored under `"ARM|flask-000"` in `claude_overall_flask.json` after un-blinding via `mapping.json`.

**Blinding:** 7 answers shuffled to **A–G** per question. Six batches × 8 questions (`batch1.txt` … `batch6.txt`).

**Not used here:** paper’s **5-vote** majority — this run is **single-pass**.

---

## 3. Paper Table 3 (SWE-QA) — and how to read it vs us

Source: SWE-QA paper Table 3 ([arXiv:2509.14635](https://arxiv.org/abs/2509.14635)). Metrics are **answer quality vs gold** (5 dims × 1–20 → Overall /100), **not** a direct “context quality” score. Better context usually raises the answer score.

### 3A. Paper Table 3 (transcribed)

| Model / system | Corr | Comp | Rel | Clar | Reason | **Overall** |
|----------------|-----:|-----:|----:|-----:|-------:|------------:|
| **Commercial** | | | | | | |
| Tongyi Lingma | 12.02 | 10.99 | 16.78 | 15.67 | 13.61 | **69.07** |
| Cursor | 12.11 | 11.44 | 17.21 | 16.01 | 13.89 | **70.66** |
| **Qwen3-Coder-30B-A3B-Instruct** (Direct) | 7.39 | 5.42 | 14.23 | 14.66 | 9.11 | **50.80** |
| + Function Chunking RAG | 10.88 | 8.72 | 15.61 | 16.24 | 11.95 | **63.40** (+12.60) |
| + Sliding Window RAG | 11.22 | 9.00 | 15.81 | 16.71 | 12.12 | **64.86** (+14.06) |
| + SWE-agent | 9.12 | 8.64 | 14.98 | 12.92 | 11.77 | **57.44** (+6.64) |
| + OpenHands | 11.09 | 10.40 | 15.94 | 15.08 | 13.36 | **65.88** (+15.08) |
| **Qwen3-Coder-480B-A35B-Instruct** (Direct) | 8.14 | 6.09 | 15.00 | 15.50 | 9.88 | **54.61** |
| + Function Chunking RAG | 10.61 | 8.56 | 16.08 | 16.07 | 11.63 | **62.96** (+8.35) |
| + Sliding Window RAG | 11.36 | 9.11 | 16.34 | 16.51 | 12.06 | **65.39** (+10.78) |
| + SWE-agent | 11.09 | 10.59 | 15.49 | 13.76 | 13.06 | **64.00** (+9.39) |
| + OpenHands | 11.89 | 11.20 | 16.21 | 15.37 | 13.90 | **68.57** (+13.96) |
| **Kimi K2** (Direct) | 7.41 | 4.93 | 15.23 | 15.75 | 8.15 | **51.47** |
| + Function Chunking RAG | 9.78 | 7.93 | 16.55 | 15.11 | 10.70 | **60.08** (+8.61) |
| + Sliding Window RAG | 10.44 | 8.55 | 16.48 | 15.65 | 11.32 | **62.44** (+10.97) |
| + SWE-agent | 11.84 | 11.72 | 15.88 | 14.48 | 13.80 | **67.72** (+16.25) |
| + OpenHands | 11.74 | 11.33 | 15.21 | 14.66 | 13.24 | **66.18** (+14.71) |
| **GLM-4.6** (Direct) | 8.71 | 6.09 | 15.79 | 16.53 | 9.53 | **56.64** |
| + Function Chunking RAG | 9.48 | 7.52 | 16.07 | 14.41 | 9.99 | **57.46** (+0.82) |
| + Sliding Window RAG | 9.02 | 7.85 | 16.33 | 16.08 | 10.44 | **60.72** (+3.08) |
| + SWE-agent | 12.31 | 12.37 | 16.15 | 14.81 | 14.21 | **69.85** (+13.21) |
| + OpenHands | 11.91 | 12.70 | 16.90 | 13.82 | 14.82 | **70.15** (+13.51) |
| **Gemini 2.5 Pro** (Direct) | 7.84 | 5.59 | 16.28 | 15.83 | 9.18 | **54.71** |
| + Function Chunking RAG | 9.91 | 7.53 | 16.79 | 15.32 | 10.47 | **60.02** (+5.31) |
| + Sliding Window RAG | 11.52 | 8.83 | 16.85 | 16.66 | 11.82 | **65.68** (+10.97) |
| + SWE-agent | 10.62 | 10.11 | 15.96 | 13.81 | 13.26 | **63.76** (+9.05) |
| + OpenHands | 10.75 | 9.64 | 16.78 | 15.20 | 13.27 | **65.63** (+10.92) |
| **GPT-5.1** (Direct) | 9.62 | 7.37 | 16.23 | 16.86 | 11.33 | **61.41** |
| + Function Chunking RAG | 11.38 | 9.50 | 17.12 | 16.15 | 12.43 | **66.57** (+5.16) |
| + Sliding Window RAG | 11.45 | 9.44 | 17.01 | 16.28 | 12.61 | **66.79** (+5.38) |
| + SWE-agent | 12.94 | 11.44 | 14.80 | 16.34 | 13.68 | **69.20** (+7.79) |
| + OpenHands | 12.26 | 11.60 | 16.82 | 15.59 | 14.52 | **70.79** (+9.38) |

Paper takeaway: RAG ≫ Direct; agents often ≥ RAG; GPT-5.1+OpenHands and Cursor lead (~70–71).

### 3B. How much stronger is the paper’s stack than ours?

| Dimension | Paper (typical) | Ours | Gap |
|-----------|-----------------|------|-----|
| **Generator size** | 30B-A3B MoE, 480B-A35B MoE, GPT-5.1, Gemini, … | **`qwen3.5:9b`** | **Much stronger** — order-of-magnitude class jump |
| **Agent stack** | OpenHands / SWE-agent / Cursor commercial | Our **retrieval arms** (not a coding agent) | Different paradigm |
| **Judge** | Claude Sonnet 4.5 · **5-vote** | qwen-7B or Claude-subagent · **1-vote** | Protocol mismatch |
| **Local & free?** | Big open models: local if you have VRAM; commercial/API: paid | 9B fits RTX 4080 easily | 30B/480B on 16 GB = hard / slow / may not fit |

Rough **Overall** bands (same /100 scale, **not** same setup):

| Band | Who | Overall |
|------|-----|--------:|
| Strong agent / commercial | Paper Cursor, OpenHands+big models | **~66–71** |
| Strong model + RAG | Paper Qwen-Coder 30B/480B + Sliding/Func RAG | **~63–65** |
| Strong model Direct | Paper Direct rows | **~51–61** |
| **Our Cursor CLI agent (flask)** | `agent` `auto` · Claude-subagent judge · n=48 | **69.5** |
| **Our best 7-arm (Wave 1)** | Always-RAG · qwen-7B judge · n=144 | **45.4** |
| **Our best 7-arm (flask, Claude judge)** | GASR / RAG · this file | **~35.8** |
| Our Always-LC (flask, Claude) | Full parent dump + 9B | **~27** |

So vs paper **Qwen3-Coder-30B Direct 50.8 → RAG ~64**: our 9B Always-RAG **45** is **below** their Direct, and **~18–20 points** below their RAG — consistent with a much smaller generator.  
Vs paper **Cursor 70.7**: our **Cursor-agent flask 69.5** is **in the same ballpark** (scaffold reference). Our **7-arm ~36–45** is **not** competing with Cursor/OpenHands absolute scores.

### 3C. Ways to compare fairly (what to put in the thesis)

| Comparison type | What to align | What you can claim |
|-----------------|---------------|-------------------|
| **A. Directional (safe)** | Same story: context helps | “Like the paper, adding retrieval/routing beats naive full-dump LC; RAG helps over weak context.” |
| **B. Within-ours (primary)** | Fixed `qwen3.5:9b` + same judge | Always-RAG > GASR > GRAPH > Self-Route > LC; GASR saves tokens vs Self-Route — **main thesis** |
| **C. Scaffold vs paper Cursor** | Same questions/SHA; agent explores repo | Our Cursor CLI flask **69.5** ≈ paper Cursor **70.7** — **label** different CLI version + single- vs 5-vote judge |
| **D. Absolute vs paper RAG/agents** | Need similar **generator size** + paper **judge** | Only after Anthropic (or matching) judge and a 30B+/API generator — **not** with current 9B table alone |
| **E. Dim pattern** | Completeness often lowest | Paper and our agent run both show **completeness** as a weak dim vs relevance/clarity |

**Do compare:** relative gains (+RAG, +agent) and our arm rankings.  
**Do not compare:** “Always-RAG 45 vs Sliding RAG 64” as proof we underperform their method on equal footing.

### 3D. Is our result “good” given agent / model strength?

**Yes, for what we set out to measure — with the right framing.**

1. **7-arm / 9B run (Table B + this Claude flask file)**  
   - Goal = **routing cost–quality**, not matching GPT-5.1.  
   - Always-RAG **45** and flask Claude **~36** look “low” next to Table 3’s **63–70**, but those rows use **much stronger models**.  
   - Internally the result is **good**: clear ranking, LC collapse explained, GASR beats Self-Route on tokens **and** quality.  
   - Against paper Qwen-30B Direct (**50.8**), a 9B model at **45** Overall is not embarrassing; against their RAG **64** you should expect a large gap until you scale the generator.

2. **Cursor-agent flask (69.5)**  
   - Goal = paper-style **agent scaffold** reference.  
   - **Good:** within ~1 point of paper Cursor **70.66** Overall, with dims in a similar shape (relevance/clarity high, completeness weaker).  
   - Caveats: flask-only, single-pass Claude-subagent judge, newer CLI — still a strong sanity check that our eval loop + questions behave like the paper’s top commercial band when the **agent is strong**.

3. **Bottom line for the write-up**  
   - Table 3 shows **ceiling** with big models/agents (~70).  
   - Our **9B arms** show **routing matters** under a weak generator (~15–45).  
   - Our **Cursor-agent** shows we can reach the **same ceiling band** when the generator/agent is strong (~69.5).  
   - That pair of facts is coherent — not a failure of the 7-arm study.

### 3E. Protocol match vs this Claude cross-judge file

| Piece | Paper | This cross-judge / our runs |
|-------|-------|------------------------------|
| Task / scale / dims | SWE-QA · /100 · 5 dims | Same family |
| Generator | Table 3 models/agents | **9B arms** or separate **Cursor agent** |
| Judge | Claude Sonnet 4.5 · 5-vote | qwen-7B or Claude-subagent · 1-vote |
| Scope | Full benchmark (Table 3) | Wave 1 144 or flask 48 |

---

---

## 4. APIs / services needed for a **fairer** paper comparison

To get closer to the paper’s evaluation protocol (not just “Claude-ish”), you need:

### A. Fair **judge** (highest priority for score protocol)

| Need | Why | What to get |
|------|-----|-------------|
| **Anthropic API** (`ANTHROPIC_API_KEY`) | Paper judge = **Claude Sonnet 4.5** via real API | Call Sonnet 4.5 (or closest available Sonnet) with the **same** SWE-QA judge prompt |
| **5-vote majority** | Paper averages/votes 5 times per item | Loop judge 5×; majority (or median) per dim |
| Optional: Azure OpenAI | Upstream `llm-as-a-judge.py` is Azure-shaped | Only if you mirror their **script** path, not the paper’s Claude text |

**Cursor API / Cursor subagents alone are not enough** for a paper-faithful judge: Cursor’s API is **not** a chat-completions drop-in, and subagents are not reproducible Anthropic Sonnet 4.5 · 5-vote.

### B. Fair **generator** (if you want absolute Overall near paper)

| Need | Why | What to get |
|------|-----|-------------|
| Strong LLM API (**Anthropic** and/or **OpenAI**) | Paper generators are large models / agents, not 9B local | Replace `ollama_generate` with Claude/GPT **after** our arm builds context — keeps 7-arm meaning |
| Or Cursor **CLI agent** (`CURSOR_API_KEY` + `agent`) | Paper Cursor baseline | Separate **agent** row (already done on flask) — **not** a substitute for arm-conditioned generation |
| **Voyage** (optional) | Paper’s own RAG baselines use Voyage embeddings | Only if re-running **their** Func/Sliding RAG scripts |

**Not a fair substitute for Claude API as judge/generator:** Cursor Admin/Cloud Agents API as if it were OpenAI Chat Completions.

### C. Minimal checklist for “paper-fair” next step

1. **`ANTHROPIC_API_KEY`** — re-judge flask (or Wave 1) with Sonnet, preferably **5-vote**.  
2. Keep generator fixed (`qwen3.5:9b`) first → isolate **judge** effect vs this Claude-subagent file.  
3. Later (optional): same arms + **Claude/GPT generator** → isolate **model** effect.  
4. Keep Cursor-agent flask (**69.5**) as the **commercial-agent scaffold** anchor next to paper Cursor ~70.7.  
5. Label every table: `judge=… · votes=… · generator=… · n=… · repo=…`.

---

## 5. Artifact map

| Path | Role |
|------|------|
| `claude_overall_flask.json` | Overall /100 per `ARM\|task_id` (this summary’s table) |
| `scores_batch*.json` | Five dims per item (still A–G until mapped) |
| `mapping.json` | A–G → arm per task |
| `batch*.txt` | Full judge prompts (rubric + Q + gold + 7 candidates) |
| [`TABLE_B_SWEQA_W1.md`](TABLE_B_SWEQA_W1.md) | Main Wave 1 table (qwen-7B judge, n=144) |
| [`PHASE2_RUNS_SUMMARY.md`](PHASE2_RUNS_SUMMARY.md) | All runs overview |
| [`agent_cursor/TABLE_B_AGENT_FLASK.md`](agent_cursor/TABLE_B_AGENT_FLASK.md) | Cursor-agent flask (not 7-arm) |

---

## 6. One-paragraph takeaway

`claude_overall_flask.json` stores **Claude-subagent overall scores** for our **qwen3.5:9b** 7-arm answers on **48 flask** questions (336 cells). Paper **Table 3** puts strong models/agents at ~**51–71** Overall; our 9B arms at ~**27–45** are weaker in absolute terms (expected) but **good for routing claims**. Our Cursor-agent flask **69.5** sits next to paper Cursor **70.7** as a scaffold check. Metrics always score the **answer vs gold**, not context directly. Fair absolute comparison needs a stronger generator and preferably Anthropic Sonnet · 5-vote judging — not Cursor chat-API as a Claude substitute.
