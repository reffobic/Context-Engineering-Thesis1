"""Shared loading and statistics for the Nov 3 analysis (nov3-* jobs only; reads logs and report CSVs, writes nothing)."""
from __future__ import annotations

import csv
import glob
import json
import math
import os
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "eval_harness"
REPORT = Path(os.environ.get("NOV3_REPORT", HARNESS / "data" / "reports" / "nov3_83"))   # override to test a regenerated folder
RUNS_GLOB = str(HARNESS / "data" / "runs" / "2350c0b" / "nov3-*" / "results.jsonl")

ARMS = ["ALWAYS_GRAPH", "ALWAYS_RAG", "ALWAYS_LC", "GASR", "GAPA", "SELF_ROUTE", "PREASSESS"]
ALWAYS = ["ALWAYS_GRAPH", "ALWAYS_RAG", "ALWAYS_LC"]
GATED = ["GASR", "GAPA", "SELF_ROUTE", "PREASSESS"]
LABEL = {"ALWAYS_GRAPH": "Always-GRAPH", "ALWAYS_RAG": "Always-RAG", "ALWAYS_LC": "Always-LC", "GASR": "GASR",
         "GAPA": "GAPA", "SELF_ROUTE": "Self-Route", "PREASSESS": "PREASSESS"}
DATASETS = ["swe-bench-lite", "loc-bench"]
DS_LABEL = {"swe-bench-lite": "SWE-Bench-Lite", "loc-bench": "Loc-Bench"}
N_TASKS = {"swe-bench-lite": 27, "loc-bench": 56}
VERSIONS = ["primary", "sens"]
ROUTE_RANK = {"RAG": 0, "GRAPH": 1, "LC": 2}          # escalation order: cheapest context first
ANS_COLS = ["file_acc@1", "file_acc@3", "file_acc@5", "module_acc@5", "module_acc@10", "function_acc@5", "function_acc@10"]
SEED = 20261028
N_BOOT = 10_000


def _num(v):
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return v


def read_csv(p: Path) -> list[dict]:
    return [{k: _num(v) for k, v in r.items()} for r in csv.DictReader(open(p, encoding="utf-8"))]


def load_per_task() -> dict:
    """{(dataset, arm): {task_id: row}} from the report's per_task.csv."""
    out: dict = {}
    for r in read_csv(REPORT / "per_task.csv"):
        out.setdefault((r["dataset"], r["arm"]), {})[r["task_id"]] = r
    return out


def load_answers() -> dict:
    out: dict = {}
    for r in csv.DictReader(open(REPORT / "answer_scores.csv", encoding="utf-8")):
        row = {k: (float(v) if k.startswith("answer.") or k.startswith("n_") else v) for k, v in r.items()}
        out.setdefault((r["dataset"], r["arm"]), {})[r["task_id"]] = row
    return out


def load_results() -> dict:
    out: dict = {}
    for f in sorted(glob.glob(RUNS_GLOB)):
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            out.setdefault((r["dataset"], r["arm"]), {})[r["task_id"]] = r
    return out


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def f3(x):
    return "" if x is None else f"{x:.3f}"


def f0(x):
    return "" if x is None else f"{x:.0f}"


def f1(x):
    return "" if x is None else f"{x:.1f}"


def f4(x):
    return "" if x is None else f"{x:.4f}"


def fp(x):
    """p-value: 4 decimals, "<0.0001" below that."""
    return "" if x is None else ("<0.0001" if x < 0.00005 else f"{x:.4f}")


# ── answer-level, with the zero rule applied here (the scorer itself does not) ─────────────────────────────────
def answer_rows(pt: dict, ans: dict, ds: str, arm: str, version: str) -> list[dict]:
    """One dict per task: the answer metrics, with failed tasks zeroed in the primary version.
    harness.answer_scoring scores whatever answer text a task has, failed or not; the zero rule lives only here."""
    rows = []
    for tid, p in pt[(ds, arm)].items():
        a = ans[(ds, arm)][tid]
        failed = bool(p["failed_kind"])
        zero = failed and (version == "primary" or p["failed_kind"] in ("hard", "missing"))
        g = {c: (0.0 if zero else a[f"answer.{c}"]) for c in ANS_COLS}
        rows.append({"task_id": tid, "route": p["route"], "failed": failed, "zeroed": zero, **g,
                     "no_loc": (not zero) and a["failure_class"] != "", "trunc": (not zero) and a["truncated"] == "True",
                     "n_paths": 0 if zero else int(a["n_paths"]), "n_bad": 0 if zero else int(a["n_paths_not_in_checkout"]),
                     "ctx_all": (not zero) and a["context_has_all_gold_files"] == "True",
                     "ans_all": (not zero) and a["answer_has_all_gold_files"] == "True"})
    return rows


# ── oracle route ────────────────────────────────────────────────────────────────────────────────────────────────
def oracle(pt: dict, ans: dict, ds: str, version: str, kind: str, metric: str) -> dict:
    """{task_id: (oracle route, best quality)}: the highest-quality route among the three Always arms; ties go to the
    cheapest route in the order RAG < GRAPH < LC. kind "ans": answer-level `metric` (e.g. function_acc@10);
    kind "ctx": final-context `metric`."""
    vec = {}
    for rt, arm in (("RAG", "ALWAYS_RAG"), ("GRAPH", "ALWAYS_GRAPH"), ("LC", "ALWAYS_LC")):
        if kind == "ans":
            vec[rt] = {r["task_id"]: r[metric] for r in answer_rows(pt, ans, ds, arm, version)}
        else:
            vec[rt] = {t: r[f"{version}.final_context.{metric}"] for t, r in pt[(ds, arm)].items()}
    out = {}
    for tid in vec["RAG"]:
        scores = {rt: vec[rt][tid] for rt in vec}
        best = max(scores.values())
        out[tid] = (min((rt for rt in scores if scores[rt] == best), key=ROUTE_RANK.get), best)
    return out


def routing_stats(pt: dict, ans: dict, ds: str, arm: str, version: str, kind: str, metric: str) -> dict:
    """Routing against the oracle. Primary: a failed task has no valid decision (counted wrong, neither over nor under).
    `decisive` = tasks where some route scores above 0 (elsewhere the oracle is the cheapest route by tie-break)."""
    orc = oracle(pt, ans, ds, version, kind, metric)
    s = {"n": 0, "correct": 0, "over": 0, "under": 0, "failed": 0, "dn": 0, "dcorrect": 0, "dover": 0, "dunder": 0}
    for tid, p in pt[(ds, arm)].items():
        s["n"] += 1
        dec = orc[tid][1] > 0
        s["dn"] += dec
        if version == "primary" and p["failed_kind"]:
            s["failed"] += 1
            continue
        d = ROUTE_RANK[p["route"]] - ROUTE_RANK[orc[tid][0]]
        for key, hit in (("correct", d == 0), ("over", d > 0), ("under", d < 0)):
            s[key] += hit
            if dec:
                s["d" + key] += hit
    s["accuracy"] = s["correct"] / s["n"]
    s["d_accuracy"] = s["dcorrect"] / s["dn"] if s["dn"] else None
    s["oracle_mix"] = {rt: sum(1 for v in orc.values() if v[0] == rt) for rt in ROUTE_RANK}
    s["n_no_route_helps"] = sum(1 for v in orc.values() if v[1] == 0)
    return s


# ── paired statistics ───────────────────────────────────────────────────────────────────────────────────────────
def sign_test(wins: int, losses: int) -> float:
    """Exact two-sided sign test, ties dropped: p = min(1, 2 * P(Bin(n, 1/2) <= min(w, l)))."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def boot_ci(d: np.ndarray) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    m = d[idx].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


PAIRS = [(a, b) for a in GATED for b in ALWAYS] + [("GAPA", "GASR"), ("GAPA", "PREASSESS"), ("GASR", "SELF_ROUTE")]


def metric_vector(pt, ans, ds, arm, version, metric) -> dict:
    """{task_id: value} for a metric key: ctx_function@10, ctx_file@5, ans_function@10, ans_file@5 or tokens."""
    if metric == "tokens":
        return {t: r["tok.total"] for t, r in pt[(ds, arm)].items()}
    kind, m = metric.split("_", 1)
    if kind == "ctx":
        return {t: r[f"{version}.final_context.{m.replace('function@', 'function_acc@').replace('file@', 'file_acc@')}"]
                for t, r in pt[(ds, arm)].items()}
    return {r["task_id"]: r[m.replace("function@", "function_acc@").replace("file@", "file_acc@")]
            for r in answer_rows(pt, ans, ds, arm, version)}


def paired(pt, ans, ds, a, b, version, metric) -> dict:
    va, vb = metric_vector(pt, ans, ds, a, version, metric), metric_vector(pt, ans, ds, b, version, metric)
    ids = sorted(va)
    d = np.array([va[t] - vb[t] for t in ids], dtype=float)
    sign = -1 if metric == "tokens" else 1                       # tokens: fewer is a win
    wins, losses = int((sign * d > 0).sum()), int((sign * d < 0).sum())
    out = {"n": len(ids), "wins": wins, "losses": losses, "ties": len(ids) - wins - losses,
           "mean_diff": float(d.mean()), "p_sign": sign_test(wins, losses)}
    if metric == "tokens":
        out["ci_lo"], out["ci_hi"] = boot_ci(d)
    return out


# ── GAPA call 2 ─────────────────────────────────────────────────────────────────────────────────────────────────
NEED = re.compile(r'"need"\s*:\s*"(\w+)"')


def gate_tokens(call: dict) -> int:
    return (call.get("prompt_eval_count") or 0) + (call.get("eval_count") or 0)


def gapa_call2(res: dict, ds: str) -> dict:
    gapa, gasr = res[(ds, "GAPA")], res[(ds, "GASR")]
    labels, went = {}, {}
    c1, c2 = [], []
    for tid, r in gapa.items():
        calls = r.get("ollama_calls") or []
        k1 = [c for c in calls if c["role"] == "gapa_gate_call1"]
        k2 = [c for c in calls if c["role"] == "gapa_gate_call2"]
        c1 += [gate_tokens(c) for c in k1]
        c2 += [gate_tokens(c) for c in k2]
        if not k2:
            lab = "(no call 2)"
        else:
            m = NEED.search(k2[0].get("content_head") or "")
            lab = m.group(1).upper() if m else "(unparsed)"
        labels[lab] = labels.get(lab, 0) + 1
        went.setdefault(lab, {}).setdefault(r["route"], 0)
        went[lab][r["route"]] += 1
    g = [gate_tokens(c) for r in gasr.values() for c in (r.get("ollama_calls") or []) if c["role"] == "gasr_gate"]
    n = len(gapa)
    mc1, mc2, mg = mean(c1), mean(c2), mean(g)
    p = len(c2) / n
    be = (mg - mc1) / mc2
    return {"n": n, "labels": labels, "went": went, "n_call1": len(c1), "n_call2": len(c2), "p_call2": p, "c1": mc1, "c2": mc2,
            "gasr_gate": mg, "n_gasr_gate": len(g), "gapa_gate_mean": mc1 * len(c1) / n + mc2 * len(c2) / n, "break_even": be}
