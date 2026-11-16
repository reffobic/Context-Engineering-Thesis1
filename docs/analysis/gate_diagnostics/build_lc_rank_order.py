"""Offline LC context rebuild: alphabetical file order (current) versus retrieval-rank order (read-only, no model calls).

  .venv/bin/python docs/analysis/gate_diagnostics/build_lc_rank_order.py

Rebuilds, for the 83 Always-LC tasks, the context the prompt builder produced: the five retrieved chunks' parent files
(retrieval_tool.reconstruct_file_context: all chunks of the file, sorted by start_line, first 10 kept, each as
"# [type] name  (lines s-e)\\n<raw code>"), one snippet per retrieved chunk (arms._build_lc_chunks), grouped by file, then
PromptBuilder._truncate_file_blocks with the 24000-token budget (estimate = len // 4). The cached backend's own prompt_builder is
imported, so the grouping/truncation code is the code that ran. Calibration compares the rebuilt prompt with inject.prompt_chars.
Writes lc_rank_order_tasks.csv and lc_rank_order_report.md next to this file. Nothing else is touched.
"""
from __future__ import annotations

import csv
import glob
import json
import sqlite3
import statistics as S
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "eval_harness" / "data"
BACKEND = DATA / "cache" / "pipeline" / "2350c0b2e064e4fd808e941731366e6cc71a9551" / "backend"
sys.path.insert(0, str(ROOT / "docs" / "analysis"))
sys.path.insert(0, str(ROOT / "eval_harness" / "src"))
sys.path.insert(0, str(BACKEND))
import nov3_lib as L                                                       # noqa: E402
from harness.locmap import Mapper                                          # noqa: E402
from injection import prompt_builder as pb                                 # noqa: E402

SEP = "\n\n--- RAW CODE ---\n"                                             # retrieval_tool.py:39
CAP = 10                                                                    # retrieval_tool.py:67
BUDGET = 24000                                                              # prompt_builder_endpoint.DEFAULT_MAX_CONTEXT_TOKENS
res = L.load_results()
SUFFIX = next(iter(res[("swe-bench-lite", "ALWAYS_LC")].values()))["extras"]["answer_suffix"]
tasks = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "tasks.jsonl") if x.strip()} for ds in L.DATASETS}
gold = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "labels.jsonl") if x.strip()} for ds in L.DATASETS}
builder = pb.PromptBuilder(system_prompt=pb.DEFAULT_SYSTEM_PROMPT)

Q = """select e.id, m.key, m.string_value, m.int_value from embeddings e
       join segments s on s.id = e.segment_id join collections co on co.id = s.collection join embedding_metadata m on m.id = e.id
       where co.name = 'code_embeddings' and e.id in
         (select m2.id from embedding_metadata m2 join embeddings e2 on e2.id = m2.id join segments s2 on s2.id = e2.segment_id
          join collections c2 on c2.id = s2.collection where c2.name = 'code_embeddings' and m2.key = 'file_path' and m2.string_value = ?)
       and m.key in ('start_line','end_line','name','chunk_type','chroma:document','language') order by e.id"""
_conn: dict = {}


def chunks_of(repo, base, path):
    k = (repo, base)
    if k not in _conn:
        d = glob.glob(str(DATA / "cache" / "indexes" / f"{repo.replace('/', '__')}__{base[:12]}__*" / "chroma" / "chroma.sqlite3"))
        _conn[k] = sqlite3.connect(f"file:{d[0]}?mode=ro", uri=True)
    rows = defaultdict(dict)
    for i, key, sv, iv in _conn[k].execute(Q, (path,)):
        rows[i][key] = iv if sv is None else sv
    return sorted(rows.values(), key=lambda x: x.get("start_line") or 0)


def raw(doc: str) -> str:
    return doc.split(SEP, 1)[1] if SEP in (doc or "") else (doc or "")


def stitched(ch: list[dict]) -> str:
    parts = [f"# [{c.get('chunk_type', '')}] {c.get('name', 'block')}  (lines {c.get('start_line', '?')}–{c.get('end_line', '?')})\n{raw(c.get('chroma:document', ''))}" for c in ch]
    return "\n\n".join(parts)


def render(order_paths, blocks):
    ctx = builder._truncate_file_blocks([blocks[p] for p in order_paths], BUDGET)
    user = f"<CONTEXT>\n<FILES_AND_CODE>\n{ctx}\n</FILES_AND_CODE>\n</CONTEXT>\n\n<TASK>\n"
    return ctx, user


rows, calib = [], []
for ds in L.DATASETS:
    for tid, r in sorted(res[(ds, "ALWAYS_LC")].items()):
        items = r["ranked"]["final_context"]["unmapped"]                    # the five LC chunks in rank order (file, name)
        gf, gfn = set(gold[ds][tid]["files"]), list(gold[ds][tid]["functions"])
        files_rank = list(dict.fromkeys(i["file"] for i in items))
        per_file, kept, lang = {}, {}, {}
        for p in files_rank:
            allc = chunks_of(r["repo"], r["base_commit"], p)
            per_file[p] = allc
            kept[p] = allc[:CAP]
            lang[p] = next((c.get("language") for c in allc if c.get("language")), "")
        st = {p: stitched(kept[p]) for p in files_rank}
        task = tasks[ds][tid]["query"].strip() + "\n\n" + SUFFIX.strip()

        def blocks_for(repeat: bool):
            by = defaultdict(list)
            for it in items:
                by[it["file"]].append({"text": st[it["file"]], "path": it["file"], "symbol": it.get("name") or "", "language": lang[it["file"]]})
            if not repeat:
                by = {p: v[:1] for p, v in by.items()}
            return {p: builder._file_block(p, v) for p, v in by.items()}

        for repeat in (True, False):
            b = blocks_for(repeat)
            ctx, head = render(sorted(b, key=lambda p: (p == "(unknown path)", p)), b)
            total = len(pb.DEFAULT_SYSTEM_PROMPT) + len(head) + len(task) + len("\n</TASK>")
            calib.append({"dataset": ds, "task_id": tid, "repeat": repeat, "sim_chars": total, "logged_chars": r["inject"]["prompt_chars"],
                          "logged_hit": bool(r["inject"]["hit_max_context_tokens"]), "sim_hit": pb.CONTEXT_TRUNCATED_TAG in ctx,
                          "max_per_file": max(Counter(i["file"] for i in items).values()), "ctx_chars": len(ctx) })
        b = blocks_for(True)
        out = {"dataset": ds, "task_id": tid, "n_files": len(files_rank), "items_per_file": ";".join(f"{p}={n}" for p, n in Counter(i['file'] for i in items).items())}
        for name, order in (("alpha", sorted(b, key=lambda p: (p == "(unknown path)", p))), ("rank", files_rank)):
            ctx, head = render(order, b)
            kept_files = [p for p in order if f"=== File: {p} ===" in ctx]
            out[f"{name}_files_kept"] = len(kept_files)
            out[f"{name}_truncated"] = pb.CONTEXT_TRUNCATED_TAG in ctx
            out[f"{name}_prompt_chars"] = len(pb.DEFAULT_SYSTEM_PROMPT) + len(head) + len(task) + len("\n</TASK>")
            out[f"{name}_gold_files_kept"] = sum(1 for g in gf if g in kept_files)
            out[f"{name}_all_gold_files_kept"] = bool(gf) and all(g in kept_files for g in gf)
            n_fn = 0
            for g in gfn:
                path, _ = g.split(":", 1)
                if path not in kept_files:
                    continue
                d = None
                m = Mapper(DATA / "cache" / "checkouts" / r["repo"].replace("/", "__") / r["base_commit"])
                defs = m.defs(path)
                if isinstance(defs, str):
                    continue
                d = next((x for x in defs if (x.qualname[:-9] if x.qualname.endswith(".__init__") else x.qualname) == g.split(":", 1)[1] or x.qualname == g.split(":", 1)[1]), None)
                if d is None:
                    continue
                ov = [c for c in kept[path] if (c.get("start_line") or 0) <= d.end and (c.get("end_line") or 0) >= d.start]
                if any(raw(c.get("chroma:document", "")).strip() and raw(c.get("chroma:document", "")).strip()[:4000] in ctx for c in ov):
                    n_fn += 1
            out[f"{name}_gold_functions_in_ctx"] = n_fn
        # stage-wise survival of gold functions (name-independent of order): in top-5 files / after the 10-chunk cap
        n_file = n_cap = 0
        m = Mapper(DATA / "cache" / "checkouts" / r["repo"].replace("/", "__") / r["base_commit"])
        for g in gfn:
            path, qual = g.split(":", 1)
            if path not in per_file:
                continue
            n_file += 1
            defs = m.defs(path)
            d = None if isinstance(defs, str) else next((x for x in defs if x.qualname == qual or (x.qualname.endswith(".__init__") and x.qualname[:-9] == qual)), None)
            if d and any((c.get("start_line") or 0) <= d.end and (c.get("end_line") or 0) >= d.start for c in kept[path]):
                n_cap += 1
        out.update({"n_gold_files": len(gf), "gold_files_in_top5_files": sum(1 for g in gf if g in per_file), "n_gold_functions": len(gfn),
                    "gold_functions_in_top5_files": n_file, "gold_functions_after_cap": n_cap,
                    "logged_prompt_chars": r["inject"]["prompt_chars"], "logged_hit_max": bool(r["inject"]["hit_max_context_tokens"]),
                    "actual_prompt_tokens": r["answer_call"]["prompt_eval_count"]})
        rows.append(out)

with open(HERE / "lc_rank_order_tasks.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

# ── report ────────────────────────────────────────────────────────────────────────────────────────────────────────
out = []
P = out.append
P("# LC context: file order and budget (offline rebuild, 83 tasks)\n")
P("Rebuilt with the cached backend's own `PromptBuilder` (`_truncate_file_blocks`, 24000-token budget, estimate = chars // 4). No model was run. Per-task rows: `lc_rank_order_tasks.csv`.\n")
P("## Calibration against the logged prompt sizes\n")
P("`inject.prompt_chars` (system + user message) is logged for every LC run; the rebuilt prompt is compared with it. 'Repeat' = one snippet per retrieved chunk (the code path); 'no repeat' = one snippet per file.\n")
P("| Variant | Tasks | Median rel. error | Mean abs. rel. error | Max abs. rel. error | Exact (< 0.5%) | Truncation flag agrees with log |")
P("|---|---|---|---|---|---|---|")
for rep in (True, False):
    C = [c for c in calib if c["repeat"] == rep]
    err = [(c["sim_chars"] - c["logged_chars"]) / c["logged_chars"] for c in C]
    P(f"| {'repeat (code path)' if rep else 'no repeat'} | {len(C)} | {S.median(err):+.4f} | {S.mean(abs(e) for e in err):.4f} | {max(abs(e) for e in err):.4f} | "
      f"{sum(abs(e) < 0.005 for e in err)} | {sum(c['sim_hit'] == c['logged_hit'] for c in C)} of {len(C)} |")
P("")
P("Check of the repeat question on tasks where a file has several retrieved chunks and the budget flag did not fire (untruncated, so prompt size is directly comparable):\n")
P("| Task | Items per file | Logged chars | Rebuilt (repeat) | Rebuilt (no repeat) |")
P("|---|---|---|---|---|")
cand = [c for c in calib if c["repeat"] and c["max_per_file"] >= 2 and not c["logged_hit"]]
cand = sorted(cand, key=lambda c: -c["max_per_file"])
shown = 0
for c in cand:
    nr = next(x for x in calib if x["task_id"] == c["task_id"] and not x["repeat"])
    row = next(x for x in rows if x["task_id"] == c["task_id"])
    P(f"| {c['task_id']} | {row['items_per_file'][:120]} | {c['logged_chars']} | {c['sim_chars']} | {nr['sim_chars']} |")
    shown += 1
    if shown == 5:
        break
P("")
# token estimate vs actual tokens
rt = [r["logged_prompt_chars"] / 4 / r["actual_prompt_tokens"] for r in rows]
P("Token estimate (chars // 4, the quantity the budget uses) against the model's own prompt token count (`prompt_eval_count`), LC runs: "
  f"estimate / actual median {S.median(rt):.3f}, range {min(rt):.3f} to {max(rt):.3f}; the budget therefore over-counts real tokens by about {100 * (S.median(rt) - 1):.0f}% at the median.\n")

P("## Alphabetical (current) vs retrieval-rank order\n")
P("| Dataset | Tasks | Budget flag: alphabetical | Budget flag: rank | Tasks whose set of kept files differs | All gold files kept: alpha | rank | Gold functions kept (sum over tasks): alpha | rank | Gold functions in top-5 files | ...after the 10-chunk cap |")
P("|---|---|---|---|---|---|---|---|---|---|---|")
for ds in L.DATASETS:
    R = [r for r in rows if r["dataset"] == ds]
    P(f"| {L.DS_LABEL[ds]} | {len(R)} | {sum(r['alpha_truncated'] for r in R)} | {sum(r['rank_truncated'] for r in R)} | "
      f"{sum(1 for r in R if r['alpha_files_kept'] != r['rank_files_kept'] or r['alpha_gold_files_kept'] != r['rank_gold_files_kept'])} | "
      f"{sum(r['alpha_all_gold_files_kept'] for r in R)} | {sum(r['rank_all_gold_files_kept'] for r in R)} | {sum(r['alpha_gold_functions_in_ctx'] for r in R)} | "
      f"{sum(r['rank_gold_functions_in_ctx'] for r in R)} | {sum(r['gold_functions_in_top5_files'] for r in R)} | {sum(r['gold_functions_after_cap'] for r in R)} |")
P("")
P("Tasks where the order changes which gold files or gold functions survive:\n")
for ds in L.DATASETS:
    ch = [r for r in rows if r["dataset"] == ds and (r["alpha_gold_files_kept"] != r["rank_gold_files_kept"] or r["alpha_gold_functions_in_ctx"] != r["rank_gold_functions_in_ctx"])]
    P(f"- {L.DS_LABEL[ds]} ({len(ch)}): " + (", ".join(f"{r['task_id']} (files {r['alpha_gold_files_kept']}→{r['rank_gold_files_kept']}, functions {r['alpha_gold_functions_in_ctx']}→{r['rank_gold_functions_in_ctx']})" for r in ch) or "none"))
P("")
(HERE / "lc_rank_order_report.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
