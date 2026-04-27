import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, List, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

try:
    from tool_registry import call_tool
    from indexing.code_indexer_endpoint import get_index_summary
    from pruning.file import prune_context as prune_context_with_stats
except ModuleNotFoundError:  # when running as package: python -m uvicorn mcp_server.server:app
    from mcp_server.tool_registry import call_tool
    from indexing.code_indexer_endpoint import get_index_summary
    from pruning.file import prune_context as prune_context_with_stats

app = FastAPI()
# Lets the UI work from file:// or another port during local demos (not for public deployment).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).resolve().parent / "static"


class GenerateRequest(BaseModel):
    """JSON body for POST /generate (matches `run_demo.py` and `requests.post(..., json={...})`)."""

    prompt: str

logging.basicConfig(level=logging.INFO)

# Confirm server is running
@app.get("/")
async def root():
    logging.info("Context Engine is running")
    return {"message": "Context Engine Running", "ui": "/ui"}


@app.get("/ui")
async def ui_page():
    """Simple browser UI for /generate and /index (same origin — no CORS needed)."""
    index = _STATIC / "index.html"
    if not index.is_file():
        return JSONResponse(
            {"error": "UI not found", "path": str(index)},
            status_code=404,
        )
    return FileResponse(
        index,
        media_type="text/html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _preview_text(value: Any, limit: int = 360) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _estimate_tokens(value: str) -> int:
    return max(1, len(value) // 4) if value else 0


def _time_stage(action: Callable[[], Any]) -> tuple[Any, int]:
    started = time.perf_counter()
    result = action()
    return result, round((time.perf_counter() - started) * 1000)


def _retrieval_summary(items: list[dict]) -> dict:
    files = []
    seen = set()
    top_chunks = []
    for item in items:
        meta = item.get("metadata", {}) or {}
        file_path = meta.get("file") or "(unknown path)"
        if file_path not in seen:
            seen.add(file_path)
            files.append(file_path)
        top_chunks.append(
            {
                "file": file_path,
                "symbol": meta.get("function") or "",
                "language": meta.get("language") or "",
                "chunk_type": meta.get("chunk_type") or "",
                "start_line": meta.get("start_line"),
                "end_line": meta.get("end_line"),
                "similarity": item.get("similarity"),
                "hybrid_score": item.get("hybrid_score"),
                "final_score": item.get("final_score"),
                "path_prior": item.get("path_prior"),
                "preview": _preview_text(item.get("code")),
                "parent_context_chars": len(item.get("parent_context") or ""),
            }
        )
    return {
        "candidate_chunks": len(items),
        "unique_files": len(files),
        "files": files,
        "top_chunks": top_chunks,
    }


def _pruning_summary(chunks: list[dict], stats: Any, token_budget: int) -> dict:
    stats_dict = asdict(stats)
    kept_tokens = stats_dict.get("kept_est_tokens", 0)
    dropped_chunks = stats_dict.get("dropped_chunks", 0)
    if dropped_chunks > 0:
        reason = "Pruning removed lower-priority chunks to stay within token budget."
    elif kept_tokens < token_budget:
        reason = "No chunks dropped: selected context is already below token budget."
    else:
        reason = "No chunks dropped after scoring and cleanup."
    return {
        "stats": stats_dict,
        "token_budget": token_budget,
        "decision_reason": reason,
        "kept_chunks": [
            {
                "file": chunk.get("path") or "(unknown path)",
                "symbol": chunk.get("symbol") or "",
                "language": chunk.get("language") or "",
                "start_line": chunk.get("start_line"),
                "estimated_tokens": _estimate_tokens(chunk.get("text") or ""),
                "preview": _preview_text(chunk.get("text")),
            }
            for chunk in chunks
        ],
    }


def _prompt_summary(prompt: dict[str, str]) -> dict:
    system = prompt.get("system", "") or ""
    user = prompt.get("user", "") or ""
    return {
        "system_chars": len(system),
        "user_chars": len(user),
        "total_chars": len(system) + len(user),
        "estimated_tokens": _estimate_tokens(system) + _estimate_tokens(user),
        "system_preview": _preview_text(system),
        "user_preview": _preview_text(user),
    }


def _stage(name: str, duration_ms: int, summary: dict, raw: Any = None) -> dict:
    payload = {
        "name": name,
        "status": "complete",
        "duration_ms": duration_ms,
        "summary": summary,
    }
    if raw is not None:
        payload["raw"] = raw
    return payload


# Current pipeline
def run_pipeline(user_prompt: str) -> dict:
    token_budget = 1800
    pipeline = {
        "prompt": user_prompt,
        "stages": {},
        "stage_order": ["retrieve", "prune", "inject", "generate"],
    }

    logging.info("Retrieval started")
    context, duration_ms = _time_stage(lambda: call_tool("retrieve_context", user_prompt))
    pipeline["stages"]["retrieve"] = _stage(
        "Retrieve context",
        duration_ms,
        _retrieval_summary(context),
        context,
    )

    logging.info("Pruning started")
    # P4 expects (query, retrieved_items). Use the core function here so the
    # dashboard can show the same pruning stats used to choose the final chunks.
    (pruned_context, pruning_stats), duration_ms = _time_stage(
        lambda: prune_context_with_stats(user_prompt, context, token_budget=token_budget)
    )
    pipeline["stages"]["prune"] = _stage(
        "Pruning",
        duration_ms,
        _pruning_summary(pruned_context, pruning_stats, token_budget),
        {"chunks": pruned_context, "stats": asdict(pruning_stats)},
    )

    logging.info("Injection started")
    final_prompt, duration_ms = _time_stage(
        lambda: call_tool("inject_context", user_prompt, pruned_context)
    )
    pipeline["stages"]["inject"] = _stage(
        "Build final prompt",
        duration_ms,
        _prompt_summary(final_prompt),
        final_prompt,
    )

    logging.info("Generating response")
    result, duration_ms = _time_stage(lambda: call_tool("ollama_generate", final_prompt))
    pipeline["stages"]["generate"] = _stage(
        "Generate answer",
        duration_ms,
        {
            "output_type": type(result).__name__,
            "output_chars": len(result) if isinstance(result, str) else len(str(result)),
            "preview": _preview_text(result),
        },
        result,
    )
    return {"output": result, "pipeline": pipeline}


# Add API endpoints
@app.get("/index/summary")
def index_summary(file_limit: int = Query(500, ge=1, le=5000)):
    """Show indexed files and chunk coverage for the dashboard."""
    return get_index_summary(file_limit=file_limit)


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
    result = {
        **result,
        "repo_path": resolved,
        "index_summary": get_index_summary(),
    }
    logging.info("Indexing complete: %s", result)
    return result

@app.post("/generate")
def generate(req: GenerateRequest):
    return run_pipeline(req.prompt)