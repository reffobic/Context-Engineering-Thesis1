"""Relaxed (secondary) function scoring on the stored Nov 3 answers: which tasks change, and how ambiguous the leaf matches are.

  .venv/bin/python docs/analysis/gate_diagnostics/build_relaxed_scoring.py

Strict stays primary. Uses harness.answer_scoring.relaxed_function_metrics (branch answer-scoring-relaxed) on the logged answers of the
three fixed tiers; the zero rule (failed task = 0) is applied as in nov3_lib.answer_rows. Reads eval_harness/data, writes only
relaxed_scoring_tasks.csv and relaxed_scoring_report.md next to this file.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "docs" / "analysis"))
sys.path.insert(0, str(ROOT / "eval_harness" / "src"))
import nov3_lib as L                                                       # noqa: E402
from harness.answer_scoring import answer_lists, leaf_key, relaxed_function_metrics   # noqa: E402
from harness.locmap import Mapper                                          # noqa: E402

DATA = ROOT / "eval_harness" / "data"
TIERS = [("RAG", "ALWAYS_RAG"), ("GRAPH", "ALWAYS_GRAPH"), ("LC", "ALWAYS_LC")]
res = L.load_results()
pt, ans = L.load_per_task(), L.load_answers()
gold = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "labels.jsonl") if x.strip()} for ds in L.DATASETS}
strict = {(ds, arm): {r["task_id"]: r for r in L.answer_rows(pt, ans, ds, arm, "primary")} for ds in L.DATASETS for _, arm in TIERS}
mappers: dict = {}


def n_defs_with_leaf(repo: str, base: str, path: str, leaf: str) -> int | None:
    key = (repo, base)
    if key not in mappers:
        mappers[key] = Mapper(DATA / "cache" / "checkouts" / repo.replace("/", "__") / base)
    defs = mappers[key].defs(path)
    if isinstance(defs, str):
        return None                                                          # not python / not in checkout / syntax error
    return sum(1 for d in defs if d.kind == "function" and d.qualname.rsplit(".", 1)[-1] == leaf)


rows = []
for ds in L.DATASETS:
    for tid in sorted(strict[(ds, "ALWAYS_RAG")]):
        gfn = list(gold[ds][tid]["functions"])
        for rt, arm in TIERS:
            r = res[(ds, arm)][tid]
            s = strict[(ds, arm)][tid]
            _, lists = answer_lists(r.get("answer"))
            top = lists["functions"][:10]
            m = relaxed_function_metrics(top, gfn, 10)
            if s["zeroed"]:
                m = {k: 0.0 for k in m}
            top_leaf = {leaf_key(p) for p in top}
            leaf_only, ambiguous, unknown = 0, 0, 0
            for g in gfn:
                if g in top or leaf_key(g) not in top_leaf:
                    continue
                leaf_only += 1                                               # found only by leaf matching
                n = n_defs_with_leaf(r["repo"], r["base_commit"], *leaf_key(g))
                if n is None:
                    unknown += 1
                elif n > 1:
                    ambiguous += 1
            sa = s["function_acc@10"]
            rows.append({"dataset": ds, "task_id": tid, "tier": rt, "n_gold_functions": len(gfn),
                         "strict_function_acc@10": sa, "leaf_function_acc@10": m["answer.function_acc@10_leaf"],
                         "strict_recall@10": round(m["answer.function_recall@10"], 4), "leaf_recall@10": round(m["answer.function_recall@10_leaf"], 4),
                         "flips_acc_0_to_1": sa == 0 and m["answer.function_acc@10_leaf"] == 1.0,
                         "gold_functions_found_only_by_leaf": leaf_only, "of_which_leaf_ambiguous_in_gold_file": ambiguous,
                         "of_which_gold_file_unparsable": unknown,
                         "partial_recall_strict_acc0": sa == 0 and 0 < m["answer.function_recall@10"] < 1,
                         "answer_functions_top10": ";".join(top), "gold_functions": ";".join(gfn)})
with open(HERE / "relaxed_scoring_tasks.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

out = []
P = out.append
P("# Relaxed scoring (secondary) on the stored Nov 3 answers\n")
P("Strict (exact `path:Class.method`, LocAgent cap rule) stays primary. Variants: **(a) leaf** = same file and same final name component, class prefix ignored; **(b) recall@10** = gold functions found / min(#gold, 10). "
  "Zero rule applied (no fixed-tier task failed, so it changes nothing here). Per-task rows: `relaxed_scoring_tasks.csv`.\n")
P("## Mean function metrics and tasks that change\n")
P("| Dataset | Tier | Tasks | Strict Acc@10 | Leaf Acc@10 | Tasks flipping 0→1 | Strict recall@10 | Leaf recall@10 | Tasks with partial recall but strict Acc 0 |")
P("|---|---|---|---|---|---|---|---|---|")
for ds in L.DATASETS:
    for rt, _ in TIERS:
        R = [r for r in rows if r["dataset"] == ds and r["tier"] == rt]
        n = len(R)
        P(f"| {L.DS_LABEL[ds]} | {rt} | {n} | {sum(r['strict_function_acc@10'] for r in R) / n:.3f} | {sum(r['leaf_function_acc@10'] for r in R) / n:.3f} | "
          f"{sum(r['flips_acc_0_to_1'] for r in R)} | {sum(r['strict_recall@10'] for r in R) / n:.3f} | {sum(r['leaf_recall@10'] for r in R) / n:.3f} | "
          f"{sum(r['partial_recall_strict_acc0'] for r in R)} |")
P("")
P("## Tasks that flip 0→1 under leaf matching (IDs)\n")
for ds in L.DATASETS:
    for rt, _ in TIERS:
        ids = [r["task_id"] for r in rows if r["dataset"] == ds and r["tier"] == rt and r["flips_acc_0_to_1"]]
        P(f"- {L.DS_LABEL[ds]} {rt} ({len(ids)}): " + (", ".join(ids) or "none"))
anyf = defaultdict = None
P("")
P("Tasks that flip in at least one tier, and whether they were all-tiers-zero under strict scoring:\n")
for ds in L.DATASETS:
    flip = sorted({r["task_id"] for r in rows if r["dataset"] == ds and r["flips_acc_0_to_1"]})
    floor = [t for t in flip if all(r["strict_function_acc@10"] == 0 for r in rows if r["dataset"] == ds and r["task_id"] == t)]
    after = [t for t in sorted(strict[(ds, 'ALWAYS_RAG')]) if all(next(x for x in rows if x['dataset'] == ds and x['task_id'] == t and x['tier'] == rt)['leaf_function_acc@10'] == 0 for rt, _ in TIERS)]
    P(f"- {L.DS_LABEL[ds]}: {len(flip)} tasks flip in some tier ({', '.join(flip) or 'none'}); all-tiers-zero count strict -> leaf: "
      f"{sum(all(x['strict_function_acc@10'] == 0 for x in rows if x['dataset'] == ds and x['task_id'] == t) for t in sorted(strict[(ds, 'ALWAYS_RAG')]))} -> {len(after)}.")
P("")
P("## Ambiguity of leaf-only matches\n")
P("A gold function found only by leaf matching is *ambiguous* when the gold file at the base commit defines more than one function with that leaf name (Class A.run and Class B.run), so the leaf match may point at the wrong definition. Counted over (task, tier) answers.\n")
P("| Dataset | Tier | Gold functions found only by leaf | Ambiguous | File not parsable | Flips whose every leaf-only match is unambiguous |")
P("|---|---|---|---|---|---|")
for ds in L.DATASETS:
    for rt, _ in TIERS:
        R = [r for r in rows if r["dataset"] == ds and r["tier"] == rt]
        clean = sum(1 for r in R if r["flips_acc_0_to_1"] and r["of_which_leaf_ambiguous_in_gold_file"] == 0 and r["of_which_gold_file_unparsable"] == 0)
        P(f"| {L.DS_LABEL[ds]} | {rt} | {sum(r['gold_functions_found_only_by_leaf'] for r in R)} | {sum(r['of_which_leaf_ambiguous_in_gold_file'] for r in R)} | "
          f"{sum(r['of_which_gold_file_unparsable'] for r in R)} | {clean} of {sum(r['flips_acc_0_to_1'] for r in R)} |")
P("")
(HERE / "relaxed_scoring_report.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
