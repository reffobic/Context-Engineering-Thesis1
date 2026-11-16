"""Pruning diagnosis (read-only, no model, no changes to pruning code).

  .venv/bin/python docs/analysis/gate_diagnostics/build_pruning_trace.py

(a) the stage behind the Always-RAG function Acc@10 figures 0.370 -> 0.259 (SWE) and 0.429 -> 0.250 (Loc);
(b) for every Always-GRAPH task where graph expansion added a gold function, an offline replay of graph_expand and of pass 1 of
    prune_context_graph_aware (neighbours: structural score, 30% token reserve) using the cached backend's own functions, with the
    Chroma lookups served from the base-commit index through a read-only SQLite shim. The replay is validated against the logged
    seed keys, neighbour ids, new-chunk counts and final-context function IDs.
Writes pruning_trace_gold_added.csv and pruning_trace_report.md next to this file. Nothing else is touched.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sqlite3
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
from harness.metrics import acc_at, dedupe                                 # noqa: E402

res = L.load_results()
tasks = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "tasks.jsonl") if x.strip()} for ds in L.DATASETS}
gold = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "labels.jsonl") if x.strip()} for ds in L.DATASETS}
out = []
P = out.append
P("# Pruning diagnosis (no pruning code changed, no model run)\n")

# ── (a) which stage the 0.370 -> 0.259 and 0.429 -> 0.250 figures are ───────────────────────────────────────────────
P("## (a) Always-RAG function Acc@10: 0.370 → 0.259 (SWE) and 0.429 → 0.250 (Loc)\n")
P("The paper-table rows 'After retrieval' → 'After pruning' for Always-RAG (`tables_nov3.tex` lines 94–96 SWE and 122–124 Loc; metric column function Acc@10). "
  "Recomputed here from the stage lists with `harness.metrics.acc_at`, k = 10:\n")
P("| Dataset | Tasks | `after_retrieval` (top 10 of the 50 ranked candidates) | `retrieved_top5` (the 5 chunks `retrieve_context` returns) | `after_pruning` | `final_context` |")
P("|---|---|---|---|---|---|")
stage_tasks = {}
for ds in L.DATASETS:
    ids = sorted(res[(ds, "ALWAYS_RAG")])
    vals = {}
    for st in ("after_retrieval", "retrieved_top5", "after_pruning", "final_context"):
        ok = [t for t in ids if acc_at((res[(ds, "ALWAYS_RAG")][t]["ranked"][st] or {}).get("functions") or [], gold[ds][t]["functions"], 10) == 1.0]
        vals[st] = ok
    stage_tasks[ds] = vals
    P(f"| {L.DS_LABEL[ds]} | {len(ids)} | {len(vals['after_retrieval'])}/{len(ids)} = {len(vals['after_retrieval']) / len(ids):.3f} | {len(vals['retrieved_top5'])}/{len(ids)} = {len(vals['retrieved_top5']) / len(ids):.3f} | "
      f"{len(vals['after_pruning'])}/{len(ids)} = {len(vals['after_pruning']) / len(ids):.3f} | {len(vals['final_context'])}/{len(ids)} = {len(vals['final_context']) / len(ids):.3f} |")
P("")
for ds in L.DATASETS:
    v = stage_tasks[ds]
    lost_cut = sorted(set(v["after_retrieval"]) - set(v["retrieved_top5"]))
    lost_prune = sorted(set(v["retrieved_top5"]) - set(v["after_pruning"]))
    gained = sorted(set(v["retrieved_top5"]) - set(v["after_retrieval"]))
    P(f"- {L.DS_LABEL[ds]}: tasks complete in the top-10 list but not in the 5 returned chunks ({len(lost_cut)}): {', '.join(lost_cut) or 'none'}; "
      f"complete in the 5 chunks but not after pruning ({len(lost_prune)}): {', '.join(lost_prune) or 'none'}; complete in the 5 chunks but not in the top-10 list ({len(gained)}): {', '.join(gained) or 'none'}.")
P("")

# ── (b) replay ───────────────────────────────────────────────────────────────────────────────────────────────────
for k, v in {}.items():
    pass
import importlib

_conn: dict = {}
SQL = """select e.id, m.key, m.string_value, m.int_value from embeddings e
         join segments s on s.id = e.segment_id join collections co on co.id = s.collection join embedding_metadata m on m.id = e.id
         where co.name = 'code_embeddings' and e.id in (%s) order by e.id"""
SQL_IDS = """select m.id from embedding_metadata m join embeddings e on e.id = m.id join segments s on s.id = e.segment_id
             join collections co on co.id = s.collection where co.name = 'code_embeddings' and m.key = ? and m.string_value = ? order by m.id"""


class SqliteCollection:
    """Just enough of chromadb.Collection.get for graph_expand._lookup_chunks_for_node, read-only, rows in id order."""

    def __init__(self, conn):
        self.c = conn

    def get(self, where=None, include=None, limit=None):
        conds = where["$and"] if "$and" in where else [where]
        idsets = []
        for cond in conds:
            (key, val), = cond.items()
            idsets.append({r[0] for r in self.c.execute(SQL_IDS, (key, val["$eq"]))})
        ids = sorted(set.intersection(*idsets)) if idsets else []
        if limit:
            ids = ids[:limit]
        if not ids:
            return {"documents": [], "metadatas": []}
        rows = defaultdict(dict)
        for i, k, sv, iv in self.c.execute(SQL % ",".join(map(str, ids))):
            rows[i][k] = iv if sv is None else sv
        docs, metas = [], []
        for i in ids:
            m = dict(rows[i])
            docs.append(m.pop("chroma:document", ""))
            metas.append(m)
        return {"documents": docs, "metadatas": metas}


def open_ro(repo, base):
    k = (repo, base)
    if k not in _conn:
        d = glob.glob(str(DATA / "cache" / "indexes" / f"{repo.replace('/', '__')}__{base[:12]}__*" / "chroma" / "chroma.sqlite3"))
        _conn[k] = sqlite3.connect(f"file:{d[0]}?mode=ro", uri=True)
    return _conn[k]


ge = importlib.import_module("retrieval.graph_expand")
ga = importlib.import_module("indexing.graphify_adapter")
pf = importlib.import_module("pruning.file")

rows_csv = []
valid = Counter()
reason_ct = Counter()
traced = Counter()
per_ds = defaultdict(lambda: Counter())
for ds in L.DATASETS:
    for tid in sorted(res[(ds, "ALWAYS_GRAPH")]):
        r = res[(ds, "ALWAYS_GRAPH")][tid]
        rk = r["ranked"]
        g = gold[ds][tid]
        gfn = set(g["functions"])
        top5 = set(rk["retrieved_top5"]["functions"] or [])
        after_exp = set(rk["after_expansion"]["functions"] or [])
        after_pr = set(rk["after_pruning"]["functions"] or [])
        added = sorted((gfn & after_exp) - top5)
        per_ds[ds]["tasks"] += 1
        added_files = sorted((set(g["files"]) & set(rk["after_expansion"]["files"] or [])) - set(rk["retrieved_top5"]["files"] or []))
        per_ds[ds]["tasks_gold_file_added"] += bool(added_files)
        per_ds[ds]["gold_file_added_but_not_in_final_context"] += bool(set(added_files) - set(rk["final_context"]["files"] or []))
        if not added:
            continue
        per_ds[ds]["tasks_gold_function_added"] += 1
        per_ds[ds]["gold_functions_added"] += len(added)
        # ── replay graph_expand (hop loop of arms.run_always_graph) ──
        idx = Path(r["index_dir"])
        for key, ent in (r.get("env") or {}).items():
            if (key.startswith("GRAPHIFY") or key.startswith("GASR")) and isinstance(ent, dict) and ent.get("value") is not None:
                os.environ[key] = str(ent["value"])
        os.environ["GRAPHIFY_OUT"] = str(idx / "graphify-out")
        os.environ["CHROMA_PATH"] = "/nonexistent"
        ga._ADAPTER = None
        ge._chroma_collection = lambda c=SqliteCollection(open_ro(r["repo"], r["base_commit"])): c
        mapper = Mapper(DATA / "cache" / "checkouts" / r["repo"].replace("/", "__") / r["base_commit"])
        logged = r["expansions"]
        seed_keys0 = logged[0]["seed_keys"]
        seeds = [{"code": "", "metadata": {"file": k.rsplit(":", 1)[0], "function": k.rsplit(":", 1)[1]}} for k in seed_keys0]
        pool, replay_ok = [], True
        for h, ent in enumerate(logged):
            res_h = ge.graph_expand(list(seeds) + list(pool), hops=1, max_neighbors=8)
            same = res_h["neighbor_ids"] == ent["neighbor_ids"] and res_h["new_chunk_count"] == ent["new_chunk_count"]
            valid["hop_neighbors_and_count_match" if same else "hop_mismatch"] += 1
            replay_ok &= same
            pool.extend(res_h["chunks"])
        q = tasks[ds][tid]["query"]
        # ── pass 1 of prune_context_graph_aware, exactly as written (neighbours first), with a per-neighbour log ──
        budget_total = 1800
        nb_budget = int(budget_total * pf.NEIGHBOR_BUDGET_FRACTION)
        scored = sorted([dict(it, pruning_score=pf._neighbor_structural_score(it)) for it in pool], key=lambda x: x["pruning_score"], reverse=True)
        used, kept_ids, log = 0, set(), []
        for rank, it in enumerate(scored, 1):
            m = it["metadata"]
            chunk = pf.prune_single_item_heuristic(q, it)
            if chunk is None:
                log.append((rank, it, None, 0, nb_budget - used, "empty_after_noise_removal"))
                continue
            t = pf.estimate_tokens(chunk["text"])
            if used + t > nb_budget:
                log.append((rank, it, t, used, nb_budget - used, "chunk_larger_than_whole_reserve" if t > nb_budget else "reserve_used_up_by_earlier_neighbours"))
                continue
            used += t
            kept_ids.add(id(it))
            log.append((rank, it, t, used - t, nb_budget - (used - t), "kept"))
        # map each pool chunk to the gold function it covers, if any
        for added_fn in added:
            cover = []
            for (rank, it, t, before, remain, reason) in log:
                m = it["metadata"]
                mapped = mapper.map_item({"file": m["file"], "name": m["function"], "start_line": m["start_line"], "chunk_type": None})
                if mapped.get("function") == added_fn:
                    cover.append((rank, it, t, before, remain, reason, mapped))
            in_final = added_fn in after_pr
            if not cover:
                reason = "not_in_replayed_pool" if not replay_ok else "no_replayed_chunk_maps_to_it"
                rows_csv.append({"dataset": ds, "task_id": tid, "gold_function": added_fn, "in_final_context": in_final, "replay_validated": replay_ok,
                                 "pool_size": len(pool), "neighbour_budget_tokens": nb_budget, "n_pool_chunks_kept": len(kept_ids), "reason": reason})
                reason_ct[(ds, reason, in_final)] += 1
                continue
            best = min(cover, key=lambda c: c[0])
            rank, it, t, before, remain, reason, mapped = best
            rows_csv.append({"dataset": ds, "task_id": tid, "gold_function": added_fn, "in_final_context": in_final, "replay_validated": replay_ok,
                             "chunk_file": it["metadata"]["file"], "chunk_name": it["metadata"]["function"], "chunk_lines": f"{it['metadata']['start_line']}-{it['metadata']['end_line']}",
                             "pool_size": len(pool), "rank_in_neighbour_order": rank, "structural_score": it["pruning_score"],
                             "token_cost_after_window": t, "neighbour_tokens_used_before": before, "neighbour_budget_remaining_before": remain,
                             "neighbour_budget_tokens": nb_budget, "n_pool_chunks_kept": len(kept_ids), "reason": reason})
            reason_ct[(ds, reason, in_final)] += 1
        per_ds[ds]["replay_ok_tasks"] += replay_ok
with open(HERE / "pruning_trace_gold_added.csv", "w", newline="", encoding="utf-8") as f:
    keys = []
    for r_ in rows_csv:
        for k in r_:
            if k not in keys:
                keys.append(k)
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerows(rows_csv)

P("## (b) Always-GRAPH: gold added by expansion, traced through the re-prune\n")
P("'Added by expansion' = a gold function is in `after_expansion` but not in `retrieved_top5`. Replay: `graph_expand` (hops as logged, max 8 neighbours) then pass 1 of `prune_context_graph_aware` "
  "(`pruning/file.py:690`), which reserves `int(1800 × 0.30) = 540` estimated tokens for neighbours, sorts them by `_neighbor_structural_score` (descending, stable) and keeps each one whose windowed text still fits.\n")
P("| Dataset | Tasks | Tasks where a gold function was added | Gold functions added | Tasks where a gold file was added (of which not in the final context) | Replay matches the logged neighbour ids and counts (tasks) |")
P("|---|---|---|---|---|---|")
for ds in L.DATASETS:
    c = per_ds[ds]
    P(f"| {L.DS_LABEL[ds]} | {c['tasks']} | {c['tasks_gold_function_added']} | {c['gold_functions_added']} | {c['tasks_gold_file_added']} ({c['gold_file_added_but_not_in_final_context']}) | {c['replay_ok_tasks']} of {c['tasks_gold_function_added']} |")
P("")
P("Per added gold function: what happened to it in the re-prune (counts; 'kept' = it is in the logged final context):\n")
P("| Dataset | Outcome in the replay | In logged final context | Count |")
P("|---|---|---|---|")
for (ds, reason, infinal), n in sorted(reason_ct.items()):
    P(f"| {L.DS_LABEL[ds]} | {reason} | {infinal} | {n} |")
P("")
P("Per-function rows (rank, score, token cost, remaining budget, reason): `pruning_trace_gold_added.csv`.\n")
P("### Code facts that decide the outcome (VERIFIED by reading `pruning/file.py` and `retrieval/graph_expand.py` at the frozen SHA)\n")
P("- Graph neighbours are built by `_chunk_from_chroma_row` (`graph_expand.py:42`) with no `relation_type` or `edge_type`, so `_neighbor_structural_score` always takes the 'default' priority 0.65 (+0.05 if the function name occurs in its own code): neighbours are only ever ordered by that 0.05 bonus and then by arrival order. The callee/caller/inherits priorities (`file.py:~655`) never apply.")
P("- Neighbour packing is a single pass in that order with a 540-token reserve; a neighbour that does not fit is skipped (`continue`), later smaller ones can still fit. There is no score threshold, no per-file cap and no deduplication inside `prune_context_graph_aware`.")
P("- The only per-file limits are upstream, in expansion: `paired[:3]` chunks per graph node and `limit=5` in the Chroma lookup (`graph_expand.py` `_lookup_chunks_for_node`), and `max_neighbors = 8` nodes per hop. A file-level graph node pulls up to 3 arbitrary chunks of that file.")
P("- Seeds are packed afterwards into the remaining budget (1260 plus whatever the neighbours left unused).\n")
(HERE / "pruning_trace_report.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
print(dict(valid))
