"""Do Graphify paths match index chunk paths exactly? (the GRAPHIFY_PATH_PREFIX check)

  python -m harness.pathcheck static  --profile P --pipeline-config PC --index-config IC --dataset D... --out F
  python -m harness.pathcheck dynamic --profile P --pipeline-config PC --index-config IC --experiment E
                                      --task-id ID ... [--prefix-variants ""  superset/] --out F

static : for every sampled task, compare the Graphify node files (as the pipeline's adapter normalises them)
         with the chunk files of the index; every non-matching path is put into a path form.
dynamic: run the pipeline's retrieve_context + graph_expand on a scratch copy of the index for each task and
         prefix setting; count resolved and unresolved (stub) neighbours by category.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from .common import load_toml
from .envinfo import _graphify_version
from .index_cache import index_dir, make_key
from .pipeline_src import snapshot
from .pipelineenv import build_env
from .sampling import load_sample


def sampled_tasks(data: Path, datasets: list[str]) -> list[dict]:
    out = []
    for d in datasets:
        tf = data / "datasets" / d / "tasks.jsonl"
        ids = load_sample(data / "samples" / f"{d}.sample.json", tf)
        byid = {t["instance_id"]: t for t in (json.loads(x) for x in tf.read_text(encoding="utf-8").splitlines())}
        out += [{**byid[i], "dataset": d} for i in ids]
    return out


def index_files(idx: Path) -> set[str]:
    bm = json.loads((idx / "chroma" / "bm25_index.json").read_text(encoding="utf-8"))
    chunks = bm["chunks"] if isinstance(bm, dict) else bm
    return {(c.get("metadata") or {}).get("file_path") or (c.get("metadata") or {}).get("file") or "" for c in chunks} - {""}


def path_form(f: str, files: set[str], lower: dict[str, str]) -> str:
    """Why a graph path is not an index path (empty string = exact match)."""
    if f in files:
        return ""
    if "." + f in files:
        return "leading_dot_stripped"                 # _norm_path uses lstrip("./"), which also strips a leading dot
    if "\\" in f:
        return "backslash"
    if f.lower() in lower:
        return "case_only"
    parts = f.split("/")
    if any("/".join(parts[i:]) in files for i in range(1, len(parts))):
        return "extra_leading_prefix"
    if len(parts) > 1 and any(g.endswith("/" + f) for g in files):
        return "missing_leading_prefix"
    ext = os.path.splitext(f)[1].lower() or "(no extension)"
    return f"not_indexed:{ext}"


def layout(files: set[str]) -> dict:
    py = sum(1 for f in files if f.endswith(".py"))
    tops = Counter(f.split("/")[0] for f in files if "/" in f)
    return {"files": len(files), "py_share": round(py / max(1, len(files)), 2), "top_dirs": len(tops),
            "src_layout": any(f.startswith("src/") and f.endswith(".py") for f in files),
            "top3": tops.most_common(3)}


def static(a, data: Path, sha_root: Path) -> dict:
    sys.path.insert(0, str(sha_root / "backend"))
    from indexing.graphify_adapter import _norm_path          # the pipeline's own normalisation
    pcfg, icfg = load_toml(a.pipeline_config), load_toml(a.index_config)
    sha, gver = pcfg["pipeline"]["frozen_sha"], _graphify_version()
    rows, forms_all = [], Counter()
    for t in sampled_tasks(data, a.dataset):
        idx = index_dir(data, make_key(t["repo"], t["base_commit"], sha, icfg, gver))
        files = index_files(idx)
        lower = {f.lower(): f for f in files}
        g = json.loads((idx / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
        gfiles = {_norm_path(str(n.get("source_file") or n.get("file") or "")) for n in g.get("nodes", [])} - {""}
        forms = Counter(path_form(f, files, lower) for f in gfiles)
        exact = forms.pop("", 0)
        forms_all.update(forms)
        rows.append({"task_id": t["instance_id"], "repo": t["repo"], "layout": layout(files), "graph_files": len(gfiles),
                     "exact_match": exact, "exact_share": round(exact / max(1, len(gfiles)), 4),
                     "non_matching_forms": dict(forms.most_common(8)), "has_superset_prefix": any(f.startswith("superset/") for f in gfiles)})
    return {"mode": "static", "frozen_sha": sha, "tasks": len(rows), "path_forms_total": dict(forms_all.most_common()), "rows": rows,
            "any_superset_prefix_in_graph_paths": any(r["has_superset_prefix"] for r in rows)}


def dynamic(a, data: Path, sha_root: Path) -> dict:
    pcfg, icfg, exp = load_toml(a.pipeline_config), load_toml(a.index_config), load_toml(a.experiment)
    sha, gver = pcfg["pipeline"]["frozen_sha"], _graphify_version()
    tasks = {t["instance_id"]: t for t in sampled_tasks(data, a.dataset)}
    scratch_root = data / "cache" / "scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    results = []
    for tid in a.task_id:
        t = tasks[tid]
        idx = index_dir(data, make_key(t["repo"], t["base_commit"], sha, icfg, gver))
        entry = {"task_id": tid, "repo": t["repo"], "layout": layout(index_files(idx)), "prefix_variants": {}}
        for prefix in a.prefix_variants:
            sc = scratch_root / f"pathcheck__{tid}__{'default' if prefix == 'superset/' else 'empty'}"
            if sc.exists():
                shutil.rmtree(sc)
            sc.mkdir()
            shutil.copytree(idx / "chroma", sc / "chroma")
            shutil.copytree(idx / "graphify-out", sc / "graphify-out")
            (sc / "q.txt").write_text(t["query"], encoding="utf-8")
            env = build_env(pcfg, a.model, exp, extra={
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "CHROMA_PATH": str(sc / "chroma"),
                "GRAPHIFY_OUT": str(sc / "graphify-out"), "GRAPHIFY_PATH_PREFIX": prefix})
            t0 = time.time()
            r = subprocess.run([sys.executable, "-m", "harness.pathcheck_worker", "--pipeline-root", str(sha_root),
                                "--query-file", str(sc / "q.txt"), "--out", str(sc / "out.json")],
                               capture_output=True, text=True, env=env)
            try:
                if r.returncode != 0:
                    entry["prefix_variants"][prefix or "(empty)"] = {"error": r.stderr[-600:]}
                else:
                    entry["prefix_variants"][prefix or "(empty)"] = json.loads((sc / "out.json").read_text(encoding="utf-8")) | {
                        "seconds": round(time.time() - t0, 1)}
            finally:
                if scratch_root.resolve() in sc.resolve().parents:
                    shutil.rmtree(sc)
        results.append(entry)
    return {"mode": "dynamic", "frozen_sha": sha, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["static", "dynamic"])
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--pipeline-config", type=Path, required=True)
    ap.add_argument("--index-config", type=Path, required=True)
    ap.add_argument("--experiment", type=Path, default=None)
    ap.add_argument("--dataset", action="append", default=None)
    ap.add_argument("--task-id", action="append", default=[])
    ap.add_argument("--prefix-variants", nargs="+", default=["", "superset/"])
    ap.add_argument("--model", default="qwen2.5-coder:7b-instruct")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.dataset = a.dataset or ["swe-bench-lite", "loc-bench"]
    for n in ("profile", "pipeline_config", "index_config", "experiment", "out"):
        if getattr(a, n) is not None:
            setattr(a, n, getattr(a, n).resolve())
    data = Path(load_toml(a.profile)["data_dir"]).resolve()
    sha_root = snapshot(load_toml(a.pipeline_config)["pipeline"]["frozen_sha"], data)[1]
    rep = static(a, data, sha_root) if a.mode == "static" else dynamic(a, data, sha_root)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
