"""Answer-level localisation metric: score the files / functions the answer model itself lists (logs only, CPU only).

  python -m harness.answer_scoring --runs DIR [DIR ...] --data-dir D --out OUT

The answer is expected to start with the LOCATIONS section that the opt-in `answer_locations_instruction` flag asks for:

    LOCATIONS
    - `path/to/file.py`
    - path/to/other.py::Class.method
    END_LOCATIONS
    short explanation ...

Parsing: the section starts at the first line that is only the word LOCATIONS (case-insensitive; markdown hashes, bold,
backticks and a trailing colon are tolerated). It ends at a line END_LOCATIONS; if the marker is absent it ends at the
first non-blank line that is not a path. `truncated` is not a parser notion: it is true only when the answer call's
done_reason is "length" (num_predict reached); a stop-sequence hit (done_reason "stop") is not truncation. Bullets,
numbering, backticks, quotes and trailing punctuation are stripped; "path - reason" keeps the path. Line order is the rank.

Normalisation follows locmap (LocAgent naming): file = path with "/" separators and no leading "./"; function =
"path:Class.method" (single colon, a trailing ".__init__" dropped); module = "path:" + first dotted component. An item
without "::" contributes a file only. Lists are de-duplicated keeping the first rank, then scored with metrics.acc_at at
the same k as the context metrics. An answer with no LOCATIONS section or no valid item scores 0 on every metric and is
counted in the separate failure class `answer_no_locations` (not mixed into the gate / hard failure kinds).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Callable

from .metrics import LEVELS, acc_at, dedupe, load_gold, load_jobs, recall_at

FAIL_CLASS = "answer_no_locations"
_HEADER = re.compile(r"^\W*LOCATIONS\W*$", re.I)
_END = re.compile(r"^\W*END_LOCATIONS\W*$", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s+")
_PATH = re.compile(r"^(?P<path>[A-Za-z0-9_.@+\-][A-Za-z0-9_./@+\-]*)(?:(?:::|:(?=[A-Za-z_]))(?P<qual>[A-Za-z_][\w.]*))?$")
_SEP_REST = re.compile(r"^\s*(?:[-–—:(#]|→)")


def _strip_decor(line: str) -> str:
    s = _BULLET.sub("", line.strip())
    return s.strip().strip("`'\"* ").rstrip(",;:").strip("`'\"* ")


def parse_item(line: str) -> str | None:
    """One LOCATIONS line -> "path" or "path::qual", or None if the line is not a path."""
    s = _strip_decor(line)
    if not s:
        return None
    if " " in s:                                    # "path - reason", "path (reason)": keep the first token only then
        head, rest = s.split(None, 1)[0], s.split(None, 1)[1]
        if not _SEP_REST.match(" " + rest):
            return None
        s = _strip_decor(head)
    s = s.rstrip(".") if not re.search(r"\.\w+$", s) else s
    m = _PATH.match(s)
    if not m:
        return None
    path = m.group("path")
    if "/" not in path and not re.search(r"\.[A-Za-z0-9]{1,8}$", path):
        return None                                 # a bare word is prose, not a path
    return path + (f"::{m.group('qual')}" if m.group("qual") else "")


def parse_locations(text: str | None) -> dict:
    """-> {"section": bool, "marker": bool, "items": [raw "path[::qual]"...]} (rank = order)."""
    lines = (text or "").splitlines()
    start = next((i for i, ln in enumerate(lines) if _HEADER.match(ln.strip())), None)
    if start is None:
        return {"section": False, "marker": False, "items": []}
    items, marker = [], False
    for ln in lines[start + 1:]:
        if _END.match(ln.strip()):
            marker = True
            break
        if not ln.strip():
            continue
        it = parse_item(ln)
        if it is None:
            break
        items.append(it)
    return {"section": True, "marker": marker, "items": items}


def normalise(raw: str) -> dict:
    path, _, qual = raw.partition("::")
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    out = {"file": path, "module": None, "function": None}
    if qual:
        q = qual[: -len(".__init__")] if qual.endswith(".__init__") else qual
        out["function"] = f"{path}:{q}"
        out["module"] = f"{path}:{qual.split('.')[0]}"
    return out


def answer_lists(text: str | None) -> tuple[dict, dict]:
    """(parse info, rank lists {"files","modules","functions"}); each list de-duplicated keeping the first rank."""
    info = parse_locations(text)
    norm = [normalise(i) for i in info["items"]]
    lists = {"files": dedupe([n["file"] for n in norm if n["file"]]),
             "modules": dedupe([n["module"] for n in norm if n["module"]]),
             "functions": dedupe([n["function"] for n in norm if n["function"]])}
    return info, lists


def score_answer(text: str | None, gold: dict, done_reason: str | None = None) -> dict:
    """Per-task result: status, failure class, lists and the `answer.<level>_acc@k` / `answer.file_recall@5` metrics.
    `truncated` = the answer call stopped on num_predict (done_reason == "length")."""
    info, lists = answer_lists(text)
    ok = bool(lists["files"])
    status = "ok" if ok else ("no_valid_items" if info["section"] else "no_section")
    out: dict = {"status": status, "failure_class": "" if ok else FAIL_CLASS, "marker": info["marker"],
                 "truncated": done_reason == "length", "n_items": len(info["items"]), "lists": lists}
    for lvl, key, ks in LEVELS:
        for k in ks:
            out[f"answer.{lvl}_acc@{k}"] = acc_at(lists[key], gold[key], k) if ok else 0.0
    out["answer.file_recall@5"] = recall_at(lists["files"], gold["files"], 5) if ok else 0.0
    return out


def conversion(rows: list[dict]) -> dict:
    """Of tasks whose final context holds ALL gold files, the share whose answer also lists all of them."""
    base = [r for r in rows if r["context_has_all_gold_files"]]
    conv = [r for r in base if r["answer_has_all_gold_files"]]
    return {"n_context_has_all_gold": len(base), "n_also_in_answer": len(conv),
            "conversion_rate": (len(conv) / len(base)) if base else None}


def path_validity(rows: list[dict]) -> dict:
    """Share of distinct listed paths (per task) that are not files of the base-commit checkout."""
    total = sum(r["n_paths"] for r in rows)
    bad = sum(r["n_paths_not_in_checkout"] for r in rows)
    return {"n_paths": total, "n_not_in_base_commit": bad, "share_not_in_base_commit": (bad / total) if total else None}


def score_job(job: dict, gold: dict[str, dict], data_dir: Path,
              exists: Callable[[dict, str], bool] | None = None) -> list[dict]:
    def default_exists(line: dict, path: str) -> bool:
        root = data_dir / "cache" / "checkouts" / line["repo"].replace("/", "__") / line["base_commit"]
        return (root / path).is_file()

    exists = exists or default_exists
    rows = []
    for line in job["rows"]:
        g = gold[line["task_id"]]
        s = score_answer(line.get("answer"), g, (line.get("answer_call") or {}).get("done_reason"))
        files = s["lists"]["files"]
        ctx_files = set(((line.get("ranked") or {}).get("final_context") or {}).get("files") or [])
        gf = set(g["files"])
        rows.append({
            "job_id": job["job_id"], "arm": job["arm"], "dataset": job["dataset"], "ablation": job["ablation"], "task_id": line["task_id"],
            **{k: v for k, v in s.items() if k != "lists"},
            "n_paths": len(files), "n_paths_not_in_checkout": sum(1 for p in files if not exists(line, p)),
            "context_has_all_gold_files": bool(gf) and gf <= ctx_files,
            "answer_has_all_gold_files": bool(gf) and gf <= set(files)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    data = a.data_dir.resolve()
    a.out.mkdir(parents=True, exist_ok=True)
    summary, all_rows = {}, []
    for j in load_jobs([p.resolve() for p in a.runs]):
        gold = load_gold(data, j["dataset"])
        rows = score_job(j, gold, data)
        all_rows += rows
        n = len(rows)
        mean = lambda k: (sum(r[k] for r in rows) / n) if n else None
        summary[f"{j['model']}|{j['dataset']}|{j['arm']}|{j['ablation']}"] = {
            "n_tasks": n, "n_answer_no_locations": sum(1 for r in rows if r["failure_class"]),
            "n_truncated": sum(1 for r in rows if r["truncated"]),
            **{f"answer.{lvl}_acc@{k}": mean(f"answer.{lvl}_acc@{k}") for lvl, _, ks in LEVELS for k in ks},
            **path_validity(rows), **conversion(rows)}
    cols = list(all_rows[0]) if all_rows else []
    with open(a.out / "answer_scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in all_rows:
            w.writerow([r[c] for c in cols])
    (a.out / "answer_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
