"""Build one index in a fresh process (the pipeline modules keep global state).

Called by harness.index_cache; not meant to be run by hand. Uses the pipeline's own
CodeIndexer unchanged. Instrumentation only wraps functions to time them.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from .common import load_toml
from .envinfo import environment_info


def _timed(timers: dict, counts: dict, name: str, fn):
    def wrapper(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            timers[name] += time.perf_counter() - t
            counts[name] += 1
    wrapper.__wrapped__ = fn
    return wrapper


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline-root", type=Path, required=True)
    ap.add_argument("--checkout", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--index-config", type=Path, required=True)
    ap.add_argument("--meta", type=Path, required=True, help="json with key parts to store in the report")
    a = ap.parse_args()
    cfg = load_toml(a.index_config)
    meta = json.loads(a.meta.read_text(encoding="utf-8"))
    checkout = a.checkout.resolve()
    out = a.out.resolve()
    t_start = time.perf_counter()

    # guards: the checkout must be pristine (no .git, no graph output that could be indexed)
    for bad in (".git", "graphify-out"):
        if (checkout / bad).exists():
            print(f"[FAIL] {bad} present in checkout {checkout}", file=sys.stderr)
            return 3

    os.environ["EMBEDDING_DEVICE"] = cfg["build"]["index_device"]
    os.environ["INDEX_MAX_FILES"] = str(cfg["build"]["max_files"])
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    for k, v in cfg["index"].items():
        os.environ[k] = str(v)
    sys.path.insert(0, str(a.pipeline_root / "backend"))

    t = time.perf_counter()
    import indexing.code_indexer as ci          # noqa: E402  (pipeline code, unchanged)
    import_s = time.perf_counter() - t

    ch = cfg["chunking"]
    assert ci.MAX_CHUNK_TOKENS == ch["max_chunk_tokens"], ci.MAX_CHUNK_TOKENS
    assert ci.INDEX_SCHEMA_VERSION == ch["index_schema_version"], ci.INDEX_SCHEMA_VERSION
    assert set(ci.EXCLUDED_DIRS) == set(ch["excluded_dirs"]), ci.EXCLUDED_DIRS
    assert ci.DEFAULT_MODEL == cfg["embedding"]["model"], ci.DEFAULT_MODEL
    assert ci.USE_HYBRID_EMBEDDINGS == (cfg["index"]["USE_HYBRID_EMBEDDINGS"] == "1")

    timers: dict = defaultdict(float)
    counts: dict = defaultdict(int)
    ci.read_and_chunk_file = _timed(timers, counts, "chunking", ci.read_and_chunk_file)
    for meth in ("_upsert_chunks", "_build_call_graph", "_save_call_graph", "_update_bm25_index"):
        setattr(ci.CodeIndexer, meth, _timed(timers, counts, meth, getattr(ci.CodeIndexer, meth)))

    out.mkdir(parents=True, exist_ok=False)
    chroma_dir = out / "chroma"
    t = time.perf_counter()
    idx = ci.CodeIndexer(db_path=str(chroma_dir))
    model_load_s = time.perf_counter() - t
    idx.model.encode = _timed(timers, counts, "encode", idx.model.encode)
    import torch
    device = str(idx.model.device)
    gpu = torch.cuda.get_device_name(0) if device.startswith("cuda") else None

    stats = idx.index_repository(checkout, max_files=cfg["build"]["max_files"])
    n_code, n_meta = idx.collection.count(), idx.meta_collection.count()
    del idx.client  # release Chroma handles before the parent reads the folder
    (chroma_dir / ".embedding_cache.json").unlink(missing_ok=True)   # derived, large, unused at query time
    (chroma_dir / ".index_cache.json").unlink(missing_ok=True)

    # Graphify runs on a throw-away copy so the checkout never contains graph output and the
    # graph JSON can never be indexed. Paths in graph.json are relative to the copy's root.
    gcfg = cfg["build"]
    tmp = out / "_graphify_src"
    t = time.perf_counter()
    shutil.copytree(checkout, tmp, symlinks=True)
    copy_s = time.perf_counter() - t
    t = time.perf_counter()
    r = subprocess.run([gcfg["graphify_command"], "update", str(tmp)], capture_output=True, text=True)
    graphify_s = time.perf_counter() - t
    gj = tmp / "graphify-out" / "graph.json"
    if r.returncode != 0 or not gj.is_file():
        print(f"[FAIL] graphify update rc={r.returncode}: {(r.stderr or r.stdout)[-500:]}", file=sys.stderr)
        return 4
    (out / "graphify-out").mkdir()
    shutil.copyfile(gj, out / "graphify-out" / "graph.json")
    shutil.rmtree(tmp)
    graph = json.loads((out / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    raw = (out / "graphify-out" / "graph.json").read_text(encoding="utf-8")
    abs_leak = [m for m in (str(checkout), str(tmp)) if m in raw]

    n_default_cap = cfg["build"]["pipeline_default_max_files"]
    report = {
        **meta,
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "index_device": device, "index_gpu": gpu,
        "env": environment_info(),
        "pipeline_constants": {"MAX_CHUNK_TOKENS": ci.MAX_CHUNK_TOKENS,
                               "INDEX_SCHEMA_VERSION": ci.INDEX_SCHEMA_VERSION,
                               "DEFAULT_MODEL": ci.DEFAULT_MODEL,
                               "USE_HYBRID_EMBEDDINGS": ci.USE_HYBRID_EMBEDDINGS,
                               "DENSE_ALPHA": ci.DENSE_ALPHA,
                               "EMBED_BATCH_SIZE": ci.DEFAULT_EMBED_BATCH_SIZE},
        "counts": {"chunks_code": n_code, "chunks_meta": n_meta,
                   "files_scanned": stats["files_scanned"], "files_indexed": stats["files_indexed"],
                   "files_beyond_pipeline_default_cap": max(0, stats["files_scanned"] - n_default_cap),
                   "graph_nodes_callgraph": stats["graph_nodes"], "graph_edges_callgraph": stats["graph_edges"],
                   "graphify_nodes": len(graph.get("nodes", [])),
                   "graphify_edges": len(graph.get("links") or graph.get("edges") or [])},
        "indexer_stats": stats,
        "graphify_abs_path_markers": abs_leak,
        "timing_s": {
            "pipeline_import": round(import_s, 3),
            "embedding_model_load": round(model_load_s, 3),
            "chunking": round(timers["chunking"], 3),
            "call_graph_build": round(timers["_build_call_graph"], 3),
            "call_graph_save": round(timers["_save_call_graph"], 3),
            "embed_and_upsert_total": round(timers["_upsert_chunks"], 3),
            "model_encode_only": round(timers["encode"], 3),
            "upsert_and_overhead": round(timers["_upsert_chunks"] - timers["encode"], 3),
            "bm25": round(timers["_update_bm25_index"], 3),
            "graphify_copy": round(copy_s, 3),
            "graphify_update": round(graphify_s, 3),
            "worker_total": round(time.perf_counter() - t_start, 3),
        },
        "call_counts": dict(counts),
    }
    (out / "build_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "timing_s": report["timing_s"], "counts": report["counts"]}))
    return 0 if not abs_leak else 5


if __name__ == "__main__":
    raise SystemExit(main())
