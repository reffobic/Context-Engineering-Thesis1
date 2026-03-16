# Context Engineering Optimization Engine

**Team:** Hana Shalaby, Hoda Hussein, Mohamed El-Refai, Haya Shalaby, Laila Sayed, Farida Elgharabawy  
**Supervisor:** Dr. Nouri, Dr. Dina Mahmoud | **Sponsor:** Valeo (Dr. Ibrahim Sobh)

---

## Overview

This repository implements the **Context Engineering Optimization Engine** described in our literature survey.  The system sits between a user's prompt and an AI agent (GPT / Gemini / Claude) and ensures the agent only receives **relevant, clean context** — not noise.

### Block Diagram

```
User ──Prompt──► [Online Pipeline]
                      │
                  ┌───▼───┐
                  │  (+)  │  ◄── Feeds Preprocessed Data (Offline)
                  └───┬───┘
                      │
                 ┌────▼────┐
                 │Answerable│  ◄── THIS is what we are building
                 │    ?    │
                 └──┬───┬──┘
               Yes  │   │ No
                    │   ▼
                    │  [Retrieval: LongRAG + FastGen]
                    │         │
                    │  [Context Pruning & Injection]
                    │         │
                    └────►  AI Agent (GPT / Gemini / Claude)
```

---

## Task Split

| Task | Owner | Status |
|------|-------|--------|
| Approaches for Answerable? diamond + tests | **Hana, Refai** | ✅ In progress (this branch) |
| Evaluation metrics | **Hana** | ✅ `tests/metrics.py` |
| AGENTS.md | **Haya** | ⬜ Pending |
| Context window overflow (tool output) | **Haya** | ⬜ Pending |
| LongRAG offline processing | **Hoda** | ⬜ Stub in `src/retrieval/longrag.py` |

---

## The Answerable? Diamond — Four Approaches

The core problem: **given a user query and a set of RAG-retrieved chunks, is the query answerable from those chunks alone?**

If YES → send directly to AI Agent (cheap, fast).  
If NO  → escalate to LongRAG + FastGen (expensive, thorough).

We implement and compare **four approaches**:

### Approach 1 — SELF-ROUTE (LLM-as-judge)
> *Li et al. (2024) — "Retrieval Augmented Generation or Long Context LLMs?"*

Prompt the LLM with the retrieved chunks and ask it to declare `ANSWERABLE` or `UNANSWERABLE`.  No fine-tuning required.  Routes >50% of queries to the cheaper RAG pipeline.

**File:** `src/answerable_diamond/approaches.py` → `SelfRouteApproach`

### Approach 2 — Keyword Overlap Heuristic
Fast zero-LLM-call baseline.  Tokenises the query into content words and checks what fraction appear in the retrieved chunks.  Deterministic and interpretable.

**File:** `src/answerable_diamond/approaches.py` → `KeywordOverlapApproach`

### Approach 3 — Semantic Similarity (Embedding-based)
> *Alansari & Luqman (2025) — embedding-based hallucination detection*

Computes cosine similarity between the query embedding and the mean chunk embedding.  Catches semantic paraphrases that keyword overlap misses.

**File:** `src/answerable_diamond/approaches.py` → `SemanticSimilarityApproach`

### Approach 4 — Confidence / Uncertainty Estimation
> *Alansari & Luqman (2025) — uncertainty-based detection*

Asks the LLM to answer AND self-report a confidence score (0–10).  Low confidence → escalate.  Useful as a proxy for token-level probability when model internals are inaccessible.

**File:** `src/answerable_diamond/approaches.py` → `ConfidenceEstimationApproach`

---

## File-level context selection (Dr. Ibrahim's idea)

We implement the **context optimization agent** from Dr. Ibrahim's demo: given a project described by a **file map** (path + one-line purpose per file, no code) and a **user change request**, the system returns the **minimal set of files** an LLM (or developer) needs as context to implement that change.

- **Same idea as the Answerable? diamond**, but at **file granularity**: "Is this change answerable from this small set of files?" → if not, expand context.
- **Ollama-ready**: run with local Ollama (`--ollama`) or use a mock for tests.

**How to run:**

```bash
# Mock LLM (no Ollama)
python scripts/demo_file_selector.py

# Local Ollama (e.g. ollama run llama2)
python scripts/demo_file_selector.py --ollama

# Custom request (e.g. "Use another vector database for the RAG")
python scripts/demo_file_selector.py --request "Use another vector database for the RAG"

# Apache Subversion (team codebase)
python scripts/demo_file_selector.py --project subversion
python scripts/demo_file_selector.py --project subversion --ollama
```

**What’s included:**

- `src/file_context/` — `FileMap`, `FileContextSelector`, `OllamaClient`, `MockFileSelectorClient`
- `tests/fixtures/ai_project_file_map.py` — file map from the shared AI chatbot + RAG project (50 files)
- `tests/fixtures/subversion_file_map.py` — file map for [Apache Subversion](https://github.com/apache/subversion) (39 entries)
- Gold set for *"Enable uploading an image to the custom chat"* so the demo can report recall vs. Claude’s answer

---

## Quick Start

```bash
# Install dependencies (only pytest needed for mock tests)
pip install -r requirements.txt

# Run the Answerable? diamond demo (no API key needed — uses mock LLM)
python tests/test_answerable_diamond.py

# Run file-level context selection demo (mock or --ollama; use --project subversion for Apache Subversion)
python scripts/demo_file_selector.py

# Run full evaluation with metrics table
python tests/evaluate_approaches.py

# Run pytest suite
python -m pytest tests/test_answerable_diamond.py -v
```

---

## Test Corpus — "The Missing Notebook"

We use a short detective story as a stand-in for Valeo's internal documents.  This lets us test all four approaches without needing corporate access.

| ID | Description | Expected |
|----|-------------|----------|
| TC-01 | Direct fact — one chunk | ANSWERABLE |
| TC-02 | Multi-hop — two chunks | ANSWERABLE |
| TC-03 | Answer not in chunks | UNANSWERABLE |
| TC-04 | Irrelevant chunks | UNANSWERABLE |
| TC-05 | Conclusion paragraph | ANSWERABLE |
| TC-06 | Escalation test | UNANSWERABLE → ANSWERABLE after LongRAG |

---

## Evaluation Metrics

Defined in `tests/metrics.py`:

| Metric | What it measures |
|--------|-----------------|
| Routing Accuracy | % of queries correctly classified |
| Precision / Recall / F1 | Standard classification metrics (ANSWERABLE = positive) |
| Escalation Rate | % of queries sent to expensive LongRAG |
| False Escalation Rate | % of answerable queries unnecessarily escalated (wasted cost) |
| Missed Escalation Rate | % of unanswerable queries not escalated (hallucination risk) |
| Average Confidence | Mean confidence score |
| Cost Proxy | Weighted token cost (RAG=1×, LongRAG=10×) |

---

## Project Structure

```
Context-Engineering-Thesis1/
├── src/
│   ├── answerable_diamond/
│   │   ├── approaches.py     ← Four approaches for the diamond
│   │   └── router.py         ← Routing logic (escalation pipeline)
│   ├── file_context/
│   │   ├── file_map.py       ← FileEntry, FileMap (path + purpose)
│   │   └── selector.py       ← FileContextSelector, Ollama + mock clients
│   ├── retrieval/
│   │   └── longrag.py        ← LongRAG retriever stub (Hoda)
│   └── pruning/
│       └── context_pruner.py ← Context pruning stub
├── scripts/
│   └── demo_file_selector.py ← File-level context selection demo
├── tests/
│   ├── fixtures/
│   │   ├── ai_project_file_map.py   ← AI chatbot + RAG file map + gold sets
│   │   └── subversion_file_map.py   ← Apache Subversion file map (team codebase)
│   ├── stories/
│   │   └── story_corpus.py   ← Test cases ("The Missing Notebook")
│   ├── test_answerable_diamond.py
│   ├── evaluate_approaches.py
│   └── metrics.py           ← Evaluation metrics (Hana)
├── requirements.txt
└── README.md
```

---

## Literature References

- Li et al. (2024) — SELF-ROUTE: RAG vs Long Context routing
- Jiang et al. (2024) — LongRAG: large retrieval units
- Ge et al. (2024) — FastGen: adaptive KV cache compression
- Lu et al. (2024) — TurboRAG: offline KV cache pre-computation
- Asai et al. (2023) — SELF-RAG: reflection tokens
- Alansari & Luqman (2025) — Hallucination detection survey
- Liu et al. (2024) — "Lost in the Middle": positional bias
