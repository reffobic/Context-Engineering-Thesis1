# Architecture & Failure-Mode Notes (SRS/SDD addendum)

> Status: the repository contains **no formal SRS/SDD**; `README.md` and
> `TESTCASES.md` are the only specs. This file is the interim design record and
> should be folded into the SRS/SDD.

## 1. What the system actually is

- A **FastAPI REST API** (`backend/mcp_server/server.py`, run as
  `uvicorn mcp_server.server:app`).
- `backend/mcp_server/tool_registry.py` is a **plain Python dictionary** mapping tool
  names to functions; `call_tool(name, ...)` dispatches and raises `ValueError`
  for unknown names.
- It is **NOT** an Anthropic Model Context Protocol (MCP) server: no JSON-RPC 2.0,
  no MCP transport (stdio/SSE), no MCP tool discovery. The `mcp_server` package
  name is historical. Clients use HTTP endpoints (`/generate`, `/generate/stream`,
  `/index`, `/index/summary`, `/models`, `/evaluate`).

## 2. Diamond pipeline additions

| Component | File | Role |
|---|---|---|
| GASR multi-hop ladder | `backend/diamond/gasr_ladder.py` | Pure control flow `RAG -> GRAPH expand (hops, capped) -> LC`. Post-RAG decision is **one merged gate** (`assess_rag_gate`: sufficiency + `STRUCTURE`/`CONTEXT` need); the legacy `assess_rag` + `assess_structure` pair is still accepted and composed into one gate. Once `STRUCTURE` is chosen the need is sticky across hops. Other steps are injected callbacks (`expand_hop`, `re_prune`, `assess_after_expand`, `build_lc`), so it is unit-testable without Ollama. Returns `route`, `hops_used`, `context`, `assess_trace` (first entry `phase=rag_gate`), `expand_trace`. |
| GAPA entry gate | `backend/diamond/preroute_gate.py` | `mode="GAPA"` (renamed from `PREROUTE`; the old name is now rejected with 422). In `server.py` the entry decision is `preroute_structured_gate`: a six-step structured prompt over raw retrieval **metadata** (no chunk text), one LLM call. The two-stage rule heuristic (`preroute_gate`, Stage 1 query keywords / Stage 2 score and dependency rules) and a generative variant still exist in the file; the module docstring still describes the older rule-based design. **Known limitation:** retrieval and pruning still run unconditionally before the gate, so pruning cost is not avoided. |
| Sufficiency judge | `backend/diamond/sufficiency_assessor.py` | LLM (+ score shortcut) judging whether RAG context suffices. |
| Graph backend | `backend/indexing/graphify_adapter.py` | Loads Graphify `graph.json`; `backend == "empty"` when none found. |

Policy: after a successful expand that adds neighbours the ladder stays on
`GRAPH` (re-pruned seeds + neighbours) rather than falling to full LC. LC is used
for `CONTEXT` needs or when expand yields nothing.

## 3. VS Code extension architecture

`vscode-extension/` (TypeScript host + plain-JS webview):

- `src/extension.ts`: activation, status bar, spawns
  `python -m uvicorn mcp_server.server:app` (cwd `backend/`), probes the server.
  Preflight: **port-in-use check** (TCP connect to the configured
  `contextEngine.backendPort`; offers *connect to existing* / *change port*) and
  **Ollama reachability check** (`http://localhost:11434/api/tags`; non-blocking
  warning at activation and before each query, throttled to once per 60 s).
- `src/sidebarProvider.ts`: webview host; relays messages (`startServer`,
  `getActiveFile`, `checkOllama`, ...).
- `webview/main.js`: UI; talks **directly over HTTP** to the backend
  (`/generate/stream`, `/models`, `/index/summary`) on `127.0.0.1:<port>`.

## 4. Failure sequences (as implemented)

| Situation | Behaviour |
|---|---|
| **`graph.json` missing** | `CodeGraphAdapter` logs a warning, sets `backend = "empty"`. `expand_hop` returns 0 chunks; the ladder records `expand_trace[0]` with `new_chunk_count=0`, `graph_backend="empty"`, then falls through to **LC**. Covered by `test_missing_graph_falls_through_to_lc_with_diagnostics`. |
| **Chroma empty / not indexed** | `retrieval_tool.py` binds the collection at import (`client.get_collection(COLLECTION_NAME)`), so a *non-existent* collection fails at import/startup. An *empty* collection yields no retrieved chunks, so pruning returns nothing and the sufficiency/ladder path proceeds with empty context (answer will be ungrounded). Index first via `POST /index`. *(Empty-collection end-to-end behaviour should be confirmed by an integration test.)* |
| **Ollama offline** | `OllamaAgent._post_json_raw` catches `URLError` and returns the string `"[Ollama error: ...]"` as the model output, so `/generate` can return HTTP 200 with an error string as the answer; it is not raised as an HTTP error. The extension shows: *"Context Engine: Ollama appears offline. Ensure 'ollama serve' is running."* |
| **Python file with syntax errors** | `_chunk_python` now falls back to `_chunk_generic` (previously returned `[]` and the file was dropped); `read_and_chunk_file` additionally emits one whole-file block if no chunks result. |
| **Port 8000 busy** | Extension does not spawn a second server; warns and offers to connect or change port. |

## 5. Tests

- Tier 1: `tests/backend/test_indexing_robustness.py` (syntax-error fallback,
  token-budget invariant, empty/whitespace files); `tests/backend/test_pruning.py`.
- Tier 2: `tests/backend/test_gasr_ladder.py` (mocked ladder: legacy callbacks and the
  merged `assess_rag_gate` path, sticky STRUCTURE need, `hard_cap=0`, unknown need,
  missing graph); `tests/backend/test_api_contract.py` (422 on bad body/mode/pruning
  method; `PREROUTE` rejected, `GAPA` is the valid name).
- Selective-pruning tests need the cached `Salesforce/codegen-350M-multi` model and are
  skipped when it is not in the local HuggingFace cache.
- Live baseline: `scripts/live_gasr_ollama_tests.py` (requires running API + Ollama).
