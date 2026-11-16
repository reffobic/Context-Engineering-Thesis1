"""Materialize Phase 2 SWE-QA (w1 / rest) + RepoProbe Python-8 into harness dataset layout.

Writes under <data_dir>/datasets/<name>/:
  tasks.jsonl   — instance_id, repo, base_commit, query  (pipeline input)
  labels.jsonl  — gold answer / checklist (metrics only; never opened by harness.run)
  manifest.json

Also writes <data_dir>/samples/<name>.sample.json with **all** instance_ids (Wave 1 = full set).

Usage:
  PYTHONPATH=eval_harness/src python -m harness.phase2_datasets \\
      --profile eval_harness/profiles/ubuntu-4080.toml \\
      --config eval_harness/configs/datasets_phase2.toml \\
      [--only swe-qa-w1|swe-qa-rest|repoprobe-py8] [--skip-clone] [--resolve-commits]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.common import load_toml
from harness.datasets import _write_jsonl, fetch_checkout, interleave_by_repo


def _resolve(repo_root: Path, path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    # configs are relative to thesis repo root (parents[3] from this file = eval_harness/src/harness -> repo)
    thesis = Path(__file__).resolve().parents[3]
    return (thesis / p).resolve()


def _git_full_sha(url: str, short: str, cache_dir: Path | None = None) -> str:
    """Resolve a short commit to a full SHA via GitHub Commits API (abbreviated SHAs are not fetchable as refs)."""
    del cache_dir  # reserved for future local cache
    if len(short) >= 40:
        return short
    # url like https://github.com/owner/repo.git
    parts = url.rstrip("/").removesuffix(".git").split("/")
    owner, repo = parts[-2], parts[-1]
    api = f"https://api.github.com/repos/{owner}/{repo}/commits/{short}"
    import urllib.request
    req = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json", "User-Agent": "phase2-datasets"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    sha = data.get("sha") or ""
    if not sha.startswith(short):
        raise RuntimeError(f"cannot resolve {short} on {url} (got {sha!r})")
    return sha


def build_sweqa(ds: dict, bench: Path) -> tuple[list[dict], list[dict], dict]:
    tasks, labels = [], []
    repo_map: dict = ds["repo_map"]
    commits: dict = ds["commits"]
    counts = {}
    for short in ds["repos"]:
        path = bench / "Benchmark" / f"{short}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(path)
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        counts[short] = len(lines)
        full_repo = repo_map[short]
        commit = commits[short]
        for i, row in enumerate(lines):
            iid = f"{short}-{i:03d}"
            q = row["question"]
            ans = row["answer"]
            tasks.append({
                "instance_id": iid,
                "repo": full_repo,
                "base_commit": commit,
                "query": q,
                "suite": "swe-qa",
                "repo_short": short,
            })
            labels.append({
                "instance_id": iid,
                "gold_answer": ans,
                "question": q,
                "files": [],  # explanation suite — no LocAgent edit gold
                "suite": "swe-qa",
            })
    total = sum(counts.values())
    if total != int(ds["expected_count"]):
        raise RuntimeError(f"{ds.get('name', 'swe-qa')} expected {ds['expected_count']} got {total}: {counts}")
    meta = {"kind": "swe-qa", "counts_by_repo": counts, "bench_root": str(bench)}
    return tasks, labels, meta


def build_repoprobe(ds: dict, root: Path) -> tuple[list[dict], list[dict], dict]:
    info = json.loads((root / "repos_info.json").read_text(encoding="utf-8"))
    by_short = {}
    for x in info:
        if x.get("primary_language") != "Python":
            continue
        short = x["name"].split("/")[-1]
        by_short[short] = x
    tasks, labels = [], []
    counts = {}
    for short in ds["repos"]:
        if short not in by_short:
            raise KeyError(f"{short} not in RepoProbe Python repos_info")
        meta_repo = by_short[short]
        full = meta_repo["name"]
        head = meta_repo["snapshot"]["git"]["head"]
        csv_path = root / "dataset" / f"{short}.csv"
        if not csv_path.is_file():
            raise FileNotFoundError(csv_path)
        with csv_path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        counts[short] = len(rows)
        for row in rows:
            qid = (row.get("question_id") or "").strip()
            if not qid:
                raise RuntimeError(f"missing question_id in {csv_path}")
            iid = f"{short}__{qid}"
            q = row["question"]
            tasks.append({
                "instance_id": iid,
                "repo": full,
                "base_commit": head,
                "query": q,
                "suite": "repoprobe",
                "repo_short": short,
                "taxonomy": row.get("taxonomy") or "",
                "difficulty": row.get("difficulty") or "",
            })
            labels.append({
                "instance_id": iid,
                "gold_answer": row.get("answer") or "",
                "checklist": row.get("checklist") or "",
                "question": q,
                "taxonomy": row.get("taxonomy") or "",
                "difficulty": row.get("difficulty") or "",
                "files": [],
                "suite": "repoprobe",
            })
    meta = {"kind": "repoprobe", "counts_by_repo": counts, "total": sum(counts.values()),
            "repoprobe_root": str(root)}
    return tasks, labels, meta


def write_dataset(data_dir: Path, name: str, tasks: list[dict], labels: list[dict], meta: dict) -> dict:
    out = data_dir / "datasets" / name
    out.mkdir(parents=True, exist_ok=True)
    # Strip harness-only extras from tasks written for run.py (keep instance_id/repo/base_commit/query)
    slim_tasks = [{"instance_id": t["instance_id"], "repo": t["repo"],
                   "base_commit": t["base_commit"], "query": t["query"]} for t in tasks]
    h_t = _write_jsonl(out / "tasks.jsonl", slim_tasks)
    h_l = _write_jsonl(out / "labels.jsonl", labels)
    manifest = {
        **meta,
        "name": name,
        "count": len(slim_tasks),
        "repos": sorted({t["repo"] for t in slim_tasks}),
        "tasks_sha256": h_t,
        "labels_sha256": h_l,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "free_form": True,
        "answer_locations_instruction": False,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    # Full-population sample (Wave 1 = all tasks)
    sample = {
        "dataset": name,
        "fraction": 1.0,
        "seed": 42,
        "n": len(slim_tasks),
        "instance_ids": [t["instance_id"] for t in slim_tasks],
        "source_tasks_sha256": hashlib.sha256((out / "tasks.jsonl").read_bytes()).hexdigest(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Phase 2 Wave 1 uses the full task set (fraction=1.0)",
    }
    sp = data_dir / "samples"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / f"{name}.sample.json").write_text(json.dumps(sample, indent=1), encoding="utf-8")
    return manifest


def resolve_commits_in_tasks(
    tasks: list[dict],
    url_template: str = "https://github.com/{repo}.git",
    cache_dir: Path | None = None,
) -> list[dict]:
    cache: dict[tuple[str, str], str] = {}
    out = []
    for t in tasks:
        key = (t["repo"], t["base_commit"])
        if key not in cache:
            if len(t["base_commit"]) >= 40:
                cache[key] = t["base_commit"]
            else:
                cache[key] = _git_full_sha(
                    url_template.format(repo=t["repo"]), t["base_commit"], cache_dir=cache_dir
                )
                print(f"  resolved {t['repo']}@{t['base_commit']} -> {cache[key][:12]}", flush=True)
        nt = dict(t)
        nt["base_commit"] = cache[key]
        out.append(nt)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--only", default=None)
    ap.add_argument("--skip-clone", action="store_true")
    ap.add_argument("--resolve-commits", action="store_true",
                    help="expand short SHAs to full via git ls-remote (recommended before index)")
    args = ap.parse_args()
    profile, cfg = load_toml(args.profile), load_toml(args.config)
    data_dir = Path(profile["data_dir"])
    if not data_dir.is_absolute():
        data_dir = (Path(__file__).resolve().parents[3] / data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / "cache"
    git_cfg = {"git": {"repo_url_template": "https://github.com/{repo}.git",
                       "fetch_depth": 1, "timeout_s": 3600, "workers": 4}}

    for ds in cfg["dataset"]:
        if args.only and ds["name"] != args.only:
            continue
        kind = ds["kind"]
        if kind == "swe-qa":
            bench = _resolve(Path(__file__).resolve().parents[3], cfg["sweqa_bench_root"])
            tasks, labels, meta = build_sweqa(ds, bench)
        elif kind == "repoprobe":
            root = _resolve(Path(__file__).resolve().parents[3], cfg["repoprobe_root"])
            tasks, labels, meta = build_repoprobe(ds, root)
        else:
            raise ValueError(f"unknown kind {kind}")
        if args.resolve_commits:
            print(f"resolving commits for {ds['name']}...", flush=True)
            tasks = resolve_commits_in_tasks(tasks, cache_dir=cache / "repos")
            # labels stay keyed by instance_id
        manifest = write_dataset(data_dir, ds["name"], tasks, labels, meta)
        print(f"[PASS] {ds['name']}: {manifest['count']} tasks -> {data_dir / 'datasets' / ds['name']}")
        print(f"       sample: {data_dir / 'samples' / (ds['name'] + '.sample.json')}")
        if kind == "swe-qa":
            print(f"       by_repo: {meta['counts_by_repo']}")
        else:
            print(f"       by_repo: {meta['counts_by_repo']} total={meta['total']}")

        if args.skip_clone:
            continue
        # Unique (repo, commit) pairs
        pairs = {(t["repo"], t["base_commit"]) for t in tasks}
        todo_tasks = [{"repo": r, "base_commit": c, "instance_id": f"{r}@{c[:8]}"} for r, c in sorted(pairs)]
        todo_tasks = interleave_by_repo(todo_tasks)
        results = []
        for i, t in enumerate(todo_tasks, 1):
            r = fetch_checkout(git_cfg, cache, t["repo"], t["base_commit"])
            results.append(r)
            print(f"  checkout [{i}/{len(todo_tasks)}] {t['repo']}@{t['base_commit'][:10]} {r['status']}", flush=True)
            if r["status"] == "error":
                print(f"    ERROR: {r.get('error')}", flush=True)
        n_err = sum(r["status"] == "error" for r in results)
        summary = {"fetched": sum(r["status"] == "fetched" for r in results),
                   "exists": sum(r["status"] == "exists" for r in results),
                   "errors": [r for r in results if r["status"] == "error"]}
        (data_dir / "datasets" / ds["name"] / "checkout_summary.json").write_text(
            json.dumps(summary, indent=1), encoding="utf-8")
        print(f"[{'PASS' if not n_err else 'FAIL'}] checkouts: {summary}")
        if n_err:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
