"""Final context handed to the answer model by each fixed tier (Always-RAG, Always-GRAPH, Always-LC), 83 tasks (read-only).

  .venv/bin/python docs/analysis/gate_diagnostics/build_tier_contexts.py

The answer-call request text is NOT stored in the results rows (ollama_calls[].request is null for role "answer"), so the context
is rebuilt from what is logged: ranked.final_context (chunk/function IDs), inject.* (prompt size, n_chunks, token-budget flag) and
answer_call.* (true prompt tokens, truncation flag). For LC the injected text is the stitched parent file of each retrieved chunk
(retrieval_tool.reconstruct_file_context); the kept chunks are read from the base-commit index with a read-only SQLite connection.
Writes tier_contexts.csv and tier_contexts_summary.md next to this file; nothing else is touched.
"""
from __future__ import annotations

import csv
import glob
import json
import sqlite3
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
from harness.locmap import Mapper                                          # noqa: E402

DATA = ROOT / "eval_harness" / "data"
CAP = 10                                                                    # retrieval_tool.MAX_CONTEXT_CHUNKS_PER_FILE
TIERS = [("RAG", "ALWAYS_RAG"), ("GRAPH", "ALWAYS_GRAPH"), ("LC", "ALWAYS_LC")]
res = L.load_results()
gold = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "labels.jsonl") if x.strip()} for ds in L.DATASETS}

_conn: dict = {}


def conn_for(repo: str, base: str):
    key = (repo, base)
    if key not in _conn:
        d = glob.glob(str(DATA / "cache" / "indexes" / f"{repo.replace('/', '__')}__{base[:12]}__*" / "chroma" / "chroma.sqlite3"))
        _conn[key] = sqlite3.connect(f"file:{d[0]}?mode=ro", uri=True) if d else None
    return _conn[key]


def file_chunks(c, path: str) -> list[dict]:
    """All chunks of `path` in the code collection, as reconstruct_file_context sees them (sorted by start_line, stable)."""
    q = """select e.id, m.key, m.string_value, m.int_value from embeddings e
           join segments s on s.id = e.segment_id join collections co on co.id = s.collection
           join embedding_metadata m on m.id = e.id
           where co.name = 'code_embeddings' and e.id in
             (select m2.id from embedding_metadata m2 join embeddings e2 on e2.id = m2.id join segments s2 on s2.id = e2.segment_id
              join collections c2 on c2.id = s2.collection where c2.name = 'code_embeddings' and m2.key = 'file_path' and m2.string_value = ?)
           and m.key in ('start_line', 'end_line', 'name', 'chunk_type') order by e.id"""
    rows: dict = defaultdict(dict)
    for i, k, sv, iv in c.execute(q, (path,)):
        rows[i][k] = iv if sv is None else sv
    out = [{"id": i, **v} for i, v in rows.items()]
    return sorted(out, key=lambda x: x.get("start_line") or 0)


def def_range(mapper: Mapper, gid: str):
    path, qual = gid.split(":", 1)
    defs = mapper.defs(path)
    if isinstance(defs, str):
        return None
    for d in defs:
        q = d.qualname[: -len(".__init__")] if d.qualname.endswith(".__init__") else d.qualname
        if q == qual or d.qualname == qual:
            return d
    return None


def chunk_ids(fc: dict) -> list[str]:
    ids = list(fc.get("functions") or [])
    ids += [f"{u['file']}:{u['name']}@{u['start_line']}" for u in fc.get("unmapped") or []]
    return ids


rows = []
for ds in L.DATASETS:
    for tid in sorted(res[(ds, "ALWAYS_RAG")]):
        g = gold[ds][tid]
        gf, gfn = set(g["files"]), list(g["functions"])
        answers = {}
        for rt, arm in TIERS:
            r = res[(ds, arm)][tid]
            fc = (r.get("ranked") or {}).get("final_context") or {}
            inj, ac = r["inject"], r["answer_call"]
            ctx_files = list(fc.get("files") or [])
            rec = {"dataset": ds, "task_id": tid, "tier": rt, "route": r["route"], "status": r["status"], "failed": bool(r.get("counted_as_failed")),
                   "n_chunks": inj["n_chunks"], "context_files": ";".join(ctx_files), "n_context_files": len(ctx_files),
                   "prompt_chars": inj["prompt_chars"], "est_tokens_div4": inj["est_tokens_div4"], "prompt_tokens_actual": ac["prompt_eval_count"],
                   "max_context_tokens": inj["max_context_tokens"], "hit_max_context_tokens": inj["hit_max_context_tokens"],
                   "answer_truncation_suspected": ac["truncation_suspected"], "answer_done_reason": ac["done_reason"]}
            _, lists = answer_lists(r.get("answer"))
            answers[rt] = lists
            if rt in ("RAG", "GRAPH"):
                rec["chunk_ids"] = ";".join(chunk_ids(fc))
                rec["context_has_all_gold_files"] = bool(gf) and gf <= set(ctx_files)
                rec["n_gold_functions_in_context"] = sum(1 for x in gfn if x in set(fc.get("functions") or []))
                rec["context_has_all_gold_functions"] = bool(gfn) and rec["n_gold_functions_in_context"] == len(gfn)
            else:
                c = conn_for(r["repo"], r["base_commit"])
                mapper = Mapper(DATA / "cache" / "checkouts" / r["repo"].replace("/", "__") / r["base_commit"])
                kept_ids, cap_hit, n_kept_total = [], False, 0
                kept_by_file = {}
                for p in ctx_files:
                    ch = file_chunks(c, p) if c else []
                    cap_hit |= len(ch) > CAP
                    kept = ch[:CAP]
                    kept_by_file[p] = kept
                    n_kept_total += len(kept)
                    kept_ids += [f"{p}:{k.get('name')}@{k.get('start_line')}" for k in kept]
                rec["chunk_ids"] = ";".join(kept_ids)
                rec["lc_cap_truncated_any_file"] = cap_hit
                rec["lc_chunks_total_in_files"] = sum(len(file_chunks(c, p)) for p in ctx_files) if c else None
                rec["lc_chunks_kept"] = n_kept_total
                rec["context_has_all_gold_files"] = bool(gf) and gf <= set(ctx_files)
                n_overlap = n_full = n_uncapped = 0
                for x in gfn:
                    d = def_range(mapper, x)
                    kept = kept_by_file.get(x.split(":", 1)[0], [])
                    if d is None:
                        continue
                    allch = file_chunks(c, x.split(":", 1)[0]) if x.split(":", 1)[0] in kept_by_file else []
                    hit = lambda k: (k.get("start_line") or 0) <= d.end and (k.get("end_line") or 0) >= d.start
                    n_overlap += any(hit(k) for k in kept)
                    n_uncapped += any(hit(k) for k in allch)
                    n_full += any((k.get("start_line") or 0) <= d.start and (k.get("end_line") or 0) >= d.end for k in kept)
                rec["n_gold_functions_in_context"] = n_overlap
                rec["n_gold_functions_fully_in_context"] = n_full
                rec["n_gold_functions_in_files_uncapped"] = n_uncapped
                rec["context_has_all_gold_functions"] = bool(gfn) and n_overlap == len(gfn)
                rec["lc_lost_to_chunk_cap"] = bool(gfn) and n_uncapped == len(gfn) and n_overlap < len(gfn)
                # items per file: the same stitched parent text is added once per retrieved chunk of that file (5 items in total)
                per_file = Counter(u["file"] for u in (fc.get("unmapped") or []))
                rec["lc_items_per_file"] = ";".join(f"{p}={n}" for p, n in per_file.items())
                first = sorted(ctx_files)[0] if ctx_files else None                      # prompt_builder keeps the alphabetically first file blocks first
                rec["lc_gold_file_at_risk_of_budget_drop"] = bool(inj["hit_max_context_tokens"]) and any(x != first for x in gf if x in ctx_files)
            rec["answer_files"] = ";".join(lists["files"])
            rec["answer_functions"] = ";".join(lists["functions"])
            gold_named = [x for x in lists["files"] if x in gf]
            rec["answer_names_gold_file"] = bool(gold_named)
            rec["answer_gold_files_not_in_own_context"] = ";".join(x for x in gold_named if x not in set(ctx_files))
            rows.append(rec)
        rows[-3]["_pair"] = rows[-2]["_pair"] = rows[-1]["_pair"] = (answers["RAG"], answers["GRAPH"], answers["LC"])

keys = []
for r in rows:
    for k in r:
        if k != "_pair" and k not in keys:
            keys.append(k)
with open(HERE / "tier_contexts.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in keys})

# ── summary ──────────────────────────────────────────────────────────────────────────────────────────────────────
by = {(r["dataset"], r["task_id"], r["tier"]): r for r in rows}
out = []
P = out.append
P("# Tier contexts: RAG vs GRAPH vs LC (83 tasks)\n")
P("Generated by `build_tier_contexts.py`; per-task, per-tier rows in `tier_contexts.csv` (249 rows).\n")
P("**What is stored.** The answer-call request text is not in the logs (`ollama_calls[].request` is null for the answer call), so the exact injected text cannot be read back; "
  "chunk IDs come from `ranked.final_context`, sizes from `inject.*` and `answer_call.*`. For LC the chunk IDs are the kept chunks of the stitched parent files, read from the base-commit index.\n")
P("## Token counts and truncation\n")
P("| Dataset | Tier | Tasks | Median est. tokens (chars/4) | Max est. tokens | Median actual prompt tokens | Max actual | hit_max_context_tokens | answer truncation_suspected | done_reason=length |")
P("|---|---|---|---|---|---|---|---|---|---|")
import statistics as S
for ds in L.DATASETS:
    for rt, _ in TIERS:
        R = [by[(ds, t, rt)] for t in sorted(res[(ds, 'ALWAYS_RAG')])]
        P(f"| {L.DS_LABEL[ds]} | {rt} | {len(R)} | {S.median(r['est_tokens_div4'] for r in R):.0f} | {max(r['est_tokens_div4'] for r in R)} | "
          f"{S.median(r['prompt_tokens_actual'] for r in R):.0f} | {max(r['prompt_tokens_actual'] for r in R)} | {sum(bool(r['hit_max_context_tokens']) for r in R)} | "
          f"{sum(bool(r['answer_truncation_suspected']) for r in R)} | {sum(r['answer_done_reason'] == 'length' for r in R)} |")
P("")
P("## RAG vs GRAPH chunk sets\n")
P("| Dataset | Tasks | Identical chunk-ID sets | Differ | Identical file sets (chunk sets may differ) |")
P("|---|---|---|---|---|")
for ds in L.DATASETS:
    ids = sorted(res[(ds, 'ALWAYS_RAG')])
    same = sum(set(by[(ds, t, 'RAG')]["chunk_ids"].split(";")) == set(by[(ds, t, 'GRAPH')]["chunk_ids"].split(";")) for t in ids)
    samef = sum(set(by[(ds, t, 'RAG')]["context_files"].split(";")) == set(by[(ds, t, 'GRAPH')]["context_files"].split(";")) for t in ids)
    P(f"| {L.DS_LABEL[ds]} | {len(ids)} | {same} | {len(ids) - same} | {samef} |")
P("")
P("## LC context\n")
P("LC parent files come from the top-5 retrieved chunks (not the 50 ranked candidates); see the code citations in the reply. 'In context' for a gold function = a kept chunk's line range overlaps the function's definition at the base commit.\n")
P("| Dataset | Tasks | All gold files in LC context | All gold functions in LC context | ...of which fully inside a kept chunk | Tasks where a context file has >10 chunks | Gold functions lost to the 10-chunk cap | Budget flag (`<CONTEXT_TRUNCATED>`) | ...with a gold file at risk of the alphabetical file drop |")
P("|---|---|---|---|---|---|---|---|---|")
for ds in L.DATASETS:
    R = [by[(ds, t, 'LC')] for t in sorted(res[(ds, 'ALWAYS_RAG')])]
    full = sum(1 for r in R if gold[ds][r['task_id']]['functions'] and r['n_gold_functions_fully_in_context'] == len(gold[ds][r['task_id']]['functions']))
    P(f"| {L.DS_LABEL[ds]} | {len(R)} | {sum(bool(r['context_has_all_gold_files']) for r in R)} | {sum(bool(r['context_has_all_gold_functions']) for r in R)} | {full} | "
      f"{sum(bool(r['lc_cap_truncated_any_file']) for r in R)} | {sum(bool(r['lc_lost_to_chunk_cap']) for r in R)} | {sum(bool(r['hit_max_context_tokens']) for r in R)} | "
      f"{sum(bool(r['lc_gold_file_at_risk_of_budget_drop']) for r in R)} |")
P("")
P("## LOCATIONS lists identical across the three tiers\n")
P("| Dataset | Tasks | Identical file lists (ordered) | Identical function lists (ordered) | Identical file AND function lists (ordered) | Same sets (order ignored) |")
P("|---|---|---|---|---|---|")
for ds in L.DATASETS:
    ids = sorted(res[(ds, 'ALWAYS_RAG')])
    fo = fu = both = sets = 0
    for t in ids:
        a, b, c = (by[(ds, t, rt)]["_pair"][i] for i, (rt, _) in enumerate(TIERS))
        pr = by[(ds, t, "RAG")]["_pair"]
        F = [x["files"] for x in pr]
        Fn = [x["functions"] for x in pr]
        fo += F[0] == F[1] == F[2]
        fu += Fn[0] == Fn[1] == Fn[2]
        both += F[0] == F[1] == F[2] and Fn[0] == Fn[1] == Fn[2]
        sets += set(F[0]) == set(F[1]) == set(F[2]) and set(Fn[0]) == set(Fn[1]) == set(Fn[2])
    P(f"| {L.DS_LABEL[ds]} | {len(ids)} | {fo} | {fu} | {both} | {sets} |")
P("")
P("## Answer names a gold file that is not in that tier's own context\n")
P("| Dataset | Tier | Tasks where the answer names a gold file | ... of which a named gold file is not in the tier's context |")
P("|---|---|---|---|")
listing = {}
for ds in L.DATASETS:
    for rt, _ in TIERS:
        R = [by[(ds, t, rt)] for t in sorted(res[(ds, 'ALWAYS_RAG')])]
        bad = [r for r in R if r["answer_gold_files_not_in_own_context"]]
        listing[(ds, rt)] = [r["task_id"] for r in bad]
        P(f"| {L.DS_LABEL[ds]} | {rt} | {sum(bool(r['answer_names_gold_file']) for r in R)} | {len(bad)} |")
P("")
for (ds, rt), ids in listing.items():
    P(f"- {L.DS_LABEL[ds]} {rt} ({len(ids)}): " + (", ".join(ids) or "none"))
P("")
(HERE / "tier_contexts_summary.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
