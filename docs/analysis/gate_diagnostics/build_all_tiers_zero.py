"""Tasks where all three fixed tiers score 0 on function Acc@10: failure stage per task and a scoring-artefact check (read-only).

  python docs/analysis/gate_diagnostics/build_all_tiers_zero.py

Same data and rules as build_funnel_floor.py (nov3_lib.answer_rows, primary version: failed tasks score 0).
Writes  all_tiers_zero_tasks.csv, all_tiers_zero_artefact.md next to this file. Nothing else is touched.

Failure stage, first rule that applies (A..F as in the request):
  A  no scorable gold function (functions list empty after added_functions are excluded)
  B  at least one fixed tier failed (zero rule)
  C  not every gold file is among the 50 ranked candidates (ranked.after_retrieval)
  D  every gold file is among the 50 but not every gold file is in the top-5 (ranked.retrieved_top5)
  E  every gold file is in the top-5 but not every gold function is in the final context of the RAG or GRAPH tier (union)
  F  every gold function is in a RAG/GRAPH final context but no tier's answer scored (answer missed it)
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "docs" / "analysis"))
sys.path.insert(0, str(ROOT / "eval_harness" / "src"))
import nov3_lib as L                                                       # noqa: E402
from harness.answer_scoring import answer_lists                            # noqa: E402

DATA = ROOT / "eval_harness" / "data" / "datasets"
TIERS = [("RAG", "ALWAYS_RAG"), ("GRAPH", "ALWAYS_GRAPH"), ("LC", "ALWAYS_LC")]
FN, FI = "function_acc@10", "file_acc@5"


def jl(p):
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


res = L.load_results()
pt, ans = L.load_per_task(), L.load_answers()
gold = {ds: {t["instance_id"]: t for t in jl(DATA / ds / "labels.jsonl")} for ds in L.DATASETS}
score = {(ds, arm): {r["task_id"]: r for r in L.answer_rows(pt, ans, ds, arm, "primary")} for ds in L.DATASETS for _, arm in TIERS}


def leaf(fid: str) -> str:
    return fid.split(":", 1)[-1].split(".")[-1]


rows, detail = [], {}
for ds in L.DATASETS:
    ids = sorted(score[(ds, "ALWAYS_RAG")])
    for tid in ids:
        if not all(score[(ds, a)][tid][FN] == 0 for _, a in TIERS):
            continue
        g = gold[ds][tid]
        gf, gfn = list(g["files"]), list(g["functions"])
        r0 = res[(ds, "ALWAYS_RAG")][tid]
        rk = r0["ranked"]
        ret50 = set(rk["after_retrieval"]["files"])
        top5 = set(rk["retrieved_top5"]["files"])
        ctx_fn, ctx_fi = set(), set()
        per_tier = {}
        for rt, arm in TIERS:
            row = res[(ds, arm)][tid]
            fc = (row.get("ranked") or {}).get("final_context") or {}
            if rt != "LC":                                                  # an LC answerer saw the long context, not these lists
                ctx_fn |= set(fc.get("functions") or [])
                ctx_fi |= set(fc.get("files") or [])
            _, lists = answer_lists(row.get("answer"))
            per_tier[rt] = lists
        n_ret = sum(1 for x in gf if x in ret50)
        n_top5 = sum(1 for x in gf if x in top5)
        n_ctx_f = sum(1 for x in gf if x in ctx_fi)
        n_ctx_fn = sum(1 for x in gfn if x in ctx_fn)
        failed = {rt: bool(score[(ds, a)][tid]["failed"]) for rt, a in TIERS}
        if not gfn:
            stage = "A"
        elif any(failed.values()):
            stage = "B"
        elif n_ret < len(gf):
            stage = "C"
        elif n_top5 < len(gf):
            stage = "D"
        elif n_ctx_fn < len(gfn):
            stage = "E"
        else:
            stage = "F"
        # stage ignoring A/B, to show what the failure would be if the zero rule / empty gold did not apply
        if not gfn:
            alt = ""
        elif n_ret < len(gf):
            alt = "C"
        elif n_top5 < len(gf):
            alt = "D"
        elif n_ctx_fn < len(gfn):
            alt = "E"
        else:
            alt = "F"
        strict = {rt: sum(1 for x in gfn if x in set(per_tier[rt]["functions"][:10])) for rt in per_tier}
        lenient = {rt: sum(1 for x in gfn if any(leaf(x) == leaf(y) and x.split(":")[0] == y.split(":")[0] for y in per_tier[rt]["functions"][:10])) for rt in per_tier}
        lenient_any_file = {rt: sum(1 for x in gfn if any(leaf(x) == leaf(y) for y in per_tier[rt]["functions"][:10])) for rt in per_tier}
        ans_files_hit = {rt: sum(1 for x in gf if x in set(per_tier[rt]["files"][:5])) for rt in per_tier}
        rows.append({
            "dataset": ds, "task_id": tid, "repo": r0["repo"], "n_gold_files": len(gf), "n_gold_functions": len(gfn),
            "n_added_functions_excluded": len(g["added_functions"]),
            **{f"{rt}_function_acc@10": score[(ds, a)][tid][FN] for rt, a in TIERS},
            **{f"{rt}_file_acc@5": score[(ds, a)][tid][FI] for rt, a in TIERS},
            "file_acc@5_also_0_in_all_three": all(score[(ds, a)][tid][FI] == 0 for _, a in TIERS),
            "failure_stage": stage, "stage_ignoring_A_B": alt,
            "gold_files_in_ret50": n_ret, "gold_files_in_top5": n_top5, "gold_files_in_rag_graph_context": n_ctx_f,
            "gold_functions_in_rag_graph_context": n_ctx_fn,
            **{f"{rt}_failed": failed[rt] for rt in failed},
            **{f"{rt}_gold_functions_hit_strict": strict[rt] for rt in strict},
            **{f"{rt}_gold_functions_hit_leaf_same_file": lenient[rt] for rt in lenient},
            **{f"{rt}_gold_functions_hit_leaf_any_path": lenient_any_file[rt] for rt in lenient_any_file},
            **{f"{rt}_gold_files_in_answer_top5": ans_files_hit[rt] for rt in ans_files_hit},
            "gold_files": ";".join(gf), "gold_functions": ";".join(gfn), "added_functions_excluded": ";".join(g["added_functions"]),
        })
        detail[(ds, tid)] = {"gold": gfn, "answers": {rt: per_tier[rt]["functions"] for rt in per_tier}, "answer_files": {rt: per_tier[rt]["files"] for rt in per_tier}}

with open(HERE / "all_tiers_zero_tasks.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

# ── report ───────────────────────────────────────────────────────────────────────────────────────────────────────
out = []
P = out.append
P("# All-tiers-zero tasks: counts and scoring-artefact check\n")
P("Generated by `build_all_tiers_zero.py`; per-task rows in `all_tiers_zero_tasks.csv`. Zero rule applied.\n")
P("## Counts\n")
P("| Dataset | Function Acc@10 all-tiers-0 | of which file Acc@5 also 0 | A | B | C | D | E | F |")
P("|---|---|---|---|---|---|---|---|---|")
for ds in L.DATASETS:
    R = [r for r in rows if r["dataset"] == ds]
    c = Counter(r["failure_stage"] for r in R)
    P(f"| {L.DS_LABEL[ds]} | {len(R)} | {sum(r['file_acc@5_also_0_in_all_three'] for r in R)} | " + " | ".join(str(c[k]) for k in "ABCDEF") + " |")
P("")
for ds in L.DATASETS:
    P(f"### {L.DS_LABEL[ds]}: task IDs by stage\n")
    for k in "ABCDEF":
        ids = [r["task_id"] for r in rows if r["dataset"] == ds and r["failure_stage"] == k]
        P(f"- **{k}** ({len(ids)}): " + (", ".join(ids) if ids else "none"))
    P("")
P("## Overlap with tasks that have added_functions\n")
for ds in L.DATASETS:
    all_added = [t for t in sorted(score[(ds, "ALWAYS_RAG")]) if gold[ds][t]["added_functions"]]
    zero = [r["task_id"] for r in rows if r["dataset"] == ds]
    ov = [t for t in all_added if t in zero]
    P(f"- {L.DS_LABEL[ds]}: {len(all_added)} tasks have added_functions; {len(ov)} of them are all-tiers-zero on function Acc@10: {', '.join(ov) or 'none'}. "
      f"Stages: {', '.join(next(r['failure_stage'] for r in rows if r['task_id'] == t) for t in ov) or '-'}.")
P("")

P("## Artefact check: gold functions beside the functions each tier's answer returned\n")
P("Answers parsed with the harness's own `answer_lists` (top 10 of the function list). 'strict' is the scorer's exact match on `path:Class.method`; "
  "'leaf same file' counts a gold function as hit when the answer names the same file and the same final method/function name (so `Class.method` vs `method` or a different class prefix would count); "
  "'leaf any path' ignores the file too.\n")
P("| Stage | Tasks | Some tier hit >=1 gold fn (strict) | Some tier hit >=1 under 'leaf same file' | 'leaf same file' hits ALL gold fns in some tier | 'leaf any path' hits ALL gold fns in some tier | Some tier named a gold file in its answer top-5 |")
P("|---|---|---|---|---|---|---|")
for k in "ABCDEF":
    R = [r for r in rows if r["failure_stage"] == k]
    if not R:
        continue
    def best(r, suffix):
        return max(r[f"{rt}_{suffix}"] for rt, _ in TIERS)
    P(f"| {k} | {len(R)} | {sum(1 for r in R if best(r, 'gold_functions_hit_strict') >= 1)} | {sum(1 for r in R if best(r, 'gold_functions_hit_leaf_same_file') >= 1)} | "
      f"{sum(1 for r in R if r['n_gold_functions'] and best(r, 'gold_functions_hit_leaf_same_file') == r['n_gold_functions'])} | "
      f"{sum(1 for r in R if r['n_gold_functions'] and best(r, 'gold_functions_hit_leaf_any_path') == r['n_gold_functions'])} | "
      f"{sum(1 for r in R if best(r, 'gold_files_in_answer_top5') >= 1)} |")
P("")
for k in "ABCDEF":
    R = [r for r in rows if r["failure_stage"] == k]
    if len(R) < 5:
        continue
    P(f"### Stage {k}: five examples (first five by dataset, task id)\n")
    for r in R[:5]:
        d = detail[(r["dataset"], r["task_id"])]
        P(f"**{r['task_id']}** ({r['dataset']})\n")
        P("- gold functions: " + (", ".join(f"`{x}`" for x in d["gold"]) or "none"))
        for rt, _ in TIERS:
            P(f"- {rt} answer functions: " + (", ".join(f"`{x}`" for x in d['answers'][rt][:10]) or "none") +
              f" | answer files: " + (", ".join(f"`{x}`" for x in d['answer_files'][rt][:5]) or "none"))
        P("")
(HERE / "all_tiers_zero_artefact.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out[:40]))
