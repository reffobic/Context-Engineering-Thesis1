import json
import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

try:
    from tool_registry import call_tool
    from indexing.code_indexer_endpoint import get_index_summary
    from pruning.file import (
        prune_context as prune_context_with_stats,
        prune_context_graph_aware,
        estimate_tokens,
    )
    from diamond.sufficiency_assessor import assess_sufficiency, assess_structure_vs_context
except ModuleNotFoundError:  # when running as package: python -m uvicorn mcp_server.server:app
    from mcp_server.tool_registry import call_tool
    from indexing.code_indexer_endpoint import get_index_summary
    from pruning.file import (
        prune_context as prune_context_with_stats,
        prune_context_graph_aware,
        estimate_tokens,
    )
    from diamond.sufficiency_assessor import assess_sufficiency, assess_structure_vs_context

app = FastAPI()
# Lets the UI work from file:// or another port during local demos (not for public deployment).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).resolve().parent / "static"
# Repo root: backend/mcp_server/server.py → parent → backend → project root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _persist_eval_metrics(
    metrics: dict[str, Any],
    *,
    retrieval_mode: Literal["hybrid", "vector", "graph"],
) -> tuple[Optional[str], Optional[str]]:
    """
    Write evaluate payload to ``results/{retrieval_mode}/json/eval-{timestamp}-{id}.json``.
    Returns ``(relative_posix_path, error_message)``.
    """
    try:
        safe_mode = retrieval_mode if retrieval_mode in ("hybrid", "vector", "graph") else "hybrid"
        out_dir = _REPO_ROOT / "results" / safe_mode / "json"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fname = f"eval-{ts}-{uuid.uuid4().hex[:8]}.json"
        rel = f"results/{safe_mode}/json/{fname}"
        saved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        blob = {
            **metrics,
            "metrics_json_path": rel,
            "metrics_saved_at_utc": saved_at,
            "retrieval_mode": safe_mode,
        }
        path = out_dir / fname
        with path.open("w", encoding="utf-8") as fh:
            json.dump(blob, fh, indent=2, ensure_ascii=False, default=str)
        logging.info("evaluate: wrote metrics JSON %s", rel)
        return rel, None
    except OSError as exc:
        logging.exception("evaluate: failed to write metrics JSON")
        return None, str(exc)


# class GenerateRequest(BaseModel):
#     """JSON body for POST /generate (matches `run_demo.py` and `requests.post(..., json={...})`)."""

#     prompt: str

class GenerateRequest(BaseModel):
    prompt: str
    mode: Literal["RAG", "LC", "SELF_ROUTE", "GASR"] = "SELF_ROUTE"

class EvaluateRequest(BaseModel):
    """
    Lightweight eval: retrieve + prune, structural stats, optional label-based P/R,
    optional DeepEval contextual relevancy on retrieval, optional inject+generate+faithfulness.
    """

    prompt: str
    top_k: int = 10
    token_budget: int = 1800
    relevant_files: Optional[List[str]] = None
    judge_retrieval_relevancy: bool = False
    faithfulness: bool = False
    #: Ablation answer path: if set, also run full ``run_pipeline`` with this mode.
    generate_mode: Optional[Literal["RAG", "LC", "SELF_ROUTE", "GASR"]] = None
    #: Where to store metrics JSON (``results/{mode}/json/``). Retrieval path in code may still be hybrid until vector/graph backends exist.
    retrieval_mode: Literal["hybrid", "vector", "graph"] = "hybrid"
    #: If true (default), persist full evaluate response under ``results/{retrieval_mode}/json/``.
    save_metrics_json: bool = True


def _ensure_backend_on_path() -> None:
    import sys

    backend = Path(__file__).resolve().parent.parent
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


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


@app.get("/graphify-view")
async def graphify_view_page():
    """Serve Graphify HTML (or setup instructions) from static/graphify_view.html."""
    try:
        call_tool("visualize_graphify")
    except Exception as exc:
        logging.warning("graphify-view: visualize_graphify failed: %s", exc)
    page = _STATIC / "graphify_view.html"
    if not page.is_file():
        return JSONResponse(
            {"error": "Graphify view not found", "hint": "Run graphify update . --force; open /graphify-view"},
            status_code=404,
        )
    return FileResponse(
        page,
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
    diagnostics = {}
    for item in items:
        if not diagnostics and item.get("retrieval_diagnostics"):
            diagnostics = item.get("retrieval_diagnostics") or {}
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
                "dense_score": item.get("dense_score"),
                "bm25_score": item.get("bm25_score"),
                "exact_match_score": item.get("exact_match_score"),
                "source_score": item.get("source_score"),
                "path_match_score": item.get("path_match_score"),
                "hybrid_score": item.get("hybrid_score"),
                "rerank_score": item.get("rerank_score"),
                "final_score": item.get("final_score"),
                "candidate_sources": item.get("candidate_sources", []),
                "preview": _preview_text(item.get("code")),
                "parent_context_chars": len(item.get("parent_context") or ""),
            }
        )
    return {
        "candidate_chunks": len(items),
        "unique_files": len(files),
        "files": files,
        "top_chunks": top_chunks,
        "diagnostics": diagnostics,
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
def run_pipeline(
    user_prompt: str,
    mode: str = "SELF_ROUTE",
) -> dict:
    import os

    token_budget = 1800
    gasr_hard_cap = int(os.environ.get("GASR_HARD_CAP", "2"))
    pipeline = {
        "prompt": user_prompt,
        "mode": mode,
        "stages": {},
        "stage_order": ["retrieve", "prune", "assess", "inject", "generate"],
        "tokens_injected_est": 0,
        "route": None,
        "hops_used": 0,
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
    (pruned_context, pruning_stats), duration_ms = _time_stage(
        lambda: prune_context_with_stats(user_prompt, context, token_budget=token_budget)
    )
    pipeline["stages"]["prune"] = _stage(
        "Pruning",
        duration_ms,
        _pruning_summary(pruned_context, pruning_stats, token_budget),
        {"chunks": pruned_context, "stats": asdict(pruning_stats)},
    )

    def _build_lc_chunks(raw_retrieved: list[dict]) -> list[dict]:
        """Convert retrieved chunks to ContextChunk format using parent_context."""
        lc_chunks = []
        for item in raw_retrieved:
            pc = item.get("parent_context") or item.get("code") or ""
            if not pc.strip():
                continue
            meta = item.get("metadata") or {}
            lc_chunks.append({
                "text":     pc,
                "path":     meta.get("file") or "",
                "symbol":   meta.get("function") or "",
                "language": meta.get("language") or "",
            })
        return lc_chunks

    def _injection_token_est(chunks: list[dict]) -> int:
        total = 0
        for c in chunks:
            total += estimate_tokens(c.get("text") or c.get("code") or "")
        return total

    def _to_retrieve_shaped(chunks: list[dict], source: str = "seed") -> list[dict]:
        """Map injection ContextChunks back to retrieve-shaped dicts for expand."""
        out = []
        for c in chunks:
            if c.get("metadata") and c.get("code") is not None:
                item = dict(c)
                item.setdefault("source", source)
                out.append(item)
                continue
            out.append({
                "code": c.get("text") or c.get("code") or "",
                "metadata": {
                    "file": c.get("path") or "",
                    "function": c.get("symbol") or "",
                    "language": c.get("language") or "",
                    "start_line": c.get("start_line") or 0,
                },
                "final_score": float(c.get("final_score") or c.get("pruning_score") or 0.5),
                "hybrid_score": float(c.get("hybrid_score") or 0.5),
                "source": source,
            })
        return out

    context_for_injection: list[dict] = pruned_context
    route = "RAG"

    if mode == "SELF_ROUTE":
        logging.info("Sufficiency assessment started")
        decision, duration_ms = _time_stage(
            lambda: assess_sufficiency(user_prompt, pruned_context)
        )
        pipeline["stages"]["assess"] = _stage(
            "Sufficiency assessment",
            duration_ms,
            {
                "route":      decision["route"],
                "sufficient": decision["sufficient"],
                "confidence": decision["confidence"],
                "reason":     decision["reason"],
                "assessor_model": os.environ.get("ASSESSOR_MODEL")
                or os.environ.get("OLLAMA_ASSESSOR_MODEL")
                or "",
            },
        )
        logging.info(
            "Route decision: %s (confidence=%s)", decision["route"], decision["confidence"]
        )
        if decision["route"] == "LC":
            logging.info("Escalating to LC — using parent_context")
            context_for_injection = _build_lc_chunks(context)
            route = "LC"
        else:
            logging.info("Staying on RAG path — using pruned_context")
            context_for_injection = pruned_context
            route = "RAG"

    elif mode == "GASR":
        logging.info("GASR mode — sufficiency then structure-vs-context (hop cap=%s)", gasr_hard_cap)
        pipeline["stage_order"] = [
            "retrieve", "prune", "assess", "graph_expand", "re_prune", "inject", "generate"
        ]
        from diamond.gasr_ladder import run_gasr_ladder
        from retrieval.graph_expand import graph_expand

        seed_pool = list(context)
        hops_box = {"n": 0}

        def _assess_rag():
            decision, ms = _time_stage(
                lambda: assess_sufficiency(user_prompt, pruned_context)
            )
            decision = {**decision, "duration_ms": ms}
            return decision

        def _assess_structure():
            # Use latest pruned context from closure via nonlocal list
            judge, ms = _time_stage(
                lambda: assess_structure_vs_context(user_prompt, state["pruned"])
            )
            return {**judge, "duration_ms": ms}

        def _expand(seeds, hops):
            result, ms = _time_stage(lambda: graph_expand(seeds, hops=hops))
            result = dict(result)
            result["duration_ms"] = ms
            hops_box["n"] += 1
            return result

        # Graph-aware re-prune after expand: reserve budget for neighbors, then seeds.
        def _re_prune(seeds, neighbors):
            (pruned, stats), p_ms = _time_stage(
                lambda: prune_context_graph_aware(
                    user_prompt,
                    list(seeds),
                    list(neighbors),
                    token_budget=token_budget,
                )
            )
            state["pruned"] = pruned
            state["last_re_prune_ms"] = p_ms
            state["last_re_prune_kept"] = stats.kept_chunks
            return pruned

        def _assess_graph():
            decision, ms = _time_stage(
                lambda: assess_sufficiency(
                    user_prompt, state["pruned"], phase="graph"
                )
            )
            return {**decision, "duration_ms": ms}

        state = {
            "pruned": pruned_context,
            "last_re_prune_ms": 0,
            "last_re_prune_kept": 0,
        }

        ladder = run_gasr_ladder(
            seed_pool=seed_pool,
            initial_pruned=pruned_context,
            assess_rag=_assess_rag,
            assess_structure=_assess_structure,
            expand_hop=_expand,
            re_prune=_re_prune,
            assess_after_expand=_assess_graph,
            build_lc=lambda: _build_lc_chunks(context),
            hard_cap=gasr_hard_cap,
        )
        route = ladder.route
        hops_used = ladder.hops_used
        context_for_injection = ladder.context

        for i, exp in enumerate(ladder.expand_trace):
            if i == len(ladder.expand_trace) - 1 and state["last_re_prune_ms"]:
                exp["re_prune_ms"] = state["last_re_prune_ms"]
                exp["re_prune_kept"] = state["last_re_prune_kept"]
                exp["re_prune_kind"] = "graph_aware"

        pipeline["stages"]["assess"] = _stage(
            "GASR assessment",
            sum(t.get("duration_ms", 0) for t in ladder.assess_trace),
            {
                "route": route,
                "hops_used": hops_used,
                "gasr_hard_cap": gasr_hard_cap,
                "re_prune_kind": "graph_aware",
                "assessor_model": os.environ.get("ASSESSOR_MODEL")
                or os.environ.get("OLLAMA_ASSESSOR_MODEL")
                or "",
                "trace": ladder.assess_trace,
            },
        )
        if ladder.expand_trace:
            pipeline["stages"]["graph_expand"] = _stage(
                "Graph expand",
                sum(t.get("duration_ms", 0) for t in ladder.expand_trace),
                {"hops_used": hops_used, "expansions": ladder.expand_trace},
                ladder.expand_trace,
            )
            pipeline["stages"]["re_prune"] = _stage(
                "Graph-aware re-prune",
                sum(t.get("re_prune_ms", 0) for t in ladder.expand_trace),
                {
                    "token_budget": token_budget,
                    "re_prune_kind": "graph_aware",
                    "kept": len(context_for_injection)
                    if hasattr(context_for_injection, "__len__")
                    else None,
                },
            )
        pipeline["hops_used"] = hops_used
        logging.info("GASR finished route=%s hops=%s", route, hops_used)

    elif mode == "LC":
        logging.info("LC mode forced — using parent_context, skipping assessment")
        pipeline["stages"]["assess"] = _stage(
            "Sufficiency assessment", 0,
            {
                "route":      "LC",
                "sufficient": False,
                "confidence": "high",
                "reason":     "LC mode forced — assessment skipped.",
            },
        )
        context_for_injection = _build_lc_chunks(context)
        route = "LC"

    else:  # RAG
        logging.info("RAG mode forced — using pruned_context, skipping assessment")
        pipeline["stages"]["assess"] = _stage(
            "Sufficiency assessment", 0,
            {
                "route":      "RAG",
                "sufficient": True,
                "confidence": "high",
                "reason":     "RAG mode forced — assessment skipped.",
            },
        )
        context_for_injection = pruned_context
        route = "RAG"

    pipeline["route"] = route
    pipeline["tokens_injected_est"] = _injection_token_est(context_for_injection)
    # ─────────────────────────────────────────────────────────────────────────

    logging.info("Injection started (route=%s tokens_est=%s)", route, pipeline["tokens_injected_est"])
    final_prompt, duration_ms = _time_stage(
        lambda: call_tool("inject_context", user_prompt, context_for_injection)
    )
    inject_chunks = (
        list(context_for_injection)
        if isinstance(context_for_injection, list)
        else []
    )
    pipeline["stages"]["inject"] = _stage(
        "Build final prompt",
        duration_ms,
        {
            **_prompt_summary(final_prompt),
            "route": route,
            "tokens_injected_est": pipeline["tokens_injected_est"],
            "chunk_count": len(inject_chunks),
        },
        {"prompt": final_prompt, "chunks": inject_chunks},
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
    return {"output": result, "pipeline": pipeline, "route": route, "tokens_injected_est": pipeline["tokens_injected_est"]}


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
    include_dirs: Optional[List[str]] = Query(
        None,
        description="Optional repo-relative directories to scan, e.g. superset or superset-core/src.",
    ),
    max_files: Optional[int] = Query(
        600,
        ge=1,
        le=10000,
        description="Limit files per indexing run to avoid CPU spikes. Repeat indexing to continue.",
    ),
    rebuild_bm25: bool = Query(
        False,
        description="Rebuild BM25 from the whole Chroma collection. Slower; normally leave false.",
    ),
):
    """
    One-time / batch indexing into ChromaDB (not part of /generate pipeline).

    Query params: ``repo_path`` (optional), ``languages`` (optional).
    """
    logging.info("Indexing started")
    base_dir = Path(__file__).resolve().parent.parent  # backend/
    resolved = repo_path if repo_path else str(base_dir / "flask_test")
    result = call_tool(
        "index_codebase",
        repo_path=resolved,
        languages=languages,
        include_dirs=include_dirs,
        max_files=max_files,
        rebuild_bm25=rebuild_bm25,
    )
    result = {
        **result,
        "repo_path": resolved,
        "index_summary": get_index_summary(),
    }
    logging.info("Indexing complete: %s", result)
    return result

@app.post("/generate")
def generate(req: GenerateRequest):
    return run_pipeline(
        req.prompt,
        mode=req.mode,
    )


@app.post("/evaluate")
def evaluate(req: EvaluateRequest) -> dict[str, Any]:
    """
    Runs inject+generate only when ``faithfulness`` is true (then scores answer vs pruned CONTEXT).
    Install ``deepeval`` and PyPI ``ollama`` for judge metrics (see ``backend/context_metrics/requirements.txt``).
    """
    _ensure_backend_on_path()
    from context_metrics.run_pipeline_metrics import (
        chunks_as_retrieval_context,
        measure_contextual_relevancy_only,
        measure_faithfulness_only,
        precision_recall_at_k,
        structural_pipeline_stats,
    )

    retrieved = call_tool("retrieve_context", req.prompt, req.top_k)
    pruned, _stats = prune_context_with_stats(
        req.prompt, retrieved, token_budget=req.token_budget
    )
    retrieve_ctx = chunks_as_retrieval_context(retrieved)

    gold = [x.strip() for x in (req.relevant_files or []) if x and str(x).strip()]

    out: dict[str, Any] = {
        "prompt": req.prompt,
        "top_k": req.top_k,
        "token_budget": req.token_budget,
        "pipeline_counts": {
            "retrieved_chunks": len(retrieved),
            "pruned_chunks": len(pruned),
        },
        "structural": structural_pipeline_stats(retrieved, pruned, req.top_k),
    }

    if gold:
        pr = precision_recall_at_k(retrieved, req.top_k, gold)
        pr["stage"] = "after_retrieval"
        out["precision_at_k_retrieval"] = pr
        if len(pruned) > 0:
            pp = precision_recall_at_k(pruned, len(pruned), gold)
            pp["stage"] = "after_prune"
            pp["note"] = (
                "k equals len(pruned); recall counts distinct gold files still present after pruning."
            )
            out["precision_at_k_pruned"] = pp
        else:
            out["precision_at_k_pruned"] = {
                "skipped": True,
                "reason": "No chunks after pruning.",
                "stage": "after_prune",
            }

    if req.judge_retrieval_relevancy:
        try:
            out["contextual_relevancy_retrieval"] = measure_contextual_relevancy_only(
                req.prompt, retrieve_ctx
            )
        except Exception as exc:  # pragma: no cover - missing deepeval / Ollama
            logging.exception("evaluate: judge_retrieval_relevancy failed")
            out["contextual_relevancy_retrieval"] = {"error": str(exc)}

    if req.faithfulness:
        try:
            final_prompt = call_tool("inject_context", req.prompt, pruned)
            answer = call_tool("ollama_generate", final_prompt)
            if not isinstance(answer, str):
                answer = str(answer)
            out["generation_preview"] = _preview_text(answer, 480)
            out["faithfulness"] = measure_faithfulness_only(req.prompt, pruned, answer)
        except Exception as exc:  # pragma: no cover
            logging.exception("evaluate: faithfulness path failed")
            out["faithfulness"] = {"error": str(exc)}

    if req.generate_mode:
        try:
            gen = run_pipeline(req.prompt, mode=req.generate_mode)
            out["ablation_generate"] = {
                "mode": req.generate_mode,
                "route": gen.get("route"),
                "tokens_injected_est": gen.get("tokens_injected_est"),
                "hops_used": (gen.get("pipeline") or {}).get("hops_used"),
                "output_preview": _preview_text(gen.get("output"), 480),
            }
        except Exception as exc:
            logging.exception("evaluate: generate_mode=%s failed", req.generate_mode)
            out["ablation_generate"] = {"mode": req.generate_mode, "error": str(exc)}

    if req.save_metrics_json:
        rel, err = _persist_eval_metrics(out, retrieval_mode=req.retrieval_mode)
        if rel:
            out["metrics_json_path"] = rel
        if err:
            out["metrics_json_error"] = err

    return out