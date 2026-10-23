"""One task in its own process (the pipeline binds Chroma and models at import time).

Started by harness.run with a pinned environment. Writes <out> (JSON) and, before running the task,
<ready> so the parent can time subprocess start-up separately from task latency.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


def main() -> int:
    t_boot = time.perf_counter()
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(spec["pipeline_root"]).resolve()
    sys.path.insert(0, str(root / "backend"))
    os.chdir(root)

    from harness.common import load_toml
    from harness.instrument import Instrument, _item
    from harness.ollama_shim import OllamaShim, Recorder
    from harness.pipelineenv import install_chroma_guard

    install_chroma_guard([Path(p) for p in spec["forbid"]])
    rec = Recorder()
    redirect = {}
    if os.environ.get("HARNESS_OLLAMA_REDIRECT_PORT"):                    # tests only
        tgt = f"127.0.0.1:{os.environ['HARNESS_OLLAMA_REDIRECT_PORT']}"
        redirect = {"localhost:11434": tgt, "127.0.0.1:11434": tgt}
    OllamaShim(load_toml(Path(spec["experiment"])), rec, redirect).install()

    import mcp_server.server as server
    ins = Instrument(rec, expected_structure_rerank=spec["expect_structure_rerank"])
    ins.install(server)
    Path(spec["ready"]).write_text(json.dumps({"startup_s": round(time.perf_counter() - t_boot, 3), "pid": os.getpid(),
                                                "t": time.time()}), encoding="utf-8")

    out: dict = {"status": "ok", "error": None}
    result = None
    ins.begin_task()
    try:
        if spec["arm_impl"] == "harness":
            from harness.arms import run_always_graph
            result = run_always_graph(server, spec["prompt"], spec["hops"], spec["pruning_method"])
        else:
            result = server.run_pipeline(spec["prompt"], mode=spec["mode"], pruning_method=spec["pruning_method"])
    except Exception:
        out = {"status": "error", "error": traceback.format_exc()[-3000:]}
    record = ins.end_task(result)
    pipe = (result or {}).get("pipeline") or {}
    chunks = (((pipe.get("stages") or {}).get("inject") or {}).get("raw") or {}).get("chunks") or []
    out.update({
        "route": (result or {}).get("route"), "hops_used": pipe.get("hops_used"),
        "answer": (result or {}).get("output"), "tokens_injected_est": (result or {}).get("tokens_injected_est"),
        "record": record, "worker_startup_s": json.loads(Path(spec["ready"]).read_text())["startup_s"],
        "lists_raw": {
            "ranked_candidates": record["retrieval"]["ranked_candidates"], "retrieved": record["retrieval"]["retrieved"],
            "seeds": record["seed_pool"]["seeds"] if record["seed_pool"] else None,
            "neighbors": [e["neighbors"] for e in record["expansions"]],
            "pruned": record["pruned"], "re_pruned_last": record["re_pruned"][-1] if record["re_pruned"] else None,
            "final_context": [_item(c) for c in chunks]},
    })
    for k in ("ranked_candidates", "retrieved"):
        record["retrieval"].pop(k, None)
    record.pop("pruned", None)
    record.pop("re_pruned", None)
    if record.get("seed_pool"):
        record["seed_pool"].pop("seeds", None)
    for e in record["expansions"]:
        e.pop("neighbors", None)
    Path(spec["out"]).write_text(json.dumps(out, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
