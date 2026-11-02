"""Gate diagnostics for the Nov 3 GAPA and GASR jobs (read-only on logs, datasets and the nov3_83 report).

  python docs/analysis/gate_diagnostics/build_gate_diagnostics.py

Reads   eval_harness/data/runs/2350c0b/nov3-{01,02,09,10}-*  (GAPA, GASR)  and nov3-{03..06,11,12} for the counterfactual arms,
        eval_harness/data/datasets/{swe-bench-lite,loc-bench}/{tasks,labels}.jsonl,
        eval_harness/data/reports/nov3_83/{per_task,answer_scores}.csv  (through docs/analysis/nov3_lib.py).
Writes  issue_queries.csv, issue_queries.md, gate_diagnosis.md and gate_tables.json next to this file. Nothing else is touched.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "docs" / "analysis"))
import nov3_lib as L                                                       # noqa: E402  (read-only helper module)

RUNS = ROOT / "eval_harness" / "data" / "runs" / "2350c0b"
DATA = ROOT / "eval_harness" / "data" / "datasets"
DSETS = ["swe-bench-lite", "loc-bench"]
JOB_DIRS = {                                                              # (arm, dataset) -> (run dir, job id the task names)
    ("GAPA", "swe-bench-lite"): ("nov3-01-qwen3.5-9b-GAPA-swe-bench-lite", "GAPA__qwen3.5-9b__swe-bench-lite__2350c0b__20261028T153945"),
    ("GAPA", "loc-bench"): ("nov3-02-qwen3.5-9b-GAPA-loc-bench", "GAPA__qwen3.5-9b__loc-bench__2350c0b__20261028T154904"),
    ("GASR", "swe-bench-lite"): ("nov3-09-qwen3.5-9b-GASR-swe-bench-lite", "GASR__qwen3.5-9b__swe-bench-lite__2350c0b__20261028T170906"),
    ("GASR", "loc-bench"): ("nov3-10-qwen3.5-9b-GASR-loc-bench", "GASR__qwen3.5-9b__loc-bench__2350c0b__20261028T171833"),
    ("ALWAYS_RAG", "swe-bench-lite"): ("nov3-05-qwen3.5-9b-ALWAYS_RAG-swe-bench-lite", None),
    ("ALWAYS_RAG", "loc-bench"): ("nov3-06-qwen3.5-9b-ALWAYS_RAG-loc-bench", None),
}
SEED = 20261028
N_PERM = 10_000
CSV_OUT, MD_OUT, DIAG_OUT, JSON_OUT = (HERE / n for n in ("issue_queries.csv", "issue_queries.md", "gate_diagnosis.md", "gate_tables.json"))


# ── loading ──────────────────────────────────────────────────────────────────────────────────────────────────────
def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in open(p, encoding="utf-8", newline="") if x.strip()]


def load_all():
    tasks, gold, res = {}, {}, {}
    for ds in DSETS:
        tasks[ds] = {t["instance_id"]: t for t in jl(DATA / ds / "tasks.jsonl")}
        gold[ds] = {t["instance_id"]: t for t in jl(DATA / ds / "labels.jsonl")}
    for (arm, ds), (d, jid) in JOB_DIRS.items():
        rows = jl(RUNS / d / "results.jsonl")
        if jid:
            assert all(r["job_id"] == jid for r in rows), (d, jid)
        res[(arm, ds)] = {r["task_id"]: r for r in rows}
    pt, ans = L.load_per_task(), L.load_answers()
    ctx = {(arm, ds, tid): (row["primary.final_context.function_acc@10"], row["primary.final_context.file_acc@5"])
           for (ds, arm), rows in pt.items() for tid, row in rows.items() if arm in ("GAPA", "ALWAYS_RAG")}
    scores = {}
    for arm in ("GAPA", "GASR", "ALWAYS_RAG"):
        for ds in DSETS:
            for ver in ("primary", "sens"):
                for r in L.answer_rows(pt, ans, ds, arm, ver):
                    scores[(arm, ds, ver, r["task_id"])] = r
    return tasks, gold, res, scores, ctx


def call(row: dict, role: str):
    return next((c for c in row["ollama_calls"] if c["role"] == role), None)


def calls(row: dict, role: str):
    return [c for c in row["ollama_calls"] if c["role"] == role]


def parse_json_reply(text):
    """-> (dict | None, ok). A reply that is not valid JSON falls back to regexes on the same keys; ok is then False."""
    if text is None:
        return {}, False
    try:
        d = json.loads(text)
        return (d if isinstance(d, dict) else {}), isinstance(d, dict)
    except ValueError:
        d = {}
        m = re.search(r'"sufficient"\s*:\s*(true|false)', text)
        if m:
            d["sufficient"] = m.group(1) == "true"
        for k in ("confidence", "need", "reason"):
            m = re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)', text)
            if m:
                d[k] = m.group(1)
        return d, False


def msgs(c) -> tuple[str, str]:
    m = (c.get("request") or {}).get("messages") or []
    return (next((x["content"] for x in m if x["role"] == "system"), ""), next((x["content"] for x in m if x["role"] == "user"), ""))


def prompt_text(c) -> str:
    if c is None:
        return ""
    s, u = msgs(c)
    return f"[system]\n{s}\n\n[user]\n{u}"


def signals(user: str) -> dict:
    out = {}
    for k in ("top_score", "score_margin", "candidate_files"):
        m = re.search(rf"^{k}: (\S+)", user, re.M)
        out[k] = float(m.group(1)) if m else None
    m = re.search(r"^dependencies: (\d+)", user, re.M)
    out["dependencies"] = int(m.group(1)) if m else None
    return out


def post_judges(row: dict) -> tuple[list, list]:
    """The post-expansion judge prompt and reply are not logged (request / reply are null); only content_head (<= 240 chars)."""
    heads = [c.get("content_head") or "" for c in calls(row, "post_expand_judge")]
    suff = []
    for h in heads:
        m = re.search(r'"sufficient"\s*:\s*(true|false)', h)
        suff.append(None if m is None else m.group(1) == "true")
    return suff, heads


def fbool(v):
    return "" if v is None else bool(v)


# ── per-task records ─────────────────────────────────────────────────────────────────────────────────────────────
def build_records():
    tasks, gold, res, scores, ctx = load_all()
    recs = []
    for fam in ("GAPA", "GASR"):
        for ds in DSETS:
            for tid, r in res[(fam, ds)].items():
                t, g = tasks[ds][tid], gold[ds][tid]
                import hashlib
                assert hashlib.sha256(t["query"].encode()).hexdigest() == r["query_sha256"], tid        # the dataset text is the retriever query
                rag = res[("ALWAYS_RAG", ds)][tid]
                gapa = res[("GAPA", ds)][tid]
                gf = set(g["files"])
                top5 = r["ranked"]["retrieved_top5"]["files"] if r.get("ranked") else []
                rag_ctx = (rag.get("ranked") or {}).get("final_context", {}).get("files") or []
                c1, c2, gg = call(r, "gapa_gate_call1"), call(r, "gapa_gate_call2"), call(r, "gasr_gate")
                d1, ok1 = parse_json_reply(c1["reply"] if c1 else None)
                d2, ok2 = parse_json_reply(c2["reply"] if c2 else None)
                dg, okg = parse_json_reply(gg["reply"] if gg else None)
                # score signals: the GAPA call-1 prompt carries them; GASR does not print them, but both arms see the same retrieval
                gc1 = call(gapa, "gapa_gate_call1")
                sig = signals(msgs(gc1)[1]) if gc1 else {}
                suff, heads = post_judges(r)
                sc = {v: scores[(fam, ds, v, tid)] for v in ("primary", "sens")}
                rg = {v: scores[("ALWAYS_RAG", ds, v, tid)] for v in ("primary", "sens")}
                gi = gapa.get("gate_inputs") or {}
                rec = {
                    "arm_family": fam, "task_id": tid, "dataset": ds, "repo": r["repo"], "base_commit": r["base_commit"],
                    "query": t["query"], "query_chars": len(t["query"]), "query_words": len(t["query"].split()),
                    "gold_files": g["files"], "gold_functions": g["functions"],
                    "n_retrieved_chunks": gi.get("n_chunks"), "n_ranked_candidates": (gapa["ranked"]["after_retrieval"] or {}).get("n_items"),
                    "retrieved_top5_files": top5, "retrieved_top5_has_all_gold_files": bool(gf) and gf <= set(top5),
                    "rag_ctx_files": rag_ctx, "rag_ctx_has_all_gold_files": bool(gf) and gf <= set(rag_ctx),
                    "top_score": sig.get("top_score"), "score_margin": sig.get("score_margin"), "candidate_files": sig.get("candidate_files"),
                    "top_chunk_dependencies": sig.get("dependencies"),
                    "status": r["status"], "failed": bool(r.get("counted_as_failed")), "final_route": r["route"], "hops_used": r.get("hops_used"),
                    "post_expand_sufficient": suff, "post_expand_reply_heads": heads,
                    "ans_function_acc@10": sc["primary"]["function_acc@10"], "ans_file_acc@5": sc["primary"]["file_acc@5"],
                    "ans_function_acc@10_sens": sc["sens"]["function_acc@10"], "ans_file_acc@5_sens": sc["sens"]["file_acc@5"],
                    "rag_function_acc@10": rg["primary"]["function_acc@10"], "rag_file_acc@5": rg["primary"]["file_acc@5"],
                    "rag_function_acc@10_sens": rg["sens"]["function_acc@10"], "rag_file_acc@5_sens": rg["sens"]["file_acc@5"],
                    "ctx_function_acc@10": ctx[(fam, ds, tid)][0] if fam == "GAPA" else None, "ctx_file_acc@5": ctx[(fam, ds, tid)][1] if fam == "GAPA" else None,
                    "rag_ctx_function_acc@10": ctx[("ALWAYS_RAG", ds, tid)][0], "rag_ctx_file_acc@5": ctx[("ALWAYS_RAG", ds, tid)][1],
                }
                if fam == "GAPA":
                    rec.update({
                        "call1_prompt": prompt_text(c1), "call1_response": (c1 or {}).get("reply") or "", "call1_json_ok": ok1,
                        "call1_sufficient": d1.get("sufficient"), "call1_confidence": d1.get("confidence"), "call1_reason": d1.get("reason") or "",
                        "call2_ran": c2 is not None, "call2_prompt": prompt_text(c2), "call2_response": (c2 or {}).get("reply") or "",
                        "call2_json_ok": ok2 if c2 else None, "call2_need": d2.get("need"), "call2_confidence": d2.get("confidence"),
                        "call2_reason": d2.get("reason") or "", "gapa_path": r["gate"].get("gapa_path"),
                        "call1_error": (c1 or {}).get("error"), "call2_error": (c2 or {}).get("error")})
                else:
                    rec.update({
                        "gate_prompt": prompt_text(gg), "gate_response": (gg or {}).get("reply") or "", "gate_json_ok": okg,
                        "gate_sufficient": dg.get("sufficient"), "gate_need": dg.get("need"), "gate_confidence": dg.get("confidence"),
                        "gate_reason": dg.get("reason") or "", "gate_error": (gg or {}).get("error")})
                recs.append(rec)
    return recs


# ── statistics helpers ───────────────────────────────────────────────────────────────────────────────────────────
def fisher(a, b, c, d) -> float:
    """Two-sided exact p for [[a, b], [c, d]] (sum of tables no more likely than the observed one)."""
    n1, n2, k = a + b, c + d, a + c
    tot = n1 + n2
    lo, hi = max(0, k - n2), min(k, n1)
    pr = lambda x: math.comb(n1, x) * math.comb(n2, k - x) / math.comb(tot, k)
    p0 = pr(a)
    return min(1.0, sum(pr(x) for x in range(lo, hi + 1) if pr(x) <= p0 * (1 + 1e-9)))


def perm_p(x, y, seed=SEED) -> float:
    """Two-sided permutation test on the difference of means (10,000 shuffles)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    allv, n = np.concatenate([x, y]), len(x)
    obs = abs(x.mean() - y.mean())
    rng = np.random.default_rng(seed)
    hits = sum(abs((p := rng.permutation(allv))[:n].mean() - p[n:].mean()) >= obs - 1e-12 for _ in range(N_PERM))
    return (hits + 1) / (N_PERM + 1)


def auc(x, y) -> float:
    """P(a random x exceeds a random y), ties count half."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(((x[:, None] > y[None, :]).sum() + 0.5 * (x[:, None] == y[None, :]).sum()) / (len(x) * len(y)))


def fp(p):
    return "<0.0001" if p < 1e-4 else f"{p:.4f}"


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def md_table(header, rows):
    return "\n".join(["| " + " | ".join(map(str, header)) + " |", "|" + "|".join("---" for _ in header) + "|"] +
                     ["| " + " | ".join(map(str, r)) + " |" for r in rows])


def fence(s: str) -> str:
    n = max([len(m) for m in re.findall(r"`+", s)] + [2]) + 1
    return "`" * n


def block(s: str, lang: str = "") -> str:
    f = fence(s)
    return f"{f}{lang}\n{s}\n{f}"


# ── reason themes (call 1; first match wins) ────────────────────────────────────────────────────────────────────
THEMES = [
    ("sufficient: chunk holds the definition / diff the query asks about", None),                       # sufficient == true
    ("off-target chunk (test file, docs, changelog, template or unrelated code)",
     r"unrelated|test (?:file|case|assertion|code|suite)|tests for|changelog|release-notes|contributing|documentation file|bug report template|template file|example that|projection example|low-scoring|\.md\b"),
    ("shows only a definition / signature, lacks body or surrounding context",
     r"\bonly (?:shows|defines|provides|contains|the)\b|without the (?:specific|surrounding|implementation)"),
    ("defines a related symbol but not the code / logic the issue concerns",
     r"\b(?:defines|contains|is) .{0,160}\b(?:but|and) (?:does not|doesn't|lacks|do not|lacking)|\blacks\b|does not (?:contain|include)"),
]
SCORE_WORDS = re.compile(r"\bscore|margin|candidate[_ ]files?|similarity", re.I)
DEP_WORDS = re.compile(r"dependenc", re.I)
FILE_WORDS = re.compile(r"\b[\w/]+\.(?:py|md|rst|txt|toml|cfg|yaml|yml|json)\b|another file|other file|different file|multiple files|across files", re.I)


def theme(rec) -> str:
    if rec["call1_sufficient"] is True:
        return THEMES[0][0]
    if rec["call1_sufficient"] is None and not rec["call1_reason"]:
        return "no parseable reason"
    txt = rec["call1_reason"].lower()
    for name, rx in THEMES[1:]:
        if re.search(rx, txt):
            return name
    return "other"


# ── analysis ─────────────────────────────────────────────────────────────────────────────────────────────────────
def main() -> int:
    recs = build_records()
    gapa = [r for r in recs if r["arm_family"] == "GAPA"]
    gasr = [r for r in recs if r["arm_family"] == "GASR"]
    assert len(gapa) == 83 and len(gasr) == 83, (len(gapa), len(gasr))
    T: dict = {}                                                               # machine-readable copy of the tables

    # consistency: GASR and GAPA share the same retrieval
    two_readings = sum(r["rag_ctx_has_all_gold_files"] == r["retrieved_top5_has_all_gold_files"] for r in gapa)
    T["two_readings_agree"] = [two_readings, 83]
    same = sum(1 for a, b in zip(gapa, gasr) if a["task_id"] == b["task_id"] and a["retrieved_top5_files"] == b["retrieved_top5_files"])
    T["retrieval_identical_gapa_gasr"] = [same, 83]

    # ── CSV ──
    cols: list[str] = []
    for r in recs:
        cols += [k for k in r if k not in cols]
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in recs:
            w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else ("" if v is None else v)) for k, v in r.items()})

    # ── Markdown listing ──
    o = ["# Issue queries and gate calls, Nov 3 GAPA and GASR jobs",
         "",
         "Generated by `docs/analysis/gate_diagnostics/build_gate_diagnostics.py`; read-only on logs. One entry per task, 83 per arm family "
         "(27 SWE-Bench-Lite, 56 Loc-Bench). The same fields are in `issue_queries.csv`.",
         "",
         "- **Query**: the dataset `query` field; its SHA-256 equals `query_sha256` in every results row (VERIFIED, 166 of 166).",
         "- **Gold**: `labels.jsonl` of the dataset. **Retrieved chunks**: `gate_inputs.n_chunks` of the GAPA row (chunks passed to the gate); "
         "`n_ranked_candidates` is the ranked list before the top-5 cut. GASR rows carry no `gate_inputs`; the values shown are from the GAPA row of the same task "
         f"(the top-5 file lists are identical in {same} of 83 pairs).",
         "- **RAG-stage context has all gold files**: two readings are given. `final` = the context Always-RAG injects (pruned to 1800 tokens, "
         "`ranked.final_context.files` of the Always-RAG row); `top-5` = the files of the five raw retrieved chunks that the gate sees.",
         "- **Answer-level scores** are the primary version (failed tasks score 0); the sensitivity version is in the CSV.",
         "- **Post-expansion judges**: the harness logs only `content_head` (at most 240 characters of the reply); the judge prompt and the full reply are "
         "not stored (`request` and `reply` are null for `post_expand_judge`). This is all that exists in the logs.",
         "- Prompts and replies are shown exactly as logged: system message, then user message.",
         ""]
    for fam, group in (("GAPA", gapa), ("GASR", gasr)):
        o += [f"## {fam}", ""]
        for r in group:
            o += [f"### {fam} · {r['dataset']} · {r['task_id']}", "",
                  f"- repo `{r['repo']}`, base commit `{r['base_commit'][:12]}`; issue length {r['query_chars']} chars, {r['query_words']} words",
                  f"- gold files ({len(r['gold_files'])}): {', '.join('`' + x + '`' for x in r['gold_files']) or '(none)'}",
                  f"- gold functions ({len(r['gold_functions'])}): {', '.join('`' + x + '`' for x in r['gold_functions']) or '(none)'}",
                  f"- retrieved chunks passed to the gate: {r['n_retrieved_chunks']}; ranked candidates: {r['n_ranked_candidates']}",
                  f"- RAG-stage context contains all gold files: final **{r['rag_ctx_has_all_gold_files']}**, top-5 **{r['retrieved_top5_has_all_gold_files']}**",
                  f"- gate score inputs: top_score {r['top_score']}, score_margin {r['score_margin']}, candidate_files {r['candidate_files']}, "
                  f"top-chunk dependencies {r['top_chunk_dependencies']}",
                  f"- final route **{r['final_route']}**, hops {r['hops_used']}, status {r['status']}",
                  f"- answer-level (primary): function Acc@10 **{r['ans_function_acc@10']:.3f}**, file Acc@5 **{r['ans_file_acc@5']:.3f}**; "
                  f"Always-RAG on the same task: {r['rag_function_acc@10']:.3f} / {r['rag_file_acc@5']:.3f}", ""]
            o += ["**Query (issue text as passed to the retriever)**", "", block(r["query"]), ""]
            if fam == "GAPA":
                o += [f"**Call 1 verdict (parsed)**: sufficient={r['call1_sufficient']}, confidence={r['call1_confidence']}, JSON valid: {r['call1_json_ok']}", "",
                      "<details><summary>Call 1 prompt</summary>", "", block(r["call1_prompt"]), "", "</details>", "",
                      "**Call 1 raw response**", "", block(r["call1_response"]), "",
                      f"**Call 2 ran**: {r['call2_ran']}" + (f" (need={r['call2_need']}, confidence={r['call2_confidence']}, JSON valid: {r['call2_json_ok']})" if r["call2_ran"] else ""), ""]
                if r["call2_ran"]:
                    o += ["<details><summary>Call 2 prompt</summary>", "", block(r["call2_prompt"]), "", "</details>", "",
                          "**Call 2 raw response**", "", block(r["call2_response"]), ""]
            else:
                o += [f"**Merged gate (call A) verdict (parsed)**: sufficient={r['gate_sufficient']}, need={r['gate_need']}, confidence={r['gate_confidence']}, JSON valid: {r['gate_json_ok']}", "",
                      "<details><summary>Merged gate prompt</summary>", "", block(r["gate_prompt"]), "", "</details>", "",
                      "**Merged gate raw response**", "", block(r["gate_response"]), ""]
            o += [f"**Post-expansion judges** ({len(r['post_expand_sufficient'])}; sufficient flags {r['post_expand_sufficient']}; logged reply heads only):", ""]
            o += [f"{i + 1}. {block(h)}" for i, h in enumerate(r["post_expand_reply_heads"])] or ["(none: no expansion happened)"]
            o += ["", "---", ""]
    MD_OUT.write_text("\n".join(o), encoding="utf-8", newline="")

    # ── diagnosis ──
    D: list[str] = ["# Why GAPA call 2 runs so often: gate diagnostics, Nov 3 jobs", "",
                    "Read-only analysis of `nov3-01, 02, 09, 10` (GAPA, GASR) with `nov3-05, 06` (Always-RAG) as the no-expansion counterfactual. "
                    "Regenerate with `python docs/analysis/gate_diagnostics/build_gate_diagnostics.py`. Claims are marked VERIFIED (read from a file or counted from the logs) "
                    "or INFERRED (a reading of the counts).", ""]
    fired = [r for r in gapa if r["call2_ran"]]
    skipped = [r for r in gapa if not r["call2_ran"]]
    D += ["## Summary", ""]

    # (a) call 1 verdict distribution
    def verdict(r):
        return "sufficient=true" if r["call1_sufficient"] is True else ("sufficient=false" if r["call1_sufficient"] is False else "unparseable")
    rows_a = []
    for lab, sel in (("SWE-Bench-Lite", [r for r in gapa if r["dataset"] == "swe-bench-lite"]),
                     ("Loc-Bench", [r for r in gapa if r["dataset"] == "loc-bench"]), ("Both", gapa)):
        c = Counter(verdict(r) for r in sel)
        conf = Counter(f"{r['call1_confidence']}" for r in sel)
        rows_a.append([lab, len(sel), c["sufficient=true"], c["sufficient=false"], c["unparseable"],
                       "; ".join(f"{k} {v}" for k, v in sorted(conf.items())), sum(r["call2_ran"] for r in sel)])
    T["a_call1_verdicts"] = rows_a
    json_bad = [r["task_id"] for r in gapa if not r["call1_json_ok"]]
    D += ["## (a) Call 1 verdict distribution (GAPA)", "",
          md_table(["Dataset", "Tasks", "sufficient=true", "sufficient=false", "unparseable", "Call-1 confidence", "Call 2 ran"], rows_a), "",
          f"- VERIFIED: call 2 runs exactly when call 1 does not say `sufficient=true` (a call-1 reply that fails to parse counts as not sufficient). "
          f"Call 1 returned `sufficient=true` for {sum(r['call1_sufficient'] is True for r in gapa)} of 83 tasks; call 2 therefore ran on {len(fired)} of 83 "
          f"(25 of 27 SWE, 48 of 56 Loc, as reported).",
          f"- VERIFIED: call-1 replies that are not valid JSON as logged: {len(json_bad)} ({', '.join(json_bad) or 'none'}). "
          "The reply is `...\"reason\": \"...CursorResult.}` (the string is never closed), so `json.loads` fails. VERIFIED in the frozen `preroute_structured_gate`: an unparseable reply "
          "returns `sufficient: False, stage: preroute_structured_error, route: LC`, so the task counts as not sufficient and call 2 ran. The tables above count it as `sufficient=false` "
          "(flag recovered by regex from the logged text).", ""]
    T["json_bad_call1"] = json_bad
    # GASR merged gate for comparison
    rows_g = []
    for lab, sel in (("SWE-Bench-Lite", [r for r in gasr if r["dataset"] == "swe-bench-lite"]),
                     ("Loc-Bench", [r for r in gasr if r["dataset"] == "loc-bench"]), ("Both", gasr)):
        c = Counter((r["gate_sufficient"], r["gate_need"]) for r in sel)
        rows_g.append([lab, len(sel), c[(True, None)] + sum(v for (s, n), v in c.items() if s is True and n is not None),
                       c[(False, "STRUCTURE")], c[(False, "CONTEXT")], c[(False, "UNCLEAR")],
                       sum(v for (s, n), v in c.items() if s is None), Counter(r["final_route"] for r in sel)])
    T["a_gasr_gate"] = [[*x[:7], dict(x[7])] for x in rows_g]
    D += ["GASR merged gate (call A) for comparison:", "",
          md_table(["Dataset", "Tasks", "sufficient=true", "false, need STRUCTURE", "false, need CONTEXT", "false, need UNCLEAR", "unparseable", "Final routes"],
                   [[*x[:7], dict(x[7])] for x in rows_g]), ""]

    # (b) all gold files already in the RAG context
    D += ["## (b) Does call 1 still route to call 2 when the RAG context already holds all gold files?", ""]
    rows_b, fish = [], {}
    for lab, key in (("final (Always-RAG injected context)", "rag_ctx_has_all_gold_files"), ("top-5 raw chunks seen by the gate", "retrieved_top5_has_all_gold_files")):
        for dlab, sel in (("SWE-Bench-Lite", [r for r in gapa if r["dataset"] == "swe-bench-lite"]),
                          ("Loc-Bench", [r for r in gapa if r["dataset"] == "loc-bench"]), ("Both", gapa)):
            yes = [r for r in sel if r[key]]
            no = [r for r in sel if not r[key]]
            a, b = sum(r["call2_ran"] for r in yes), len(yes) - sum(r["call2_ran"] for r in yes)
            c, d = sum(r["call2_ran"] for r in no), len(no) - sum(r["call2_ran"] for r in no)
            p = fisher(a, b, c, d) if yes and no else float("nan")
            rows_b.append([lab, dlab, len(yes), pct(a, len(yes)), len(no), pct(c, len(no)), fp(p) if p == p else "n/a"])
            fish[(lab, dlab)] = [a, b, c, d]
    T["b_all_gold_vs_call2"] = rows_b
    D += [md_table(["RAG-stage reading", "Dataset", "Tasks with all gold files", "of those, call 2 ran", "Tasks without", "of those, call 2 ran", "Fisher p (two-sided)"], rows_b), "",
          f"- VERIFIED (count): the two readings of 'RAG-stage context' (Always-RAG injected context after pruning; the five raw chunks the gate sees) give the same yes/no for {two_readings} of 83 tasks, "
          "so the rows are the same split.",
          f"- VERIFIED (count): when the RAG context already holds every gold file, call 1 still sends the task to call 2 in {fish[('final (Always-RAG injected context)', 'Both')][0]} of "
          f"{fish[('final (Always-RAG injected context)', 'Both')][0] + fish[('final (Always-RAG injected context)', 'Both')][1]} cases; when it does not, it does so in all "
          f"{fish[('final (Always-RAG injected context)', 'Both')][2] + fish[('final (Always-RAG injected context)', 'Both')][3]}.", "",
          "GASR merged gate, same split (does `sufficient=false`):", ""]
    rows_bg = []
    for dlab, sel in (("SWE-Bench-Lite", [r for r in gasr if r["dataset"] == "swe-bench-lite"]),
                      ("Loc-Bench", [r for r in gasr if r["dataset"] == "loc-bench"]), ("Both", gasr)):
        yes = [r for r in sel if r["rag_ctx_has_all_gold_files"]]
        no = [r for r in sel if not r["rag_ctx_has_all_gold_files"]]
        a, c = sum(r["gate_sufficient"] is False for r in yes), sum(r["gate_sufficient"] is False for r in no)
        p = fisher(a, len(yes) - a, c, len(no) - c) if yes and no else float("nan")
        rows_bg.append([dlab, len(yes), pct(a, len(yes)), len(no), pct(c, len(no)), fp(p) if p == p else "n/a"])
    T["b_gasr"] = rows_bg
    D += [md_table(["Dataset", "Tasks with all gold files (final)", "gate said insufficient", "Tasks without", "gate said insufficient", "Fisher p"], rows_bg), ""]

    # (c)/(f) skipped vs fired
    def stats(sel, key):
        v = np.array([r[key] for r in sel], float)
        f_ = (lambda x: f"{x:,.0f}") if key in ("query_chars", "query_words") else (lambda x: f"{x:.3f}" if key in ("top_score", "score_margin") else f"{x:g}")
        return f"{f_(np.median(v))} [{f_(v.min())}, {f_(v.max())}]" if len(v) else "n/a"
    D += ["## (c) and (f) Tasks that skipped call 2 versus tasks that ran it", "",
          "Median [min, max]. Score signals are the three numbers printed in the call-1 prompt (step 3). p = two-sided permutation test on the difference of means "
          f"({N_PERM:,} shuffles, seed {SEED}); AUC = P(a skipped task has the larger value). INFERRED reading: with 10 skipped tasks the tests have little power.", ""]
    rows_c = []
    feats = [("issue length (chars)", "query_chars"), ("issue length (words)", "query_words"), ("candidate files", "candidate_files"),
             ("top score", "top_score"), ("score margin", "score_margin"), ("top-chunk dependencies", "top_chunk_dependencies")]   # retrieved chunks is 5 for every task: omitted
    for dlab, dfilter in (("Both", lambda r: True), ("SWE-Bench-Lite", lambda r: r["dataset"] == "swe-bench-lite"), ("Loc-Bench", lambda r: r["dataset"] == "loc-bench")):
        sk, fi = [r for r in skipped if dfilter(r)], [r for r in fired if dfilter(r)]
        for flab, key in feats:
            x, y = [r[key] for r in sk], [r[key] for r in fi]
            p = perm_p(x, y) if len(x) > 1 and len(y) > 1 else float("nan")
            rows_c.append([dlab, flab, f"{len(sk)}: " + stats(sk, key), f"{len(fi)}: " + stats(fi, key),
                           f"{auc(x, y):.2f}" if x and y else "n/a", p])
    ok_idx = [i for i, r in enumerate(rows_c) if r[5] == r[5]]
    holm_adj = L.holm([rows_c[i][5] for i in ok_idx])
    for i, h in zip(ok_idx, holm_adj):
        rows_c[i] = [*rows_c[i][:5], fp(rows_c[i][5]), fp(h)]
    for r in rows_c:
        if len(r) == 6:
            r += ["n/a"]
    T["c_f_skipped_vs_fired"] = rows_c
    n_holm_sig = sum(1 for r in rows_c if r[6] != "n/a" and r[6] != "<0.0001" and float(r[6]) < 0.05) + sum(1 for r in rows_c if r[6] == "<0.0001")
    D += [md_table(["Dataset", "Feature", "Skipped call 2 (n: median [min,max])", "Ran call 2 (n: median [min,max])", "AUC", "p", "Holm-adjusted p (18 tests)"], rows_c), "",
          f"- VERIFIED (count): {n_holm_sig} of {len(ok_idx)} tests are below 0.05 after Holm adjustment over all {len(ok_idx)}. Unadjusted, the pooled top score (skipped tasks higher), candidate files (skipped tasks fewer) and "
          "margin differ at p < 0.05; with 10 skipped tasks and 18 tests that is weak evidence. No single value separates the groups: the ranges overlap in every row "
          "(for example the lowest top score among skipped tasks, 0.328, is below the median of the tasks that ran call 2).", ""]
    # what the skipped tasks have in common
    all_gold_skip = sum(r["rag_ctx_has_all_gold_files"] for r in skipped)
    D += ["Skipped tasks (all had call 1 `sufficient=true`):", "",
          md_table(["Task", "Issue chars", "Cand. files", "Top score", "Margin", "Final route", "All gold in RAG ctx", "Call-1 reason (first 140 chars)"],
                   [[r["task_id"], r["query_chars"], r["candidate_files"], r["top_score"], r["score_margin"], r["final_route"], r["rag_ctx_has_all_gold_files"],
                     r["call1_reason"][:140].replace("|", "/").replace("\n", " ")] for r in skipped]), "",
          f"- VERIFIED: {sum(r['final_route'] == 'RAG' for r in skipped)} of {len(skipped)} skipped tasks ended on RAG (call 1 `sufficient=true` gives RAG with no further call); "
          f"{all_gold_skip} of {len(skipped)} had all gold files in the RAG context.", ""]

    # (d) route change versus call 1 alone
    D += ["## (d) Did call 2 change the route that call 1 alone implies?", "",
          "Route implied by call 1 alone: RAG if `sufficient=true`, otherwise LC. VERIFIED convention: the PREASSESS arm is defined as exactly this "
          "(`harness/arms.py:run_preassess`, 'RAG if sufficient else LC'), and `preroute_structured_gate` returns `route: LC` when it is not sufficient. "
          "Route after call 2: STRUCTURE leads to graph expansion (GRAPH), CONTEXT or UNCLEAR to LC (VERIFIED in the frozen pipeline, `diamond/gasr_ladder.py`: "
          "`if need != \"STRUCTURE\" or hard_cap <= 0: route LC`; a failed expansion with an empty neighbour pool also ends in LC).", ""]
    rows_d = []
    for dlab, sel in (("SWE-Bench-Lite", [r for r in gapa if r["dataset"] == "swe-bench-lite"]),
                      ("Loc-Bench", [r for r in gapa if r["dataset"] == "loc-bench"]), ("Both", gapa)):
        f_ = [r for r in sel if r["call2_ran"]]
        chg = [r for r in f_ if r["final_route"] != "LC"]
        rows_d.append([dlab, len(sel), len(f_), sum(r["final_route"] == "GRAPH" for r in f_), sum(r["final_route"] == "LC" for r in f_),
                       sum(r["final_route"] == "RAG" for r in f_), pct(len(chg), len(f_)),
                       sum(1 for r in f_ if (r["call2_need"] == "STRUCTURE") != (r["final_route"] == "GRAPH")),
                       sum(r["final_route"] != ("RAG" if r["call1_sufficient"] is True else "LC") for r in sel)])
    T["d_route_change"] = rows_d
    fail_exp = [r for r in gapa if r["call2_need"] == "STRUCTURE" and r["final_route"] == "LC"]
    ctx_none = {r["task_id"]: [(e["new_chunk_count"], len(e["neighbor_ids"])) for e in load_all()[2][("GAPA", r["dataset"])][r["task_id"]]["expansions"]] for r in fail_exp}
    T["d_failed_expansion"] = ctx_none
    D += [md_table(["Dataset", "Tasks", "Call 2 ran", "Final GRAPH", "Final LC", "Final RAG", "Call 2 changed route (not LC)", "Final route differs from call-2 label", "Final route differs from call-1 implied route (all tasks)"], rows_d), "",
          f"- VERIFIED: the {len(fail_exp)} tasks where call 2 said STRUCTURE but the route is LC ({', '.join(r['task_id'] for r in fail_exp)}) each took one hop that added 0 new chunks and found no neighbours "
          f"(`expansions[].new_chunk_count`, `neighbor_ids`: {ctx_none}); `gasr_ladder.py` then returns LC. The one CONTEXT verdict "
          f"({', '.join(r['task_id'] for r in gapa if r['call2_need'] == 'CONTEXT')}) went to LC directly.",
          "- INFERRED: call 1 only ever says 'not sufficient', so the implied route is LC for nearly every task; call 2 is the step that turns that LC into GRAPH. "
          "The post-expansion judges do not change the route: after an expansion that added chunks the ladder returns GRAPH whether the judge says sufficient or not "
          "(VERIFIED in `gasr_ladder.py`); they only decide whether a second hop is taken (hard cap 2).", ""]

    # (e) reasons by theme
    D += ["## (e) Stated reasons in call 1 responses, grouped by theme", "",
          "Themes are assigned by keyword rules, first match wins (rules are in `build_gate_diagnostics.py`, `THEMES`); counts are not hand-labelled. "
          "Flag columns are not exclusive.", ""]
    th = defaultdict(list)
    for r in gapa:
        th[theme(r)].append(r)
    names = [n for n, _ in THEMES] + ["other", "no parseable reason"]
    rows_e = []
    for n in names:
        sel = th.get(n, [])
        if not sel:
            continue
        rows_e.append([n, len(sel), sum(r["dataset"] == "swe-bench-lite" for r in sel), sum(r["dataset"] == "loc-bench" for r in sel),
                       sum(r["call2_ran"] for r in sel), sum(bool(SCORE_WORDS.search(r["call1_reason"])) for r in sel),
                       sum(bool(DEP_WORDS.search(r["call1_reason"])) for r in sel), sum(bool(FILE_WORDS.search(r["call1_reason"])) for r in sel),
                       sel[0]["task_id"] + ": " + sel[0]["call1_reason"][:150].replace("|", "/").replace("\n", " ")])
    T["e_reason_themes"] = rows_e
    cites_score = sum(bool(SCORE_WORDS.search(r["call1_reason"])) for r in gapa)
    cites_dep = sum(bool(DEP_WORDS.search(r["call1_reason"])) for r in gapa)
    D += [md_table(["Theme", "Tasks", "SWE", "Loc", "Call 2 ran", "Cites score / margin / candidate files", "Cites dependencies", "Names a file", "Example"], rows_e), "",
          f"- VERIFIED (count): {cites_score} of 83 call-1 reasons mention a score, margin or candidate-file count; {cites_dep} mention dependencies. "
          "The prompt's decision rule (step 5) says to prefer `sufficient=true` unless step 3 or 4 clearly indicates scattered evidence or heavy dependencies.",
          f"- INFERRED: the stated reasons are about whether the single top chunk contains the code the issue concerns ({sum(len(th.get(n, [])) for n in names[1:4])} of 83 say it defines something else, shows only a definition, or is off-target); "
          f"the score rule in step 5 is almost never cited. The verdict is `false` for {sum(r['call1_sufficient'] is not True for r in gapa)} of 83 tasks.", ""]
    T["cites_score"], T["cites_dep"] = cites_score, cites_dep

    # information content and cross-tab
    D += ["## Do call 1 and call 2 add information?", "",
          "### Cross-tab: call 1 verdict × call 2 verdict × final route (GAPA)", ""]
    ct = Counter()
    for r in gapa:
        ct[(r["dataset"], verdict(r), (r["call2_need"] or "(not run)") if r["call2_ran"] else "(not run)", r["final_route"])] += 1
    rows_ct = [[k[0], k[1], k[2], k[3], v] for k, v in sorted(ct.items())]
    tot = Counter()
    for k, v in ct.items():
        tot[k[1:]] += v
    rows_ct += [["Both", *k[:3], v] for k, v in sorted(tot.items())]
    T["crosstab"] = rows_ct
    D += [md_table(["Dataset", "Call 1 verdict", "Call 2 verdict", "Final route", "Tasks"], rows_ct), ""]
    # information tests
    need_ct = Counter(r["call2_need"] for r in fired)
    s_all = [r for r in gapa]
    yes = [r for r in s_all if r["rag_ctx_has_all_gold_files"]]
    no = [r for r in s_all if not r["rag_ctx_has_all_gold_files"]]
    sa, sb = sum(r["call1_sufficient"] is True for r in yes), sum(r["call1_sufficient"] is True for r in no)
    p1 = fisher(sa, len(yes) - sa, sb, len(no) - sb)
    st_yes = sum(r["call2_need"] == "STRUCTURE" for r in fired if r["rag_ctx_has_all_gold_files"])
    n_yes = sum(1 for r in fired if r["rag_ctx_has_all_gold_files"])
    st_no = sum(r["call2_need"] == "STRUCTURE" for r in fired if not r["rag_ctx_has_all_gold_files"])
    n_no = sum(1 for r in fired if not r["rag_ctx_has_all_gold_files"])
    p2 = fisher(st_yes, n_yes - st_yes, st_no, n_no - st_no) if n_yes and n_no else float("nan")
    # does the verdict track answer quality? sufficient=true (RAG) versus the rest on Always-RAG's own answer quality
    rag_q_suff = [r["rag_function_acc@10"] for r in gapa if r["call1_sufficient"] is True]
    rag_q_rest = [r["rag_function_acc@10"] for r in gapa if r["call1_sufficient"] is not True]
    rag_f_suff = [r["rag_file_acc@5"] for r in gapa if r["call1_sufficient"] is True]
    rag_f_rest = [r["rag_file_acc@5"] for r in gapa if r["call1_sufficient"] is not True]
    pq = perm_p(rag_q_suff, rag_q_rest)
    pf = perm_p(rag_f_suff, rag_f_rest)
    info = [
        ["call 1 `sufficient=true`", f"{sa}/{len(yes)} when the RAG context holds all gold files", f"{sb}/{len(no)} when it does not", fp(p1)],
        ["call 2 `STRUCTURE` (of tasks where call 2 ran)", f"{st_yes}/{n_yes} when all gold files are in the RAG context", f"{st_no}/{n_no} when not", fp(p2) if p2 == p2 else "n/a"],
        ["Always-RAG function Acc@10 on the same tasks", f"mean {np.mean(rag_q_suff):.3f} where call 1 said sufficient (n={len(rag_q_suff)})",
         f"mean {np.mean(rag_q_rest):.3f} where it did not (n={len(rag_q_rest)})", fp(pq)],
        ["Always-RAG file Acc@5 on the same tasks", f"mean {np.mean(rag_f_suff):.3f} (n={len(rag_f_suff)})", f"mean {np.mean(rag_f_rest):.3f} (n={len(rag_f_rest)})", fp(pf)],
    ]
    T["information"] = info
    D += ["### Does either call track anything about the task?", "", md_table(["Quantity", "Group A", "Group B", "p"], info), "",
          f"Call 2 verdicts on the {len(fired)} tasks where it ran: {dict(need_ct)}. VERIFIED (count).", "",
          f"- VERIFIED (count), call 1: all {sa} `sufficient=true` verdicts fall on tasks whose RAG context already holds every gold file ({sa} of {len(yes)} such tasks; 0 of {len(no)} others; Fisher p {fp(p1)}). "
          f"So a `true` verdict is reliable here, but it fires on only {100 * sa / len(yes):.0f}% of the tasks whose RAG context already held every gold file. On Always-RAG's own answers, tasks with a `true` verdict score "
          f"higher (function Acc@10 {np.mean(rag_q_suff):.3f} against {np.mean(rag_q_rest):.3f}).",
          f"- INFERRED, call 1: it carries real but low-recall information: it identifies a subset of the easy tasks and sends the other {len(yes) - sa} 'already sufficient' tasks onward. "
          "It does not separate the rest: call 2 then ran on every task where the RAG context lacked a gold file (37 of 37), as expected.",
          f"- VERIFIED (count), call 2: it answered STRUCTURE for {need_ct['STRUCTURE']} of {len(fired)} tasks and CONTEXT for {need_ct['CONTEXT']}; the STRUCTURE rate is the same whether or not the RAG context "
          f"held all gold files ({st_yes}/{n_yes} against {st_no}/{n_no}, p {fp(p2) if p2 == p2 else 'n/a'}).",
          "- INFERRED, call 2: with one label on 72 of 73 tasks it adds almost no information about the task. Its effect is to route every call-1 'no' to graph expansion. "
          "The final route differs from the call-2 label only where the expansion found no neighbours (listed under (d)). "
          "The GAPA call-2 rate (0.926 SWE, 0.857 Loc) is above the break-even rates in `docs/analysis/tables/t6_break_even.csv`.", ""]

    # helped / hurt: GAPA minus Always-RAG on the same task, at the answer level (primary) and at the final-context level
    def effect(d1_, d2_):
        return "helped" if (d1_ > 0 or (d1_ == 0 and d2_ > 0)) else ("hurt" if (d1_ < 0 or (d1_ == 0 and d2_ < 0)) else "tied")
    for r in gapa:
        r["delta_func"], r["delta_file"] = r["ans_function_acc@10"] - r["rag_function_acc@10"], r["ans_file_acc@5"] - r["rag_file_acc@5"]
        r["effect"] = effect(r["delta_func"], r["delta_file"])
        r["cdelta_func"], r["cdelta_file"] = r["ctx_function_acc@10"] - r["rag_ctx_function_acc@10"], r["ctx_file_acc@5"] - r["rag_ctx_file_acc@5"]
        r["ceffect"] = effect(r["cdelta_func"], r["cdelta_file"])
    exp_fired = [r for r in fired if r["final_route"] == "GRAPH"]
    eff_rows = []
    for dlab, sel in (("SWE-Bench-Lite", [r for r in exp_fired if r["dataset"] == "swe-bench-lite"]), ("Loc-Bench", [r for r in exp_fired if r["dataset"] == "loc-bench"]), ("Both", exp_fired)):
        c, cc = Counter(r["effect"] for r in sel), Counter(r["ceffect"] for r in sel)
        eff_rows.append([dlab, len(sel), c["helped"], c["hurt"], c["tied"], cc["helped"], cc["hurt"], cc["tied"],
                         f"{np.mean([r['ans_function_acc@10'] for r in sel]):.3f} / {np.mean([r['rag_function_acc@10'] for r in sel]):.3f}",
                         f"{np.mean([r['ctx_function_acc@10'] for r in sel]):.3f} / {np.mean([r['rag_ctx_function_acc@10'] for r in sel]):.3f}"])
    T["expansion_effect"] = eff_rows
    same_ans = sum(1 for r in exp_fired if r["delta_func"] == 0 and r["delta_file"] == 0)
    D += ["## Did expansion help? (GAPA tasks that fired call 2 and ended on GRAPH, against Always-RAG on the same task)", "",
          "Definition (INFERRED proxy, not causal): Always-RAG uses the same retrieval and pruning without expansion, so GAPA minus Always-RAG is the effect of the whole GRAPH route on that task. "
          "`helped` = function Acc@10 higher, or equal with file Acc@5 higher; `hurt` = the reverse; else `tied`. Two levels: answer (primary scores, failed tasks 0) and final context "
          "(`primary.final_context` in the report, before the answer model).", "",
          md_table(["Dataset", "GRAPH-route tasks", "answer: helped", "answer: hurt", "answer: tied", "context: helped", "context: hurt", "context: tied",
                    "mean function Acc@10 answer, GAPA / RAG", "mean function Acc@10 context, GAPA / RAG"], eff_rows), "",
          f"- VERIFIED (count): {same_ans} of {len(exp_fired)} GRAPH-route tasks have identical answer-level function Acc@10 and file Acc@5 to Always-RAG. "
          "At the answer level no GRAPH-route task improved on Always-RAG.",
          f"- VERIFIED (count): the {sum(r['effect'] == 'hurt' for r in exp_fired)} answer-level `hurt` tasks all had every gold file in the RAG context before expansion "
          f"({sum(r['effect'] == 'hurt' and r['rag_ctx_has_all_gold_files'] for r in exp_fired)} of {sum(r['effect'] == 'hurt' for r in exp_fired)}); "
          f"at the context level {sum(r['effect'] == 'hurt' and r['ceffect'] == 'tied' for r in exp_fired)} of them are tied, i.e. the final context kept the same Acc@k and the answer model still scored lower. "
          "INFERRED: single deterministic runs; the answer-level differences are point differences, not an estimate of the expansion effect.", ""]

    # shortest / longest issue texts
    uniq = sorted({r["task_id"]: r for r in gapa}.values(), key=lambda r: (r["query_chars"], r["task_id"]))
    short, long_ = uniq[:10], uniq[::-1][:10]
    D += ["## The 10 shortest and 10 longest issue texts (83 tasks pooled)", "",
          md_table(["Rank", "Task", "Chars", "Words", "Call 1", "Call 2 ran", "Final route"],
                   [[f"short {i + 1}", r["task_id"], r["query_chars"], r["query_words"], verdict(r), r["call2_ran"], r["final_route"]] for i, r in enumerate(short)] +
                   [[f"long {i + 1}", r["task_id"], r["query_chars"], r["query_words"], verdict(r), r["call2_ran"], r["final_route"]] for i, r in enumerate(long_)]), "",
          f"Median issue length: skipped {np.median([r['query_chars'] for r in skipped]):.0f} chars, ran {np.median([r['query_chars'] for r in fired]):.0f} chars.", ""]
    T["shortest"] = [[r["task_id"], r["query_chars"]] for r in short]
    T["longest"] = [[r["task_id"], r["query_chars"]] for r in long_]
    for title, group in (("Shortest", short), ("Longest", long_)):
        D += [f"### {title} 10: full text", ""]
        for i, r in enumerate(group):
            D += [f"**{i + 1}. {r['task_id']}** ({r['query_chars']} chars)", "", block(r["query"]), ""]

    # examples
    def pick(sel, key):
        return sorted(sel, key=key)[:5]
    helped_a = pick([r for r in exp_fired if r["effect"] == "helped"], lambda r: (-(r["delta_func"]), -r["delta_file"], r["task_id"]))
    hurt_a = pick([r for r in exp_fired if r["effect"] == "hurt"], lambda r: (r["delta_func"], r["delta_file"], r["task_id"]))
    helped_c = pick([r for r in exp_fired if r["ceffect"] == "helped"], lambda r: (-(r["cdelta_func"]), -r["cdelta_file"], r["task_id"]))
    skip5 = [r for r in skipped if r["dataset"] == "swe-bench-lite"][:2] + [r for r in sorted(skipped, key=lambda r: r["task_id"]) if r["dataset"] == "loc-bench"][:3]
    n_ = lambda k: sum(r[k] == "helped" for r in exp_fired)
    D += ["## Examples", "",
          "Selection is deterministic: largest difference first, ties by task id. The skipped examples are both SWE tasks that skipped (there are two) and the first three Loc tasks by id "
          f"(of {len(skipped)} skipped). A group shows fewer than five when fewer qualify.", ""]
    T["examples"] = {}
    groups = [(f"Call 2 fired and expansion helped, answer level (showing {len(helped_a)} of {sum(r['effect'] == 'helped' for r in exp_fired)})", helped_a, "ans"),
              (f"Call 2 fired and expansion helped, final-context level (showing {len(helped_c)} of {sum(r['ceffect'] == 'helped' for r in exp_fired)}; "
               "used because the answer level has none)", helped_c, "ctx"),
              (f"Call 2 fired and expansion hurt, answer level (showing {len(hurt_a)} of {sum(r['effect'] == 'hurt' for r in exp_fired)})", hurt_a, "ans"),
              (f"Call 2 skipped (showing {len(skip5)} of {len(skipped)})", skip5, "ans")]
    for title, group, lvl in groups:
        D += [f"### {title}", ""]
        if not group:
            D += ["No task qualifies, so there are no examples to show.", ""]
        T["examples"][title] = [r["task_id"] for r in group]
        for r in group:
            D += [f"**{r['task_id']}** ({r['dataset']}, issue {r['query_chars']} chars)", "",
                  f"- call 1: sufficient={r['call1_sufficient']}; reason: {r['call1_reason']}",
                  f"- call 2: {r['call2_need'] if r['call2_ran'] else 'not run'}" + (f"; reason: {r['call2_reason']}" if r["call2_ran"] else ""),
                  f"- final route {r['final_route']}, hops {r['hops_used']}; post-expansion judges sufficient={r['post_expand_sufficient']}",
                  f"- gold files {r['gold_files']}; RAG-stage context has all gold files: {r['rag_ctx_has_all_gold_files']}",
                  f"- GAPA answer: function Acc@10 {r['ans_function_acc@10']:.3f}, file Acc@5 {r['ans_file_acc@5']:.3f}; Always-RAG: {r['rag_function_acc@10']:.3f}, {r['rag_file_acc@5']:.3f}",
                  f"- final context: function Acc@10 {r['ctx_function_acc@10']:.3f}, file Acc@5 {r['ctx_file_acc@5']:.3f}; Always-RAG context: {r['rag_ctx_function_acc@10']:.3f}, {r['rag_ctx_file_acc@5']:.3f}", ""]
    k = D.index("## Summary") + 1
    tf, tn = sum(r["rag_ctx_has_all_gold_files"] for r in gapa), sum(not r["rag_ctx_has_all_gold_files"] for r in gapa)
    fa = sum(r["rag_ctx_has_all_gold_files"] and r["call2_ran"] for r in gapa)
    summ = ["",
            f"- Call 2 ran on {len(fired)} of 83 tasks because call 1 returned `sufficient=false` on {len(fired)} of 83 (one reply was invalid JSON and counts as not sufficient). VERIFIED.",
            f"- The RAG context already held every gold file in {tf} of 83 tasks; call 1 still declined RAG on {fa} of those {tf} ({100 * fa / tf:.0f}%). It said `true` on {sa} and was right on all {sa}. VERIFIED.",
            f"- Call 2 answered STRUCTURE on {need_ct['STRUCTURE']} of {len(fired)}, so it adds almost no information beyond routing call 1's 'no' to graph expansion. {len(fail_exp)} STRUCTURE tasks fell back to LC because the hop found no neighbours. VERIFIED (counts), INFERRED (reading).",
            f"- The 10 tasks that skipped call 2 all ended on RAG and all had every gold file in context. Their gate score inputs overlap those of the other 73 tasks (top score median 0.508 against 0.430; none of 18 tests survives Holm). INFERRED.",
            f"- Expansion did not improve the answer-level score of any of the {len(exp_fired)} GRAPH-route tasks over Always-RAG (helped 0, hurt {sum(r['effect'] == 'hurt' for r in exp_fired)}, tied {sum(r['effect'] == 'tied' for r in exp_fired)}). INFERRED proxy.",
            "- Not in the logs: the post-expansion judges' prompts and full replies (only a 240-character head of each reply). See the notes in `issue_queries.md`.", ""]
    D[k:k] = summ
    DIAG_OUT.write_text("\n".join(D) + "\n", encoding="utf-8", newline="")
    JSON_OUT.write_text(json.dumps(T, indent=1, default=str), encoding="utf-8")
    print(f"wrote {CSV_OUT.name} ({len(recs)} rows), {MD_OUT.name}, {DIAG_OUT.name}, {JSON_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
