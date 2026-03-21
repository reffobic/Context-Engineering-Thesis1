# Context Engineering Optimization Engine

**Team:** Hana Shalaby, Hoda Hussein, Mohamed El-Refai, Haya Shalaby, Laila Sayed, Farida Elgharabawy  
**Supervisor:** Dr. Nouri, Dr. Dina Mahmoud | **Sponsor:** Valeo (Dr. Ibrahim Sobh)

---

## Overview

This repository implements the **Context Engineering Optimization Engine** from our literature survey: manage what the model sees (retrieval, pruning, injection) so coding assistants get **relevant, bounded context** instead of noise.

### Target pipeline (`context-engine` branch)

```
Developer → Client (CLI / Web / IDE) → MCP Server (Context Engine)
              ├ retrieve_context
              ├ prune_context
              ├ inject_context
              └ search_codebase
         → Vector DB (code embeddings) → Ollama (local LLM) → generated code
```

Evaluation runs **outside** this loop (golden tasks, baselines, metrics).

---

## Git branches

| Branch | Purpose |
|--------|---------|
| **`context-engine`** | **Active integration branch** — backend layout (MCP + tools + P5 agent). Develop the full engine here. |
| `main` | Stable / default line with the thesis repo history. |
| `approaches+test` | Earlier prototypes: Answerable? diamond, story tests, file-level selector demos. |

Use `git branch -a` to see remotes (`origin/main`, `origin/context-engine`, etc.).

---

## Team map (parallel work)

| Person | Role | Main deliverable | Location (planned / current) |
|--------|------|------------------|------------------------------|
| P1 | MCP server + integration | Working context engine shell | `backend/mcp_server/` (TBD) |
| P2 | Code indexing + embeddings | Vector DB population | `backend/indexing/` (TBD) |
| P3 | Retrieval tool | Semantic search / `retrieve_context` | `backend/retrieval/` (TBD) |
| P4 | Context pruning | Token reduction | `backend/pruning/` (TBD) |
| **P5** | **LLM agent + prompt builder** | **Structured injection + Ollama** | **`backend/injection/`**, **`backend/agent/`** ✅ |
| P6 | Evaluation | Benchmarks + metrics | `evaluation/` (TBD) |

**Person 5 (implemented):**

- `backend/injection/prompt_builder.py` — builds system + user messages with `<CONTEXT>`, `<FILES_AND_CODE>`, optional `<DEPENDENCIES>`, `<TASK>`.
- `backend/agent/ollama_agent.py` — `OllamaAgent.generate_response()`, `generate_with_context()` using Ollama `/api/chat` or `/api/generate`.

Details: [`backend/README_P5.md`](backend/README_P5.md).

**Ollama vs DeepSeek Coder:** Ollama is the runtime. DeepSeek Coder is an optional **model** (`ollama pull …`); no separate DeepSeek install required.

### Run Person 5 smoke test

From repo root:

```bash
export PYTHONPATH=.

# Build prompt only (no HTTP)
OLLAMA_SKIP=1 python backend/agent/ollama_agent.py

# Full round-trip (Ollama running + model pulled)
python backend/agent/ollama_agent.py llama3.2:3b
```

### P5 API for integrators (P1)

```python
from backend.agent import OllamaAgent

agent = OllamaAgent(model="qwen3.5:4b")
answer = agent.generate_with_context(
    user_task="Add logging to the checkout flow.",
    chunks=[
        {"path": "app.py", "text": "def checkout(): pass", "language": "python"},
    ],
)
```

Chunks are `list[dict]` with at least `text`; optional `path`, `symbol`, `language`, `dependencies` (from P3/P4).

---

## Legacy prototypes (`src/` + `tests/`)

These predate the `backend/` engine layout but remain useful for **research and demos**.

### Block diagram (Answerable? diamond)

```
User ──Prompt──► [Online Pipeline]
                      │
                  ┌───▼───┐
                  │  (+)  │  ◄── Feeds Preprocessed Data (Offline)
                  └───┬───┘
                      │
                 ┌────▼────┐
                 │Answerable?│
                 │    ?    │
                 └──┬───┬──┘
               Yes  │   │ No
                    │   ▼
                    │  [Retrieval: LongRAG + FastGen]
                    │         │
                    │  [Context Pruning & Injection]
                    │         │
                    └────►  AI Agent
```

### Task split (earlier checklist)

| Task | Owner | Status |
|------|-------|--------|
| Approaches for Answerable? diamond + tests | **Hana, Refai** | ✅ `src/answerable_diamond/` |
| Evaluation metrics (routing) | **Hana** | ✅ `tests/metrics.py` |
| File-level context selection | **Refai / team** | ✅ `src/file_context/`, `scripts/demo_file_selector.py` |
| AGENTS.md | **Haya** | ⬜ |
| Context window overflow (tool output) | **Haya** | ⬜ |
| LongRAG offline processing | **Hoda** | ⬜ stub `src/retrieval/longrag.py` |

### Four Answerable? approaches

Implemented in `src/answerable_diamond/approaches.py`: SELF-ROUTE, keyword overlap, semantic similarity (mock embedder), confidence estimation. See file docstrings for paper refs (Li et al., Alansari & Luqman, etc.).

### File-level context selection (Dr. Ibrahim’s demo)

Given a **file map** (path + purpose, no code) and a **change request**, the selector returns a minimal file list. Run:

```bash
python scripts/demo_file_selector.py                    # mock / Ollama
python scripts/demo_file_selector.py --project subversion --ollama --model llama3.2:3b
```

Fixtures: `tests/fixtures/ai_project_file_map.py`, `tests/fixtures/subversion_file_map.py` ([Apache Subversion](https://github.com/apache/subversion)).

---

## Quick start

```bash
pip install -r requirements.txt

# --- context-engine (P5) ---
export PYTHONPATH=.
OLLAMA_SKIP=1 python backend/agent/ollama_agent.py

# --- Legacy demos ---
python tests/test_answerable_diamond.py
python scripts/demo_file_selector.py
python tests/evaluate_approaches.py
python -m pytest tests/test_answerable_diamond.py -v
```

---

## Test corpus — “The Missing Notebook”

Story-based cases for the Answerable? diamond (`tests/stories/story_corpus.py`): TC-01 … TC-06 (answerable / unanswerable / escalation).

---

## Evaluation metrics (diamond / routing)

`tests/metrics.py`: routing accuracy, precision / recall / F1, escalation rates, cost proxy.

---

## Project structure

```
Context-Engineering-Thesis1/
├── backend/                      ← context-engine integration
│   ├── README_P5.md              ← Person 5 usage
│   ├── injection/
│   │   └── prompt_builder.py     ← P5: structured prompts
│   └── agent/
│       └── ollama_agent.py       ← P5: Ollama client + generate_with_context
│   ├── mcp_server/               ← P1 (TBD)
│   ├── indexing/                 ← P2 (TBD)
│   ├── retrieval/                ← P3 (TBD)
│   └── pruning/                  ← P4 (TBD)
├── src/                          ← legacy / research prototypes
│   ├── answerable_diamond/
│   ├── file_context/
│   ├── retrieval/
│   └── pruning/
├── scripts/
│   └── demo_file_selector.py
├── tests/
│   ├── fixtures/
│   ├── stories/
│   ├── test_answerable_diamond.py
│   ├── evaluate_approaches.py
│   └── metrics.py
├── evaluation/                   ← P6 (TBD)
├── data/                         ← sample codebase (TBD)
├── requirements.txt
└── README.md
```

---

## Literature references

- Li et al. (2024) — SELF-ROUTE: RAG vs Long Context routing  
- Jiang et al. (2024) — LongRAG: large retrieval units  
- Ge et al. (2024) — FastGen: adaptive KV cache compression  
- Lu et al. (2024) — TurboRAG: offline KV cache pre-computation  
- Asai et al. (2023) — SELF-RAG: reflection tokens  
- Alansari & Luqman (2025) — Hallucination detection survey  
- Liu et al. (2024) — “Lost in the Middle”: positional bias  
