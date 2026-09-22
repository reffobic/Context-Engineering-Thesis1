# How to run live GASR tests

**Use a normal Terminal (bash), not PowerShell.** On Ubuntu/Linux the command is usually `python3` (not `python`).

Script: `scripts/live_gasr_ollama_tests.py`  
Results: `results/live_gasr/`

**Recommended Ollama model:** `qwen3.5:9b`  
(Alternatives: `qwen3.6:latest` = max quality/slower; `qwen3.5:4b` = fastest)

If `python3` is missing:

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

Check:

```bash
python3 --version
which python3
```

---

## 0. Once — Ollama model

```bash
# if ollama is not already running as a service:
ollama serve

# other terminal:
ollama pull qwen3.5:9b
curl -s http://127.0.0.1:11434/api/tags | head
```

Optional — let the script recommend a model:

```bash
cd ~/Documents/ThesisProject/Context-Engineering-Thesis1
# or wherever this repo lives:
# cd "/path/to/Context-Engineering-Thesis1"

python3 -u scripts/live_gasr_ollama_tests.py --recommend-model
```

---

## 1. Terminal A — start the API

Set `OLLAMA_MODEL` **before** starting uvicorn.

```bash
cd ~/Documents/ThesisProject/Context-Engineering-Thesis1

export OLLAMA_MODEL="qwen3.5:9b"
export PYTHONPATH="backend"
# Do NOT set GASR_OFFLINE=1

cd backend
python3 -m uvicorn mcp_server.server:app --host 127.0.0.1 --port 8000
```

If `uvicorn` is not found:

```bash
python3 -m pip install uvicorn fastapi
# or use your project venv if you have one:
# source .venv/bin/activate
```

Check:

```bash
curl -s http://127.0.0.1:8000/
```

Repo must already be **indexed** and Graphify `graph.json` available to the server.

---

## 2. Terminal B — run the live suite

```bash
cd ~/Documents/ThesisProject/Context-Engineering-Thesis1

# Preflight only (no /generate calls)
python3 -u scripts/live_gasr_ollama_tests.py --dry-preflight

# Core thesis cases (5)
python3 -u scripts/live_gasr_ollama_tests.py --suite core --timeout 600

# Full suite (8)
python3 -u scripts/live_gasr_ollama_tests.py --suite full --timeout 900

# Compare RAG vs LC vs GASR — tokens + speed (12 cases)
python3 -u scripts/live_gasr_ollama_tests.py --suite compare --timeout 900
```

One compare group:

```bash
python3 -u scripts/live_gasr_ollama_tests.py \
  --cases C1_simple_return_RAG C1_simple_return_LC C1_simple_return_GASR \
  --timeout 600
```

### Compare suite notes

| Group | Stresses |
|-------|----------|
| `C1_simple_return` | Simple Q — RAG usually fewest tokens |
| `C2_cross_file_callees` | Structure / graph expand |
| `C3_same_file_context` | LC parent_context (more tokens) |
| `C4_config_deps` | Cross-file neighbors |

Reports include **Mode cost summary** and **Prompt comparison** (Δ vs RAG).

---

## 3. Where results go

| File | What |
|------|------|
| `results/live_gasr/live_gasr_run_<UTC>.json` | `meta`, `aggregates`, `results` |
| `results/live_gasr/live_gasr_run_<UTC>.md` | Readable report |
| `results/live_gasr/LATEST.md` | Newest report |
| `results/live_gasr/INDEX.jsonl` | One line per run |

---

## Quick checklist

1. `python3 --version` works  
2. Ollama up + model pulled  
3. `export OLLAMA_MODEL=...` then uvicorn on `:8000`  
4. Indexed corpus + Graphify graph  
5. `python3 -u scripts/live_gasr_ollama_tests.py --suite compare --timeout 900`  
6. Open `results/live_gasr/LATEST.md`
