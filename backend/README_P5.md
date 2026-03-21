# Person 5 — LLM agent & prompt builder

## Branches (check locally)

```bash
git branch -a
```

Typical branches: `main`, `approaches+test`, `context-engine` (new engine layout).

## What this folder contains (P5 only)

| Path | Role |
|------|------|
| `injection/prompt_builder.py` | Structured injection: `FILES_AND_CODE`, `DEPENDENCIES`, `TASK`; **TypedDict** `ContextChunk`; **group by file**; optional **`start_line`** + line numbers; **token budget** + pluggable `estimate_tokens` |
| `agent/ollama_agent.py` | `OllamaAgent.generate_response`, `generate_with_context` (optional `max_context_tokens`, `prompt_builder`) |

P3/P4 pass **chunks** as `list[ContextChunk]` (typed dict): at least `text`; optional `path`, `symbol`, `language`, `dependencies`, `start_line`.

**Tests:** `tests/backend/test_prompt_builder.py` — run with `PYTHONPATH=. pytest tests/backend/` (install `typing_extensions` on Python 3.7).

## Ollama vs DeepSeek Coder

- **Ollama alone is enough** — it is the local runtime.
- **DeepSeek Coder** is an optional *model* you can pull if you want a coding-focused weights file, e.g.  
  `ollama pull deepseek-coder`  
  Then use `OllamaAgent(model="deepseek-coder")` (exact tag from `ollama list`).

## Run from repo root

```bash
export PYTHONPATH=.
# Preview prompt + call Ollama (requires `ollama serve` and a pulled model)
python backend/agent/ollama_agent.py llama3.2:3b

# Skip HTTP call (prompt build only)
OLLAMA_SKIP=1 python backend/agent/ollama_agent.py
```

## API for P1 (integration)

```python
from backend.agent import OllamaAgent

agent = OllamaAgent(model="qwen3.5:4b")
answer = agent.generate_with_context(
    user_task="Add logging to the checkout flow.",
    chunks=[{"path": "app.py", "text": "def checkout(): pass", "language": "python"}],
)
```

```python
from backend.injection import PromptBuilder, build_coding_prompt

system, user = build_coding_prompt("task", chunks)
# hand off to any chat API
```
