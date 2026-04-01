import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel

try:
    from tool_registry import call_tool
except ModuleNotFoundError:  # when running as package: python -m uvicorn mcp_server.server:app
    from mcp_server.tool_registry import call_tool

app = FastAPI()


class GenerateRequest(BaseModel):
    """JSON body for POST /generate (matches `run_demo.py` and `requests.post(..., json={...})`)."""

    prompt: str

logging.basicConfig(level=logging.INFO)

# Confirm server is running
@app.get("/")
async def root():
    logging.info("Context Engine is running")
    return {"message": "Context Engine Running"}


# Current pipeline  
def run_pipeline(user_prompt):
    logging.info("Retrieval started")
    context = call_tool("retrieve_context", user_prompt)

    logging.info("Pruning started")
    # P4 expects (query, retrieved_items). Tool can still choose to passthrough.
    pruned_context = call_tool("prune_context", user_prompt, context)

    logging.info("Injection started")
    final_prompt = call_tool("inject_context", user_prompt, pruned_context) 
    logging.info("Generating response")
    result = call_tool("ollama_generate", final_prompt)
    return result


# Add API endpoints
@app.post("/index")
def index(
    repo_path: Optional[str] = Query(
        None,
        description="Absolute path to repository root. Defaults to backend/flask_test when omitted.",
    ),
    languages: Optional[List[str]] = Query(
        None,
        description="Optional language filter passed to the indexer.",
    ),
):
    """
    One-time / batch indexing into ChromaDB (not part of /generate pipeline).

    Query params: ``repo_path`` (optional), ``languages`` (optional).
    """
    logging.info("Indexing started")
    base_dir = Path(__file__).resolve().parent.parent  # backend/
    resolved = repo_path if repo_path else str(base_dir / "flask_test")
    result = call_tool("index_codebase", repo_path=resolved, languages=languages)
    logging.info("Indexing complete: %s", result)
    return result

@app.post("/generate")
def generate(req: GenerateRequest):
    result = run_pipeline(req.prompt)
    return {"output": result}