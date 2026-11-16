"""Our Nov 3 accuracy numbers next to the LocAgent paper's published numbers (option 2: full-set published figures vs our 83-task sample).

  .venv/bin/python docs/analysis/gate_diagnostics/build_locagent_compare.py

Published numbers are typed in from the LocAgent paper (arXiv 2503.09089), Table 4 (SWE-Bench-Lite, 274 tasks) and Table 7 (Loc-Bench, 560 tasks),
read from the PDF text on 2026-11-08. Ours: report folder nov3_83 (qwen3.5-9b), zero rule applied (nov3_lib.answer_rows, primary) for the answer level;
'After retrieval' rows of table_iv / table_v for the ranked-list level. Wilson 95% intervals for our proportions.
Writes locagent_comparison.md and locagent_comparison.csv next to this file.
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "docs" / "analysis"))
import nov3_lib as L                                                       # noqa: E402

SWE_COLS = ["file_acc@1", "file_acc@3", "file_acc@5", "module_acc@5", "module_acc@10", "function_acc@5", "function_acc@10"]
SWE_HDR = ["File@1", "File@3", "File@5", "Module@5", "Module@10", "Func@5", "Func@10"]
LOC_COLS = ["file_acc@5", "module_acc@10", "function_acc@10"]
LOC_HDR = ["File@5", "Module@10", "Func@10"]

# LocAgent paper, Table 4 (SWE-Bench-Lite, 274): File@1,@3,@5 / Module@5,@10 / Function@5,@10
PUB_SWE = [
    ("BM25", [38.69, 51.82, 61.68, 45.26, 52.92, 31.75, 36.86]),
    ("CodeRankEmbed", [52.55, 77.74, 84.67, 71.90, 78.83, 51.82, 58.76]),
    ("Agentless (Claude-3.5)", [72.63, 79.20, 79.56, 68.98, 68.98, 58.76, 58.76]),
    ("MoatlessTools (Claude-3.5)", [72.63, 85.77, 86.13, 76.28, 76.28, 64.60, 64.96]),
    ("SWE-agent (Claude-3.5)", [77.37, 87.23, 90.15, 77.74, 78.10, 64.23, 64.60]),
    ("OpenHands (Claude-3.5)", [76.28, 89.78, 90.15, 83.21, 83.58, 68.25, 70.07]),
    ("LocAgent (Qwen2.5-7B ft)", [70.80, 84.67, 88.32, 81.02, 82.85, 64.23, 71.53]),
    ("LocAgent (Qwen2.5-32B ft)", [75.91, 90.51, 92.70, 85.77, 87.23, 71.90, 77.01]),
    ("LocAgent (Claude-3.5)", [77.74, 91.97, 94.16, 86.50, 87.59, 73.36, 77.37]),
]
# Table 7 (Loc-Bench, 560): only the cells that overlap with ours: File@5, Module@10, Function@10
PUB_LOC = [
    ("CodeRankEmbed", [74.29, 63.21, 43.39]),
    ("Agentless (Claude-3.5)", [67.50, 53.39, 42.68]),
    ("OpenHands (Claude-3.5)", [79.82, 68.93, 59.11]),
    ("SWE-agent (Claude-3.5)", [77.68, 63.57, 51.96]),
    ("LocAgent (Qwen2.5-7B ft)", [78.57, 63.04, 51.43]),
    ("LocAgent (Claude-3.5)", [83.39, 70.89, 59.29]),
]


def wilson(k: float, n: int, z: float = 1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


pt, ans = L.load_per_task(), L.load_answers()
rows_csv = []
out = []
P = out.append
P("# Our accuracy vs the LocAgent paper (option 2: published full-set numbers, no same-task rerun)\n")
P("Published numbers: LocAgent paper (arXiv 2503.09089) Table 4 (SWE-Bench-Lite, 274 tasks) and Table 7 (Loc-Bench, 560 tasks), read from the PDF text. VERIFIED that the values below are typed from that text. "
  "Ours: qwen3.5-9b, Nov 3 jobs, frozen pipeline 2350c0b, **27 SWE-Bench-Lite and 56 Loc-Bench tasks**. These are a 10% proportional-by-repo sample of the same 274 and 560 tasks (`data/samples/*.sample.json`: seed 42, `n_total` 274 and 560), "
  "so the task population matches LocAgent's, but the sample is small: one task moves SWE by 3.7 points and Loc by 1.8 points. Our intervals below are Wilson 95%. Metric definitions are the same cap rule (hits == min(#gold, k)), strict match, as checked earlier in `scoring_lc_and_fix_notes.md`.\n")
P("Two kinds of our rows: **answer level** = functions/files the model wrote under LOCATIONS, failed tasks scored 0 (the headline numbers); **ranked list** = the 'After retrieval' stage of the table IV/V CSVs, which is our retrieval ranking before any pruning or generation and is the like-for-like row for BM25 and CodeRankEmbed.\n")


def our_answer(ds, arm, cols):
    R = L.answer_rows(pt, ans, ds, arm, "primary")
    n = len(R)
    res = []
    for c in cols:
        k = sum(r[c] for r in R)
        lo, hi = wilson(k, n)
        res.append((k / n, lo, hi))
    return n, res


def our_ranked(ds, cols_hdr):
    p = ROOT / "eval_harness" / "data" / "reports" / "nov3_83" / ("table_iv_context_quality_swe-bench-lite_qwen3.5-9b.csv" if ds == "swe-bench-lite" else "table_v_context_quality_loc-bench_qwen3.5-9b.csv")
    r = next(x for x in csv.DictReader(open(p)) if x["Arm"] == "Always-RAG" and x["Stage"] == "After retrieval")
    keymap = {"File@1": "File Acc@1", "File@3": "File Acc@3", "File@5": "File Acc@5", "Module@5": "Module Acc@5", "Module@10": "Module Acc@10", "Func@5": "Function Acc@5", "Func@10": "Function Acc@10"}
    n = L.N_TASKS[ds]
    res = []
    for h in cols_hdr:
        v = float(r[keymap[h]])
        lo, hi = wilson(round(v * n), n)
        res.append((v, lo, hi))
    return n, res


def fmt(v):
    return f"{100 * v:.1f}"


for ds, cols, hdr, pub, n_pub in (("swe-bench-lite", SWE_COLS, SWE_HDR, PUB_SWE, 274), ("loc-bench", LOC_COLS, LOC_HDR, PUB_LOC, 560)):
    P(f"## {L.DS_LABEL[ds]} (ours n = {L.N_TASKS[ds]}; published n = {n_pub})\n")
    if ds == "loc-bench":
        P("Loc-Bench: LocAgent reports File@5/10, Module@10/15, Function@10/15; ours are File@1/3/5, Module@5/10, Function@5/10. Only File@5, Module@10 and Function@10 overlap, so only those are shown (`oct23_appendix_source.tex` note, same reasoning).\n")
    P("| Source | Row | " + " | ".join(hdr) + " |")
    P("|---|---|" + "|".join(["---"] * len(hdr)) + "|")
    for name, vals in pub:
        P(f"| LocAgent paper | {name} | " + " | ".join(f"{v:.1f}" for v in vals) + " |")
        rows_csv.append({"dataset": ds, "source": "LocAgent paper", "row": name, "n": n_pub, **{h: v for h, v in zip(hdr, vals)}})
    n, rr = our_ranked(ds, hdr)
    P(f"| Ours, ranked list | After retrieval (same list for all arms) | " + " | ".join(f"{fmt(v)} [{fmt(lo)}, {fmt(hi)}]" for v, lo, hi in rr) + " |")
    rows_csv.append({"dataset": ds, "source": "ours ranked list", "row": "After retrieval", "n": n, **{h: round(100 * v, 2) for h, (v, lo, hi) in zip(hdr, rr)}})
    for arm in L.ARMS:
        n, ra = our_answer(ds, arm, cols)
        P(f"| Ours, answer level | {L.LABEL[arm]} | " + " | ".join(f"{fmt(v)} [{fmt(lo)}, {fmt(hi)}]" for v, lo, hi in ra) + " |")
        rows_csv.append({"dataset": ds, "source": "ours answer level", "row": L.LABEL[arm], "n": n, **{h: round(100 * v, 2) for h, (v, lo, hi) in zip(hdr, ra)}})
    P("")
    # does the interval of our best answer-level row reach the published LocAgent-7B / CodeRankEmbed values?
    P("Does the published value fall inside our 95% interval? (`in` = yes, `above` = published is higher than the interval's top, `below` = lower)\n")
    P("| Our row | vs published row | " + " | ".join(hdr) + " |")
    P("|---|---|" + "|".join(["---"] * len(hdr)) + "|")
    refs = [p for p in pub if p[0] in ("CodeRankEmbed", "LocAgent (Qwen2.5-7B ft)")]
    for label, getter in [("Ours, ranked list: After retrieval", lambda: our_ranked(ds, hdr)[1])] + [(f"Ours, answer: {L.LABEL[a]}", (lambda a=a: our_answer(ds, a, cols)[1])) for a in ("ALWAYS_RAG", "ALWAYS_GRAPH", "ALWAYS_LC", "GASR", "GAPA")]:
        ours = getter()
        for rname, rvals in refs:
            cells = []
            for (v, lo, hi), pv in zip(ours, rvals):
                pv_ = pv / 100
                cells.append("in" if lo <= pv_ <= hi else ("above" if pv_ > hi else "below"))
            P(f"| {label} | {rname} | " + " | ".join(cells) + " |")
    P("")

P("## How to read this\n")
P("- The ranked-list row is the fair comparison with the retrieval baselines (BM25, CodeRankEmbed): it is our retrieval alone, no generation. The answer-level rows are the end of our pipeline with a 9B model writing LOCATIONS, which has no counterpart in LocAgent's table, where the model is a purpose-built localization agent that explores the code graph over several rounds.")
P("- LocAgent's strongest rows use Claude-3.5 or models fine-tuned on localization trajectories; the 7B row is Qwen2.5-Coder-7B fine-tuned with LoRA on those trajectories (paper Section 3.3). Our generator is the general-purpose qwen3.5-9b with no localization fine-tuning, so the answer-level gap is a model-and-method difference, not just a pipeline one.")
P("- Interval widths: at n = 27 a 95% interval is roughly ±17 points around 50%, at n = 56 roughly ±13. Differences smaller than that between our sample and a published full-set number cannot be separated from sampling noise (INFERRED from the Wilson widths printed above).")
P("- Not comparable from this table: Loc-Bench File@10, Module@15 and Function@15 (we do not score those k).")
(HERE / "locagent_comparison.md").write_text("\n".join(out) + "\n", encoding="utf-8")
keys = []
for r in rows_csv:
    for k in r:
        if k not in keys:
            keys.append(k)
with open(HERE / "locagent_comparison.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerows(rows_csv)
print("\n".join(out))
