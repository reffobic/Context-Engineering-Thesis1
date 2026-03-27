from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.agent import OllamaAgent
from backend.indexing import CodeIndexer
from backend.injection import PromptBuilder
from backend.mcp_server.tool_registry import call_tool, register_tool

app = FastAPI(title="Context Engine MCP Server")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
_INDEXER: Optional[CodeIndexer] = None


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="User request/task")
    model: str = Field(default="llama3.2:3b", description="Ollama model")
    top_k: int = Field(default=5, ge=1, le=20)
    max_context_tokens: Optional[int] = Field(default=1500, ge=100, le=32000)
    language: Optional[str] = None
    db_path: str = Field(default="./chroma_db")
    collection: str = Field(default="code_embeddings")


def _get_indexer(db_path: str, collection: str) -> CodeIndexer:
    global _INDEXER
    if _INDEXER is None:
        _INDEXER = CodeIndexer(db_path=db_path, collection_name=collection)
    return _INDEXER


# ---------------------------------------------------------------------------
# Tool implementations (using existing modules only)
# ---------------------------------------------------------------------------
def retrieve_context(query: str, top_k: int = 5, language: Optional[str] = None, db_path: str = "./chroma_db", collection: str = "code_embeddings") -> List[Dict[str, Any]]:
    """Semantic retrieval from indexed code chunks."""
    indexer = _get_indexer(db_path, collection)
    results = indexer.query(query_text=query, top_k=top_k, language=language)
    chunks: List[Dict[str, Any]] = []
    for r in results:
        chunks.append(
            {
                "path": r.get("file_path"),
                "symbol": r.get("name"),
                "language": r.get("language"),
                "start_line": r.get("start_line"),
                "dependencies": ", ".join(r.get("dependencies") or []),
                "text": r.get("code", ""),
            }
        )
    return chunks


def prune_context(chunks: List[Dict[str, Any]], max_items: int = 8) -> List[Dict[str, Any]]:
    """
    Lightweight pruning using existing data only.
    Keeps top-N retrieved chunks and strips empty snippets.
    """
    cleaned = [c for c in chunks if (c.get("text") or "").strip()]
    return cleaned[:max_items]


def inject_context(user_prompt: str, chunks: List[Dict[str, Any]], max_context_tokens: Optional[int] = 1500) -> Dict[str, str]:
    """Build structured system+user prompt using PromptBuilder."""
    builder = PromptBuilder(max_context_tokens=max_context_tokens)
    system, user = builder.build(user_prompt, chunks, max_context_tokens=max_context_tokens)
    return {"system": system, "user": user}


def search_codebase(query: str, top_k: int = 5, language: Optional[str] = None, db_path: str = "./chroma_db", collection: str = "code_embeddings") -> List[Dict[str, Any]]:
    """Alias to retrieval tool for MCP compatibility."""
    return retrieve_context(query, top_k=top_k, language=language, db_path=db_path, collection=collection)


register_tool("retrieve_context", retrieve_context)
register_tool("prune_context", prune_context)
register_tool("inject_context", inject_context)
register_tool("search_codebase", search_codebase)


@app.get("/")
async def root() -> Dict[str, str]:
    return {"status": "Context Engine MCP Server running"}


def run_pipeline(req: GenerateRequest) -> Dict[str, Any]:
    """
    Pipeline:
      prompt -> retrieve_context -> prune_context -> inject_context -> OllamaAgent
    """
    try:
        context = call_tool(
            "retrieve_context",
            req.prompt,
            top_k=req.top_k,
            language=req.language,
            db_path=req.db_path,
            collection=req.collection,
        )
        pruned = call_tool("prune_context", context)
        injected = call_tool("inject_context", req.prompt, pruned, max_context_tokens=req.max_context_tokens)

        agent = OllamaAgent(model=req.model)
        output = agent.generate_response(injected["user"], system=injected["system"])
        return {
            "output": output,
            "context_chunks": len(pruned),
        }
    except Exception as exc:
        logger.exception("Pipeline failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/generate")
def generate(request: GenerateRequest) -> Dict[str, Any]:
    return run_pipeline(request)
