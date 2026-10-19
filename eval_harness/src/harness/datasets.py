"""Dataset step: download tasks + gold labels (separate files), assert counts,
fetch each repository at its base commit into the cache (no .git, no later commits).

Usage:
  PYTHONPATH=eval_harness/src python -m harness.datasets \
      --profile eval_harness/profiles/ubuntu-4080.toml \
      --config eval_harness/configs/datasets.toml [--skip-clone] [--limit N] [--only NAME]

Outputs (under <data_dir>):
  datasets/<name>/tasks.jsonl    pipeline input ONLY: instance_id, repo, base_commit, query
  datasets/<name>/labels.jsonl   gold labels; read ONLY by the metrics script
  datasets/<name>/manifest.json  dataset id, revision, counts, hashes
  cache/repos/<owner__repo>.git  bare store holding only the fetched base commits
  cache/checkouts/<owner__repo>/<commit>/   exported tree (no .git)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.common import load_toml


class DatasetError(RuntimeError):
    pass


def _get_json(url: str, timeout: int = 60, attempts: int = 6) -> Any:
    delay = 2.0
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            code = getattr(exc, "code", None)
            if i == attempts - 1 or (code is not None and code < 500 and code != 429):
                raise
            time.sleep(delay)
            delay *= 2


def verify_ids(cfg: dict) -> str:
    """Assert every configured dataset ID is named in the LocAgent README."""
    with urllib.request.urlopen(cfg["locagent_readme_url"], timeout=60) as resp:
        readme = resp.read().decode("utf-8", errors="replace")
    for ds in cfg["dataset"]:
        if ds["hf_id"] not in readme:
            raise DatasetError(f"dataset id {ds['hf_id']} not found in LocAgent README")
    return hashlib.sha256(readme.encode("utf-8")).hexdigest()[:16]


def fetch_rows(cfg: dict, ds: dict) -> tuple[list[dict], dict]:
    hf_id, split = ds["hf_id"], ds["split"]
    q = urllib.parse.quote(hf_id, safe="/")
    splits = _get_json(f"{cfg['rows_api_url']}/splits?dataset={q}")["splits"]
    names = sorted({s["split"] for s in splits})
    if split not in names:
        raise DatasetError(f"{hf_id}: split {split!r} not in {names}")
    meta = _get_json(f"{cfg['hf_api_url']}/{q}")
    rows: list[dict] = []
    offset, total = 0, None
    while total is None or offset < total:
        page = _get_json(
            f"{cfg['rows_api_url']}/rows?dataset={q}&config=default&split={split}"
            f"&offset={offset}&length={cfg['page_size']}")
        total = page["num_rows_total"]
        rows += [r["row"] for r in page["rows"]]
        offset += cfg["page_size"]
    return rows, {"hf_id": hf_id, "split": split, "splits_available": names,
                  "hf_revision": meta.get("sha"), "rows_total_reported": total}


LABEL_RULE = "locagent-code: eval_metric.py cal_metrics_w_dataset (module=first dotted component, __init__ stripped)"


def derive_labels(row: dict) -> dict[str, Any]:
    """Gold from the dataset's own edit_functions ("path:Class.method" / "path:func").

    Follows LocAgent's evaluation code (evaluation/eval_metric.py, cal_metrics_w_dataset):
    files     = unique files of edit_functions
    modules   = "path:" + first dotted component of the qualified name, so a top-level
                function is its own module and a method maps to its class
    functions = "path:qualname" with a trailing ".__init__" removed, de-duplicated
    functions_raw keeps the dataset strings untouched.
    """
    raw = list(row["edit_functions"])
    files: list[str] = []
    modules: list[str] = []
    functions: list[str] = []
    for f in raw:
        path, _, qual = f.partition(":")
        if path not in files:
            files.append(path)
        mod = f"{path}:{qual.split('.')[0]}"
        if mod not in modules:
            modules.append(mod)
        if qual.endswith(".__init__"):
            qual = qual[: -len(".__init__")]
        fn = f"{path}:{qual}"
        if fn not in functions:
            functions.append(fn)
    return {"files": files, "modules": modules, "functions": functions, "functions_raw": raw}


def build_records(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    tasks, labels = [], []
    for r in sorted(rows, key=lambda x: x["instance_id"]):
        tasks.append({"instance_id": r["instance_id"], "repo": r["repo"],
                      "base_commit": r["base_commit"], "query": r["problem_statement"]})
        labels.append({"instance_id": r["instance_id"], **derive_labels(r),
                       "added_functions": list(r.get("added_functions") or []),
                       "category": r.get("category")})
    return tasks, labels


def _write_jsonl(path: Path, records: list[dict]) -> str:
    blob = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    path.write_text(blob, encoding="utf-8", newline="\n")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _git(args: list[str], cwd: Path | None, timeout: int, binary_out: bool = False):
    # Never use stored credentials; public repos only, no interactive prompts.
    cmd = ["git", "-c", "credential.helper=", "-c", "core.askPass=", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout,
                          text=not binary_out)


def safe_name(repo: str) -> str:
    return repo.replace("/", "__")


_REPO_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _repo_lock(repo: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _REPO_LOCKS.setdefault(repo, threading.Lock())


def _fetch_checkout(cfg: dict, cache: Path, repo: str, commit: str) -> dict[str, Any]:
    """Export <repo>@<commit> into cache/checkouts without .git. Skips if present."""
    g = cfg["git"]
    dest = cache / "checkouts" / safe_name(repo) / commit
    report_path = dest.parent / f"{commit}.report.json"
    if report_path.is_file() and dest.is_dir():
        return {"repo": repo, "commit": commit, "status": "exists"}

    store = cache / "repos" / f"{safe_name(repo)}.git"
    store.parent.mkdir(parents=True, exist_ok=True)
    with _repo_lock(repo):  # one git process per bare store at a time
        if not (store / "HEAD").is_file():
            r = _git(["init", "--bare", "-q", "--template=", str(store)], None, 60)
            if r.returncode:
                return {"repo": repo, "commit": commit, "status": "error",
                        "error": r.stderr[:300]}
        # `git archive` silently drops paths marked export-ignore/export-subst in the repo's own
        # .gitattributes (e.g. build_tools/, benchmarks/). info/attributes has the highest
        # precedence, so override them: the checkout must hold every file in the commit.
        info = store / "info"
        info.mkdir(exist_ok=True)
        (info / "attributes").write_text("* -export-ignore -export-subst\n", encoding="utf-8")
        url = g["repo_url_template"].format(repo=repo)
        r = _git(["fetch", "-q", f"--depth={g['fetch_depth']}", "--no-tags", url, commit],
                 store, g["timeout_s"])
        if r.returncode:
            return {"repo": repo, "commit": commit, "status": "error",
                    "error": (r.stderr or "")[:300]}

    tmp = dest.parent / f".{commit}.partial"
    if tmp.exists():  # leftover from a crashed run; inside the cache folder only
        import shutil
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    skipped, seen_lower, collisions, n_files = [], {}, [], 0
    proc = subprocess.Popen(
        ["git", "-c", "credential.helper=", "archive", "--format=tar", commit],
        cwd=store, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            for m in tf:
                low = m.name.lower()
                if low in seen_lower and seen_lower[low] != m.name:
                    collisions.append([seen_lower[low], m.name])  # case-insensitive FS clash
                seen_lower[low] = m.name
                if m.issym() or m.islnk():
                    skipped.append([m.name, "link"])
                    continue
                try:
                    tf.extract(m, tmp, filter="data")
                    n_files += int(m.isfile())
                except (OSError, tarfile.TarError, ValueError) as exc:
                    skipped.append([m.name, str(exc)[:80]])
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode:
        return {"repo": repo, "commit": commit, "status": "error",
                "error": proc.stderr.read().decode("utf-8", "replace")[:300]}
    if (tmp / ".git").exists():
        raise DatasetError(f".git present in export of {repo}@{commit}")
    # Integrity: every regular file in the commit tree must be on disk (or listed as skipped).
    ls = _git(["ls-tree", "-r", "-z", commit], store, 120).stdout
    n_tree = sum(1 for e in ls.split("\0") if e.startswith(("100644", "100755")))
    n_skipped_files = sum(1 for _, why in skipped if why != "link")
    if n_files + n_skipped_files != n_tree and not skipped:
        import shutil
        shutil.rmtree(tmp)
        return {"repo": repo, "commit": commit, "status": "error",
                "error": f"export has {n_files} files, commit tree has {n_tree}"}
    tree = _git(["rev-parse", f"{commit}^{{tree}}"], store, 60).stdout.strip()
    if dest.exists():  # no report file => never verified (interrupted run); replace it
        import shutil
        shutil.rmtree(dest)
    tmp.rename(dest)
    report = {"repo": repo, "commit": commit, "tree": tree, "files": n_files, "tree_files": n_tree,
              "skipped_members": skipped, "case_collisions": collisions}
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return {"repo": repo, "commit": commit, "status": "fetched", "files": n_files,
            "skipped": len(skipped), "collisions": len(collisions)}


def fetch_checkout(cfg: dict, cache: Path, repo: str, commit: str) -> dict[str, Any]:
    """One task's failure (timeout, git error, disk) is recorded, never stops the run."""
    try:
        return _fetch_checkout(cfg, cache, repo, commit)
    except Exception as exc:  # noqa: BLE001
        return {"repo": repo, "commit": commit, "status": "error",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def interleave_by_repo(tasks: list[dict]) -> list[dict]:
    by_repo: dict[str, list[dict]] = {}
    for t in tasks:
        by_repo.setdefault(t["repo"], []).append(t)
    out: list[dict] = []
    queues = [list(v) for _, v in sorted(by_repo.items(), key=lambda kv: -len(kv[1]))]
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def check_gold_present(cache: Path, tasks: list[dict], labels: list[dict]) -> list[dict]:
    """Integrity: every gold file must exist in its checkout (reads labels; harness-only)."""
    lab = {x["instance_id"]: x for x in labels}
    missing = []
    for t in tasks:
        root = cache / "checkouts" / safe_name(t["repo"]) / t["base_commit"]
        if not root.is_dir():
            continue
        for f in lab[t["instance_id"]]["files"]:
            if not (root / f).is_file():
                missing.append({"instance_id": t["instance_id"], "file": f})
    return missing


def relabel(data_dir: Path) -> int:
    """Offline: re-derive labels.jsonl from the stored dataset strings with the current LABEL_RULE."""
    for d in sorted((data_dir / "datasets").iterdir()):
        lp, mp = d / "labels.jsonl", d / "manifest.json"
        if not (lp.is_file() and mp.is_file()):
            continue
        old = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines()]
        new = []
        for r in old:
            raw = r.get("functions_raw") or r["functions"]
            new.append({"instance_id": r["instance_id"], **derive_labels({"edit_functions": raw}),
                        "added_functions": r["added_functions"], "category": r["category"]})
        h = _write_jsonl(lp, new)
        m = json.loads(mp.read_text(encoding="utf-8"))
        m.update({"labels_sha256": h, "gold_derivation": LABEL_RULE})
        mp.write_text(json.dumps(m, indent=1), encoding="utf-8")
        print(f"[PASS] relabelled {d.name}: {len(new)} tasks, labels_sha256={h[:16]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--skip-clone", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="only first N tasks per dataset (debug)")
    ap.add_argument("--only", default=None, help="dataset name")
    ap.add_argument("--relabel", action="store_true", help="offline: regenerate labels.jsonl only")
    args = ap.parse_args()
    profile, cfg = load_toml(args.profile), load_toml(args.config)
    data_dir = Path(profile["data_dir"])
    if args.relabel:
        return relabel(data_dir)
    cache = data_dir / "cache"

    readme_hash = verify_ids(cfg)
    print(f"[PASS] dataset IDs found in LocAgent README (sha256[:16]={readme_hash})")

    for ds in cfg["dataset"]:
        if args.only and ds["name"] != args.only:
            continue
        rows, meta = fetch_rows(cfg, ds)
        if len(rows) != ds["expected_count"]:
            print(f"[FAIL] {ds['name']}: got {len(rows)} rows, expected {ds['expected_count']}. "
                  "STOPPING; nothing written for this dataset.")
            return 2
        print(f"[PASS] {ds['name']}: {len(rows)} rows == expected {ds['expected_count']} "
              f"(hf revision {meta['hf_revision'][:10]})")
        tasks, labels = build_records(rows)
        bad = [t["instance_id"] for t in tasks if not (t["base_commit"] and t["query"])]
        if bad or any(not l["files"] for l in labels):
            print(f"[FAIL] {ds['name']}: empty gold/commit/query for {bad[:5]}")
            return 2
        out = data_dir / "datasets" / ds["name"]
        out.mkdir(parents=True, exist_ok=True)
        h_t = _write_jsonl(out / "tasks.jsonl", tasks)
        h_l = _write_jsonl(out / "labels.jsonl", labels)
        repos = sorted({t["repo"] for t in tasks})
        manifest = {**meta, "name": ds["name"], "count": len(tasks), "repos": len(repos),
                    "tasks_sha256": h_t, "labels_sha256": h_l,
                    "ids_verified_against": cfg["locagent_readme_url"],
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                    "gold_derivation": LABEL_RULE}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"[PASS] wrote tasks/labels/manifest for {ds['name']} ({len(repos)} repos)")

        if args.skip_clone:
            continue
        todo = tasks[: args.limit] if args.limit else tasks
        todo = interleave_by_repo(todo)  # spread workers over different repos
        results = []
        with ThreadPoolExecutor(max_workers=cfg["git"]["workers"]) as pool:
            futs = [pool.submit(fetch_checkout, cfg, cache, t["repo"], t["base_commit"])
                    for t in todo]
            for i, f in enumerate(as_completed(futs), 1):
                r = f.result()
                results.append(r)
                if r["status"] == "error":
                    print(f"[WARN] {r['repo']}@{r['commit'][:10]}: {r['error']}", flush=True)
                if i % 25 == 0 or i == len(futs):
                    print(f"  {ds['name']}: {i}/{len(futs)} done", flush=True)
        n_err = sum(r["status"] == "error" for r in results)
        miss = check_gold_present(cache, todo, labels)
        summary = {"fetched": sum(r["status"] == "fetched" for r in results),
                   "exists": sum(r["status"] == "exists" for r in results),
                   "errors": [r for r in results if r["status"] == "error"],
                   "gold_files_missing_in_checkout": miss}
        (out / "checkout_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
        print(f"[{'PASS' if not n_err and not miss else 'FAIL'}] {ds['name']} checkouts: "
              f"{summary['fetched']} fetched, {summary['exists']} existed, {n_err} errors, "
              f"{len(miss)} gold files missing from checkout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
