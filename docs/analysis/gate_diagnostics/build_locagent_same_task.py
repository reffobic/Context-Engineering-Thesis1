"""Same-task comparison with LocAgent (Claude-3.5) on SWE-Bench-Lite, from the outputs the LocAgent repo ships. No model run.

  .venv/bin/python docs/analysis/gate_diagnostics/build_locagent_same_task.py [PATH_TO_loc_outputs.jsonl]

Input: LocAgent repo evaluation/loc_output/locagent/claude_3-5/loc_outputs.jsonl (300 SWE-Bench-Lite tasks; repo commit 4935b55, cloned 2026-11-08 into the
scratchpad, never executed). Scoring mirrors LocAgent's evaluation/eval_metric.py cal_metrics_w_file (positions counted without de-duplication for file and
module lists; function list: trailing '.__init__' stripped and de-duplicated; cap rule hits == min(#gold, k)), gold = our datasets/swe-bench-lite/labels.jsonl
(derived by the same LocAgent rule). Step 1 reproduces the paper's published Claude-3.5 row on all 274 tasks; step 2 compares on our 27 tasks.
Writes locagent_same_task.md and locagent_same_task.csv next to this file.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "docs" / "analysis"))
sys.path.insert(0, str(ROOT / "eval_harness" / "src"))
import nov3_lib as L                                                       # noqa: E402
from harness.metrics import acc_at                                         # noqa: E402

LOC_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/g7/Documents/HANA/locagent-baseline/locagent_repo_dl/repo/evaluation/loc_output/locagent/claude_3-5/loc_outputs.jsonl")
DS = "swe-bench-lite"
LEVELS = [("file", "found_files", "files", [1, 3, 5]), ("module", "found_modules", "modules", [5, 10]), ("function", "found_entities", "functions", [5, 10])]
COLS = [(lv, k) for lv, _, _, ks in LEVELS for k in ks]
HDR = [f"{lv.capitalize()}@{k}" for lv, k in COLS]
PUBLISHED_CLAUDE = [77.74, 91.97, 94.16, 86.50, 87.59, 73.36, 77.37]          # paper Table 4, LocAgent (Ours) Claude-3.5

gold = {json.loads(x)["instance_id"]: json.loads(x) for x in open(ROOT / "eval_harness" / "data" / "datasets" / DS / "labels.jsonl") if x.strip()}
pred = {}
for line in open(LOC_FILE, encoding="utf-8"):
    r = json.loads(line)
    pred[r["instance_id"]] = r
sample = json.load(open(ROOT / "eval_harness" / "data" / "samples" / f"{DS}.sample.json"))["instance_ids"]
assert set(sample) <= set(pred) and set(gold) <= set(pred)


def locagent_acc(found: list[str], gt: list[str], level: str, k_values: list[int]) -> dict:
    """Mirror of cal_metrics_w_file for one task."""
    max_k = max(k_values)
    if level == "function":
        out_, seen = [], set()
        for pf in found:
            if pf.endswith(".__init__"):
                pf = pf[:-len(".__init__")]
            if pf not in seen:
                seen.add(pf)
                out_.append(pf)
        found = out_
    pl = found[:max_k]
    pred_labels = [1 if l in gt else 0 for l in pl] + [0] * (max_k - len(pl))
    gt_labels = [1 if i < len(gt) else 0 for i in range(max_k)]
    return {k: 1.0 if sum(pred_labels[:k]) == sum(gt_labels[:k]) else 0.0 for k in k_values}


per_task = {}
for tid, g in gold.items():
    if not g["functions"]:
        continue                                                            # LocAgent skips tasks without gold functions
    row = {}
    for lv, key, gk, ks in LEVELS:
        a = locagent_acc(pred[tid].get(key) or [], g[gk], lv, ks)
        for k in ks:
            row[(lv, k)] = a[k]
    per_task[tid] = row


def mean_over(ids, col):
    ids = list(ids)
    return sum(per_task[t][col] for t in ids) / len(ids)


out = []
P = out.append
P("# Same-task comparison with LocAgent (Claude-3.5) on SWE-Bench-Lite, from LocAgent's shipped outputs (no model run)\n")
P("Source of LocAgent's predictions: `evaluation/loc_output/locagent/claude_3-5/loc_outputs.jsonl` in the LocAgent repo (commit 4935b55; 300 tasks, found_files / found_modules / found_entities per task). "
  "I cloned the repo into the scratchpad and only read the file; nothing from it was executed. The repo ships no Loc-Bench outputs and no outputs for the 7B model, so Loc-Bench and the Qwen rows cannot be compared this way.\n")

# step 1: validation
ids274 = [t for t in per_task if t in set(gold)]
P("## 1. Validation: do I reproduce LocAgent's published Claude-3.5 row?\n")
P(f"Scored on all {len(ids274)} tasks of our `labels.jsonl` (the 274-task SWE-Bench-Lite set), file / module / function accuracy mirrored from `eval_metric.py`:\n")
P("| | " + " | ".join(HDR) + " |")
P("|---|" + "|".join(["---"] * len(HDR)) + "|")
mine = [100 * mean_over(ids274, c) for c in COLS]
P("| Published, paper Table 4 (LocAgent, Claude-3.5) | " + " | ".join(f"{v:.2f}" for v in PUBLISHED_CLAUDE) + " |")
P("| Recomputed from the shipped outputs | " + " | ".join(f"{v:.2f}" for v in mine) + " |")
P("| Difference | " + " | ".join(f"{m - p:+.2f}" for m, p in zip(mine, PUBLISHED_CLAUDE)) + " |")
P("")
maxdiff = max(abs(m - p) for m, p in zip(mine, PUBLISHED_CLAUDE))
P(f"Largest absolute difference: {maxdiff:.2f} points. " + ("The scoring and the gold labels reproduce the paper to within rounding of the shipped file." if maxdiff < 1.0 else "The shipped file does not reproduce the paper's row exactly (it may be a different run); treat the same-task numbers below as 'LocAgent as shipped'.") + "\n")

# step 2: same tasks
P("## 2. Our 27 tasks: LocAgent (Claude-3.5, shipped outputs) vs ours\n")
S = list(sample)
rest = [t for t in ids274 if t not in set(S)]
P(f"LocAgent on our {len(S)} sampled tasks vs on the other {len(rest)} tasks (is our sample easier or harder than the rest of the set for LocAgent?):\n")
P("| | n | " + " | ".join(HDR) + " |")
P("|---|---|" + "|".join(["---"] * len(HDR)) + "|")
P(f"| LocAgent (Claude-3.5), our {len(S)} tasks | {len(S)} | " + " | ".join(f"{100 * mean_over(S, c):.1f}" for c in COLS) + " |")
P(f"| LocAgent (Claude-3.5), other {len(rest)} tasks | {len(rest)} | " + " | ".join(f"{100 * mean_over(rest, c):.1f}" for c in COLS) + " |")
P("")
pt, ans = L.load_per_task(), L.load_answers()
res = L.load_results()
ours_rows = {}
for arm in L.ARMS:
    ours_rows[arm] = {r["task_id"]: r for r in L.answer_rows(pt, ans, DS, arm, "primary")}
colkey = {("file", 1): "file_acc@1", ("file", 3): "file_acc@3", ("file", 5): "file_acc@5", ("module", 5): "module_acc@5", ("module", 10): "module_acc@10",
          ("function", 5): "function_acc@5", ("function", 10): "function_acc@10"}
ret = {}
for t in S:
    rk = res[(DS, "ALWAYS_RAG")][t]["ranked"]["after_retrieval"]
    row = {}
    for lv, key, gk, ks in LEVELS:
        for k in ks:
            row[(lv, k)] = acc_at(rk[gk] if gk in rk else [], gold[t][gk], k)
    ret[t] = row
P("Same 27 tasks, same gold labels, same cap rule:\n")
P("| | " + " | ".join(HDR) + " |")
P("|---|" + "|".join(["---"] * len(HDR)) + "|")
P("| **LocAgent (Claude-3.5)** | " + " | ".join(f"**{100 * mean_over(S, c):.1f}**" for c in COLS) + " |")
P("| Ours, ranked list (After retrieval) | " + " | ".join(f"{100 * sum(ret[t][c] for t in S) / len(S):.1f}" for c in COLS) + " |")
for arm in L.ARMS:
    P(f"| Ours, answer level: {L.LABEL[arm]} | " + " | ".join(f"{100 * sum(ours_rows[arm][t][colkey[c]] for t in S) / len(S):.1f}" for c in COLS) + " |")
P("")

# paired
P("Paired per task (one point of the comparison per task). W = tasks where ours is correct and LocAgent is not, L = LocAgent correct and ours not, T = same; p = exact two-sided sign test on W vs L.\n")
P("| Ours | Metric | W | L | T | Sign-test p |")
P("|---|---|---|---|---|---|")
paired_rows = []
for label, getter in [("Ranked list (After retrieval)", lambda t, c: ret[t][c])] + [(f"Answer: {L.LABEL[a]}", (lambda t, c, a=a: ours_rows[a][t][colkey[c]])) for a in ("ALWAYS_RAG", "ALWAYS_GRAPH", "ALWAYS_LC", "GASR", "GAPA")]:
    for c in [("file", 5), ("module", 10), ("function", 10)]:
        w = sum(1 for t in S if getter(t, c) == 1 and per_task[t][c] == 0)
        l_ = sum(1 for t in S if getter(t, c) == 0 and per_task[t][c] == 1)
        tie = len(S) - w - l_
        p = L.sign_test(w, l_)
        P(f"| {label} | {c[0].capitalize()}@{c[1]} | {w} | {l_} | {tie} | {p:.3f} |")
        paired_rows.append({"ours": label, "metric": f"{c[0]}@{c[1]}", "W": w, "L": l_, "T": tie, "p": round(p, 4)})
P("")
none_ours = [t for t in S if all(ours_rows[a][t]["function_acc@10"] == 0 for a in ("ALWAYS_RAG", "ALWAYS_GRAPH", "ALWAYS_LC")) and per_task[t][("function", 10)] == 1]
P(f"Tasks where LocAgent gets function Acc@10 right and all three of our fixed tiers get it wrong: {len(none_ours)} of {len(S)}"
  + (f" ({', '.join(none_ours)})" if none_ours else "") + ".\n")
P("Caveats: LocAgent's Claude-3.5 outputs are as shipped by its authors (not rerun by us); the model differs from ours (Claude-3.5 versus qwen3.5-9b), as does the method (multi-round graph-exploring agent versus single-shot retrieval-augmented answer). "
  "The comparison is therefore same-task, not same-model. n = 27, so one task is 3.7 points.\n")
(HERE / "locagent_same_task.md").write_text("\n".join(out) + "\n", encoding="utf-8")
with open(HERE / "locagent_same_task.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["task_id", "in_our_sample"] + [f"locagent_claude35_{h}" for h in HDR] + [f"ours_ranked_{h}" for h in HDR] + [f"ours_{a}_{h}" for a in ("ALWAYS_RAG", "ALWAYS_GRAPH", "ALWAYS_LC", "GASR", "GAPA") for h in HDR])
    for t in ids274:
        ins = t in set(S)
        row = [t, ins] + [int(per_task[t][c]) for c in COLS]
        if ins:
            row += [int(ret[t][c]) for c in COLS]
            for a in ("ALWAYS_RAG", "ALWAYS_GRAPH", "ALWAYS_LC", "GASR", "GAPA"):
                row += [int(ours_rows[a][t][colkey[c]]) for c in COLS]
        w.writerow(row)
print("\n".join(out))
