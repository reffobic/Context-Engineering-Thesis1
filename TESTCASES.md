# Manual test cases — Context Engineering pipeline

**Purpose:** Single place to **define** manual tests against the running system, **record** commands and outcomes, and **review** pass/fail for thesis or release notes.

**System under test (SUT):** FastAPI app in `backend/mcp_server` — pipeline  
`retrieve_context` → `prune_context` → `inject_context` → `ollama_generate`  
plus **`GET /`**, **`POST /index`**, and scripted **`backend/smoke_test.py`**.

**Recorded run (default):** Ollama **`qwen3.5:9b`**, Chroma `backend/chroma_db`, Subversion checkout indexed (see TC-10 path). Update the **summary table** and **detail** subsections when you re-run tests.

---

## 1. How to use this document

| Action | What to do |
|--------|------------|
| **Execute** | Start the server (§2), run the command for each **TC-ID** (§4). |
| **Record** | Add or edit a row in §5 (summary). Paste commands, HTTP status, body, and logs under §6 (detailed evidence per TC). |
| **Verdict** | Use **Pass**, **Partial**, or **Fail** (§7). **Partial** = acceptable with documented caveats. |

**Per-run capture (minimum):** exact command, HTTP status, response body (or error JSON), Ollama model name (`ollama list`), optional wall time.

---

## 2. Preconditions

1. **Python 3.10+** venv with backend dependencies (`backend/indexing/requirements.txt` stack).
2. **Ollama** running; model pulled (e.g. `qwen3.5:9b`).
3. **ChromaDB** populated for RAG tests (e.g. index Subversion via `POST /index` or prior indexing).
4. **Server** (for `curl` tests):

```bash
cd /path/to/Context-Engineering-Thesis1/backend/mcp_server
source ../../.venv/bin/activate   # adjust if venv lives elsewhere
PYTHONPATH=.. uvicorn server:app --host 127.0.0.1 --port 8000
```

**Optional:** `OLLAMA_NUM_PREDICT` (default `8192` in code) if answers truncate.

**Advanced runs:** **§2.1** explains how to design **hard** Subversion-based cases; **TC-12–TC-20** apply them.

---

## 2.1 Designing hard pipeline tests using the Subversion index

The pipeline is stressed when retrieval must surface the **right** mix of files (tests vs headers vs lib), pruning must fit the **token budget**, and the model must stay **faithful** to injected snippets. Use your **indexed** Apache Subversion tree as the ground truth.

### Where to find anchors in a Subversion checkout

| Source | Use for |
|--------|---------|
| `subversion/include/svn_*.h` | Public API names, typedefs (`svn_fs_t`, `svn_txn_t`, `svn_delta_editor_t`) |
| `subversion/tests/**` | Call patterns, expected errors, comments that explain *why* a test fails |
| `subversion/libsvn_fs*/**` | Implementation-heavy questions (may or may not land in top‑k chunks) |

**Quick discovery (run on your Subversion clone, not required for curl):**

```bash
# APIs mentioning transactions / commit
rg -n "svn_fs_commit_txn|svn_fs_begin_txn" /path/to/subversion --glob '*.{c,h}'

# Error codes asserted in FS tests
rg -n "SVN_ERR_[A-Z_]+" /path/to/subversion/subversion/tests/libsvn_fs --glob '*.c' | head -40
```

Turn hits into **prompts**: single-symbol recall → multi-step sequence → compare/contrast → vague paraphrase → “what is **not** in context”.

### Difficulty ladder (what each level stresses)

| Level | Prompt style | What fails first if the system is weak |
|-------|----------------|----------------------------------------|
| **L1** | Name the symbol (`svn_fs_commit_txn`) | Missing `$contains` / vector recall for rare names |
| **L2** | Order of operations (“begin txn → edit → commit”) | Single-file bias; missing second file in top‑k |
| **L3** | Error attribution (“why `SVN_ERR_SQLITE_BUSY`?”) | Wrong chunk; model invents causes |
| **L4** | No API names (“pending changes → durable revision”) | Pure semantic retrieval; may retrieve unrelated code |
| **L5** | Explicit negation (“three things **not** in CONTEXT”) | Hallucination under pressure |
| **L6** | Many symbols in one prompt (`svn_delta_editor_t`, …) | Pruning drops files; table format errors |
| **L7** | Verbatim extract with **documented** gold string (**TC-20**) | Paraphrase sold as verbatim; plausible wrong quote |

### How to record results for hard tests

1. **Save** full `output` (or hash + file path) — long answers matter for the thesis.  
2. **Spot-check** 2–3 factual claims against `rg` / editor on the real tree.  
3. Mark **Partial** if structure is good but citations drift; **Fail** if HTTP errors or fabricated files.

**Retrieval variance (seen on recorded runs):** Questions that mention **SQLite** or **errors** but not **`svn_fs_*`** can surface **`libsvn_wc/wc_db.c`** chunks instead of **`libsvn_fs`** tests—see **TC-13** vs **TC-14**. For FS-specific evidence, **anchor** the prompt with symbols or paths (`svn_fs_commit_txn`, `fs-test.c`, `rep-cache`) so hybrid / `$contains` queries pull the right neighborhood.

**Ground-truth pin (hallucination check):** **TC-20** documents an **expected verbatim substring** from the indexed tree (Apache Subversion `fs-test.c`). After a run, compare the model output to that string: a **wrong** confident quote = likely **hallucination**; **NOT IN CONTEXT** when the chunk truly was not injected = **honest miss**.

---

## 3. Test specifications (matrix)

| TC-ID | Name | Objective | Preconditions | Pass criteria |
|-------|------|-------------|---------------|----------------|
| **TC-01** | Health | API process reachable | Server up | `GET /` → **200**; JSON includes service message (e.g. `message`). |
| **TC-02** | Minimal generate | End-to-end `/generate` | Ollama + Chroma | **200**; `output` non-empty; logs show retrieve → prune → inject → generate without traceback. |
| **TC-03** | RAG — real symbol | Answers use indexed code | Index contains target repo | **200**; cites plausible paths; notes if implementation absent from context. |
| **TC-04** | RAG — fake symbol | No invented API | Same index | **200**; states symbol not in context; no fake full implementation. |
| **TC-05** | Citation discipline | Claims tied to CONTEXT | Same index | **200**; separates shown vs not shown; minimal wrong-symbol attribution. |
| **TC-06** | Long-form output | Token budget / structure | — | **200**; sections complete or clean ending; verify risky details vs source if **Partial**. |
| **TC-07** | Corpus selection | No wrong-repo hallucination | Mixed index (e.g. Flask + SVN) | SVN question uses SVN context; Flask question does not invent Flask if not retrieved. |
| **TC-08** | Empty prompt | Edge input | — | **200** or clear **4xx**; not **500**. |
| **TC-09** | Invalid JSON | Client validation | — | **422** (or clear **4xx**); no server crash. |
| **TC-10** | Index API | Batch indexing | `repo_path` exists | **200**; JSON stats; incremental re-run may show `files_indexed: 0`. |
| **TC-11** | Smoke script | Automated Tier A/B | venv + deps | Tier A **PASS**; Tier B runs; note **200** vs **500** for `/generate`. |
| **TC-12** | FS txn lifecycle (multi-step) | Retrieval spans multiple `svn_fs_*` calls + file IDs | Subversion indexed | **200**; ordered steps match **test/header** evidence or says what’s missing. |
| **TC-13** | Error pathology (`SQLITE_BUSY`) | Precise test-condition recall | Same | **200**; ties busy error to **lock / rep-cache** story in CONTEXT or admits gap. |
| **TC-14** | Cross-subsystem (WC vs FS) | Corpus disambiguation | Same | **200**; describes WC symbols **only if** in context; otherwise explicit **not shown**. |
| **TC-15** | Vague query (no symbol names) | Semantic retrieval only | Same | **200**; honest use of CONTEXT; minimal invented API names. |
| **TC-16** | Supported vs unsupported claims | Negation + faithfulness | Same | **200**; lists **unsupported** claims explicitly; supported ones tied to snippets. |
| **TC-17** | Multi-symbol stress table | Pruning + many targets | Same | **200**; table format; **NOT IN CONTEXT** per symbol where appropriate. |
| **TC-18** | Debug `PATH_SYNTAX` | Locate specific failing input | Same | **200**; path / scenario from `fs-test.c` or admits not retrieved. |
| **TC-19** | Delta / editor role | Harder API (`svn_delta_editor_t`) | Same | **200**; uses header/test snippets if retrieved; **Partial** OK if thin context. |
| **TC-20** | Verbatim quote (known answer) | Detect hallucination vs CONTEXT | Same | **200**; output contains **ground-truth substring** (§4 TC-20) **or** explicit **NOT IN CONTEXT**; **Fail** if a different “verbatim” quote is invented. |

---

## 4. Execution commands (copy-paste)

Use a **full URL** and a complete **`-d '…json…'`** string. If `curl` prompts for POST body and hangs, `-d` is incomplete — Ctrl+C and fix the line.

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

### TC-03 — Subversion / RAG

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"how does svn_fs_commit_txn work?"}'
```

### TC-04 — fake API name

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Explain function totally_fake_svn_api_xyz_123 in this tree."}'
```

### TC-05 — citation discipline

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Answer using only CONTEXT. For each claim, name the file and quote the line or say not shown. How does svn_fs_commit_txn work?"}'
```

### TC-06 — long-form (four sections)

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Answer in 4 numbered sections: (1) purpose (2) typical usage (3) errors shown in context (4) what is not in context. Topic: svn_fs_commit_txn."}'
```

### TC-07 — mixed corpus (run A and B)

**A — Subversion**

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"What does svn_fs_commit_txn do?"}'
```

**B — Flask / other**

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"How does Flask handle the home route in this codebase?"}'
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
curl -sS -X POST "http://127.0.0.1:8000/index?repo_path=/absolute/path/to/checkout"
```

### TC-11 — smoke test (no HTTP client)

From repository root (directory that contains `backend/`):

```bash
cd /path/to/Context-Engineering-Thesis1
source .venv/bin/activate
python backend/smoke_test.py
```

### TC-12 — FS transaction lifecycle (multi-step, CONTEXT-only)

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Using only CONTEXT: describe the typical sequence from creating a Subversion filesystem transaction to committing it, as shown in the tests. List svn_fs_* function names in order and name the file path(s) where each step appears. If a step is not visible in CONTEXT, say NOT SHOWN."}'
```

### TC-13 — `SVN_ERR_SQLITE_BUSY` / `rep-cache.db` (precise cause)

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Using only CONTEXT: why does the test expect SVN_ERR_SQLITE_BUSY when calling svn_fs_commit_txn? What lock or resource is involved (e.g. rep-cache.db)? Quote or paraphrase the test comment that explains it and give the file path."}'
```

### TC-14 — Working copy vs filesystem layer

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Using only CONTEXT: does the retrieved code discuss svn_wc__db_* (working copy database) APIs? If yes, summarize one function and its file. If no WC code appears, say NO WC CONTEXT and summarize what layer (e.g. libsvn_fs tests) you do see."}'
```

### TC-15 — Semantic query without API names

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"In plain English only: how do pending tree changes in a Subversion repository become a new permanent revision, based strictly on what the CONTEXT snippets show? Do not name svn_fs_* symbols unless they appear in the CONTEXT text."}'
```

### TC-16 — Supported vs unsupported claims

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Two sections. (A) List exactly three specific technical claims about svn_fs_commit_txn that you CANNOT justify from CONTEXT (say NOT IN CONTEXT for each). (B) List three claims you CAN justify with file path + short quote from CONTEXT."}'
```

### TC-17 — Multi-symbol stress (table)

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Markdown table with columns: Symbol | In CONTEXT? (yes/no) | One-sentence evidence or NOT IN CONTEXT. Rows for: svn_fs_commit_txn, svn_repos_fs_begin_txn2, svn_delta_editor_t, svn_ra_open4, svn_wc__db_read_info. Use only CONTEXT."}'
```

### TC-18 — Debug `SVN_ERR_FS_PATH_SYNTAX`

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Using only CONTEXT: in subversion/tests/libsvn_fs/fs-test.c (or nearby test code), what invalid path or input triggers SVN_ERR_FS_PATH_SYNTAX around a transaction commit? Give the bad path string if it appears in CONTEXT."}'
```

### TC-19 — Delta editor / driver role

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Using only CONTEXT: what is svn_delta_editor_t and how is it used in the Subversion code you see—driver vs editor, apply_txn, or similar? If CONTEXT only shows a declaration or one call site, say so. If absent, say NOT IN CONTEXT."}'
```

### TC-20 — Verbatim comment (ground truth / hallucination probe)

**Reviewer ground truth** (Apache Subversion `subversion/tests/libsvn_fs/fs-test.c`, `test-repo-commit-with-locked-rep-cache` / `SVN_ERR_SQLITE_BUSY` test): the C comment immediately before `err = svn_fs_commit_txn(...)` followed by `SVN_TEST_ASSERT_ERROR(err, SVN_ERR_SQLITE_BUSY)` must contain this text (whitespace / `*` margin may vary):

> `Attempt to commit fs transaction. This should result in a commit post-processing error due to us still holding the shared lock on the rep-cache.db.`

If your indexed checkout **differs** (older release), run `rg -n "Attempt to commit fs transaction" subversion/tests/libsvn_fs/fs-test.c` on **your** tree and paste the authoritative substring into your notes before scoring.

```bash
curl -sS http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Using only CONTEXT: In subversion/tests/libsvn_fs/fs-test.c, quote VERBATIM the entire C comment block that appears immediately before the line that calls svn_fs_commit_txn and is immediately followed (after that commit call) by SVN_TEST_ASSERT_ERROR(err, SVN_ERR_SQLITE_BUSY). If that exact comment is not present in CONTEXT, reply with only: NOT IN CONTEXT."}'
```

---

## 5. Execution record — summary table

_Update dates and verdicts when you re-run._

| TC-ID | Date | Model / tool | HTTP | Verdict | Summary |
|-------|------|--------------|------|---------|---------|
| TC-01 | 2026-04-01 | — | 200 | Pass | Health OK; JSON `message` field. |
| TC-02 | 2026-04-01 | qwen3.5:9b | 200 | Pass | `output`: greeting; pipeline logged. |
| TC-03 | 2026-04-01 | qwen3.5:9b | 200 | Pass (caveats) | Grounded in `fs-test.c`; verify line refs for thesis. |
| TC-04 | 2026-04-01 | qwen3.5:9b | 200 | Pass | Fake symbol rejected; no fabricated API. |
| TC-05 | 2026-04-01 | qwen3.5:9b | 200 | Pass | CONTEXT-only; admits missing signature/impl. |
| TC-06 | 2026-04-01 | qwen3.5:9b | 200 | Partial | Four sections; verify error codes/lines in source. |
| TC-07 | 2026-04-01 | qwen3.5:9b | 200 | Pass | SVN vs Flask prompts behave; no Flask hallucination. |
| TC-08 | 2026-04-01 | qwen3.5:9b | 200 | Pass | Empty prompt → safe reply, no 500. |
| TC-09 | 2026-04-01 | — | 422 | Pass | `json_invalid` detail from FastAPI. |
| TC-10 | 2026-04-01 | — | 200 | Pass | Incremental re-index: 0 files re-embedded. |
| TC-11 | 2026-04-01 | qwen3.5:9b | — | Pass | Tier A + B PASS; `/generate` 200 in TestClient. |
| TC-12 | 2026-04-01 | qwen3.5:9b | 200 | Pass (caveats) | Ordered FS txn steps + `fs-test.c`; verify line numbers vs file. |
| TC-13 | 2026-04-01 | qwen3.5:9b | 200 | Partial | CONTEXT was `wc_db.c` only—model honestly said busy/rep-cache **not** in context (retrieval miss). |
| TC-14 | 2026-04-01 | qwen3.5:9b | 200 | Pass | WC in context → correct `svn_wc__db_*` summary from `wc_db.c`. |
| TC-15 | 2026-04-01 | qwen3.5:9b | 200 | Partial | Plain English; no `svn_fs_*`; merge narrative—verify vs actual snippets. |
| TC-16 | 2026-04-01 | qwen3.5:9b | 200 | Pass (caveats) | (A)+(B) structured; `fs-test.c` quotes incl. rep-cache; verify line/symbol labels. |
| TC-17 | 2026-04-01 | qwen3.5:9b | 200 | Partial | Table OK; **`svn_fs_commit_txn` = no** conflicts with TC-16 same evening—check retrieval/pruning or model error. |
| TC-18 | 2026-04-01 | qwen3.5:9b | 200 | Partial | No `fs-test.c` in CONTEXT; PATH_SYNTAX conditions from `fs-loader`/path validation—honest + partial answer. |
| TC-19 | 2026-04-01 | qwen3.5:9b | 200 | Pass (caveats) | `svn_delta_editor_t *` in `deprecated.c` wrappers; driver/apply_txn admitted NOT IN CONTEXT. |
| TC-20 | 2026-04-01 | qwen3.5:9b | 200 | Mixed | **A: Fail** (wrong rep-cache “read transaction” block). **B–D:** `NOT IN CONTEXT`. **E: Pass** (gold “Attempt to commit fs transaction…” block). High run-to-run variance. |

---

## 6. Detailed results — evidence log (per test case)

For each TC below: **command** (exact), **HTTP status**, **response** (full JSON or abridged with “[…]”), **server log** (representative lines), **reviewer verdict / notes**.  
**TC-12–TC-20** have **recorded** detail below.  
When you re-run a test, **replace or append** dated sub-bullets so this file stays the authoritative record.

---

### TC-01 — Health check

- **Verdict (recorded):** Pass  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/
  ```
- **HTTP status:** `200 OK`  
- **Response (body):** JSON with a running message, e.g. `{"message":"Context Engine Running"}` (earlier buggy `set` literal in code produced a JSON array; fixed to a proper object).  
- **Server log (representative):**
  - `[INFO] root: Context Engine is running`
  - `INFO: … "GET / HTTP/1.1" 200 OK`
- **Notes:** Does not invoke Ollama or Chroma; only confirms Uvicorn + app import.

---

### TC-02 — Minimal `POST /generate`

- **Verdict (recorded):** Pass  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Say hello in one short sentence."}'
  ```
- **HTTP status:** `200 OK`  
- **Response (body):** `{"output":"Hello."}` (exact wording may vary per model run).  
- **Server log (representative):**
  - `[INFO] root: Pruning started`
  - `[INFO] root: Injection started`
  - `[INFO] root: Generating response`
  - `INFO: … "POST /generate HTTP/1.1" 200 OK`
- **Notes:** Confirms full pipeline (retrieve → prune → inject → Ollama) completes without Python traceback. Retrieval still runs even for a trivial prompt.

---

### TC-03 — RAG / indexed codebase (`svn_fs_commit_txn`)

- **Verdict (recorded):** Pass (caveats)  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"how does svn_fs_commit_txn work?"}'
  ```
- **HTTP status:** `200 OK`  
- **Response (abridged — recorded run):** Long answer grounded mainly in **`subversion/tests/libsvn_fs/fs-test.c`**: test usage of `svn_fs_commit_txn` (e.g. `SVN_ERR(svn_fs_commit_txn(NULL, &new_rev, txn, pool))`), conflict / `new_rev` behavior discussed in comments, `SVN_ERR_SQLITE_BUSY` scenario with **`rep-cache.db`** shared lock, version skip / `SVN_ERR_TEST_SKIPPED` for old server, path syntax tests (`SVN_ERR_FS_PATH_SYNTAX`), revision assertions (`SVN_TEST_INT_ASSERT`), transaction re-open patterns, etc.  
- **Server log (representative):**
  - `[INFO] root: Retrieval started`
  - `Batches: 100%|…|` (sentence-transformers encode for query)
  - `[INFO] root: Pruning started` → `Injection started` → `Generating response`
  - `INFO: … "POST /generate HTTP/1.1" 200 OK`
- **Caveats / reviewer notes:**
  - **Line numbers** and **argument-count phrasing** in the model output may **not** match your checkout — spot-check against `fs-test.c` and **`svn_fs.h`** before thesis-quality citations.
  - Answer is **test-driven**, not a substitute for reading the real FS implementation.

---

### TC-04 — Out-of-corpus API name (fake symbol)

- **Verdict (recorded):** Pass  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Explain function totally_fake_svn_api_xyz_123 in this tree."}'
  ```
- **HTTP status:** `200 OK`  
- **Response (abridged — recorded run):** States that **`totally_fake_svn_api_xyz_123`** does **not** appear in the provided snippets; may enumerate **other** symbols that *did* appear in retrieved context (e.g. `svn_wc__db_*`, `SVN_ERR`, `svn_error_trace`) to explain what *is* visible; asks for more context if a definition is needed.  
- **Server log:** Same pipeline pattern as TC-03 → `200 OK`.  
- **Notes:** Satisfies “no fabricated API” — no invented full implementation for the fake name. Minor verbosity listing unrelated APIs is acceptable.

---

### TC-05 — Citation discipline (CONTEXT-only)

- **Verdict (recorded):** Pass  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Answer using only CONTEXT. For each claim, name the file and quote the line or say not shown. How does svn_fs_commit_txn work?"}'
  ```
- **HTTP status:** `200 OK`  
- **Response (abridged — recorded run):** Explicitly states that CONTEXT does **not** explain **internal** behavior of `svn_fs_commit_txn`; lists **call sites** in `subversion/tests/libsvn_fs/fs-test.c` with approximate line references and notes the chunk header **`Symbol: svn_error_create`** vs the topic; describes **`SVN_ERR_SQLITE_BUSY`** / `rep-cache.db` test path; concludes **signature / implementation not in CONTEXT**.  
- **Server log:** Retrieval → prune → inject → generate → `200 OK`.  
- **Notes:** Strong alignment with citation-discipline goals. Minor nit: occasional sentence says something is “not shown” then still summarizes nearby setup — treat as soft inference.

---

### TC-06 — Long-form answer (four sections, token budget)

- **Verdict (recorded):** Partial  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Answer in 4 numbered sections: (1) purpose (2) typical usage (3) errors shown in context (4) what is not in context. Topic: svn_fs_commit_txn."}'
  ```
- **HTTP status:** `200 OK`  
- **Response (abridged — recorded run):** Delivers sections **(1)–(4)**; uses bracketed `[CONTEXT: File: … Lines …]` style references; names multiple **error codes** (e.g. `SVN_ERR_FS_CONFLICT`, `SVN_ERR_FS_GENERAL`, `SVN_ERR_TEST_FAILED`) and behavioral claims tied to `fs-test.c`.  
- **Server log:** Same pipeline → `200 OK`.  
- **Caveats / reviewer notes:** **Structure = Pass.** Factual **Pass vs Partial** depends on spot-checking **error codes and line ranges** against the real `fs-test.c` — long answers often **compress or mis-map** tests to symbolic names. Do not cite TC-06 output as verified without opening the file.

---

### TC-07 — Mixed index (SVN vs Flask)

- **Verdict (recorded):** Pass  

**TC-07A — Subversion**

- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"What does svn_fs_commit_txn do?"}'
  ```
- **HTTP status:** `200 OK`  
- **Response (abridged — recorded run):** Answer anchored in **`subversion/tests/libsvn_fs/fs-test.c`**: describes committing a filesystem transaction, parameters (`txn`, pool variants), return/error behavior, example call patterns with **approximate** line references in prose.  
- **Server log:** Retrieval → … → `200 OK`.  

**TC-07B — Flask**

- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"How does Flask handle the home route in this codebase?"}'
  ```
- **HTTP status:** `200 OK`  
- **Response (abridged — recorded run):** States CONTEXT has **no** Flask home-route material; describes only **SVN-related** handlers visible in snippets — **does not invent** `app.py` or Flask routing.  
- **Server log:** Same → `200 OK`.  

- **Notes:** Demonstrates **no cross-corpus hallucination** when Flask chunks are not retrieved. If you *need* Flask in context, index `flask_test` (or equivalent) and/or improve retrieval for that query.

---

### TC-08 — Empty prompt

- **Verdict (recorded):** Pass  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":""}'
  ```
- **HTTP status:** `200 OK`  
- **Response (abridged — recorded run):** Model reports that **`<CONTEXT>` is empty** (or equivalent) and asks for code/snippets so it can assist under the system rules.  
- **Server log:** Full pipeline still invoked (retrieve / prune / inject) → `200 OK`.  
- **Notes:** Acceptable under pass criteria (**200** or clear **4xx**, not **500**). Empty user task still runs retrieval (by design).

---

### TC-09 — Invalid JSON body

- **Verdict (recorded):** Pass  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{bad json}'
  ```
- **HTTP status:** `422 Unprocessable Entity`  
- **Response (body — recorded run):** FastAPI validation error, e.g.  
  `{"detail":[{"type":"json_invalid","loc":["body",1],"msg":"JSON decode error",…}]}`  
- **Server log:** No full indexer/model path for failed parse; e.g. `POST /generate HTTP/1.1" 422 Unprocessable Entity`.  
- **Notes:** Expected client-error shape; server must not crash.

---

### TC-10 — `POST /index` (batch indexing)

- **Verdict (recorded):** Pass  
- **Command (example — adjust path):**
  ```bash
  curl -sS -X POST "http://127.0.0.1:8000/index?repo_path=/home/g7/Documents/ThesisProject/subversion"
  ```
- **HTTP status:** `200 OK`  
- **Response (body — recorded run):**
  ```json
  {"files_scanned":1621,"files_indexed":0,"chunks_added":0,"chunks_skipped":24212,"files_skipped":1621}
  ```
- **Server log (representative):**
  - `[INFO] root: Indexing started`
  - `[INFO] code_indexer: Scanned … → 1621 files found`
  - Multiple `[WARNING] code_indexer: Python parse error in …` for legacy/contrib `.py` in Subversion tree (expected; non-fatal)
  - `[INFO] code_indexer: Indexing complete: {…}`
  - `INFO: … "POST /index?repo_path=… HTTP/1.1" 200 OK`
- **Notes:** **`files_indexed: 0`** here means **incremental** mode: hashes unchanged → **no re-embedding**. For a **forced** full re-index, use `incremental=False` in `index_codebase` or reset `backend/chroma_db` / hash cache (see indexer code).

---

### TC-11 — `backend/smoke_test.py` (Tier A + B)

- **Verdict (recorded):** Pass  
- **Command (from repo root):**
  ```bash
  cd /home/g7/Documents/ThesisProject/Context-Engineering-Thesis1
  source .venv/bin/activate
  python backend/smoke_test.py
  ```
- **HTTP status:** N/A for Tier A; Tier B uses in-process **`TestClient`** (`http://testserver`), not your live `:8000` socket.  
- **Console output (recorded — representative):**
  - `=== Tier A: PromptBuilder (no Chroma) ===`
  - `PASS: PromptBuilder context + line numbers`
  - `PASS: chunk_normalize strips duplicate editor line column when uniform`
  - `=== Tier B: FastAPI app + JSON body for /generate ===`
  - (Cold start) loading **`all-MiniLM-L6-v2`**, Chroma `code_embeddings` (~24k docs), optional **CUDA driver too old** → **CPU** warning
  - Hugging Face Hub `HEAD`/`GET` lines if cache cold; `BertModel LOAD REPORT` / `position_ids` UNEXPECTED (benign for this model)
  - `[INFO] httpx: HTTP Request: GET http://testserver/ "HTTP/1.1 200 OK"`
  - Pipeline logs for `POST /generate` with `{"prompt":"hello"}` → **`PASS: POST /generate accepted JSON (HTTP 200)`**
- **Notes:** Tier B **200** is best case (Ollama + index present). **500** would still satisfy “Tier B ran” but should be documented. **Do not confuse** `testserver` logs with concurrent manual `curl` to `127.0.0.1` (different processes).

---

### TC-12 — FS transaction lifecycle (multi-step)

- **Verdict (recorded):** Pass (caveats)  
- **Stress target:** Multiple `svn_fs_*` steps in order + file paths + “NOT SHOWN” when missing.  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Using only CONTEXT: describe the typical sequence from creating a Subversion filesystem transaction to committing it, as shown in the tests. List svn_fs_* function names in order and name the file path(s) where each step appears. If a step is not visible in CONTEXT, say NOT SHOWN."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** `Retrieval started` → batch encode → `Pruning started` → `Injection started` → `Generating response` → `200 OK`  
- **Response (abridged — recorded run):** Numbered sequence: (1) create FS via `svn_fs_create` / `svn_test__create_fs` in `subversion/tests/libsvn_fs/fs-test.c` (~lines cited); (2) `svn_fs_begin_txn`; (3) `svn_fs_txn_root`; (4) edits e.g. `svn_fs_make_file` / `svn_fs_make_dir`; note on `svn_fs_revision_root` as read path; (5) `svn_fs_commit_txn` — all attributed to **`fs-test.c`**.  
- **Reviewer notes:** **Structure and file** match the intended stress test. **Line numbers** and some counts (e.g. “line 23 and 23”) are typical model slippage—confirm against `fs-test.c` before thesis citation. Optional: compare to `svn_fs_begin_txn2` if tests use v2 in your tree.

---

### TC-13 — `SVN_ERR_SQLITE_BUSY` / `rep-cache.db`

- **Verdict (recorded):** Partial (model **Pass** for faithfulness; pipeline **retrieval gap**)  
- **Stress target:** Pull **`fs-test.c`** (or similar) chunk that documents shared lock on **`rep-cache.db`** during `svn_fs_commit_txn`.  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Using only CONTEXT: why does the test expect SVN_ERR_SQLITE_BUSY when calling svn_fs_commit_txn? What lock or resource is involved (e.g. rep-cache.db)? Quote or paraphrase the test comment that explains it and give the file path."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** Same pipeline pattern → `200 OK`  
- **Response (abridged — recorded run):** States that **CONTEXT does not contain** `svn_fs_commit_txn`, `SVN_ERR_SQLITE_BUSY`, `rep-cache.db`, or the relevant test comments; describes instead snippets from **`subversion/libsvn_wc/wc_db.c`** (`init_db`, `insert_working_node`, `verify_wcroot`, etc.).  
- **Reviewer notes:** The **answer is correct given the injected context**—good anti-hallucination. The **failure is upstream:** dense retrieval ranked **WC DB** SQLite code above the **FS test** that actually documents the busy/rep-cache scenario. **Mitigations to try:** add **`svn_fs_commit_txn`** or **`rep-cache`** to the prompt (triggers `$contains` augmentation), increase `top_k` / pruning budget, or add a second test variant prompt: *“In fs-test.c, why SVN_ERR_SQLITE_BUSY on commit?”*  
- **Optional follow-up curl (narrower retrieval):** same question with leading sentence: *“In subversion/tests/libsvn_fs/fs-test.c only…”* (honesty still required if that file is not in context).

---

### TC-14 — Working copy vs FS layer

- **Verdict (recorded):** Pass  
- **Stress target:** Answer **only** if WC APIs appear in context; else **NO WC CONTEXT**.  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Using only CONTEXT: does the retrieved code discuss svn_wc__db_* (working copy database) APIs? If yes, summarize one function and its file. If no WC code appears, say NO WC CONTEXT and summarize what layer (e.g. libsvn_fs tests) you do see."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** `Retrieval started` → … → `200 OK`  
- **Response (abridged — recorded run):** **Yes** — discusses `svn_wc__db_install_schema_statistics` in **`subversion/libsvn_wc/wc_db.c`**, SQL `STMT_INSTALL_SCHEMA_STATISTICS`, `svn_sqlite__exec_statements`, call from `init_db`.  
- **Reviewer notes:** Matches **TC-13** observation: top contexts can be **WC-heavy** for SQLite/error wording. This TC **passes** because the question allowed “yes if present.” If you need **FS-only** context, constrain the prompt (path or symbol list) or adjust retrieval/ranking.

---

### TC-15 — Semantic query (no symbol names)

- **Verdict (recorded):** Partial  
- **Stress target:** Paraphrase “pending changes → permanent revision” **without** `svn_fs_*` unless present in CONTEXT text.  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"In plain English only: how do pending tree changes in a Subversion repository become a new permanent revision, based strictly on what the CONTEXT snippets show? Do not name svn_fs_* symbols unless they appear in the CONTEXT text."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** `Retrieval started` → encode → prune → inject → generate → `200 OK`  
- **Response (abridged — recorded run):** Describes **three trees** (mutable transaction tree, **base** revision, **latest** revision), **merge** when latest ≠ base, merged txn tree as basis for new revision, conflict avoidance when txn root matches base, merge operating on the mutable txn tree. **No `svn_fs_*` tokens** in the answer (instruction followed).  
- **Reviewer notes:** **Instruction compliance: good.** **Grounding: unverified in this log**—the narrative may match a **merge** implementation chunk (e.g. FS merge logic) or may be **over-generalized**. For thesis evidence, compare answer sentences to the **injected** `<CONTEXT>` block from the same request (server log / prompt capture). If chunks were only WC/SQLite, treat as **Partial / weak grounding**.

---

### TC-16 — Supported vs unsupported claims

- **Verdict (recorded):** Pass (caveats)  
- **Stress target:** Three **unsupported** vs three **supported** claims about `svn_fs_commit_txn`, with paths + quotes for (B).  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Two sections. (A) List exactly three specific technical claims about svn_fs_commit_txn that you CANNOT justify from CONTEXT (say NOT IN CONTEXT for each). (B) List three claims you CAN justify with file path + short quote from CONTEXT."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** Full pipeline → `200 OK`  
- **Response (abridged — recorded run):**  
  - **(A)** Three internal/implementation claims flagged **not** in context (deltification/window sizing inside commit; internal SQLITE_BUSY handling; internal old-rev/datestamp validation inside `svn_fs_commit_txn`).  
  - **(B)** Three supported claims with **`subversion/tests/libsvn_fs/fs-test.c`** quotes: **rep-cache.db** shared lock → commit post-processing / busy scenario; successful commit + valid rev (`SVN_IS_VALID_REVNUM` / `svn_fs_commit_txn`); finalize txn after `txn_root` edits (`svn_fs_commit_txn` with `new_rev`). Chunk headers cite symbols like `svn_error_create`, `file_integrity_helper`.  
- **Reviewer notes:** Strong **structure** and **good FS test grounding** on (B)—notably the **rep-cache** comment (contrast with **TC-13** when WC-only context mis-fired). Spot-check that each **quote** and **line** range exists in your `fs-test.c`; **Symbol:** labels on chunks can differ from the prose claim.

---

### TC-17 — Multi-symbol table

- **Verdict (recorded):** Partial  
- **Stress target:** Markdown table for five symbols; honest **yes/no** + one sentence.  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Markdown table with columns: Symbol | In CONTEXT? (yes/no) | One-sentence evidence or NOT IN CONTEXT. Rows for: svn_fs_commit_txn, svn_repos_fs_begin_txn2, svn_delta_editor_t, svn_ra_open4, svn_wc__db_read_info. Use only CONTEXT."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** Full pipeline → `200 OK`  
- **Response (recorded — body):** Pipe table: all four FS/RA/delta symbols → **no** / NOT IN CONTEXT; **`svn_wc__db_read_info`** → **yes**, “Defined … line 11 of `wc_db.c`” with signature snippet.  
- **Reviewer notes:** **Format: Pass.** **Content: suspect row for `svn_fs_commit_txn`:** minutes earlier **TC-16** (same environment) used **`fs-test.c`** quotes containing `svn_fs_commit_txn`, so marking it **not in context** here is likely **wrong** relative to the true injected prompt **or** reflects **different top‑k / prune** outcome for this query shape. **Action:** re-run with logging of retrieved paths, or grep the built user message; if `commit_txn` appears in any chunk text, mark this run **Partial** (model/table error). **`svn_wc__db_read_info`** “line 11” may be chunk-relative—confirm in `wc_db.c`.

---

### TC-18 — `SVN_ERR_FS_PATH_SYNTAX` debug

- **Verdict (recorded):** Partial  
- **Stress target:** **Needle in haystack** — invalid path string in **`fs-test.c`** (or nearby) tied to commit; rubric allows **Partial** if error semantics are right but test snippet missing.  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Using only CONTEXT: in subversion/tests/libsvn_fs/fs-test.c (or nearby test code), what invalid path or input triggers SVN_ERR_FS_PATH_SYNTAX around a transaction commit? Give the bad path string if it appears in CONTEXT."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** `Retrieval started` → encode → prune → inject → generate → `200 OK`  
- **Response (abridged — recorded run):** States **no** `fs-test.c` or nearby test code in CONTEXT; lists retrieved areas as **`subversion/libsvn_fs/fs-loader.c`** and path validation under **`subversion/libsvn_fs/`**. From validation snippets, summarizes **`SVN_ERR_FS_PATH_SYNTAX`** for: **`.` / `..` path elements**, **invalid UTF-8**, **newline in path**; explicitly says **test / commit trigger** wording is **not** in the current CONTEXT and would need test files. **No concrete bad path string** from tests (because tests not retrieved).  
- **Reviewer notes:** **Faithfulness: good** — does not invent a test string. **Test objective: missed needle** — retrieval did not surface `fs-test.c` for this query shape (compare **TC-03** / **TC-12** where FS tests sometimes appear). Optional improvement: path-biased retrieval or prompt that includes `fs-test.c` in `$contains` / metadata filter for this TC.

---

### TC-19 — `svn_delta_editor_t` / delta pipeline

- **Verdict (recorded):** Pass (caveats)  
- **Stress target:** **Hard symbol** — declaration vs WC wrapper usage; model should admit gaps (`apply_txn`, driver loop) if absent.  
- **Command:**
  ```bash
  curl -sS http://127.0.0.1:8000/generate \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Using only CONTEXT: what is svn_delta_editor_t and how is it used in the Subversion code you see—driver vs editor, apply_txn, or similar? If CONTEXT only shows a declaration or one call site, say so. If absent, say NOT IN CONTEXT."}'
  ```
- **HTTP status:** `200 OK`  
- **Server log (representative):** Full pipeline → `200 OK`  
- **Response (abridged — recorded run):** **`subversion/libsvn_wc/deprecated.c`**: `svn_delta_editor_t *` as output/`editor` in **`svn_wc_get_status_editor4`**, **`svn_wc_get_switch_editor3`**, **`svn_wc_get_update_editor3`**; ties to **`svn_error_trace`**; notes snippets are **wrapper return paths** delegating to internal `svn_wc__get_*_editor` (creation not shown). **Driver** / close / delta driver iteration and **`apply_txn`** (or similar) marked **NOT IN CONTEXT**. Conclusion: partial picture—**editor handle** in WC APIs visible; **full lifecycle** not in snippets.  
- **Reviewer notes:** Matches **pass hints** (decl vs thin usage + honest **NOT IN CONTEXT** for driver/apply). **Caveats:** **`svn_delta_close()`** in the answer may be imprecise naming—verify against real API names in Subversion; spot-check **line numbers** in `deprecated.c`.

---

### TC-20 — Verbatim comment (ground truth / hallucination probe)

- **Verdict (recorded):** **Mixed** — **five** identical `curl`s recorded (same prompt); outcomes span **wrong excerpt**, repeated **`NOT IN CONTEXT`**, and **one correct gold quote** (see runs below).  
- **Stress target:** Model must reproduce the **gold** comment (immediately before `svn_fs_commit_txn` with `SVN_TEST_ASSERT_ERROR(err, SVN_ERR_SQLITE_BUSY)` after) or reply **only** `NOT IN CONTEXT`.  
- **Command & ground truth:** See §4 **TC-20**. **Gold substring** includes: *“Attempt to commit fs transaction. This should result in a commit post-processing error due to us still holding the shared lock on the rep-cache.db.”*  
- **Scoring (human):**
  - **Pass:** Normalized output contains the **ground-truth sentence** (allow `*` column / newline folding).
  - **Pass (honest miss):** Only **NOT IN CONTEXT** (or equivalent) and injected prompt truly lacks that comment—retrieval miss, not hallucination.
  - **Partial:** Right file/story but wording drift or mixed quote + invention—verify against source.
  - **Fail (hallucination):** Different comment presented as **verbatim**, or invented path/file for the quote.

#### Run A — 2026-04-01 (~23:44)

- **HTTP status:** `200 OK`  
- **Server log (representative):** `Retrieval started` → prune → inject → generate → `200 OK` (`127.0.0.1:46970`).  
- **Response (body):** Markdown code block quoting **the wrong** C comment: *“Obtain a shared lock on the rep-cache.db by starting a new read transaction.”* (the block **above** `svn_sqlite__open`, **not** the block immediately **above** `err = svn_fs_commit_txn(...)` / `SVN_ERR_SQLITE_BUSY`).  
- **Rubric:** **Fail** for TC-20’s stated goal: satisfies “verbatim” form but **violates the positional constraint** in the prompt; also **does not** contain the **gold** “Attempt to commit fs transaction…” text. Likely **keyword association** (`rep-cache`, `commit`) without parsing “immediately before `svn_fs_commit_txn` … followed by `SVN_TEST_ASSERT_ERROR`”.

#### Run B — 2026-04-01 (~23:45)

- **HTTP status:** `200 OK`  
- **Server log (representative):** Same pipeline → `200 OK` (`127.0.0.1:53008`).  
- **Response (body):** `NOT IN CONTEXT` only (instruction followed).  
- **Rubric:** **Pass (honest miss)** if the injected prompt truly omitted the gold comment; **inconclusive** without a prompt dump—same curl as Run A shows **retrieval/pruning variance** or **non-deterministic generation** after different retrieval.

#### Runs C–D — 2026-04-01 (later session, same command)

- **HTTP status:** `200 OK` (each).  
- **Response (body):** `NOT IN CONTEXT` only (both runs).  
- **Rubric:** **Pass (honest miss)** under the same caveat as Run B. Together with Run B, **three** consecutive-style outcomes were **`NOT IN CONTEXT`** before Run E—suggests the gold comment is **often absent** from the pruned window, not only “sometimes present.”

#### Run E — 2026-04-01 (third consecutive curl in same session as C–D)

- **HTTP status:** `200 OK`  
- **Response (body):** Markdown fenced code block (`c`) containing the **correct** multi-line comment, including *“Attempt to commit fs transaction.”* … *“…shared lock on the rep-cache.db.”* (model used **two spaces** after `transaction.` vs typical single space in upstream—treat as **normalization** when scoring).  
- **Rubric:** **Pass** for TC-20: output contains the **gold** substring and matches the **intended** comment block, not the earlier “read transaction” block from Run A.

- **Thesis takeaway:** TC-20 behaved as designed: **failure mode** (Run A: **wrong but plausible excerpt**), **honest miss** streak (Runs B–D), and **occasional full success** (Run E) on the **identical** request—strong evidence for **retrieval/pruning variance** and/or **decoder stochasticity**. For papers, log **retrieved chunk IDs** or full injected `<CONTEXT>` per request and report **multiple trials**.

---

## 7. Verdict definitions and rubric

| Verdict | Meaning |
|---------|---------|
| **Pass** | Meets pass criteria; safe for the documented use. |
| **Partial** | Usable with caveats (e.g. verify model citations against source). |
| **Fail** | Wrong status, crash, or violates pass criteria. |
| **Mixed** | Multiple runs on the same TC disagree (e.g. **TC-20**: Fail / NOT IN CONTEXT / Pass); see §6 per-run rubric. |

**Reviewer checklist:** truncation, hallucination, wrong repository/corpus, citation mismatch, unexpected **500**.

---

## 8. Related automation

- **`backend/smoke_test.py`** — Tier A (no Chroma/Ollama for A) and Tier B (full app import + TestClient). Overlaps **TC-11**; does not replace **TC-01–TC-10** HTTP checks against a live server.
- **Unit tests:** e.g. `pytest tests/backend/test_pruning.py` — not covered by this manual sheet.
- **Advanced Subversion stress:** **TC-12–TC-20** and **§2.1** — require a **full Subversion index** in `backend/chroma_db` for meaningful scores; combine with **`OLLAMA_NUM_PREDICT`** increases if answers truncate mid-table.
