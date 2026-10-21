# Context Engine — VS Code Extension

A **Cursor-style agent panel** for the Context Engineering thesis project. Brings the full retrieval → prune → assess → inject → generate pipeline directly into VS Code as a sidebar chat.

## Features

- **Live pipeline steps** — watch each stage animate in as it completes (retrieve, prune, assess, inject, generate)
- **Streaming LLM output** — tokens stream in character by character like Cursor
- **Model picker** — auto-populated from your local Ollama installation
- **Pipeline mode** — choose `Auto-Route (SELF_ROUTE)`, `RAG`, or `Long Context (LC)`
- **Context chips** — attach the active file or your current selection to the prompt with one click
- **Index workspace** — trigger ChromaDB indexing directly from the panel
- **Auto server management** — the extension can start/stop the FastAPI backend for you

## Requirements

- Python environment with the backend dependencies installed (`pip install -r backend/indexing/requirements.txt`)
- [Ollama](https://ollama.com) running locally with at least one model pulled (e.g. `ollama pull qwen3.5:9b`)
- VS Code 1.85+

## Development Setup

```bash
cd vscode-extension
npm install
npm run compile
```

Then press **F5** in VS Code to open a new Extension Development Host window.

## Configuration

| Setting | Default | Description |
|---|---|---|
| `contextEngine.backendPort` | `8000` | Port for the FastAPI backend |
| `contextEngine.pythonPath` | `python` | Path to Python interpreter |
| `contextEngine.defaultModel` | `qwen3.5:9b` | Default Ollama model |
| `contextEngine.autoStartServer` | `false` | Start backend automatically |

## Usage

1. Open a workspace containing the project
2. Click the **⬡ Context Engine** icon in the Activity Bar
3. Click **▶ Start** to launch the backend server (or start it manually: `cd backend && uvicorn mcp_server.server:app`)
4. Click **⊕** to index your workspace into ChromaDB
5. Start chatting — select a model and pipeline mode at the bottom of the panel
