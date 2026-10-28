"""Metrics and tables from run logs (logs only: no model calls, CPU only). The ONLY place that reads gold labels.

  python -m harness.metrics --runs DIR [DIR ...] --data-dir D --out OUT [--explain TASK_ID --arm A --model M --dataset DS]

Definitions (outline Appendix A; LocAgent code rules, decisions of 2026-10-18):
  * predictions are de-duplicated keeping the first rank; Acc@k = 1 if hits == min(#gold, k) (LocAgent cap rule);
    Recall@k = hits / min(#gold, k); file / module (class, else function) / function levels;
  * stages: after retrieval = ranked candidates; after graph expansion = seeds + neighbours (the retrieval list for
    tasks that did not expand); after pruning / final context = what the answer model received (the whole parent
    context for LC tasks);
  * failed tasks (timeout, OOM, error, preflight, gate/judge parse failure) score zero in every quality denominator and are
    also reported separately (failures.csv). The sensitivity variant scores gate/judge parse failures on their
    long-context fallback output (what the pipeline actually produced) and keeps hard failures at zero;
  * total tokens per task = router prompt + router completion + answer-call prompt tokens (injected context),
    all from Ollama's counters; cost averages cover tasks that have counters; quality per cost = per-task
    function Acc@10 / (total tokens / 1000), averaged over those tasks;
  * %RAG/%GRAPH/%LC over tasks that produced a route; gate error rate = failed gate/judge calls / all gate/judge
    calls (router calls); the task-level share (tasks with a failed call / tasks that made one) is also reported;
  * a job that does not have a line for every task of its sample is "not run" in the tables.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .common import load_toml
from .instrument import gapa_gate_summary, seed_tier_shares, stub_shares

STAGES = [("after_retrieval", "After retrieval"), ("after_expansion", "After graph expansion"), ("final_context", "After pruning")]
LEVELS = [("file", "files", (1, 3, 5)), ("module", "modules", (5, 10)), ("function", "functions", (5, 10))]
ARM_ORDER = ["ALWAYS_GRAPH", "GASR", "GAPA", "SELF_ROUTE"]
ARM_LABEL = {"ALWAYS_GRAPH": "Always-GRAPH", "GASR": "GASR", "GAPA": "GAPA", "SELF_ROUTE": "Self-Route"}
QUALITY_ARMS = ["ALWAYS_GRAPH", "GASR", "GAPA"]                      # Tables IV / V
COST_ARMS = ["ALWAYS_GRAPH", "GASR", "GAPA", "SELF_ROUTE"]            # Table VI
NEW_ARMS = {"ALWAYS_RAG": "Always-RAG", "ALWAYS_LC": "Always-LC", "PREASSESS": "Pre-assess"}
HARD = ("timeout", "oom", "error", "preflight_failed")


class MergeError(RuntimeError):
    pass


# ── scoring ──────────────────────────────────────────────────────────────────

def dedupe(pred: list[str]) -> list[str]:
    seen, out = set(), []
    for p in pred:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def hits_at(pred: list[str], gold: list[str], k: int) -> int:
    return len(set(dedupe(pred)[:k]) & set(gold))


def acc_at(pred: list[str], gold: list[str], k: int) -> float:
    return 1.0 if hits_at(pred, gold, k) == min(len(set(gold)), k) else 0.0


def recall_at(pred: list[str], gold: list[str], k: int) -> float:
    return hits_at(pred, gold, k) / min(len(set(gold)), k)


def stage_list(line: dict, stage: str) -> dict | None:
    r = line.get("ranked") or {}
    if stage == "after_expansion":
        return r.get("after_expansion") or r.get("after_retrieval")
    return r.get(stage)


def score_line(line: dict, gold: dict) -> dict:
    """All quality metrics of one task, per stage. Missing list = zero."""
    out = {}
    for st, _ in STAGES:
        lists = stage_list(line, st) or {"files": [], "modules": [], "functions": []}
        for lvl, key, ks in LEVELS:
            g = gold[key]
            for k in ks:
                out[f"{st}.{lvl}_acc@{k}"] = acc_at(lists[key], g, k)
        out[f"{st}.file_recall@5"] = recall_at(lists["files"], gold["files"], 5)
    return out


def tokens_of(line: dict) -> dict | None:
    t = line.get("tokens")
    if not t or line.get("status") in HARD:
        return None
    router_in, router_out, inj = t.get("router_prompt") or 0, t.get("router_completion") or 0, t.get("answer_prompt") or 0
    return {"router_input": router_in, "router_output": router_out, "injected": inj, "total": router_in + router_out + inj}


def no_usage_calls(line: dict | None) -> list[dict]:
    """Ollama calls of a task with missing or zero usage counters (prompt_eval_count / eval_count None or 0).
    Token sums treat these as 0 (`or 0`), so such a call would silently undercount tokens; the class below makes it visible."""
    return [{"role": c.get("role"), "seq": c.get("seq"), "prompt_eval_count": c.get("prompt_eval_count"), "eval_count": c.get("eval_count")}
            for c in (line or {}).get("ollama_calls") or []
            if not c.get("prompt_eval_count") or not c.get("eval_count")]


def failed_kind(line: dict | None) -> str | None:
    """None = ok; 'hard' = timeout/OOM/error/preflight; 'gate' = gate or judge parse failure; 'gate_no_usage' = a call with
    missing or zero usage counters; 'answer' = Ollama error as answer."""
    if line is None:
        return "missing"
    if line["status"] in HARD:
        return "hard"
    if line.get("gate_error"):
        return "gate"
    if no_usage_calls(line):
        return "gate_no_usage"
    if line.get("answer_error"):
        return "answer"
    return None


# ── loading and compatibility ────────────────────────────────────────────────

def load_jobs(run_dirs: list[Path]) -> list[dict]:
    jobs = []
    for root in run_dirs:
        for jf in sorted(Path(root).glob("*/job.json")) + ([Path(root) / "job.json"] if (Path(root) / "job.json").is_file() else []):
            d = jf.parent
            j = json.loads(jf.read_text(encoding="utf-8"))
            rows = []
            rp = d / "results.jsonl"
            if rp.is_file():
                for line in rp.read_text(encoding="utf-8").splitlines():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            qs = json.loads((d / "queue_status.json").read_text()) if (d / "queue_status.json").is_file() else {}
            ident = j["identity"]
            jobs.append({"dir": d, "job_id": j["job_id"], "arm": ident["arm"], "model": ident["model"], "dataset": ident["dataset"],
                         "ablation": ident.get("ablation", "full"), "identity": ident, "header": j["header"], "info": j["info"],
                         "n_tasks": j["info"]["n_tasks"], "rows": rows, "queue_state": qs.get("state"),
                         "complete": len({r["task_id"] for r in rows}) >= j["info"]["n_tasks"]})
    return jobs


def apply_lc_v2(jobs: list[dict], data_dir: Path) -> list[dict]:
    """Scorer v2: LC final_context lists get module / function ids from the retrieved chunk each LC chunk came from
    (locmap.rescore_lc_final). Edits the loaded rows in memory only; the logs on disk are untouched. -> resolution records."""
    from .locmap import Mapper, rescore_lc_final
    out = []
    for j in jobs:
        for line in j["rows"]:
            r = line.get("ranked")
            if line.get("status") != "ok" or line.get("route") != "LC" or not r or not r.get("final_context"):
                continue
            mp = Mapper(data_dir / "cache" / "checkouts" / line["repo"].replace("/", "__") / line["base_commit"])
            new, recs = rescore_lc_final(r["final_context"], r["retrieved_top5"], mp)
            r["final_context_legacy"] = r["final_context"]
            r["final_context"] = new
            out += [{"job_id": j["job_id"], "arm": j["arm"], "dataset": j["dataset"], "ablation": j["ablation"],
                     "task_id": line["task_id"], **x} for x in recs]
    return out


COMPAT_FIELDS = ["pipeline_frozen_sha", "harness_commit", "schema"]
COMPAT_CONFIGS = ["experiment", "pipeline_config", "index_config", "run_config", "ablations_config"]


def check_compat(jobs: list[dict]) -> list[str]:
    """Raise MergeError if jobs that are combined disagree on a field that must match; return warnings."""
    problems, warnings = [], []
    seen = {}
    for j in jobs:
        key = (j["model"], j["dataset"], j["arm"], j["ablation"])
        if key in seen:
            problems.append(f"duplicate job for {key}: {seen[key]} and {j['job_id']}")
        seen[key] = j["job_id"]
    ref = jobs[0] if jobs else None
    for j in jobs[1:]:
        for f in COMPAT_FIELDS:
            a, b = (ref["identity"].get(f) or ref["header"].get(f)), (j["identity"].get(f) or j["header"].get(f))
            if a != b:
                problems.append(f"{f} differs: {ref['job_id']}={a} vs {j['job_id']}={b}")
        for c in COMPAT_CONFIGS:
            a, b = ref["header"]["config_hashes"].get(c), j["header"]["config_hashes"].get(c)
            if a != b:
                problems.append(f"config {c} differs: {ref['job_id']}={a} vs {j['job_id']}={b}")
        if ref["header"].get("ollama_version") != j["header"].get("ollama_version"):
            problems.append("ollama_version differs")
    by_model = defaultdict(set)
    for j in jobs:
        by_model[j["model"]].add(j["header"].get("model_digest"))
        if j["identity"].get("sample_sha256") is None:
            problems.append(f"{j['job_id']}: no sample hash")
    for m, d in by_model.items():
        if len(d) > 1:
            problems.append(f"model {m} has several digests: {sorted(d)}")
    by_ds = defaultdict(set)
    for j in jobs:
        by_ds[j["dataset"]].add(j["identity"].get("sample_sha256"))
    for ds, s in by_ds.items():
        if len(s) > 1:
            problems.append(f"dataset {ds} was run on different sample files")
    machines = {j["header"].get("machine_id") for j in jobs}
    gpus = {j["header"].get("gpu_name") for j in jobs}
    if len(machines) > 1 or len(gpus) > 1:
        warnings.append(f"jobs come from several machines/GPUs ({sorted(map(str, machines))}); latencies are not comparable across them")
    if problems:
        raise MergeError("; ".join(problems))
    return warnings


def load_gold(data_dir: Path, dataset: str) -> dict[str, dict]:
    p = data_dir / "datasets" / dataset / "labels.jsonl"
    return {r["instance_id"]: r for r in (json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip())}


# ── per-job aggregation ──────────────────────────────────────────────────────

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def per_task_rows(job: dict, gold: dict[str, dict], sample_ids: list[str]) -> list[dict]:
    byid = {r["task_id"]: r for r in job["rows"]}
    out = []
    for tid in sample_ids:
        line = byid.get(tid)
        fk = failed_kind(line)
        g = gold[tid]
        sc = score_line(line, g) if line is not None else {}
        zero = {k: 0.0 for k in score_line({"ranked": None}, g)}
        primary = sc if fk is None else zero
        sens = sc if fk in (None, "gate", "gate_no_usage", "answer") and line is not None else zero      # parse failures keep their fallback output
        tok = tokens_of(line) if line else None
        row = {"task_id": tid, "model": job["model"], "dataset": job["dataset"], "arm": job["arm"], "ablation": job["ablation"],
               "status": line["status"] if line else "missing", "failed_kind": fk or "", "route": (line or {}).get("route") or "",
               "gate_error": bool((line or {}).get("gate_error")), "made_gate_call": bool(line and any(
                   c["role"] != "answer" for c in line.get("ollama_calls") or [])),
               "n_gate_calls": sum(1 for c in (line or {}).get("ollama_calls") or [] if c["role"] != "answer"),
               "n_gate_call_failures": len((line or {}).get("gate_error_events") or []),
               "n_gold_files": len(g["files"]), "n_gold_functions": len(g["functions"])}
        if (line or {}).get("preassess_fallback"):                     # column exists only if some PREASSESS task fell back
            row["preassess_fallback"] = line["preassess_fallback"]
        row.update({f"primary.{k}": v for k, v in primary.items()})
        row.update({f"sens.{k}": v for k, v in sens.items()})
        row.update({f"tok.{k}": v for k, v in (tok or {}).items()})
        for k in ("task_total", "retrieve", "gates_and_judges", "graph_expand", "generate"):
            row[f"lat_s.{k}"] = ((line or {}).get("latency_ms") or {}).get(k, None)
            row[f"lat_s.{k}"] = row[f"lat_s.{k}"] / 1000 if row[f"lat_s.{k}"] is not None else None
        out.append(row)
    return out


def aggregate(rows: list[dict], prefix: str = "primary") -> dict:
    n = len(rows)
    agg = {"n_tasks": n, "n_failed_primary": sum(1 for r in rows if r["failed_kind"]),
           "n_failed_hard": sum(1 for r in rows if r["failed_kind"] in ("hard", "missing")),
           "n_gate_parse_failures": sum(1 for r in rows if r["failed_kind"] == "gate")}
    for st, _ in STAGES:
        for lvl, key, ks in LEVELS:
            for k in ks:
                agg[f"{st}.{lvl}_acc@{k}"] = mean([r[f"{prefix}.{st}.{lvl}_acc@{k}"] for r in rows])
        agg[f"{st}.file_recall@5"] = mean([r[f"{prefix}.{st}.file_recall@5"] for r in rows])
    withtok = [r for r in rows if r.get("tok.total") is not None]
    agg["tokens_total_mean"] = mean([r["tok.total"] for r in withtok])
    for k in ("router_input", "router_output", "injected"):
        agg[f"tokens_{k}_mean"] = mean([r[f"tok.{k}"] for r in withtok])
    agg["n_with_token_counts"] = len(withtok)
    agg["quality_per_cost"] = mean([r[f"{prefix}.final_context.function_acc@10"] / (r["tok.total"] / 1000) for r in withtok if r["tok.total"]])
    routed = [r for r in rows if r["route"]]
    for rt in ("RAG", "GRAPH", "LC"):
        agg[f"pct_{rt}"] = 100 * sum(1 for r in routed if r["route"] == rt) / len(routed) if routed else None
    gated = [r for r in rows if r["made_gate_call"]]
    calls = sum(r["n_gate_calls"] for r in gated)
    agg["gate_error_rate"] = sum(r["n_gate_call_failures"] for r in gated) / calls if calls else None
    agg["gate_error_rate_tasks"] = sum(1 for r in gated if r["gate_error"]) / len(gated) if gated else None
    agg["n_routed"] = len(routed)
    n_nu = sum(1 for r in rows if r["failed_kind"] == "gate_no_usage")
    for kind in ("unavailable", "parse"):                             # PREASSESS gate fallbacks to LC; nothing when zero
        n_k = sum(1 for r in rows if r.get("preassess_fallback") == kind)
        if n_k:
            agg[f"n_preassess_fallback_{kind}"] = n_k
    if n_nu:                                                          # no output at all when the count is zero
        agg["n_gate_no_usage"] = n_nu
    agg["latency_task_total_mean_s"] = mean([r["lat_s.task_total"] for r in rows])
    return agg


def gate_token_rows(jobs: list[dict]) -> list[dict]:
    """Gate/judge tokens per task by path: GAPA call 1 only / call 1 + call 2; GASR's merged gate."""
    out = []
    tk = lambda c: (c["prompt_eval_count"] or 0) + (c["eval_count"] or 0)
    for j in jobs:
        if not j["complete"] or j["ablation"] != "full" and j["arm"] != "GAPA":
            continue
        ok = [r for r in j["rows"] if r["status"] == "ok" and r.get("gate")]
        groups = {}
        if j["arm"] == "GAPA":
            g = [r["gate"] for r in ok if r["gate"].get("gapa_call1")]
            groups["GAPA call 1 only"] = [tk(x["gapa_call1"]) for x in g if not x["gapa_call2"]]
            groups["GAPA call 1 + call 2"] = [tk(x["gapa_call1"]) + tk(x["gapa_call2"]) for x in g if x["gapa_call2"]]
            groups["GAPA entry gate (all tasks)"] = [tk(x["gapa_call1"]) + (tk(x["gapa_call2"]) if x["gapa_call2"] else 0) for x in g]
            groups["GAPA call 1 (all tasks)"] = [tk(x["gapa_call1"]) for x in g]
            groups["GAPA call 1 + call 2 + post-expand judges (all tasks)"] = [tk(x["gapa_call1"]) + (tk(x["gapa_call2"]) if x["gapa_call2"] else 0)
                                                                              + sum(tk(c) for c in x["post_expand_judges"]) for x in g]
        elif j["arm"] == "GASR":
            g = [r["gate"] for r in ok if r["gate"].get("gasr_gate")]
            groups["GASR merged gate (call A)"] = [tk(x["gasr_gate"]) for x in g]
            groups["GASR gate + post-expand judges (all tasks)"] = [tk(x["gasr_gate"]) + sum(tk(c) for c in x["post_expand_judges"]) for x in g]
        elif j["arm"] == "SELF_ROUTE":
            g = [r["gate"] for r in ok if r["gate"].get("selfroute_judge")]
            groups["Self-Route judge"] = [tk(x["selfroute_judge"]) for x in g]
        for name, xs in groups.items():
            out.append({"model": j["model"], "dataset": j["dataset"], "arm": j["arm"], "ablation": j["ablation"], "path": name,
                        "n": len(xs), "mean_gate_tokens": mean(xs)})
    return out


# ── tables ───────────────────────────────────────────────────────────────────

def fmt(x, nd=3):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


NOT_RUN = "not run"


def build_tables(jobs: list[dict], data_dir: Path, out: Path, warnings: list[str]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    gold = {ds: load_gold(data_dir, ds) for ds in {j["dataset"] for j in jobs}}
    sample_ids = {}
    for ds in gold:
        sf = data_dir / "samples" / f"{ds}.sample.json"
        sample_ids[ds] = json.loads(sf.read_text(encoding="utf-8"))["instance_ids"]
    done = [j for j in jobs if j["complete"]]
    per_task, aggs, aggs_sens = [], {}, {}
    for j in done:
        rows = per_task_rows(j, gold[j["dataset"]], sample_ids[j["dataset"]][: j["n_tasks"]])
        per_task += rows
        key = (j["model"], j["dataset"], j["arm"], j["ablation"])
        aggs[key], aggs_sens[key] = aggregate(rows, "primary"), aggregate(rows, "sens")
    # per-task csv
    if per_task:
        cols = sorted({k for r in per_task for k in r})
        first = ["task_id", "model", "dataset", "arm", "ablation", "status", "failed_kind", "route"]
        write_csv(out / "per_task.csv", first + [c for c in cols if c not in first], [[fmt(r.get(c), 6) if isinstance(r.get(c), float) else r.get(c, "") for c in first + [c for c in cols if c not in first]] for r in per_task])
    models = sorted({j["model"] for j in jobs})
    not_run = []

    def cell_state(model, ds, arm, abl="full"):
        j = next((x for x in jobs if (x["model"], x["dataset"], x["arm"], x["ablation"]) == (model, ds, arm, abl)), None)
        if j is None:
            return NOT_RUN
        if not j["complete"]:
            return f"{NOT_RUN} (incomplete: {len({r['task_id'] for r in j['rows']})}/{j['n_tasks']} tasks)"
        return None

    qhead = [f"File Acc@{k}" for k in (1, 3, 5)] + [f"Module Acc@{k}" for k in (5, 10)] + [f"Function Acc@{k}" for k in (5, 10)] + ["File Recall@5"]
    qkeys = [f"file_acc@{k}" for k in (1, 3, 5)] + [f"module_acc@{k}" for k in (5, 10)] + [f"function_acc@{k}" for k in (5, 10)] + ["file_recall@5"]
    summary = {"tables": []}
    for model in models:
        for tname, ds in (("table_iv_context_quality_swe-bench-lite", "swe-bench-lite"), ("table_v_context_quality_loc-bench", "loc-bench")):
            for variant, A in (("", aggs), ("_sensitivity", aggs_sens)):
                rows = []
                for arm in QUALITY_ARMS:
                    st_ = cell_state(model, ds, arm)
                    for st, lab in STAGES:
                        if st_:
                            rows.append([ARM_LABEL[arm], lab, st_] + [""] * (len(qhead) - 1))
                        else:
                            a = A[(model, ds, arm, "full")]
                            rows.append([ARM_LABEL[arm], lab] + [fmt(a[f"{st}.{k}"]) for k in qkeys])
                write_csv(out / f"{tname}_{model.replace(':', '-')}{variant}.csv", ["Arm", "Stage"] + qhead, rows)
        # Table VI
        for variant, A in (("", aggs), ("_sensitivity", aggs_sens)):
            rows = []
            for ds in ("swe-bench-lite", "loc-bench"):
                for arm in COST_ARMS:
                    st_ = cell_state(model, ds, arm)
                    if st_:
                        rows.append([ds, ARM_LABEL[arm], st_, "", "", "", "", "", "", "", ""])
                        continue
                    a = A[(model, ds, arm, "full")]
                    ag = arm == "ALWAYS_GRAPH"
                    rows.append([ds, ARM_LABEL[arm], fmt(a["tokens_total_mean"], 0), fmt(a["quality_per_cost"], 3),
                                 "–" if ag else fmt(a["pct_RAG"], 1), fmt(a["pct_GRAPH"], 1), "–" if ag else fmt(a["pct_LC"], 1),
                                 "–" if ag else fmt(a["gate_error_rate"], 3), a["n_tasks"], a["n_failed_primary"], a["n_failed_hard"]])
            write_csv(out / f"table_vi_cost_routing_{model.replace(':', '-')}{variant}.csv",
                      ["Dataset", "Arm", "Total tokens per task", "Quality per cost", "%RAG", "%GRAPH", "%LC", "Gate error rate", "Tasks", "Failed (primary)", "Failed (hard)"], rows)
        # Tables VII / VIII: after graph expansion, external baselines not run
        for tname, ds in (("table_vii_baselines_swe-bench-lite", "swe-bench-lite"), ("table_viii_baselines_loc-bench", "loc-bench")):
            rows = [[m, NOT_RUN] + [""] * 6 for m in ("BM25", "CodeRankEmbed", "LocAgent + fine-tuned Qwen2.5-Coder-7B")]
            for arm in ("GASR", "GAPA"):
                st_ = cell_state(model, ds, arm)
                if st_:
                    rows.append([ARM_LABEL[arm] + " (ours)", st_] + [""] * 6)
                else:
                    a = aggs[(model, ds, arm, "full")]
                    rows.append([ARM_LABEL[arm] + " (ours)", "our run"] + [fmt(a[f"after_expansion.{k}"]) for k in qkeys if k != "file_recall@5"])
            write_csv(out / f"{tname}_{model.replace(':', '-')}.csv", ["Method", "Source"] + [h for h in qhead if h != "File Recall@5"], rows)
        # Table IX ablation
        for variant, A in (("", aggs), ("_sensitivity", aggs_sens)):
            rows = []
            full = A.get((model, "swe-bench-lite", "GAPA", "full"))
            abl_cfg = load_toml(Path(__file__).resolve().parents[2] / "configs" / "ablations.toml")["settings"]
            for key, s in abl_cfg.items():
                if s.get("control"):
                    continue
                st_ = cell_state(model, "swe-bench-lite", "GAPA", key)
                if st_:
                    rows.append([s["label"], st_] + [""] * 9)
                    continue
                a = A[(model, "swe-bench-lite", "GAPA", key)]
                f_ = "final_context"
                df = (a[f"{f_}.function_acc@10"] - full[f"{f_}.function_acc@10"]) if full and key != "full" else None
                dt = (a["tokens_total_mean"] - full["tokens_total_mean"]) if full and key != "full" and a["tokens_total_mean"] is not None and full["tokens_total_mean"] is not None else None
                rows.append([s["label"], "run", fmt(a[f"{f_}.file_acc@5"]), fmt(a[f"{f_}.function_acc@10"]), fmt(a[f"{f_}.file_recall@5"]),
                             fmt(a["tokens_total_mean"], 0), fmt(a["quality_per_cost"], 3), "–" if key == "no_graph_expansion" else fmt(a["pct_GRAPH"], 1),
                             fmt(a["gate_error_rate"], 3), "–" if key == "full" else fmt(df), "–" if key == "full" else fmt(dt, 0)])
            write_csv(out / f"table_ix_ablation_{model.replace(':', '-')}{variant}.csv",
                      ["Setting", "State", "File Acc@5", "Function Acc@10", "File Recall@5", "Total tokens per task", "Quality per cost", "%GRAPH",
                       "Gate error rate", "Δ Func. Acc@10 vs full", "Δ Tokens vs full"], rows)
    # gate tokens, seed tiers, stubs, gapa summary, failures
    gt = gate_token_rows(done)
    write_csv(out / "gate_tokens.csv", ["model", "dataset", "arm", "ablation", "path", "n", "mean_gate_tokens"],
              [[r["model"], r["dataset"], r["arm"], r["ablation"], r["path"], r["n"], fmt(r["mean_gate_tokens"], 1)] for r in gt])
    flat = [dict(r, arm=j["arm"], dataset=j["dataset"], model=j["model"], ablation=j["ablation"]) for j in done for r in j["rows"] if r["status"] == "ok"]
    srows = []
    for (model, ds, arm, abl), _ in sorted(aggs.items()):
        recs = [r for j in done if (j["model"], j["dataset"], j["arm"], j["ablation"]) == (model, ds, arm, abl) for r in j["rows"] if r["status"] == "ok"]
        sh = seed_tier_shares(recs).get(arm)
        st = stub_shares(recs).get(arm)
        gs = gapa_gate_summary(recs) if arm == "GAPA" else {}
        srows.append([model, ds, arm, abl, len(recs), json.dumps(sh["share"]) if sh else "", fmt(sh["mean_size"], 2) if sh else "",
                      st["neighbor_chunks"] if st else "", st["stub_chunks"] if st else "", json.dumps(st["by_category"]) if st else "",
                      fmt(st["share_of_neighbor_chunks"]["stub"]) if st and st["share_of_neighbor_chunks"]["stub"] is not None else "",
                      fmt(gs.get("share_needing_call2")) if gs else ""])
    write_csv(out / "diagnostics.csv", ["model", "dataset", "arm", "ablation", "ok_tasks", "seed_tier_shares", "mean_seed_pool", "neighbor_chunks",
                                        "stub_chunks", "stub_by_category", "stub_share_of_neighbor_chunks", "gapa_share_needing_call2"], srows)
    frows = []
    for j in jobs:
        c = Counter((r["status"], "gate_parse_failure" if r.get("gate_error") else "") for r in j["rows"])
        frows.append([j["model"], j["dataset"], j["arm"], j["ablation"], j["job_id"], f"{len({r['task_id'] for r in j['rows']})}/{j['n_tasks']}",
                      "complete" if j["complete"] else "not run (incomplete)", j["queue_state"] or "", json.dumps({f"{a}|{b}": n for (a, b), n in sorted(c.items())})])
    write_csv(out / "failures.csv", ["model", "dataset", "arm", "ablation", "job_id", "lines", "table_state", "queue_state", "status_counts"], frows)
    summary.update({"jobs": len(jobs), "complete": len(done), "warnings": warnings, "aggregates": {"|".join(k): v for k, v in aggs.items()}})
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return summary


def explain(jobs: list[dict], data_dir: Path, task_id: str, arm: str, model: str, dataset: str, ablation: str = "full") -> str:
    j = next(x for x in jobs if (x["arm"], x["model"], x["dataset"], x["ablation"]) == (arm, model, dataset, ablation))
    line = next(r for r in j["rows"] if r["task_id"] == task_id)
    g = load_gold(data_dir, dataset)[task_id]
    lines = [f"task {task_id} arm {arm} status {line['status']} route {line.get('route')} failed_kind {failed_kind(line)}",
             f"GOLD files {g['files']}", f"GOLD modules {g['modules']}", f"GOLD functions {g['functions']}"]
    for st, lab in STAGES:
        ls = stage_list(line, st) or {}
        lines.append(f"-- {lab} ({st})")
        for lvl, key, ks in LEVELS:
            pred = dedupe(ls.get(key, []))
            lines.append(f"   {lvl}: pred[:10]={pred[:10]}")
            for k in ks:
                lines.append(f"      @{k}: hits={hits_at(pred, g[key], k)} need={min(len(set(g[key])), k)} acc={acc_at(pred, g[key], k)}"
                             + (f" recall={recall_at(pred, g[key], k):.3f}" if lvl == 'file' and k == 5 else ""))
    t = tokens_of(line)
    lines.append(f"tokens {t}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--explain", default=None)
    ap.add_argument("--arm", default="GAPA")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--dataset", default="swe-bench-lite")
    ap.add_argument("--ablation", default="full")
    ap.add_argument("--lc-scorer", choices=["legacy", "v2"], default="legacy",
                    help="legacy = LC chunks are file-only (module / function lists empty); v2 = module / function ids joined from the retrieved chunk")
    ap.add_argument("--with-new-arms", action="store_true",
                    help="also tabulate ALWAYS_RAG / ALWAYS_LC / PREASSESS (default: arm order and tables as for Oct 23)")
    a = ap.parse_args()
    if a.with_new_arms:
        ARM_ORDER.extend(x for x in NEW_ARMS if x not in ARM_ORDER)
        ARM_LABEL.update(NEW_ARMS)
        QUALITY_ARMS.extend(NEW_ARMS)
        COST_ARMS.extend(NEW_ARMS)
    jobs = load_jobs([p.resolve() for p in a.runs])
    if a.lc_scorer == "v2":
        recs = apply_lc_v2(jobs, a.data_dir.resolve())
        a.out.mkdir(parents=True, exist_ok=True)
        cols = ["job_id", "arm", "dataset", "ablation", "task_id", "file", "name", "function", "module", "how"]
        write_csv(a.out / "lc_resolution.csv", cols, [[r.get(c) if r.get(c) is not None else "" for c in cols] for r in recs])
    warnings = check_compat(jobs) if jobs else []
    data_dir = a.data_dir.resolve()
    if a.explain:
        print(explain(jobs, data_dir, a.explain, a.arm, a.model, a.dataset, a.ablation))
        return 0
    s = build_tables(jobs, data_dir, a.out.resolve(), warnings)
    print(f"jobs {s['jobs']} complete {s['complete']}; wrote tables to {a.out}")
    for w in warnings:
        print("WARNING:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
