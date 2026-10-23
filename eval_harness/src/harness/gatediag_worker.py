"""GAPA entry-gate diagnosis for one task (one process: the pipeline binds Chroma at import).

Runs the pipeline's own retrieve_context and preroute_structured_gate through the transport shim, then repeats
the same request directly against Ollama without the shim, and sends a few counterfactual versions of the same
request (one input line removed) to see which input line decides the verdict. The pipeline's prompts and gate
code are not changed; the counterfactuals are direct calls made by this script only.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline-root", type=Path, required=True)
    ap.add_argument("--query-file", type=Path, required=True)
    ap.add_argument("--experiment", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    root = a.pipeline_root.resolve()
    sys.path.insert(0, str(root / "backend"))
    os.chdir(root)
    from harness.common import load_toml
    from harness.ollama_shim import OllamaShim, Recorder, role
    exp = load_toml(a.experiment)
    rec = Recorder()
    shim = OllamaShim(exp, rec)
    shim.install()
    import diamond.preroute_gate as pg
    from retrieval.retrieval_tool import retrieve_context
    import requests

    query = a.query_file.read_text(encoding="utf-8")
    context = retrieve_context(query)
    with role("gapa_gate_call1"):
        res = pg.preroute_structured_gate(query, context)
    call = next(c for c in rec.calls if c["role"] == "gapa_gate_call1")
    top = context[0] if context else {}
    meta = top.get("metadata") or {}
    scores = [pg._score(c) for c in context]
    out: dict = {
        "query_head": query[:200], "model": a.model,
        "metadata": {"n_chunks": len(context), "top_final_score": top.get("final_score"), "top_hybrid_score": top.get("hybrid_score"),
                     "top_similarity": top.get("similarity"), "top_score_used": scores[0] if scores else None,
                     "second_score_used": scores[1] if len(scores) > 1 else None,
                     "sim_code": top.get("sim_code"), "sim_meta": top.get("sim_meta"), "sim_gap": top.get("sim_gap"),
                     "candidate_files": len({(c.get("metadata") or {}).get("file", "") for c in context}),
                     "n_dependencies": len(pg._parse_dependencies(meta)), "chunk_type": meta.get("chunk_type"),
                     "has_context_header": bool(meta.get("context_header")), "file": meta.get("file")},
        "pipeline_decision": {k: res.get(k) for k in ("sufficient", "confidence", "reason", "route", "stage")},
        "shim_call": {k: call.get(k) for k in ("request", "reply", "prompt_eval_count", "eval_count", "num_ctx", "shim_changed",
                                               "think_sent", "done_reason", "wall_ms", "error")},
    }
    shim.uninstall()                                            # everything below bypasses the shim
    url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
    req = call["request"]

    def direct(body: dict) -> dict:
        t = time.perf_counter()
        r = requests.post(url, json=body, timeout=300)
        j = r.json()
        raw = (j.get("message") or {}).get("content", "")
        try:
            parsed = pg._parse_generative_response(raw)
            verdict, err = parsed.get("sufficient"), None
        except Exception as exc:
            verdict, err = None, repr(exc)[:120]
        return {"reply": raw[:4000], "thinking_chars": len((j.get("message") or {}).get("thinking") or ""),
                "prompt_eval_count": j.get("prompt_eval_count"), "eval_count": j.get("eval_count"),
                "sufficient": verdict, "parse_error": err, "wall_ms": round((time.perf_counter() - t) * 1000, 1)}

    same = {"model": req["model"], "messages": req["messages"], "stream": False, "think": False,
            "keep_alive": req["keep_alive"], "options": req["options"]}
    out["direct_same_as_shim"] = direct(same)
    orig = copy.deepcopy(same)                                  # what the pipeline sends when nothing intercepts it
    orig.pop("think")
    orig["options"] = {"temperature": 0.0, "num_ctx": 2048}
    out["direct_pipeline_original"] = direct(orig)

    user = next(m["content"] for m in req["messages"] if m["role"] == "user")
    def variant(drop_prefixes: tuple[str, ...]) -> dict:
        text = "\n".join(l for l in user.split("\n") if not l.startswith(drop_prefixes))
        msgs = [m if m["role"] != "user" else {**m, "content": text} for m in req["messages"]]
        return direct({**same, "messages": msgs})
    out["probe_no_dependencies"] = variant(("dependencies:",))
    out["probe_no_candidate_files"] = variant(("candidate_files:",))
    out["probe_no_scores"] = variant(("top_score:", "score_margin:", "candidate_files:"))
    out["probe_no_sim_fields"] = variant(("sim_code:", "sim_meta:", "sim_gap:"))
    out["probe_no_dependencies_no_candidate_files"] = variant(("dependencies:", "candidate_files:"))
    a.out.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
