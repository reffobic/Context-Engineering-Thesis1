# How to run live GASR tests

PowerShell steps for this PC. Script: `scripts/live_gasr_ollama_tests.py`. Results: `results/live_gasr/`.

**Best model on this machine (RTX A2000 4 GB + ~32 GB RAM):** `qwen3.5:9b`

| Goal | Model |
|------|--------|
| Best balance (default) | `qwen3.5:9b` |
| Max quality (slower) | `qwen3.6:latest` |
| Fastest | `qwen3.5:4b` |

---

## 0. Once — confirm Ollama has the model

```powershell
ollama serve
# other terminal if needed:
ollama pull qwen3.5:9b
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

Optional: let the script pick the model for this PC:

```powershell
cd "c:\dev\context engineering\Context-Engineering-Thesis1"
python -u scripts/live_gasr_ollama_tests.py --recommend-model
```

---

## 1. Terminal A — start the API

Set `OLLAMA_MODEL` **before** starting uvicorn (the API reads it at process start).

```powershell
cd "c:\dev\context engineering\Context-Engineering-Thesis1"
$env:OLLAMA_MODEL="qwen3.5:9b"
$env:PYTHONPATH="backend"
# Do NOT set GASR_OFFLINE=1
cd backend
python -m uvicorn mcp_server.server:app --host 127.0.0.1 --port 8000
```

Check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/
```

Repo must already be **indexed** and Graphify `graph.json` available to the server.

---

## 2. Terminal B — run the live suite

```powershell
cd "c:\dev\context engineering\Context-Engineering-Thesis1"

# Preflight only (no /generate calls)
python -u scripts/live_gasr_ollama_tests.py --dry-preflight

# Core thesis cases (5) — usual run
python -u scripts/live_gasr_ollama_tests.py --suite core --timeout 600

# Full suite (8) — core + RAG/LC baselines + multi-hop
python -u scripts/live_gasr_ollama_tests.py --suite full --timeout 900

# Compare RAG vs LC vs GASR on tokens + speed (12 cases: 4 prompts × 3 modes)
python -u scripts/live_gasr_ollama_tests.py --suite compare --timeout 900

# One or more cases
python -u scripts/live_gasr_ollama_tests.py --cases L1_simple_ragish L2_cross_file_callees --timeout 600
```

### Compare suite (tokens + speed)

Runs each prompt under forced **`RAG`**, **`LC`**, and **`GASR`** and writes:

- `aggregates.cost_by_mode` — mean tokens, wall_ms, generate_ms, retrieve_ms, graph_expand_ms, ms/token  
- `aggregates.prompt_comparison` — side-by-side table + deltas vs RAG  
- Markdown sections **Mode cost summary** and **Prompt comparison**

| Group | What it stresses |
|-------|------------------|
| `C1_simple_return` | Simple Q — RAG usually fewest tokens |
| `C2_cross_file_callees` | Structure / graph expand pressure |
| `C3_same_file_context` | LC parent_context (more tokens) |
| `C4_config_deps` | Cross-file neighbors |

Example single group:

```powershell
python -u scripts/live_gasr_ollama_tests.py --cases C1_simple_return_RAG C1_simple_return_LC C1_simple_return_GASR --timeout 600
```

Force a specific installed model name in the report meta (API must still be started with the same `OLLAMA_MODEL`):

```powershell
python -u scripts/live_gasr_ollama_tests.py --suite core --model qwen3.5:9b --timeout 600
```

---

## 3. Where results go

| File | What |
|------|------|
| `results/live_gasr/live_gasr_run_<UTC>.json` | Structured: `meta`, `aggregates`, `results` |
| `results/live_gasr/live_gasr_run_<UTC>.md` | Readable report |
| `results/live_gasr/LATEST.md` | Copy of the newest report |
| `results/live_gasr/INDEX.jsonl` | One summary line per run |

---

## Quick checklist

1. Ollama up + model pulled  
2. `$env:OLLAMA_MODEL` set → uvicorn on `:8000`  
3. Indexed corpus + Graphify graph  
4. Run `--suite compare` (tokens/speed) or `--suite core`  
5. Open `results/live_gasr/LATEST.md` — check **Mode cost summary** / **Prompt comparison**
