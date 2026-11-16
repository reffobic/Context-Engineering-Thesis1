"""LocAgent external baseline -> harness metrics.

Reads the ``loc_outputs.jsonl`` that LocAgent's own ``auto_search_main`` writes (found_files / found_modules / found_entities, one
list per sample), merges the samples the way LocAgent does (``util/process_output.py: merge_sample_locations``, ranking_method="mrr"),
and scores the merged ranked lists with the harness's own ``acc_at`` against ``labels.jsonl`` for the tasks of the harness sample.

LocAgent runs on its own indexes and its own model server. It is NOT pinned to a pipeline SHA; only the task list is shared.

  PYTHONPATH=eval_harness/src python -m harness.locagent_baseline \
      --loc-outputs /home/g7/Documents/HANA/locagent-baseline/locagent_run/full83/loc_outputs.jsonl \
      --data-dir eval_harness/data [--label "LocAgent + fine-tuned Qwen2.5-Coder-7B (int8)"]

Writes <data-dir>/baselines/locagent_<dataset>.json for each dataset that has tasks in the file. harness.metrics reads these files to
fill the "LocAgent + fine-tuned Qwen2.5-Coder-7B" row of Tables VII / VIII.
Tasks of the sample that have no output are counted as failed (score 0) and listed in ``missing``; nothing is dropped silently.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from .metrics import acc_at

DATASETS = ("swe-bench-lite", "loc-bench")
# (level, key in loc_outputs, key in labels, k values) - the union of the SWE-Bench-Lite and the Loc-Bench columns
LEVELS = (("file", "found_files", "files", (1, 3, 5, 10)),
          ("module", "found_modules", "modules", (5, 10, 15)),
          ("function", "found_entities", "functions", (5, 10, 15)))


def merge_mrr(samples: list[list[str]]) -> list[str]:
    """LocAgent's mrr ranking over the per-sample lists: weight = sum of 1/rank, sorted descending, ties in first-seen order."""
    weights: dict[str, float] = collections.defaultdict(float)
    for sample in samples:
        for rank, loc in enumerate(sample, start=1):
            weights[loc] += 1 / rank
    return [loc for loc, _ in sorted(weights.items(), key=lambda x: x[1], reverse=True)]


def _samples(value) -> list[list[str]]:
    """A task has either a list of per-sample lists (raw loc_outputs) or one flat list (already merged)."""
    if not value:
        return []
    if isinstance(value[0], list):
        return [list(v) for v in value]
    return [list(value)]


def _norm_function(found: list[str]) -> list[str]:
    """LocAgent's scorer treats ``Class.__init__`` as the class; keep first occurrences only."""
    out, seen = [], set()
    for f in found:
        if f.endswith(".__init__"):
            f = f[: -len(".__init__")]
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def score_task(row: dict | None, gold: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for lvl, key, gkey, ks in LEVELS:
        pred = merge_mrr(_samples((row or {}).get(key) or []))
        if lvl == "function":
            pred = _norm_function(pred)
        for k in ks:
            out[f"{lvl}_acc@{k}"] = acc_at(pred, gold[gkey], k) if row else 0.0
    return out


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def run(loc_outputs: Path, data_dir: Path, label: str) -> dict[str, dict]:
    preds = {r["instance_id"]: r for r in load_jsonl(loc_outputs)}
    results = {}
    for ds in DATASETS:
        sample_file = data_dir / "samples" / f"{ds}.sample.json"
        labels_file = data_dir / "datasets" / ds / "labels.jsonl"
        if not (sample_file.is_file() and labels_file.is_file()):
            continue
        ids = list(json.loads(sample_file.read_text(encoding="utf-8"))["instance_ids"])
        if not any(i in preds for i in ids):
            continue
        gold = {g["instance_id"]: g for g in load_jsonl(labels_file)}
        per_task = {i: score_task(preds.get(i), gold[i]) for i in ids if i in gold}
        cols = sorted(next(iter(per_task.values())))
        mean = {c: sum(t[c] for t in per_task.values()) / len(per_task) for c in cols}
        results[ds] = {
            "dataset": ds, "label": label, "n_tasks": len(per_task),
            "n_with_output": sum(1 for i in per_task if i in preds), "missing": [i for i in per_task if i not in preds],
            "ranking_method": "mrr", "metrics": mean, "per_task": per_task,
            "note": "LocAgent runs on its own indexes and model server; not pinned to a pipeline SHA. Missing tasks score 0.",
            "source": str(loc_outputs),
        }
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--loc-outputs", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, default=Path("eval_harness/data"))
    ap.add_argument("--label", default="LocAgent + fine-tuned Qwen2.5-Coder-7B")
    a = ap.parse_args()
    res = run(a.loc_outputs, a.data_dir, a.label)
    out = a.data_dir / "baselines"
    out.mkdir(parents=True, exist_ok=True)
    for ds, r in res.items():
        (out / f"locagent_{ds}.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"{ds}: {r['n_with_output']}/{r['n_tasks']} tasks with output, missing={r['missing']}")
        print("  " + "  ".join(f"{k}={100 * v:.1f}" for k, v in r["metrics"].items()))
    if not res:
        print("no dataset has tasks in the loc outputs")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
