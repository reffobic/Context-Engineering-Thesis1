"""Index cache: build, key, export, import, verify.

  python -m harness.index_cache build  --profile P --experiment E --pipeline-config PC --index-config I [--dataset D ...] [--limit N] [--only-instance ID]
  python -m harness.index_cache export --profile P --experiment E --pipeline-config PC --index-config I --out <new-dir> [--dataset D ...]
  python -m harness.index_cache import --profile P --src <export-dir>
  python -m harness.index_cache verify --profile P --dir <index-dir>

Cache key = sha256(repo, base_commit, pipeline SHA, hash(chunking + embedding + index settings),
graphify version). One index per (repo, base_commit) of the sampled tasks. Build time is logged
per stage in <index>/build_report.json and appended to <data_dir>/logs/index_builds.jsonl.
Nothing here records evaluation results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from .common import config_hash, load_toml
from .envinfo import _graphify_version
from .pipelineenv import assert_snapshot_root, assert_unset, git_identity, pipeline_identity
from .pipeline_src import snapshot
from .smallfiles import sha256_file

FORMAT = "index-archive-v1"
BATCH = 16                      # same upsert batch size as the pipeline's DEFAULT_EMBED_BATCH_SIZE
CODE, META = "code_embeddings", "code_meta_embeddings"


def safe_name(repo: str) -> str:
    return repo.replace("/", "__")


def index_settings(icfg: dict) -> dict:
    """Everything in the index config that changes index content (device is excluded on purpose)."""
    return {"embedding_model": icfg["embedding"]["model"], "chunking": icfg["chunking"],
            "index_env": icfg["index"], "max_files": icfg["build"]["max_files"]}


def make_key(repo: str, commit: str, pipeline_sha: str, icfg: dict, graphify_version: str | None) -> dict:
    parts = {"repo": repo, "base_commit": commit, "pipeline_sha": pipeline_sha,
             "settings": index_settings(icfg), "graphify": graphify_version, "pythonhashseed": "0"}
    blob = json.dumps(parts, sort_keys=True).encode("utf-8")
    return {**parts, "key": hashlib.sha256(blob).hexdigest()[:16]}


def require_call_graph(idx: Path) -> None:
    """Fail the job if an index lacks call_graph.json (the structure re-rank would silently not apply)."""
    from .instrument import JobFailure
    if not (idx / "chroma" / "call_graph.json").is_file():
        raise JobFailure(f"call_graph.json missing in index {idx.name}")


def index_dir(data_dir: Path, parts: dict) -> Path:
    return data_dir / "cache" / "indexes" / f"{safe_name(parts['repo'])}__{parts['base_commit'][:12]}__{parts['key']}"


def sampled_pairs(data_dir: Path, datasets: list[str]) -> list[dict]:
    seen, out = set(), []
    for n in datasets:
        tasks = {t["instance_id"]: t for t in map(json.loads, (data_dir / "datasets" / n / "tasks.jsonl").read_text(encoding="utf-8").splitlines())}
        ids = json.loads((data_dir / "samples" / f"{n}.sample.json").read_text(encoding="utf-8"))["instance_ids"]
        for i in ids:
            t = tasks[i]
            k = (t["repo"], t["base_commit"])
            if k not in seen:
                seen.add(k)
                out.append({"repo": t["repo"], "base_commit": t["base_commit"], "instance_id": i, "dataset": n})
    return out


def _inside(child: Path, parent: Path) -> bool:
    return parent.resolve() in child.resolve().parents


def build_one(data_dir: Path, ident: dict, pcfg: dict, icfg_path: Path, icfg: dict, pair: dict,
              pipe: tuple[str, Path], profile: dict, gver: str | None) -> dict:
    sha, root = pipe
    parts = make_key(pair["repo"], pair["base_commit"], sha, icfg, gver)
    final = index_dir(data_dir, parts)
    if (final / "DONE").is_file():
        return {"status": "cached", "dir": str(final), **parts}
    checkout = data_dir / "cache" / "checkouts" / safe_name(pair["repo"]) / pair["base_commit"]
    if not checkout.is_dir():
        return {"status": "error", "error": f"checkout missing: {checkout}", **parts}
    partial = final.with_name(final.name + ".partial")
    cache_idx = data_dir / "cache" / "indexes"
    if partial.exists():
        assert _inside(partial, cache_idx)
        shutil.rmtree(partial)
    cache_idx.mkdir(parents=True, exist_ok=True)
    meta = {"key_parts": parts, "machine_id": profile["machine_id"], "pipeline_identity": ident,
            "record_results": pcfg["pipeline"]["record_results"], "icfg_hash": config_hash(icfg_path), "harness": git_identity(Path(__file__).resolve().parents[3], pcfg),
            "sample_instance": pair["instance_id"], "dataset": pair["dataset"]}
    meta_path = partial.with_name(partial.name + ".meta.json")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    t = time.perf_counter()
    cmd = [sys.executable, "-m", "harness.index_worker", "--pipeline-root", str(root), "--checkout", str(checkout),
           "--out", str(partial), "--index-config", str(icfg_path), "--meta", str(meta_path)]
    env = {**os.environ, "PYTHONHASHSEED": "0"}      # the indexer's output order depends on it
    for v in pcfg["assert_unset"]["vars"]:
        env.pop(v, None)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    wall = time.perf_counter() - t
    meta_path.unlink(missing_ok=True)
    if r.returncode != 0:
        return {"status": "error", "rc": r.returncode, "stderr_tail": r.stderr[-1500:], **parts}
    (partial / "key.json").write_text(json.dumps(parts, indent=1), encoding="utf-8")
    (partial / "DONE").write_text(parts["key"], encoding="utf-8")
    partial.rename(final)
    rep = json.loads((final / "build_report.json").read_text(encoding="utf-8"))
    rec = {"status": "built", "dir": str(final), "wall_s": round(wall, 2), "timing_s": rep["timing_s"],
           "counts": rep["counts"], "index_device": rep["index_device"], **parts}
    log = data_dir / "logs" / "index_builds.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


# ── export / import ───────────────────────────────────────────────────────────

def _dump_collection(coll, prefix: Path) -> int:
    import numpy as np
    got = coll.get(include=["embeddings", "documents", "metadatas"])
    ids = got["ids"]
    np.save(f"{prefix}.npy", np.asarray(got["embeddings"], dtype="<f8"), allow_pickle=False)
    with open(f"{prefix}.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for i, d, m in zip(ids, got["documents"], got["metadatas"]):
            f.write(json.dumps({"id": i, "document": d, "metadata": m}, ensure_ascii=False, sort_keys=True) + "\n")
    return len(ids)


def golden(client, n: int) -> dict:
    """Top-10 answers of the ORIGINAL index for n fixed stored vectors (seeded)."""
    import numpy as np
    out = {}
    for cname in (CODE, META):
        c = client.get_collection(cname)
        g = c.get(include=["embeddings"])
        emb = np.asarray(g["embeddings"], dtype="float64")
        pick = np.random.default_rng(0).choice(len(emb), size=min(n, len(emb)), replace=False) if len(emb) else []
        rows = []
        for i in pick:
            q = c.query(query_embeddings=[emb[i].tolist()], n_results=10)
            rows.append({"row": int(i), "ids": q["ids"][0]})
        out[cname] = rows
    return out


def export_one(idx: Path, out_dir: Path, vcfg: dict) -> Path:
    import chromadb
    parts = json.loads((idx / "key.json").read_text(encoding="utf-8"))
    name = idx.name
    stage = out_dir / f"{name}.stage"
    stage.mkdir(parents=True)
    client = chromadb.PersistentClient(path=str(idx / "chroma"))
    counts = {CODE: _dump_collection(client.get_collection(CODE), stage / "code"),
              META: _dump_collection(client.get_collection(META), stage / "meta")}
    (stage / "golden.json").write_text(json.dumps(golden(client, vcfg["golden_queries"])), encoding="utf-8", newline="\n")
    shutil.copyfile(idx / "chroma" / "bm25_index.json", stage / "bm25_index.json")
    shutil.copyfile(idx / "chroma" / "call_graph.json", stage / "call_graph.json")
    (stage / "graphify-out").mkdir()
    shutil.copyfile(idx / "graphify-out" / "graph.json", stage / "graphify-out" / "graph.json")
    shutil.copyfile(idx / "build_report.json", stage / "build_report.json")
    (stage / "key.json").write_text(json.dumps(parts, indent=1), encoding="utf-8")
    files = {str(p.relative_to(stage).as_posix()): sha256_file(p) for p in sorted(stage.rglob("*")) if p.is_file()}
    (stage / "manifest.json").write_text(json.dumps({"format": FORMAT, "name": name, "counts": counts, "verify": vcfg, "files": files}, indent=1), encoding="utf-8")
    tar_path = out_dir / f"{name}.tar"
    with tarfile.open(tar_path, "w") as tf:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                tf.add(p, arcname=f"{name}/{p.relative_to(stage).as_posix()}")
    shutil.rmtree(stage)
    (out_dir / f"{name}.tar.sha256").write_text(f"{sha256_file(tar_path)}  {tar_path.name}\n", encoding="utf-8", newline="\n")
    return tar_path


def rebuild_chroma(src: Path, chroma_dir: Path) -> dict:
    import chromadb
    import numpy as np
    client = chromadb.PersistentClient(path=str(chroma_dir))
    spec = {CODE: ("code", {"hnsw:space": "cosine"}), META: ("meta", {"hnsw:space": "cosine", "channel": "meta"})}
    counts = {}
    for cname, (prefix, md) in spec.items():
        coll = client.get_or_create_collection(name=cname, metadata=md)
        emb = np.load(src / f"{prefix}.npy", allow_pickle=False)
        rows = [json.loads(x) for x in (src / f"{prefix}.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == emb.shape[0]
        for s in range(0, len(rows), BATCH):
            b = rows[s:s + BATCH]
            coll.upsert(ids=[r["id"] for r in b], documents=[r["document"] for r in b],
                        embeddings=emb[s:s + BATCH].tolist(), metadatas=[r["metadata"] for r in b])
        counts[cname] = coll.count()
    return counts


def verify_index(idx: Path, sample: int = 200, compare_with: Path | None = None,
                 src: Path | None = None) -> dict:
    """Self-retrieval, stored-vector fidelity and overlap with the original's golden answers.

    src = unpacked archive (npy + golden.json). compare_with = original Chroma folder (same machine only).
    """
    import chromadb
    import numpy as np
    res: dict = {"dir": str(idx), "checks": {}}
    client = chromadb.PersistentClient(path=str(idx / "chroma"))
    other = chromadb.PersistentClient(path=str(compare_with / "chroma")) if compare_with else None
    gold = json.loads((src / "golden.json").read_text(encoding="utf-8")) if src else None
    for cname, prefix in ((CODE, "code"), (META, "meta")):
        c = client.get_collection(cname)
        got = c.get(include=["embeddings"])
        emb = np.asarray(got["embeddings"], dtype="float64")
        n = len(got["ids"])
        pick = np.random.default_rng(0).choice(n, size=min(sample, n), replace=False) if n else []
        self_ok = 0
        for i in pick:
            q = c.query(query_embeddings=[emb[i].tolist()], n_results=1)
            # identical code chunks have identical vectors, so match on distance, not on id
            self_ok += q["ids"][0][0] == got["ids"][i] or abs(q["distances"][0][0]) < 1e-5
        r = {"count": n, "dim": int(emb.shape[1]) if n else 0,
             "self_recall": round(self_ok / max(1, len(pick)), 4)}
        if src is not None:
            ref = np.load(src / f"{prefix}.npy", allow_pickle=False)
            ref_ids = [json.loads(x)["id"] for x in (src / f"{prefix}.jsonl").read_text(encoding="utf-8").splitlines()]
            pos = {k: j for j, k in enumerate(got["ids"])}
            r["ids_equal"] = set(ref_ids) == set(got["ids"])
            r["max_embedding_abs_diff"] = float(max(np.abs(emb[pos[k]] - ref[j]).max() for j, k in enumerate(ref_ids))) if r["ids_equal"] else None
            ov = []
            for g in gold[cname]:
                q = c.query(query_embeddings=[ref[g["row"]].tolist()], n_results=10)["ids"][0]
                ov.append(len(set(q) & set(g["ids"])) / 10)
            r["golden_overlap_mean"] = round(float(np.mean(ov)), 4)
            r["golden_overlap_min"] = round(float(np.min(ov)), 4)
            r["golden_identical_order"] = sum(
                c.query(query_embeddings=[ref[g["row"]].tolist()], n_results=10)["ids"][0] == g["ids"] for g in gold[cname])
            r["golden_n"] = len(ov)
        if other is not None:
            o = other.get_collection(cname)
            ov = [len(set(c.query(query_embeddings=[emb[i].tolist()], n_results=10)["ids"][0])
                      & set(o.query(query_embeddings=[emb[i].tolist()], n_results=10)["ids"][0])) / 10 for i in pick[:50]]
            r["overlap_with_original_mean"] = round(float(np.mean(ov)), 4)
        res["checks"][cname] = r
    docs = json.loads((idx / "chroma" / "bm25_index.json").read_text(encoding="utf-8"))
    res["checks"]["bm25_chunks"] = len(docs.get("chunks", []))
    res["checks"]["graphify_json"] = (idx / "graphify-out" / "graph.json").is_file()
    res["checks"]["call_graph_json"] = (idx / "chroma" / "call_graph.json").is_file()
    return res


def verdict(v: dict, man: dict) -> list[str]:
    """Failures against the thresholds stored in the archive manifest."""
    t, fails = man["verify"], []
    if not v["checks"].get("call_graph_json"):
        fails.append("call_graph.json missing (structure re-rank would silently not apply)")
    if not v["checks"].get("graphify_json"):
        fails.append("graphify graph.json missing")
    for cname in (CODE, META):
        c = v["checks"][cname]
        if c["count"] != man["counts"][cname]:
            fails.append(f"{cname}: count {c['count']} != {man['counts'][cname]}")
        if not c["ids_equal"]:
            fails.append(f"{cname}: ids differ")
        elif c["max_embedding_abs_diff"] > t["max_embedding_abs_diff"]:
            fails.append(f"{cname}: stored vector diff {c['max_embedding_abs_diff']:.2e}")
        if c["self_recall"] < t["min_self_recall"]:
            fails.append(f"{cname}: self_recall {c['self_recall']}")
        if c["golden_overlap_mean"] < t["min_golden_overlap_mean"]:
            fails.append(f"{cname}: golden overlap {c['golden_overlap_mean']}")
    return fails


def import_one(tar_path: Path, data_dir: Path) -> dict:
    side = tar_path.with_name(tar_path.name + ".sha256")
    want = side.read_text(encoding="utf-8").split()[0]
    if sha256_file(tar_path) != want:
        return {"status": "error", "error": f"archive checksum mismatch: {tar_path.name}"}
    cache_idx = data_dir / "cache" / "indexes"
    name = tar_path.name[:-len(".tar")]
    final = cache_idx / name
    if final.exists():
        return {"status": "exists", "dir": str(final)} if (final / "DONE").is_file() else \
               {"status": "error", "error": f"{final} exists without DONE; refusing to touch it"}
    partial = cache_idx / (name + ".partial")
    if partial.exists():
        return {"status": "error", "error": f"{partial} exists; remove it manually after checking"}
    cache_idx.mkdir(parents=True, exist_ok=True)
    tmp = cache_idx / (name + ".unpack")
    if tmp.exists():
        return {"status": "error", "error": f"{tmp} exists; remove it manually after checking"}
    with tarfile.open(tar_path) as tf:
        tf.extractall(tmp, filter="data")
    src = tmp / name
    man = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    bad = [f for f, h in man["files"].items() if sha256_file(src / f) != h]
    if bad:
        shutil.rmtree(tmp)
        return {"status": "error", "error": f"file checksum mismatch inside archive: {bad}"}
    partial.mkdir()
    t = time.perf_counter()
    counts = rebuild_chroma(src, partial / "chroma")
    rebuild_s = time.perf_counter() - t
    if counts != man["counts"]:
        shutil.rmtree(tmp); shutil.rmtree(partial)
        return {"status": "error", "error": f"count mismatch after rebuild {counts} vs {man['counts']}"}
    shutil.copyfile(src / "bm25_index.json", partial / "chroma" / "bm25_index.json")
    shutil.copyfile(src / "call_graph.json", partial / "chroma" / "call_graph.json")
    (partial / "graphify-out").mkdir()
    shutil.copyfile(src / "graphify-out" / "graph.json", partial / "graphify-out" / "graph.json")
    for f in ("build_report.json", "key.json"):
        shutil.copyfile(src / f, partial / f)
    v = verify_index(partial, src=src)
    fails = verdict(v, man)
    if fails:
        shutil.rmtree(tmp); shutil.rmtree(partial)
        return {"status": "error", "error": "verification failed", "failures": fails, "verify": v}
    (partial / "DONE").write_text(json.loads((partial / "key.json").read_text())["key"], encoding="utf-8")
    partial.rename(final)
    shutil.rmtree(tmp)
    return {"status": "imported", "dir": str(final), "chroma_rebuild_s": round(rebuild_s, 2), "verify": v["checks"]}


INDEXER_PATHS = ["backend/indexing", "backend/run_index.py", "requirements.txt"]


def prove_indexer_identity(repo: Path, old_sha: str, new_sha: str) -> dict:
    """Object hashes of everything the indexer uses must be equal at both commits, else re-keying is refused."""
    import subprocess
    out = {}
    for p in INDEXER_PATHS:
        h = [subprocess.run(["git", "rev-parse", f"{sha}:{p}"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
             for sha in (old_sha, new_sha)]
        out[p] = h[0]
        if h[0] != h[1]:
            raise RuntimeError(f"{p} differs between {old_sha[:7]} and {new_sha[:7]}; not re-keying")
    return out


def rekey(data_dir: Path, repo: Path, old_sha: str, new_sha: str, icfg: dict, gver: str | None, dry_run: bool) -> dict:
    """Rename indexes built at old_sha to the key of new_sha without rebuilding (indexer code proven identical)."""
    proof = prove_indexer_identity(repo, old_sha, new_sha)
    idx_root = data_dir / "cache" / "indexes"
    plan, skipped = [], []
    for d in sorted(idx_root.iterdir()):
        if not d.is_dir() or d.name.endswith((".partial", ".unpack")):
            continue
        parts = json.loads((d / "key.json").read_text(encoding="utf-8"))
        if parts["pipeline_sha"] == new_sha:
            skipped.append(d.name)
            continue
        if parts["pipeline_sha"] != old_sha:
            raise RuntimeError(f"{d.name} was built at {parts['pipeline_sha'][:7]}, expected {old_sha[:7]}")
        if make_key(parts["repo"], parts["base_commit"], old_sha, icfg, gver)["key"] != parts["key"]:
            raise RuntimeError(f"{d.name}: stored key does not match the current index settings; not re-keying")
        new_parts = make_key(parts["repo"], parts["base_commit"], new_sha, icfg, gver)
        plan.append({"old_dir": d.name, "old_key": parts["key"], "new_dir": index_dir(data_dir, new_parts).name,
                     "new_key": new_parts["key"], "repo": parts["repo"], "base_commit": parts["base_commit"], "_parts": new_parts})
    mapping = {"old_sha": old_sha, "new_sha": new_sha, "proof_object_hashes": proof, "n_renamed": len(plan),
               "already_new": skipped, "entries": [{k: v for k, v in e.items() if k != "_parts"} for e in plan],
               "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "dry_run": dry_run}
    map_path = data_dir / "cache" / f"REKEY_{old_sha[:7]}_to_{new_sha[:7]}.json"
    if dry_run:
        return mapping
    map_path.write_text(json.dumps(mapping, indent=1), encoding="utf-8", newline="\n")   # plan first, then act
    for e in plan:
        src, dst = idx_root / e["old_dir"], idx_root / e["new_dir"]
        if dst.exists():
            raise RuntimeError(f"{dst} already exists")
        src.rename(dst)
        (dst / "key.json").write_text(json.dumps(e["_parts"], indent=1), encoding="utf-8")
        (dst / "DONE").write_text(e["new_key"], encoding="utf-8")
        (dst / "rekey.json").write_text(json.dumps({k: v for k, v in e.items() if k != "_parts"} | {
            "old_sha": old_sha, "new_sha": new_sha, "rebuilt": False}, indent=1), encoding="utf-8")
    return mapping


def _du(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def summarize(data_dir: Path, pairs: list[dict], sha: str, icfg: dict, gver: str | None, pcfg: dict) -> int:
    import statistics
    rows, missing = [], []
    for p in pairs:
        parts = make_key(p["repo"], p["base_commit"], sha, icfg, gver)
        d = index_dir(data_dir, parts)
        if not (d / "DONE").is_file():
            missing.append(f"{p['repo']}@{p['base_commit'][:8]}")
            continue
        rep = json.loads((d / "build_report.json").read_text(encoding="utf-8"))
        c, t = rep["counts"], rep["timing_s"]
        rows.append({"repo": p["repo"], "commit": p["base_commit"][:12], "dir": d.name, "disk_bytes": _du(d),
                     "files_scanned": c["files_scanned"], "files_indexed": c["files_indexed"],
                     "beyond_cap": c["files_beyond_pipeline_default_cap"], "chunks": c["chunks_code"],
                     "graphify_nodes": c["graphify_nodes"], "graphify_edges": c["graphify_edges"],
                     "worker_total_s": t["worker_total"], "timing_s": t, "device": rep["index_device"],
                     "pythonhashseed": rep.get("pythonhashseed"), "call_graph": (d / "chroma" / "call_graph.json").is_file(),
                     "harness_commit": (rep.get("harness") or {}).get("harness_commit")})
    cap = icfg["build"]["pipeline_default_max_files"]
    over = sorted((r for r in rows if r["files_scanned"] > cap), key=lambda r: -r["files_scanned"])
    stages = {k: round(sum(r["timing_s"][k] for r in rows), 1) for k in rows[0]["timing_s"]} if rows else {}
    out = {"pipeline_sha": sha, "indexes_expected": len(pairs), "indexes_present": len(rows), "missing": missing,
           "total_worker_s": round(sum(r["worker_total_s"] for r in rows), 1), "stage_totals_s": stages,
           "disk_bytes_total": sum(r["disk_bytes"] for r in rows), "chunks_total": sum(r["chunks"] for r in rows),
           "files_total": sum(r["files_scanned"] for r in rows), "default_cap": cap,
           "repos_over_cap": [{k: r[k] for k in ("repo", "commit", "files_scanned", "files_indexed", "beyond_cap", "chunks")} for r in over],
           "median_worker_s": statistics.median(r["worker_total_s"] for r in rows) if rows else None,
           "all_hashseed_0": all(r["pythonhashseed"] == "0" for r in rows), "all_have_call_graph": all(r["call_graph"] for r in rows),
           "devices": sorted({r["device"] for r in rows}), "harness_commits": sorted({str(r["harness_commit"]) for r in rows}),
           "slowest": sorted(({k: r[k] for k in ("repo", "commit", "chunks", "worker_total_s")} for r in rows),
                             key=lambda r: -r["worker_total_s"])[:5], "per_index": rows}
    rd = data_dir / "reports"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"index_summary_{sha[:7]}.json").write_text(json.dumps(out, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in out.items() if k != "per_index"}, indent=1))
    return 0 if not missing else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("build", "export"):
        p = sub.add_parser(n)
        p.add_argument("--profile", type=Path, required=True)
        p.add_argument("--experiment", type=Path, required=True)
        p.add_argument("--pipeline-config", type=Path, required=True)
        p.add_argument("--index-config", type=Path, required=True)
        p.add_argument("--dataset", action="append")
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--only-instance", default=None)
        if n == "export":
            p.add_argument("--out", type=Path, required=True)
        else:
            p.add_argument("--continue-on-error", action="store_true")
    i = sub.add_parser("import")
    i.add_argument("--profile", type=Path, required=True)
    i.add_argument("--src", type=Path, required=True)
    sm = sub.add_parser("summary")
    for x in ("profile", "experiment", "pipeline-config", "index-config"):
        sm.add_argument(f"--{x}", type=Path, required=True)
    sm.add_argument("--dataset", action="append")
    rk = sub.add_parser("rekey")
    for x in ("profile", "pipeline-config", "index-config"):
        rk.add_argument(f"--{x}", type=Path, required=True)
    rk.add_argument("--old-sha", required=True)
    rk.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("--dir", type=Path, required=True)
    v.add_argument("--compare-with", type=Path, default=None)
    a = ap.parse_args()

    if a.cmd == "verify":
        print(json.dumps(verify_index(a.dir, compare_with=a.compare_with), indent=1))
        return 0
    profile = load_toml(a.profile)
    data_dir = Path(profile["data_dir"])
    if a.cmd == "rekey":
        pc, ic = load_toml(a.pipeline_config), load_toml(a.index_config)
        m = rekey(data_dir, Path(__file__).resolve().parents[3], a.old_sha, pc["pipeline"]["frozen_sha"], ic,
                  _graphify_version(), a.dry_run)
        print(json.dumps({k: v for k, v in m.items() if k not in ("entries", "already_new")}, indent=1))
        print(f"entries: {len(m['entries'])}; first: {m['entries'][0] if m['entries'] else None}")
        return 0
    if a.cmd == "import":
        rc = 0
        for t in sorted(a.src.glob("*.tar")):
            r = import_one(t, data_dir)
            print(json.dumps(r))
            rc |= r["status"] == "error"
        return int(rc)

    exp, icfg, pcfg = load_toml(a.experiment), load_toml(a.index_config), load_toml(a.pipeline_config)
    assert_unset(pcfg)
    ident = pipeline_identity(a.pipeline_config)
    datasets = a.dataset or exp["sampling"]["datasets"]
    pairs = sampled_pairs(data_dir, datasets)
    if getattr(a, "only_instance", None):
        pairs = [p for p in pairs if p["instance_id"] == a.only_instance]
    if getattr(a, "limit", None):
        pairs = pairs[: a.limit]
    sha, root = snapshot(pcfg["pipeline"]["frozen_sha"], data_dir)
    assert_snapshot_root(root, data_dir, pcfg, Path(__file__).resolve().parents[3])
    gver = _graphify_version()
    print(f"pipeline frozen_sha={sha} (base {ident['base_commit'][:7]}, seed alignment "
          f"{ident['seed_alignment_commit'][:7]}); record_results={pcfg['pipeline']['record_results']}; "
          f"graphify={gver}; {len(pairs)} indexes")
    if a.cmd == "summary":
        return summarize(data_dir, pairs, sha, icfg, gver, pcfg)
    if a.cmd == "build":
        failed = 0
        t_all = time.perf_counter()
        for i, p in enumerate(pairs, 1):
            r = build_one(data_dir, ident, pcfg, a.index_config, icfg, p, (sha, root), profile, gver)
            slim = {k: r[k] for k in r if k not in ("settings",)}
            print(f"[{i}/{len(pairs)}] {time.strftime('%H:%M:%S')} {p['repo']}@{p['base_commit'][:8]} {r['status']} "
                  f"wall_s={r.get('wall_s')} chunks={(r.get('counts') or {}).get('chunks_code')} "
                  f"files={(r.get('counts') or {}).get('files_scanned')}", flush=True)
            if r["status"] == "error":
                failed += 1
                print(json.dumps(slim, indent=1), flush=True)
                if not a.continue_on_error:
                    return 1
        print(f"build finished: {len(pairs) - failed} ok, {failed} failed, "
              f"{(time.perf_counter() - t_all):.0f}s total", flush=True)
        return 1 if failed else 0
    a.out.mkdir(parents=True, exist_ok=False)
    for p in pairs:
        parts = make_key(p["repo"], p["base_commit"], sha, icfg, gver)
        d = index_dir(data_dir, parts)
        print("exported", export_one(d, a.out, icfg["verify"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
