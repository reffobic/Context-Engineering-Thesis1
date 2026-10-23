"""One repo, one prefix setting: do the pipeline's Graphify paths match the index chunk paths?

Runs the pipeline's own retrieve_context and graph_expand (uncapped and with the pipeline's cap) on a scratch
copy of the index and reports resolved vs unresolved (stub) neighbours by category and path form.
Started by harness.pathcheck with a pinned environment; writes JSON to --out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline-root", type=Path, required=True)
    ap.add_argument("--query-file", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    root = a.pipeline_root.resolve()
    sys.path.insert(0, str(root / "backend"))
    os.chdir(root)
    from harness.instrument import classify_stub, is_stub
    from harness.pipelineenv import install_chroma_guard
    install_chroma_guard([])
    import indexing.graphify_adapter as ga
    from retrieval.graph_expand import graph_expand
    from retrieval.retrieval_tool import retrieve_context

    chroma = Path(os.environ["CHROMA_PATH"])
    bm = json.loads((chroma / "bm25_index.json").read_text(encoding="utf-8"))
    chunks = bm["chunks"] if isinstance(bm, dict) else bm
    index_files = {(c.get("metadata") or {}).get("file_path") or (c.get("metadata") or {}).get("file") or "" for c in chunks} - {""}
    query = a.query_file.read_text(encoding="utf-8")
    seeds = retrieve_context(query)
    adapter = ga.get_adapter()
    out: dict = {"prefix_env": os.environ.get("GRAPHIFY_PATH_PREFIX"), "index_files": len(index_files),
                 "graph_nodes": len(adapter.nodes), "seeds": len(seeds), "variants": {}}
    out["seed_keys_resolved"] = sum(1 for s in seeds if (k := adapter.seed_key_from_chunk(s)) and k in adapter.nodes)
    for name, cap in (("pipeline_cap_8", 8), ("uncapped", 0)):
        for hops in (1, 2):
            r = graph_expand(seeds, hops=hops, max_neighbors=cap)
            stubs = []
            for c in r["chunks"]:
                if is_stub(c):
                    f = (c["metadata"] or {}).get("file") or ""
                    stubs.append({"file": f, "category": classify_stub(f, index_files)})
            nodes = [adapter.node_attrs(n) for n in r["neighbor_ids"]]
            node_files = [str(n.get("file") or "") for n in nodes]
            out["variants"][f"{name}_hops{hops}"] = {
                "neighbors": len(r["neighbor_ids"]), "chunks": len(r["chunks"]), "stub_chunks": len(stubs),
                "by_category": {c: sum(1 for s in stubs if s["category"] == c) for c in ("prefix_mismatch", "unindexed_file_type", "other")},
                "neighbor_files_in_index_exact": sum(1 for f in node_files if f in index_files),
                "neighbor_files_total": len(node_files),
                "stub_examples": stubs[:6]}
    a.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
