"""Nov 3 analysis tables (primary: failed tasks score 0; sens: fallback output kept). Reads nov3-* logs and data/reports/nov3_83.

  ../.venv/bin/python docs/analysis/analyze_nov3.py        (from the repository root)

Writes docs/analysis/tables/*.csv and docs/analysis/crosscheck.txt. Exit code 1 if a cross-check against the report fails."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nov3_lib import *  # noqa: E402,F401,F403

OUT = Path(__file__).resolve().parent / "tables"
OUT.mkdir(exist_ok=True)


def write(name: str, header: list[str], rows: list[list]):
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


pt, ans, res = load_per_task(), load_answers(), load_results()
checks: list[str] = []


def check(name: str, got, want, tol=0.0005):
    ok = (got is None and want is None) or (got is not None and want is not None and abs(got - want) <= tol)
    checks.append(f"{'OK  ' if ok else 'FAIL'} {name}: analysis={got} report={want}")
    return ok


# 1 ── cost ──────────────────────────────────────────────────────────────────────────────────────────────────────
def cost_row(ds, arm, version):
    rows = list(pt[(ds, arm)].values())
    wt = [r for r in rows if r.get("tok.total") is not None]
    routed = [r for r in rows if r["route"]]
    gated = [r for r in rows if r["made_gate_call"] in (True, "True", 1.0, 1)]
    calls = sum(r["n_gate_calls"] for r in gated)
    fails = sum(r["n_gate_call_failures"] for r in gated)
    qpc = mean(r[f"{version}.final_context.function_acc@10"] / (r["tok.total"] / 1000) for r in wt if r["tok.total"])
    kinds = {}
    for r in rows:
        if r["failed_kind"]:
            kinds[r["failed_kind"]] = kinds.get(r["failed_kind"], 0) + 1
    return {"n": len(rows), "total": mean(r["tok.total"] for r in wt), "router": mean(r["tok.router_input"] + r["tok.router_output"] for r in wt),
            "inj": mean(r["tok.injected"] for r in wt), "qpc": qpc,
            **{f"pct_{rt}": 100 * sum(1 for r in routed if r["route"] == rt) / len(routed) for rt in ROUTE_RANK},
            "gate_err": fails / calls if calls else None, "n_failed": sum(kinds.values()),
            "failed_kinds": ";".join(f"{k}={v}" for k, v in sorted(kinds.items())),
            "preassess_fallback": sum(1 for r in rows if r.get("preassess_fallback"))}


rep_cost = {v: {(r["Dataset"], r["Arm"]): r for r in read_csv(REPORT / f"table_vi_cost_routing_qwen3.5-9b{'' if v == 'primary' else '_sensitivity'}.csv")}
            for v in VERSIONS}
for v in VERSIONS:
    rows = []
    for ds in DATASETS:
        for arm in ARMS:
            c = cost_row(ds, arm, v)
            rows.append([ds, arm, c["n"], f0(c["total"]), f0(c["router"]), f0(c["inj"]), f3(c["qpc"]), f1(c["pct_RAG"]), f1(c["pct_GRAPH"]),
                         f1(c["pct_LC"]), f3(c["gate_err"]), c["n_failed"], c["failed_kinds"], c["preassess_fallback"] or ""])
            rr = rep_cost[v].get((ds, "Pre-assess" if arm == "PREASSESS" else LABEL[arm]))
            if rr:
                check(f"cost[{v}] {ds} {arm} total", round(c["total"]), rr["Total tokens per task"], 0.5)
                check(f"cost[{v}] {ds} {arm} qpc", round(c["qpc"], 3), rr["Quality per cost"])
                for rt in ROUTE_RANK:
                    rv = rr[f"%{rt}"]
                    check(f"cost[{v}] {ds} {arm} %{rt}", round(c[f"pct_{rt}"], 1), rv if isinstance(rv, float) else 0.0, 0.05)
                if isinstance(rr["Gate error rate"], float):
                    check(f"cost[{v}] {ds} {arm} gate_err", round(c["gate_err"], 3), rr["Gate error rate"])
    write(f"t1_cost_{v}.csv", ["dataset", "arm", "n_tasks", "total_tokens", "routing_overhead", "injected_tokens", "quality_per_cost",
                              "pct_RAG", "pct_GRAPH", "pct_LC", "gate_error_rate", "n_failed", "failed_kinds", "preassess_fallbacks"], rows)

# 2 ── answer level ──────────────────────────────────────────────────────────────────────────────────────────────
for v in VERSIONS:
    rows = []
    for ds in DATASETS:
        for arm in ARMS:
            a = answer_rows(pt, ans, ds, arm, v)
            ctx_all = [r for r in a if r["ctx_all"]]
            conv = sum(1 for r in ctx_all if r["ans_all"])
            paths, bad = sum(r["n_paths"] for r in a), sum(r["n_bad"] for r in a)
            rows.append([ds, arm, len(a)] + [f3(mean(r[c] for r in a)) for c in ANS_COLS] +
                        [f3(bad / paths if paths else None), f"{bad}/{paths}", sum(r["no_loc"] for r in a), sum(r["trunc"] for r in a),
                         f3(conv / len(ctx_all) if ctx_all else None), f"{conv}/{len(ctx_all)}", sum(r["zeroed"] for r in a)])
    write(f"t2_answer_level_{v}.csv", ["dataset", "arm", "n_tasks"] + [f"answer_{c}" for c in ANS_COLS] +
          ["hallucination_rate", "bad_paths/paths", "no_locations", "truncated", "conversion_rate", "conversion_n/d", "n_zeroed_failed"], rows)

# 3 ── answer level by final route ──────────────────────────────────────────────────────────────────────────────
for v in VERSIONS:
    rows = []
    for ds in DATASETS:
        for arm in GATED:
            a = answer_rows(pt, ans, ds, arm, v)
            for rt in ROUTE_RANK:
                g = [r for r in a if r["route"] == rt]
                rows.append([ds, arm, rt, len(g), f3(mean(r["function_acc@10"] for r in g)), f3(mean(r["file_acc@5"] for r in g))])
    write(f"t3_answer_by_route_{v}.csv", ["dataset", "arm", "final_route", "n_tasks", "answer_function_acc@10", "answer_file_acc@5"], rows)

# 4 ── oracle routing ───────────────────────────────────────────────────────────────────────────────────────────
ORACLES = [("ans", "function_acc@10"), ("ans", "file_acc@5"), ("ctx", "function_acc@10")]      # ctx: degenerate, see README
for v in VERSIONS:
    rows = []
    for ds in DATASETS:
        for kind, metric in ORACLES:
            for arm in GATED:
                s = routing_stats(pt, ans, ds, arm, v, kind, metric)
                rows.append([ds, f"{kind}:{metric}", arm, s["n"], s["correct"], f3(s["accuracy"]), s["over"], s["under"], s["failed"],
                             s["dn"], s["dcorrect"], f3(s["d_accuracy"]), s["dover"], s["dunder"],
                             "/".join(str(s["oracle_mix"][rt]) for rt in ROUTE_RANK), s["n_no_route_helps"]])
    write(f"t4_routing_vs_oracle_{v}.csv", ["dataset", "oracle_quality", "arm", "n_tasks", "correct", "accuracy", "over_escalation",
                                           "under_escalation", "failed_no_decision", "n_decisive", "decisive_correct", "decisive_accuracy",
                                           "decisive_over", "decisive_under", "oracle_mix_RAG/GRAPH/LC", "n_no_route_helps"], rows)

# 5 ── paired comparisons ───────────────────────────────────────────────────────────────────────────────────────
METRICS = ["ctx_function@10", "ctx_file@5", "ans_function@10", "ans_file@5", "tokens"]
for v in VERSIONS:
    rows = []
    for ds in DATASETS:
        for m in METRICS:
            stats = [paired(pt, ans, ds, a, b, v, m) for a, b in PAIRS]
            adj = holm([s["p_sign"] for s in stats])
            for (a, b), s, ph in zip(PAIRS, stats, adj):
                rows.append([ds, m, a, b, s["n"], s["wins"], s["losses"], s["ties"], f4(s["mean_diff"]) if m != "tokens" else f0(s["mean_diff"]),
                             fp(s["p_sign"]), fp(ph), f0(s["ci_lo"]) if "ci_lo" in s else "", f0(s["ci_hi"]) if "ci_hi" in s else ""])
    write(f"t5_paired_{v}.csv", ["dataset", "metric", "A", "B", "n", "wins_A", "losses_A", "ties", "mean_diff_A_minus_B", "p_sign_exact_2sided",
                                 "p_holm", "tokens_ci95_lo", "tokens_ci95_hi"], rows)

# 6 ── GAPA call 2 and break-even ───────────────────────────────────────────────────────────────────────────────
lab_rows, be_rows = [], []
for ds in DATASETS:
    g = gapa_call2(res, ds)
    for lab in sorted(g["labels"]):
        w = g["went"][lab]
        lab_rows.append([ds, lab, g["labels"][lab]] + [w.get(rt, 0) for rt in ROUTE_RANK])
    be_rows.append([ds, g["n"], g["n_call1"], g["n_call2"], f3(g["p_call2"]), f0(g["c1"]), f0(g["c2"]), f0(g["gasr_gate"]), g["n_gasr_gate"],
                    f0(g["gapa_gate_mean"]), f3(g["break_even"]), "GAPA cheaper" if g["p_call2"] < g["break_even"] else "GASR cheaper"])
    check(f"gapa total call2 {ds}", sum(g["labels"][k] for k in g["labels"] if k != "(no call 2)"), g["n_call2"], 0)
write("t6_gapa_call2_labels.csv", ["dataset", "call2_label", "n_tasks", "to_RAG", "to_GRAPH", "to_LC"], lab_rows)
write("t6_break_even.csv", ["dataset", "n_gapa_tasks", "n_call1", "n_call2", "call2_rate_p", "call1_tokens_c1", "call2_tokens_c2",
                            "gasr_gate_tokens_g", "n_gasr_gate_calls", "gapa_gate_tokens_per_task", "break_even_p_star", "gate_tokens_verdict"], be_rows)

# stage table cross-check (report table_iv / table_v are primary)
stage_rows = {"swe-bench-lite": read_csv(next(REPORT.glob("table_iv_*_qwen3.5-9b.csv"))), "loc-bench": read_csv(next(REPORT.glob("table_v_*_qwen3.5-9b.csv")))}
ST = [("After retrieval", "after_retrieval"), ("After graph expansion", "after_expansion"), ("After pruning", "final_context")]
COLS = [("File Acc@1", "file_acc@1"), ("File Acc@3", "file_acc@3"), ("File Acc@5", "file_acc@5"), ("Module Acc@5", "module_acc@5"),
        ("Module Acc@10", "module_acc@10"), ("Function Acc@5", "function_acc@5"), ("Function Acc@10", "function_acc@10"),
        ("File Recall@5", "file_recall@5")]
for ds in DATASETS:
    for r in stage_rows[ds]:
        arm = next(a for a in ARMS if LABEL[a] == r["Arm"] or (a == "PREASSESS" and r["Arm"] == "Pre-assess"))
        st = dict(ST)[r["Stage"]]
        for head, key in COLS:
            check(f"stage {ds} {arm} {st} {key}", round(mean(x[f"primary.{st}.{key}"] for x in pt[(ds, arm)].values()), 3), r[head])

fails = [c for c in checks if c.startswith("FAIL")]
(Path(__file__).resolve().parent / "crosscheck.txt").write_text(
    f"Cross-check of analysis numbers against data/reports/nov3_83 table CSVs: {len(checks) - len(fails)} OK, {len(fails)} FAIL\n" + "\n".join(checks) + "\n")
print(f"crosscheck: {len(checks) - len(fails)} OK, {len(fails)} FAIL")
for f in fails[:20]:
    print(f)
sys.exit(1 if fails else 0)
