"""Gate sweep worker: one task, one retrieval, the GAPA call-1 gate under every sweep setting.

Why a separate process per task: the pipeline binds Chroma to one index when it is
imported, so a process can serve only one repository's index (the same reason as
``harness.gatediag_worker``).

What it does:
  1. Loads the pipeline from ``<pipeline-root>/backend`` (the checkout under test, NOT a
     frozen snapshot, so the temporary gate switches are present).
  2. Routes every Ollama call through the harness shim, so the gate runs under the same
     conditions as the Phase 2 runs (think off, num_ctx 32768, seed, temperature).
  3. Retrieves once, then calls the real ``preroute_structured_gate`` once per setting,
     changing only the two switches ``GATE_REASON_FIRST`` and ``GATE_CANDIDATE_TOPK``.
  4. Writes the verdict, reply, token counts and timing of every call to ``--out``.

Only the gate runs: no call 2, no graph expansion, no answer and no judge.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

# The sweep grid: prompt variant (0 = old order, 1 = reason first) x number of top
# chunks the gate counts distinct files over (0 = all retrieved chunks, as before).
PROMPTS = ("0", "1")
TOPKS = (0, 3, 5, 10)
SETTINGS = [(p, k) for p in PROMPTS for k in TOPKS]


def distinct_files(chunks: list[dict], k: int) -> int:
    """Distinct source files among the first ``k`` chunks (all chunks when ``k`` is 0)."""
    scoped = chunks[:k] if k > 0 else chunks
    return len({(c.get("metadata") or {}).get("file", "") for c in scoped})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--pipeline-root", type=Path, required=True, help="checkout whose backend/ is loaded")
    ap.add_argument("--query-file", type=Path, required=True, help="text file holding the task's question")
    ap.add_argument("--experiment", type=Path, required=True, help="experiment.toml (shim options)")
    ap.add_argument("--out", type=Path, required=True, help="where to write the JSON result")
    a = ap.parse_args()

    # Make every path absolute BEFORE the chdir below, or relative paths would break.
    a.query_file, a.experiment, a.out = a.query_file.resolve(), a.experiment.resolve(), a.out.resolve()
    root = a.pipeline_root.resolve()
    sys.path.insert(0, str(root / "backend"))     # pipeline modules (diamond, retrieval, ...)
    os.chdir(root)

    # Imported after the path is set. The harness package comes from PYTHONPATH.
    from harness.common import load_toml
    from harness.ollama_shim import OllamaShim, Recorder, role

    recorder = Recorder()
    shim = OllamaShim(load_toml(a.experiment), recorder)
    shim.install()                                # every Ollama request now goes through the shim
    try:
        import diamond.preroute_gate as pg
        from retrieval.retrieval_tool import retrieve_context

        query = a.query_file.read_text(encoding="utf-8")
        context = retrieve_context(query)         # one retrieval, reused for every setting

        rows = []
        for prompt, topk in SETTINGS:
            # The gate reads these two switches on every call (see preroute_gate.py).
            os.environ["GATE_REASON_FIRST"] = prompt
            os.environ["GATE_CANDIDATE_TOPK"] = str(topk)
            seen = len(recorder.calls)
            with role("gapa_gate_call1"):
                # A copy, so no setting can change the chunks the next one sees.
                result = pg.preroute_structured_gate(query, copy.deepcopy(context))
            call = recorder.calls[-1] if len(recorder.calls) > seen else {}
            rows.append({
                "reason_first": prompt == "1",
                "gate_topk": topk,
                "candidate_files_shown": distinct_files(context, topk),
                # The gate's own answer: sufficient, confidence, reason, route, stage, token_usage.
                # stage "preroute_structured_error" means the call failed or the reply did not parse.
                "result": result,
                "reply": call.get("reply"),
                "prompt_tokens": call.get("prompt_eval_count"),
                "completion_tokens": call.get("eval_count"),
                "wall_ms": call.get("wall_ms"),
                "call_error": call.get("error"),
            })
    finally:
        shim.uninstall()

    out = {
        "n_chunks": len(context),
        "candidate_files_all": distinct_files(context, 0),
        "settings": rows,
    }
    a.out.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())