"""Run server.run_pipeline for a batch of cases against a fake Ollama server (test helper).

Executed as a subprocess so each pipeline snapshot gets its own interpreter and environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


def ctl(port: int, path: str, obj: dict | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(obj or {}).encode(), method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get_log(port: int):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/__log") as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline-root", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fake-port", type=int, required=True)
    ap.add_argument("--instrument", action="store_true")
    ap.add_argument("--experiment", type=Path, required=True)
    a = ap.parse_args()
    root = a.pipeline_root.resolve()
    sys.path.insert(0, str(root / "backend"))
    os.chdir(root)

    from harness.common import load_toml
    from harness.ollama_shim import OllamaShim, Recorder, redirect_only
    redirect = {"localhost:11434": f"127.0.0.1:{a.fake_port}", "127.0.0.1:11434": f"127.0.0.1:{a.fake_port}"}
    rec = Recorder()
    if a.instrument:
        OllamaShim(load_toml(a.experiment), rec, redirect).install()
    else:
        redirect_only(redirect)

    import mcp_server.server as server
    ins = None
    if a.instrument:
        from harness.instrument import Instrument
        ins = Instrument(rec)
        ins.install(server)

    out = []
    for case in json.loads(a.cases.read_text(encoding="utf-8")):
        ctl(a.fake_port, "/__reset")
        ctl(a.fake_port, "/__scenario", case.get("scenario", {}))
        hidden = None
        if case.get("hide_call_graph"):
            hidden = Path(server.__file__).resolve().parents[1] / "chroma_db" / "call_graph.json"
            hidden.rename(hidden.with_suffix(".hidden"))
        if ins:
            ins.begin_task()
        try:
            result = server.run_pipeline(case["prompt"], mode=case["mode"], pruning_method="heuristic")
            err = None
        except Exception as exc:                       # recorded, not raised: some tests expect failures
            result, err = None, repr(exc)
        record = ins.end_task(result) if ins else None
        if hidden is not None:
            hidden.with_suffix(".hidden").rename(hidden)
        out.append({"id": case["id"], "result": result, "error": err, "fake_log": get_log(a.fake_port),
                    "record": record})
    a.out.write_text(json.dumps(out, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
