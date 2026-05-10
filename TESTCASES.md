# Manual test cases — Context Engineering pipeline

**Purpose:** Single place to **define** manual tests against the running system, **record** commands and outcomes, and **review** pass/fail for thesis or release notes.

**System under test (SUT):** FastAPI app in `backend/mcp_server` — pipeline  
`retrieve_context` → `prune_context` → `inject_context` → `ollama_generate`  
plus `GET /`, `POST /index`, and scripted `backend/smoke_test.py`.

**Recorded run (default):** Ollama `qwen3.5:9b`, Chroma `backend/chroma_db`, **Apache Superset** checkout indexed (see TC-10). Update the **summary table** and **detail** subsections when you re-run tests.

---

## 1. How to use this document

| Action      | What to do                                                                                                               |
| ----------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Execute** | Start the server (§2), run the command for each **TC-ID** (§4).                                                          |
| **Record**  | Add or edit a row in §5 (summary). For **`/evaluate`** runs, also fill **§5.1** (vector / hybrid / graph). Paste commands, HTTP status, body, and logs under §6 (detailed evidence per TC). |
| **Verdict** | Use **Pass**, **Partial**, or **Fail** (§7). **Partial** = acceptable with documented caveats.                        |

**Per-run capture (minimum):** exact command, HTTP status, response body (or error JSON), Ollama model name (`ollama list`), optional wall time.

---

## 2. Preconditions

1. **Python 3.10+** venv with backend dependencies (`backend/indexing/requirements.txt` stack).
2. **Ollama** running; model pulled (e.g. `qwen3.5:9b`).
3. **ChromaDB** populated with **Superset**: `POST /index?repo_path=/absolute/path/to/superset` (or your checkout path).
4. **Server** (for `curl` tests):

```bash
cd /path/to/Context-Engineering-Thesis1/backend/mcp_server
source ../../.venv/bin/activate   # adjust if venv lives elsewhere
PYTHONPATH=.. uvicorn server:app --host 127.0.0.1 --port 8000
```

**Optional:** `OLLAMA_NUM_PREDICT` (default `8192` in code) if answers truncate.

**Evaluation API:** With the MCP server branch that exposes `POST /evaluate`, use identical JSON bodies (prompt, `top_k`, `token_budget`, optional `relevant_files`, judge flags) for each retrieval mode, then transcribe scalars into **§5.1**. *Hybrid* matches the default `retrieve_context` pipeline today; *vector* and *graph* require separate retrieval backends when implemented.

---

## 2.1 Designing prompts against a Superset index

Use **concrete filenames and symbols** from your checkout so retrieval can anchor (e.g. `superset/app.py`, `create_app`, `superset/mcp_service/middleware.py`, `superset-core/src/superset_core/...`). For harder cases: ask for **CONTEXT-only** answers, multi-section formats, tables, or explicit **NOT IN CONTEXT** discipline. Natural-language variants are listed in **§4.1**.

---

## 3. Test specifications (matrix)

| TC-ID     | Name                                            | Objective                                      | Preconditions              | Pass criteria                                                                                                                                 |
| --------- | ----------------------------------------------- | ---------------------------------------------- | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **TC-01** | Health                                          | API process reachable                          | Server up                  | `GET /` → **200**; JSON includes service message (e.g. `message`).                                                                            |
| **TC-02** | Minimal generate                                | End-to-end `/generate`                         | Ollama + Chroma            | **200**; `output` non-empty; logs show retrieve → prune → inject → generate without traceback.                                                |
| **TC-03** | RAG — Superset symbol                           | Answers use indexed Superset code              | Superset index             | **200**; cites plausible Superset paths; notes if implementation absent from context.                                                         |
| **TC-04** | RAG — fake symbol                               | No invented API                                | Same index                 | **200**; states symbol not in context; no fake full implementation.                                                                          |
| **TC-05** | Citation discipline                             | Claims tied to CONTEXT                         | Same index                 | **200**; separates shown vs not shown; minimal wrong attribution.                                                                              |
| **TC-06** | Long-form output                                | Token budget / structure                       | Same index                 | **200**; sections complete or clean ending; verify risky details vs source if **Partial**.                                                    |
| **TC-07** | Corpus focus (two Superset areas)               | No cross-topic hallucination                   | Same index                 | **200** each; Topic A stays grounded in retrieved Superset snippets; Topic B likewise; minimal invented files.                               |
| **TC-08** | Empty prompt                                    | Edge input                                     | —                          | **200** or clear **4xx**; not **500**.                                                                                                        |
| **TC-09** | Invalid JSON                                    | Client validation                              | —                          | **422** (or clear **4xx**); no server crash.                                                                                                  |
| **TC-10** | Index API                                       | Batch indexing                                 | `repo_path` exists         | **200**; JSON stats; incremental re-run may show `files_indexed: 0`.                                                                          |
| **TC-11** | Smoke script                                    | Automated Tier A/B                             | venv + deps                | Tier A **PASS**; Tier B runs; note **200** vs **500** for `/generate`.                                                                        |
| **TC-12** | Superset onboarding flow                        | Trace pipeline + concrete files/functions      | Superset indexed           | **200**; names concrete Superset files/functions; honest **NOT IN CONTEXT** when missing.                                                    |
| **TC-13** | `create_app` deep trace                         | App factory + config/env/middleware            | Same                       | **200**; file references grounded in CONTEXT.                                                                                                  |
| **TC-14** | Middleware extraction context                   | MCP middleware grounding                       | Same                       | **200**; `_extract_context_info` behavior accurate; no invented retrieval internals for that module.                                          |
| **TC-15** | Serializer summary behavior                     | Tool-search serializer vs tests                | Same                       | **200**; matches tests where visible; separates fact from assumption.                                                                        |
| **TC-16** | Supported vs unsupported claims (Superset)      | Explicit NOT IN CONTEXT                        | Same                       | **200**; supported vs unsupported lists; no fabricated classes/functions.                                                                       |
| **TC-17** | Multi-topic Superset comparison               | Stress under broad query                       | Same                       | **200**; scope boundaries; minimal cross-topic drift.                                                                                         |
| **TC-18** | Source-first `Query` vs `SavedQuery`            | Model defs over docs/tests                     | Same (post-retrieval work) | **200**; expects `superset-core/src/superset_core/queries/models.py` when indexed; honest if absent.                                           |
| **TC-19** | Source-first dashboard/chart/dataset models     | Core model file retrieval                       | Same                       | **200**; points to defining files in CONTEXT or **NOT IN CONTEXT**.                                                                           |
| **TC-20** | Source-first `superset/app.py` config behavior  | Config path + precedence                        | Same                       | **200**; `superset/app.py` in evidence when indexed; precedence/fallback from context.                                                         |

---

## 4. Execution commands (copy-paste)

Use a **full URL** and a complete `-d '{...}'` JSON string. If `curl` prompts for POST body and hangs, `-d` is incomplete — Ctrl+C and fix the line.

### TC-01 — `GET /`

```bash
curl -sS http://127.0.0.1:8000/
```

### TC-02 — minimal `POST /generate`

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Say hello in one short sentence."}'
```

### TC-03 — Superset / RAG (real symbol)

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"How does create_app work in this Superset codebase? Name files and functions you rely on."}'
```

### TC-04 — fake API name

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Explain the function totally_fake_superset_api_xyz_999 in this tree."}'
```

### TC-05 — citation discipline

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Answer using only CONTEXT. For each claim, name the file and quote or say not shown. How is the Superset app factory create_app wired to config loading?"}'
```

### TC-06 — long-form (four sections)

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Answer in 4 numbered sections: (1) purpose of create_app (2) typical initialization (3) errors or edge cases visible in CONTEXT (4) what is not in CONTEXT. Topic: Superset application startup."}'
```

### TC-07 — two Superset prompts (run A and B)

**A — App factory**

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Summarize create_app and where SUPERSET_CONFIG is read in Superset."}'
```

**B — MCP / tooling**

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Where does Superset extract MCP request context (middleware) and which fields are populated?"}'
```

### TC-08 — empty prompt

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":""}'
```

### TC-09 — invalid JSON

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{bad json}'
```

### TC-10 — `POST /index` (set your path)

```bash
curl -sS -X POST "http://127.0.0.1:8000/index?repo_path=/absolute/path/to/superset"
```

### TC-11 — smoke test (no HTTP client)

From repository root (directory that contains `backend/`):

```bash
cd /path/to/Context-Engineering-Thesis1
source .venv/bin/activate
python backend/smoke_test.py
```

### TC-12 — Superset onboarding flow (pipeline-focused)

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"I am onboarding to Superset. Trace exactly how a user prompt flows through your context-engineering pipeline from API entry to final model response. Name concrete files/functions involved in indexing assumptions, retrieval scoring, pruning decisions, prompt injection, and generation. End with a short architecture summary. Use only CONTEXT and say NOT IN CONTEXT if a stage file is missing."}'
```

### TC-13 — Superset `create_app` deep trace

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Explain what create_app does in Superset and how it connects to config loading, env overrides (SUPERSET_CONFIG / SUPERSET_APP_ROOT), middleware/path handling, CLI app factory usage, and test fixtures. Use section headings and file/function references only from CONTEXT."}'
```

### TC-14 — Superset MCP middleware context extraction

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"In superset/mcp_service/middleware.py, explain _extract_context_info precisely: input sources (message, metadata), fallback order, extracted fields (agent_id, user_id, dashboard_id, slice_id, dataset_id), and one likely failure mode. Use only CONTEXT and cite file/function names."}'
```

### TC-15 — Superset serializer summary behavior

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Based only on CONTEXT, summarize how Superset tool search serializer summary mode works: include_schemas defaults, description truncation behavior, and parameters_hint generation. Cross-check against the relevant unit tests and separate guaranteed behavior from assumptions."}'
```

### TC-16 — Superset supported vs unsupported discipline

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Two sections only. (A) Three claims about Superset MCP/RAG pipeline that are supported by CONTEXT with file/function evidence. (B) Three claims that are NOT IN CONTEXT. Do not invent functions, classes, or scores."}'
```

### TC-17 — Superset multi-topic comparison stress

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Compare in one answer: create_app initialization flow, config/env overrides, middleware/path handling, REST API decorator routing, and MCP serializer summary mode tests in Superset. Use markdown table + concise notes per row. If evidence is missing in CONTEXT, write NOT IN CONTEXT in that row."}'
```

### TC-18 — Superset source-first `Query` vs `SavedQuery`

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Source-first check: What is the difference between Query and SavedQuery models in Superset? Prefer model definition files over tests/docs. The expected source file is superset-core/src/superset_core/queries/models.py. If that file is not in CONTEXT, start with NOT IN CONTEXT: superset-core/src/superset_core/queries/models.py, then summarize only what is visible."}'
```

### TC-19 — Superset source-first dashboard/chart/dataset definitions

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Source-first check: Where are dashboards, charts, and datasets defined in Superset? Prefer core source model files over docs/tests. Expected source includes superset-core/src/superset_core/common/models.py. If that file is not in CONTEXT, say NOT IN CONTEXT for it and list the retrieved files that are visible."}'
```

### TC-20 — Superset source-first `superset/app.py` exact path

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Source-first check: Compare SUPERSET_CONFIG vs SUPERSET_APP_ROOT in superset/app.py: precedence order, fallback behavior, and side effects. Expected path: superset/app.py. If superset/app.py is not in CONTEXT, start with NOT IN CONTEXT: superset/app.py."}'
```

## 4.1 Natural user prompts (Superset)

Use these as realistic, non-research user prompts. Keep wording natural.

| Prompt ID | Natural prompt (copy/paste)                                                       | Expected result                                                                            | Pass check                                                     |
| --------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| N-01      | `Where does Superset app startup begin in this codebase?`                         | Mentions `superset/app.py` `create_app` (or says NOT IN CONTEXT if absent).                | Names at least one concrete startup file/function.             |
| N-02      | `What does create_app do in Superset, in simple terms?`                           | High-level summary: app creation, config load, app root handling, middleware side effects. | Clear plain-English explanation with file/function evidence.   |
| N-03      | `How is Superset app created from the CLI?`                                       | Mentions `superset/cli/main.py` calling `superset.app.create_app`.                         | Includes CLI file + factory call path.                         |
| N-04      | `If I set SUPERSET_CONFIG and SUPERSET_APP_ROOT, what changes?`                   | Explains config module selection and app root path behavior/middleware impact.             | Correct precedence + side effects, or explicit NOT IN CONTEXT. |
| N-05      | `How do tests create an app instance in Superset?`                                | Mentions `self.create_app().app_context()` patterns in integration/unit tests.             | Includes at least one test file call site.                     |
| N-06      | `Where is middleware context extracted for MCP requests?`                         | Points to `superset/mcp_service/middleware.py` and `_extract_context_info` behavior.       | Lists extracted IDs/fields or says unavailable in context.     |
| N-07      | `How are Superset API routes registered in core code?`                            | Mentions API decorator flow in `superset-core/src/superset_core/rest_api/decorators.py`.   | Names decorator/API files and route role.                      |
| N-08      | `What is serializer summary mode in MCP tool search?`                             | References serializer helpers/tests; describes truncation and `parameters_hint` behavior.  | Matches tests if available; avoids unsupported claims.         |
| N-09      | `What is the difference between Query and SavedQuery models?`                     | Uses `superset-core/src/superset_core/queries/models.py` distinction and key fields.       | Correct model-level comparison from context.                   |
| N-10      | `Where are dashboards, charts, and datasets defined?`                             | Points to `superset-core/src/superset_core/common/models.py` (and possibly DAOs).          | Names model file(s) and entities accurately.                   |
| N-11      | `How are background tasks defined in Superset core?`                              | Mentions task decorators/models/DAOs in `superset-core/src/superset_core/tasks/`.          | Identifies decorator + model roles.                            |
| N-12      | `I’m new — what 5 files should I read first to understand Superset backend flow?` | Gives practical ordered list with one-line reason each.                                    | File list is concrete and grounded in retrieved context.       |

### Natural prompt scoring notes

- **Pass:** grounded answer with concrete file/function references, no fabricated APIs/classes.
- **Partial:** useful answer but misses concrete file names or mixes in weak inferences.
- **Fail:** invents unsupported details, ignores explicit context limits, or contradicts retrieved snippets.

---

## 5. Execution record — summary table

*Update dates and verdicts when you re-run.*

| TC-ID | Date | Model / tool | HTTP | Verdict | Summary |
| ----- | ---- | ------------ | ---- | ------- | ------- |
| TC-01 | — | — | — | Pending | |
| TC-02 | — | — | — | Pending | |
| TC-03 | — | — | — | Pending | |
| TC-04 | — | — | — | Pending | |
| TC-05 | — | — | — | Pending | |
| TC-06 | — | — | — | Pending | |
| TC-07 | — | — | — | Pending | |
| TC-08 | — | — | — | Pending | |
| TC-09 | — | — | — | Pending | |
| TC-10 | — | — | — | Pending | |
| TC-11 | — | — | — | Pending | |
| TC-12 | — | — | — | Pending | Superset onboarding / pipeline trace |
| TC-13 | — | — | — | Pending | `create_app` deep trace |
| TC-14 | — | — | — | Pending | MCP middleware extraction |
| TC-15 | — | — | — | Pending | Serializer summary vs tests |
| TC-16 | — | — | — | Pending | Supported vs unsupported discipline |
| TC-17 | — | — | — | Pending | Multi-topic comparison stress |
| TC-18 | — | — | — | Pending | Source-first Query vs SavedQuery |
| TC-19 | — | — | — | Pending | Source-first dashboard/chart/dataset |
| TC-20 | — | — | — | Pending | Source-first `superset/app.py` |

---

## 5.1 Final evaluation results — vector × hybrid × graph

Aggregate **`POST /evaluate`** outputs here for side-by-side comparison. Use the **same** prompt, gold paths (`relevant_files`), `top_k`, `token_budget`, and optional flags (`judge_retrieval_relevancy`, `faithfulness`) for each column; only the **retrieval backend** should differ once *vector*-only and *graph* modes exist in code.

**Column guide**

| Column   | Intended backend |
| -------- | ---------------- |
| **Vector** | Dense / embedding-only retrieval (when exposed). |
| **Hybrid** | Current default hybrid `retrieve_context` (dense + lexical / ranking stack). |
| **Graph**  | Graph-augmented or graph-first retrieval (*reserved — fill when wired*). |

Copy numbers or short strings from the JSON; for nested blocks (e.g. long path lists), summarize (e.g. `2 paths`) or point to a saved `evaluate-*.json` file.

### Results table (fill per experiment)

| Metric / field (`POST /evaluate`) | Vector | Hybrid | Graph |
| --------------------------------- | ------ | ------ | ----- |
| **Run metadata** — date | | | |
| **Run metadata** — git commit / branch | | | |
| **Run metadata** — TC-ID or prompt label | | | |
| **`top_k`** | | | |
| **`token_budget`** | | | |
| **`relevant_files`** (gold count; list if few) | | | |
| **`judge_retrieval_relevancy`** / **`faithfulness`** (on/off) | | | |
| **Judge model** (`EVAL_JUDGE_MODEL` or default) | | | |
| `pipeline_counts.retrieved_chunks` | | | |
| `pipeline_counts.pruned_chunks` | | | |
| `structural.retrieved_top_mean_rank_score` | | | |
| `structural.retrieved_unique_paths_in_top_k` | | | |
| `structural.pruned_chunk_count` | | | |
| `structural.pruned_unique_paths` | | | |
| `structural.retrieved_top_paths` (summary or first files) | | | |
| `structural.pruned_paths` (summary or first files) | | | |
| `precision_at_k_retrieval.precision_at_k` | | | |
| `precision_at_k_retrieval.recall_at_k` | | | |
| `precision_at_k_retrieval.relevant_chunks_in_top_depth` | | | |
| `precision_at_k_pruned.precision_at_k` | | | |
| `precision_at_k_pruned.recall_at_k` | | | |
| `precision_at_k_pruned.relevant_chunks_in_top_depth` | | | |
| `contextual_relevancy_retrieval.score` | | | |
| `contextual_relevancy_retrieval.success` | | | |
| `contextual_relevancy_retrieval.error` (if any) | | | |
| `generation_preview` (first line / char count) | | | |
| `faithfulness.score` | | | |
| `faithfulness.success` | | | |
| `faithfulness.error` (if any) | | | |
| **Reviewer notes** (variance, failures, index hash) | | | |

*Rows with no gold labels omit P/R fields; rows with judges off omit relevancy/faithfulness. Delete unused rows or mark N/A in all three columns.*

---

## 6. Detailed results — evidence log (per test case)

For each TC: **command** (exact), **HTTP status**, **response** (full JSON or abridged), **server log** (representative lines), **reviewer verdict / notes**.  
When you re-run a test, **replace or append** dated sub-bullets so this file stays the authoritative record.

### TC-01 — Health check

- **Command:** `curl -sS http://127.0.0.1:8000/`
- **Pass criteria:** `200`; JSON includes e.g. `message` and `ui`.

### TC-02 — Minimal `POST /generate`

- **Command:** See §4 TC-02.
- **Notes:** Confirms full pipeline completes without Python traceback. Retrieval still runs for a trivial prompt.

### TC-03 through TC-20 — Superset RAG and stress

- Add dated evidence here after each run (HTTP status, abridged `output`, pipeline stage notes, reviewer verdict).

### TC-08 — Empty prompt

- **Pass criteria:** `200` or clear `4xx`, not `500`.

### TC-09 — Invalid JSON

- **Pass criteria:** `422` (or clear `4xx`); no crash.

### TC-10 — `POST /index`

- **Command:** See §4; set `repo_path` to your Superset checkout.
- **Notes:** `files_indexed: 0` on repeat runs usually means incremental mode and unchanged files. For a full rebuild, reset `backend/chroma_db` / hash cache per indexer docs.

### TC-11 — `backend/smoke_test.py`

- **Command:** See §4.
- **Notes:** Tier B uses in-process `TestClient`, not necessarily your live `:8000` socket. **200** vs **500** on `/generate` depends on Ollama + index state.

---

## 7. Verdict definitions and rubric

| Verdict     | Meaning                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------- |
| **Pass**    | Meets pass criteria; safe for the documented use.                                          |
| **Partial** | Usable with caveats (e.g. verify model citations against source).                            |
| **Fail**    | Wrong status, crash, or violates pass criteria.                                            |
| **Mixed**   | Multiple runs on the same TC disagree; document each run under §6.                         |

**Reviewer checklist:** truncation, hallucination, wrong repository/corpus, citation mismatch, unexpected **500**.

---

## 8. Related automation

- `backend/smoke_test.py` — Tier A (no Chroma/Ollama for A) and Tier B (full app import + TestClient). Overlaps **TC-11**; does not replace live **curl** checks.
- **`POST /evaluate`** (when enabled in `server.py`) — structural + optional gold P/R@k, contextual relevancy, faithfulness; tabulate cross-mode results in **§5.1**.
- **Unit tests:** e.g. `pytest tests/backend/test_pruning.py` — not covered by this manual sheet.
- Increase `OLLAMA_NUM_PREDICT` if long table answers truncate mid-response.
