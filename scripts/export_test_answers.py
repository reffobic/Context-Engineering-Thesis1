"""Export test-run SWE-QA answers into judge input files. Reads only; writes into --out.

Run from the repo root. Per arm it merges swe-qa-w1 and swe-qa-rest (the 120-task sample) into <ARM>_answers.jsonl,
writes one reference.jsonl (question + gold answer from labels.jsonl) and join.jsonl (status, route, failure flag per task).
"""
import argparse
import json
from pathlib import Path

ARMS = ["GAPA", "ALWAYS_RAG", "ALWAYS_GRAPH", "GASR", "SELF_ROUTE", "PREASSESS", "ALWAYS_LC"]
DATASETS_DEFAULT = ["swe-qa-w1", "swe-qa-rest"]


def rows(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=Path, required=True, help="folder holding <dataset>/<ARM>/results.jsonl")
ap.add_argument("--data", type=Path, default=Path("eval_harness/data/datasets"))
ap.add_argument("--out", type=Path, required=True)
ap.add_argument("--arms", nargs="*", default=ARMS)
ap.add_argument(
    "--datasets",
    nargs="*",
    default=DATASETS_DEFAULT,
    help="SWE-QA dataset dirs under --runs / --data (default: swe-qa-w1 swe-qa-rest)",
)
a = ap.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
DATASETS = list(a.datasets)

tasks, gold = {}, {}
for ds in DATASETS:
    for t in rows(a.data / ds / "tasks.jsonl"):
        tasks[t["instance_id"]] = t
    lab = rows(a.data / ds / "labels.jsonl")
    key = next((k for k in ("instance_id", "task_id", "id") if k in lab[0]), None)
    if key is None:
        raise SystemExit(f"labels.jsonl has no id field; keys are {list(lab[0])}")
    for l in lab:
        gold[l[key]] = l["gold_answer"]

with (a.out / "reference.jsonl").open("w", encoding="utf-8") as f:
    for tid, t in tasks.items():
        if tid in gold:
            f.write(json.dumps({"question": t["query"], "answer": gold[tid]}, ensure_ascii=False) + "\n")

with (a.out / "join.jsonl").open("w", encoding="utf-8") as jf:
    for arm in a.arms:
        n = 0
        with (a.out / f"{arm}_answers.jsonl").open("w", encoding="utf-8") as cf:
            for ds in DATASETS:
                p = a.runs / ds / arm / "results.jsonl"
                if not p.is_file():
                    print("MISSING", p)
                    continue
                for r in rows(p):
                    t = tasks.get(r["task_id"])
                    if not t:
                        continue
                    cf.write(json.dumps({"question": t["query"], "answer": r.get("answer") or "", "task_id": r["task_id"],
                                         "arm": arm, "repo": r.get("repo")}, ensure_ascii=False) + "\n")
                    jf.write(json.dumps({"task_id": r["task_id"], "arm": arm, "route": r.get("route"), "status": r.get("status"),
                                         "failed": bool(r.get("counted_as_failed")),
                                         "answer_chars": len(r.get("answer") or "")}) + "\n")
                    n += 1
        print(arm, n, "answers")
print("gold answers:", len(gold), "tasks:", len(tasks))