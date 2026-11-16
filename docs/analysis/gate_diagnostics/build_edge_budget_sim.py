"""Edge-type scoring and token-budget simulation for the Always-GRAPH neighbour pass (read-only, no model, no pruning code changed).

  .venv/bin/python docs/analysis/gate_diagnostics/build_edge_budget_sim.py

Replays, for all 83 Always-GRAPH tasks, graph_expand (hops as logged) with the relation label of every discovering edge kept, then pass 1 of
prune_context_graph_aware (pruning/file.py:690) under different neighbour scores and reserve sizes. Uses the cached backend's own functions
(estimate_tokens, prune_single_item_heuristic, _neighbor_structural_score) with Chroma served read-only from the base-commit index.
Validation: the baseline replay must reproduce the logged final-context functions that are not seeds.
Also measures gold function sizes against the 540 / 1260 / 1800 token budgets and how often the flat prune drops chunks (Always-RAG).
Writes edge_budget_report.md, edge_budget_variants.csv and edge_budget_gold_chunks.csv next to this file.
"""
from __future__ import annotations

import csv
import glob
import importlib
import json
import os
import sqlite3
import statistics as st
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

ge = importlib.import_module("retrieval.graph_expand")
ga = importlib.import_module("indexing.graphify_adapter")
pf = importlib.import_module("pruning.file")

res = L.load_results()
tasks = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "tasks.jsonl") if x.strip()} for ds in L.DATASETS}
gold = {ds: {json.loads(x)["instance_id"]: json.loads(x) for x in open(DATA / "datasets" / ds / "labels.jsonl") if x.strip()} for ds in L.DATASETS}

SQL = """select e.id, m.key, m.string_value, m.int_value from embeddings e
         join segments s on s.id = e.segment_id join collections co on co.id = s.collection join embedding_metadata m on m.id = e.id
         where co.name = 'code_embeddings' and e.id in (%s) order by e.id"""
SQL_IDS = """select m.id from embedding_metadata m join embeddings e on e.id = m.id join segments s on s.id = e.segment_id
             join collections co on co.id = s.collection where co.name = 'code_embeddings' and m.key = ? and m.string_value = ? order by m.id"""
_conn: dict = {}


class SqliteCollection:
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


# ── relation handling ─────────────────────────────────────────────────────────────────────────────────────────────
def canon(rel: str) -> str:
    """My mapping of a stored graph label onto the keys of pruning.file._RELATION_PRIORITY (not in the backend)."""
    rev = rel.startswith("rev_")
    b = rel[4:] if rev else rel
    if b in ("calls", "call", "indirect_call"):
        return "caller" if rev else "callee"
    if b in ("imports", "imports_from", "dynamic_import", "import"):
        return "default" if rev else "import"
    if b in ("inherits", "inherits_from", "inherit", "extends", "mixes_in", "implements"):
        return "default" if rev else "inherits"
    if b in ("contains", "contain", "defines", "contained_in", "defined_in"):
        return "contains"
    return "default"


def rel_map(adapter, keys):
    """Mirror of CodeGraphAdapter.neighbors(hops=1) that also records every (relation, from_node) edge reaching a neighbour."""
    start = [adapter._resolve(s) or s for s in keys]
    start = [s for s in start if s]
    sset = set(start)
    seen = set(start)
    found, rels = [], defaultdict(list)
    for node in start:
        for nbr, rel, conf in adapter.adj.get(node, []):
            if conf not in ("EXTRACTED", "") and not str(conf).upper().startswith("EXTRACT"):
                continue
            if nbr in sset:
                continue
            rels[nbr].append((rel, node))
            if nbr in seen:
                continue
            seen.add(nbr)
            found.append(nbr)
    return found, rels


def pack(order, cost, budget):
    used, kept, log = 0, [], {}
    for i in order:
        t = cost[i]
        if t is None:
            log[i] = ("empty", None)
            continue
        if used + t > budget:
            log[i] = ("too_big_for_whole_reserve" if t > budget else "reserve_used_up", used)
            continue
        kept.append(i)
        log[i] = ("kept", used)
        used += t
    return kept, used, log


def score_with(items, mode):
    out = []
    for it in items:
        it2 = dict(it)
        if mode == "V1_literal_first":
            it2["relation_type"] = it["rel_first"]
        elif mode == "V2_mapped_first":
            it2["relation_type"] = canon(it["rel_first"])
        elif mode == "V3_mapped_best":
            it2["relation_type"] = max((canon(r) for r, _ in it["rel_all"]), key=lambda k: pf._RELATION_PRIORITY.get(k, 0.65)) if it["rel_all"] else "default"
        if mode == "V4_posthoc_in_sample":
            rels = {r for r, _ in it["rel_all"]}
            pri = max([POSTHOC.get(r, 0.5) for r in rels] or [0.5])
            fn = (it.get("metadata") or {}).get("function") or ""
            out.append(round(min(pri + (0.05 if fn and fn in (it.get("code") or "") else 0.0), 1.0), 4))
            continue
        out.append(pf._neighbor_structural_score(it2))
    return out


# V4 is tuned by eye on the discovering-edge table of these same 83 tasks (section 1): in-sample, an upper-bound illustration only.
POSTHOC = {"rev_contains": 1.0, "contains": 1.0, "calls": 0.9, "indirect_call": 0.9, "rev_calls": 0.8, "inherits": 0.7, "rev_inherits": 0.6,
           "imports": 0.5, "imports_from": 0.5, "rev_imports": 0.3}
MODES = ["V0_current_all_default", "V1_literal_first", "V2_mapped_first", "V3_mapped_best", "V4_posthoc_in_sample"]
RESERVES = [540, 720, 900, 1080, 1440, 1800]
rows_gold, per_task = [], []
valid = Counter()
rel_first_ct, rel_first_ct_gold, rel_first_ct_kept0 = Counter(), Counter(), Counter()
cost_all, cost_gold_cov = [], []

for ds in L.DATASETS:
    for tid in sorted(res[(ds, "ALWAYS_GRAPH")]):
        r = res[(ds, "ALWAYS_GRAPH")][tid]
        rk = r["ranked"]
        g = gold[ds][tid]
        gfn, gfl = set(g["functions"]), set(g["files"])
        idx = Path(r["index_dir"])
        for key, ent in (r.get("env") or {}).items():
            if (key.startswith("GRAPHIFY") or key.startswith("GASR")) and isinstance(ent, dict) and ent.get("value") is not None:
                os.environ[key] = str(ent["value"])
        os.environ["GRAPHIFY_OUT"] = str(idx / "graphify-out")
        os.environ["CHROMA_PATH"] = "/nonexistent"
        ga._ADAPTER = None
        coll = SqliteCollection(open_ro(r["repo"], r["base_commit"]))
        ge._chroma_collection = lambda c=coll: c
        adapter = ga.get_adapter()
        mapper = Mapper(DATA / "cache" / "checkouts" / r["repo"].replace("/", "__") / r["base_commit"])
        logged = r["expansions"]
        seed_keys0 = logged[0]["seed_keys"]
        seeds = [{"code": "", "metadata": {"file": k.rsplit(":", 1)[0], "function": k.rsplit(":", 1)[1]}} for k in seed_keys0]
        seed_start = {adapter._resolve(k) or k for k in seed_keys0}
        pool, ok = [], True
        for h, ent in enumerate(logged):
            allc = list(seeds) + list(pool)
            keys = [k for k in (adapter.seed_key_from_chunk(c) for c in allc) if k]
            found, rels = rel_map(adapter, keys)
            res_h = ge.graph_expand(allc, hops=1, max_neighbors=8)
            same_ids = res_h["neighbor_ids"] == ent["neighbor_ids"] and res_h["new_chunk_count"] == ent["new_chunk_count"]
            same_rel = found[:8] == res_h["neighbor_ids"]
            valid["hop_logged_match" if same_ids else "hop_logged_MISMATCH"] += 1
            valid["hop_relation_bfs_match" if same_rel else "hop_relation_bfs_MISMATCH"] += 1
            ok &= same_ids and same_rel
            for ch in res_h["chunks"]:
                nid = ch["metadata"].get("graph_node_id")
                ra = rels.get(nid, [])
                ch["rel_first"] = ra[0][0] if ra else "default"
                ch["rel_all"] = ra
                ch["hop"] = h + 1
                ch["from_seed"] = bool(ra) and ra[0][1] in seed_start
            pool.extend(res_h["chunks"])
        q = tasks[ds][tid]["query"]
        cost, mapped = [], []
        for it in pool:
            c = pf.prune_single_item_heuristic(q, it)
            cost.append(None if c is None else pf.estimate_tokens(c["text"]))
            m = it["metadata"]
            mp = mapper.map_item({"file": m["file"], "name": m["function"], "start_line": m["start_line"], "chunk_type": None})
            mapped.append(mp.get("function"))
        isgold_fn = [(mapped[i] in gfn) for i in range(len(pool))]
        isgold_fl = [(pool[i]["metadata"]["file"] in gfl) for i in range(len(pool))]
        top5 = set(rk["retrieved_top5"]["functions"] or [])
        fin = list(rk["final_context"]["functions"] or [])
        finfiles = set(rk["final_context"]["files"] or [])
        for i, it in enumerate(pool):
            rel_first_ct[it["rel_first"]] += 1
            if cost[i] is not None:
                cost_all.append(cost[i])
            if isgold_fn[i] or isgold_fl[i]:
                rel_first_ct_gold[it["rel_first"]] += 1
                if cost[i] is not None:
                    cost_gold_cov.append(cost[i])
        # baseline order and validation
        sc = {m_: score_with(pool, m_) for m_ in MODES}
        order = {m_: sorted(range(len(pool)), key=lambda i, s=sc[m_]: s[i], reverse=True) for m_ in MODES}
        kept0, used0, log0 = pack(order[MODES[0]], cost, 540)
        kf0 = {mapped[i] for i in kept0 if mapped[i]}
        a = (kf0 - top5) <= set(fin)
        b = (set(fin) - top5) <= kf0
        valid["task_baseline_reproduces_logged_final"] += bool(a and b and ok)
        valid["task_total"] += 1
        # minimal reserve that admits each gold-covering chunk, under V0 and V3
        for i in range(len(pool)):
            if not (isgold_fn[i] or isgold_fl[i]) or cost[i] is None:
                continue
            row = {"dataset": ds, "task_id": tid, "chunk": f"{pool[i]['metadata']['file']}:{pool[i]['metadata']['function']}",
                   "mapped_function": mapped[i] or "", "gold_function": isgold_fn[i], "gold_file": isgold_fl[i],
                   "rel_first": pool[i]["rel_first"], "rel_all": "|".join(sorted({x for x, _ in pool[i]["rel_all"]})), "hop": pool[i]["hop"],
                   "from_original_seed": pool[i]["from_seed"], "token_cost": cost[i], "pool_size": len(pool),
                   "kept_at_540_V0": i in kept0, "reason_V0_540": log0[i][0],
                   "mapped_function_already_in_retrieved_top5": (mapped[i] in top5), "mapped_function_in_logged_final": (mapped[i] in set(fin))}
            for m_ in (MODES[0], MODES[3]):
                need = None
                for R in range(20, 6001, 20):
                    kk, _, _ = pack(order[m_], cost, R)
                    if i in kk:
                        need = R
                        break
                row[f"min_reserve_to_keep_{m_[:2]}"] = need
                row[f"rank_{m_[:2]}"] = order[m_].index(i) + 1
            rows_gold.append(row)
        top5f = set(rk["retrieved_top5"]["files"] or [])
        nb0_funcs = kf0 - top5
        nb0_files = {pool[i]["metadata"]["file"] for i in kept0} - top5f
        base_final_funcs = set(fin) - nb0_funcs
        base_final_files = finfiles - nb0_files
        # variants

        for m_ in MODES:
            for R in RESERVES:
                kept, used, _ = pack(order[m_], cost, R)
                kf = {mapped[i] for i in kept if mapped[i]}
                kfl = {pool[i]["metadata"]["file"] for i in kept}
                funcs = base_final_funcs | kf
                files = base_final_files | kfl
                hits = len(funcs & gfn)
                fh = len(files & gfl)
                gtok = sum(cost[i] for i in kept if isgold_fn[i] or isgold_fl[i])
                per_task.append({"dataset": ds, "task_id": tid, "mode": m_, "reserve": R, "n_pool": len(pool), "n_kept": len(kept), "tokens_used": used,
                                 "gold_tokens": gtok, "func_hits": hits, "func_need": min(len(gfn), 10), "func_complete": hits >= min(len(gfn), 10),
                                 "file_hits": fh, "file_need": min(len(gfl), 5), "file_complete": fh >= min(len(gfl), 5),
                                 "kept_gold_fn": sum(1 for i in kept if isgold_fn[i]), "kept_set_changed_vs_baseline": set(kept) != set(kept0),
                                 "kept_rel": "|".join(sorted(Counter(pool[i]["rel_first"] for i in kept).elements()))})
        for i in kept0:
            rel_first_ct_kept0[pool[i]["rel_first"]] += 1

with open(HERE / "edge_budget_gold_chunks.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows_gold[0]))
    w.writeheader()
    w.writerows(rows_gold)

# ── aggregate ────────────────────────────────────────────────────────────────────────────────────────────────────
agg_rows = []
for ds in L.DATASETS:
    for m_ in MODES:
        for R in RESERVES:
            T = [x for x in per_task if x["dataset"] == ds and x["mode"] == m_ and x["reserve"] == R]
            agg_rows.append({"dataset": ds, "mode": m_, "reserve_tokens": R, "tasks": len(T),
                             "tasks_kept_set_changed_vs_baseline": sum(x["kept_set_changed_vs_baseline"] for x in T),
                             "mean_neighbours_kept": round(st.mean(x["n_kept"] for x in T), 2), "mean_neighbour_tokens": round(st.mean(x["tokens_used"] for x in T), 1),
                             "tasks_function_complete": sum(x["func_complete"] for x in T), "sum_gold_functions_found": sum(x["func_hits"] for x in T),
                             "tasks_file_complete": sum(x["file_complete"] for x in T), "sum_gold_files_found": sum(x["file_hits"] for x in T),
                             "gold_token_share_of_neighbour_tokens": round(sum(x["gold_tokens"] for x in T) / max(1, sum(x["tokens_used"] for x in T)), 3)})
with open(HERE / "edge_budget_variants.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(agg_rows[0]))
    w.writeheader()
    w.writerows(agg_rows)

# ── gold function sizes vs budgets (from the base-commit source) ─────────────────────────────────────────────────
size_rows = []
for ds in L.DATASETS:
    for tid in sorted(res[(ds, "ALWAYS_RAG")]):
        r = res[(ds, "ALWAYS_RAG")][tid]
        mapper = Mapper(DATA / "cache" / "checkouts" / r["repo"].replace("/", "__") / r["base_commit"])
        toks, miss = [], 0
        for fid in gold[ds][tid]["functions"]:
            path, qual = fid.split(":", 1)
            d = mapper.defs(path)
            hit = None if isinstance(d, str) else next((x for x in d if x.kind == "function" and x.qualname == qual), None)
            if hit is None:
                miss += 1
                continue
            lines = (DATA / "cache" / "checkouts" / r["repo"].replace("/", "__") / r["base_commit"] / path).read_text(encoding="utf-8", errors="replace").splitlines()
            toks.append(len("\n".join(lines[hit.start - 1:hit.end])) // 4)
        rk = r["ranked"]
        size_rows.append({"dataset": ds, "task_id": tid, "n_gold_fn": len(gold[ds][tid]["functions"]), "sized": len(toks), "missing": miss,
                          "sum_tokens": sum(toks), "max_tokens": max(toks) if toks else 0,
                          "rag_top5_items": rk["retrieved_top5"]["n_items"], "rag_pruned_items": rk["after_pruning"]["n_items"]})


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else 0


out = []
P = out.append
P("# Edge types, neighbour scoring and token budgets (offline simulation, nothing run, no pruning code changed)\n")
P("VERIFIED = read from code/logs or computed by the replay; INFERRED = a reading or an assumption stated next to it. Nothing here changes `eval_harness/data`, the backend or any run.\n")
P("## 1. Which edge types exist, and what the pruner does with them\n")
tot = Counter()
for d in {x["index_dir"] for (ds, a), dd in res.items() if a == "ALWAYS_GRAPH" for x in dd.values()}:
    for e in json.load(open(os.path.join(d, "graphify-out", "graph.json")))["links"]:
        tot[str(e.get("relation") or e.get("type"))] += 1
allowed = ga.DEFAULT_EDGE_TYPES
P("Relation labels in the 83 `graphify-out/graph.json` files (all edges, VERIFIED), and whether `load()` keeps them (`_relation_ok` against `DEFAULT_EDGE_TYPES`, `graphify_adapter.py:24, 108`):\n")
P("| Relation | Edges (83 graphs) | Kept by `load()` | Reaches the pruner as |")
P("|---|---|---|---|")
for k, n in tot.most_common():
    P(f"| {k} | {n:,} | {'yes' if ga._relation_ok(k, allowed) else 'no'} | {pf._RELATION_PRIORITY.get(canon(k), 0.65) if ga._relation_ok(k, allowed) else '-'} if the label were passed on (my mapping `{canon(k)}`) |")
P("")
P("Reverse edges are stored too: `_add_edge` appends `(tgt, rel)` to the source and `(src, 'rev_' + rel)` to the target (`graphify_adapter.py:~215`). `neighbors()` returns node keys only, so the relation is lost there; `graph_expand` then builds each chunk with `_chunk_from_chroma_row` (`graph_expand.py:42`) without `relation_type`/`edge_type`. "
  "`_neighbor_structural_score` (`pruning/file.py:667`) therefore always takes `default` (0.65, +0.05 if the function name occurs in its own code = 0.70).\n")
P("The pruner's table (`_RELATION_PRIORITY`, `file.py:653`): callee/calls 1.00, inherits/inherits_from 0.95, caller 0.85, import/imports 0.75, contains 0.70, default 0.65. Even if the raw label were passed on, `rev_calls`, `rev_imports`, `imports_from`, `indirect_call`, `defines`, `implements` and the other labels would miss the table and fall back to 0.65; only `calls`, `imports`, `inherits`, `inherits_from` and `contains` would hit. "
  "The mapping I use in the simulation (`canon` in `build_edge_budget_sim.py`, my choice, not backend code): calls/indirect_call forward → callee 1.00; rev_calls → caller 0.85; imports/imports_from/dynamic_import forward → import 0.75; inherits/extends/mixes_in/implements forward → inherits 0.95; contains/defines either direction → contains 0.70; every other label and the reverse of imports/inherits → default 0.65.\n")
P("Relation of the discovering edge for every pool chunk of the 83 GRAPH tasks (VERIFIED by replay):\n")
P("| Relation (discovering edge) | Pool chunks | Of which cover a gold function or gold file | Kept by the current 540 reserve |")
P("|---|---|---|---|")
for k, n in rel_first_ct.most_common():
    P(f"| {k} | {n} | {rel_first_ct_gold.get(k, 0)} | {rel_first_ct_kept0.get(k, 0)} |")
P("")
P("## 2. Validation of the replay\n")
P(f"- Hops whose neighbour ids and new-chunk counts equal the log: {valid['hop_logged_match']} of {valid['hop_logged_match'] + valid['hop_logged_MISMATCH']}. Hops where my relation-tracking BFS returns the same neighbour order as `graph_expand`: {valid['hop_relation_bfs_match']} of {valid['hop_relation_bfs_match'] + valid['hop_relation_bfs_MISMATCH']}.")
P(f"- Baseline pass 1 (current scoring, 540 reserve) reproduces the logged final context for {valid['task_baseline_reproduces_logged_final']} of {valid['task_total']} tasks, tested as: the replayed neighbour functions that are not seeds are all in the logged final context, and every logged final-context function that is not a retrieved top-5 function was a replayed kept neighbour.\n")
P("## 3. Scoring variants at the current 540-token reserve\n")
P("Seeds are assumed unchanged (final context = logged final context, minus the baseline's kept neighbours, plus the variant's kept neighbours). 'Function complete' = all gold functions (cap 10) present; 'file complete' = all gold files (cap 5). INFERRED for anything that depends on the seed pass (see section 5).\n")
P("| Dataset | Scoring | Tasks whose kept set differs | Mean neighbours kept | Mean neighbour tokens | Function-complete tasks | Gold functions found | File-complete tasks | Gold-bearing share of neighbour tokens |")
P("|---|---|---|---|---|---|---|---|---|")
for a_ in agg_rows:
    if a_["reserve_tokens"] == 540:
        P(f"| {L.DS_LABEL[a_['dataset']]} | {a_['mode']} | {a_['tasks_kept_set_changed_vs_baseline']} | {a_['mean_neighbours_kept']} | {a_['mean_neighbour_tokens']} | {a_['tasks_function_complete']}/{a_['tasks']} | {a_['sum_gold_functions_found']} | {a_['tasks_file_complete']}/{a_['tasks']} | {a_['gold_token_share_of_neighbour_tokens']} |")
P("")
P("## 4. Reserve size (neighbour tokens), V0 current scoring and V3 edge-aware scoring\n")
P("| Dataset | Scoring | Reserve | Mean neighbours kept | Mean neighbour tokens | Function-complete tasks | Gold functions found | File-complete tasks | Gold-bearing share of neighbour tokens |")
P("|---|---|---|---|---|---|---|---|---|")
for a_ in agg_rows:
    if a_["mode"] in (MODES[0], MODES[3]):
        P(f"| {L.DS_LABEL[a_['dataset']]} | {a_['mode'][:2]} | {a_['reserve_tokens']} | {a_['mean_neighbours_kept']} | {a_['mean_neighbour_tokens']} | {a_['tasks_function_complete']}/{a_['tasks']} | {a_['sum_gold_functions_found']} | {a_['tasks_file_complete']}/{a_['tasks']} | {a_['gold_token_share_of_neighbour_tokens']} |")
P("")
P(f"Windowed token cost of pool chunks (`estimate_tokens` of the `prune_single_item_heuristic` output; all {len(cost_all)} non-empty chunks): median {q(cost_all, .5)}, p75 {q(cost_all, .75)}, p90 {q(cost_all, .9)}, max {max(cost_all)}; share above 540: {sum(c > 540 for c in cost_all) / len(cost_all):.1%}. "
  f"For chunks that cover a gold function or file ({len(cost_gold_cov)}): median {q(cost_gold_cov, .5)}, p90 {q(cost_gold_cov, .9)}, share above 540: {sum(c > 540 for c in cost_gold_cov) / max(1, len(cost_gold_cov)):.1%}. The window is capped at 40 lines plus radius (`find_best_window(max_lines=40)`, `file.py:306`) but long lines push a chunk above that.\n")
gf = [r for r in rows_gold if r["gold_function"] and r["mapped_function"]]
P("Smallest reserve that admits each gold-function chunk of the pool (`edge_budget_gold_chunks.csv`, columns `min_reserve_to_keep_V0` / `_V3`):\n")
P("| Dataset | Gold-function chunks in pool | Kept at 540 (V0) | Need ≤ 720 | ≤ 900 | ≤ 1080 | ≤ 1800 | never within 6000 |")
P("|---|---|---|---|---|---|---|---|")
for ds in L.DATASETS:
    R_ = [r for r in gf if r["dataset"] == ds]
    for m_ in ("V0", "V3"):
        key = f"min_reserve_to_keep_{m_}"
        P(f"| {L.DS_LABEL[ds]} {m_} | {len(R_)} | {sum(r['kept_at_540_V0'] for r in R_)} | " + " | ".join(str(sum(1 for r in R_ if r[key] is not None and r[key] <= x)) for x in (720, 900, 1080, 1800)) + f" | {sum(1 for r in R_ if r[key] is None)} |")
P("")
P("## 5. Seed side: not measurable offline (INFERRED)\n")
P("`prune_context_graph_aware` gives the seeds `total − neighbour tokens used` (pass 2, `file.py`), so every neighbour token above the baseline displaces a seed token one for one once the 1800 total is full. "
  "I tried to estimate the seeds' kept size from the base-commit index (window each logged seed function with `prune_single_item_heuristic`), but the estimate came out at a median of about 2,100 to 2,300 tokens, above the 1800 cap the baseline respected. The seeds the pipeline actually used are the output of the earlier flat prune, whose text I cannot rebuild (retrieval scores are not logged), so I discarded that estimate. "
  "Consequence: the 'function-complete' and 'gold functions found' columns above are upper bounds for any reserve larger than 540, because the extra neighbour tokens would come out of the seeds.\n")
P("## 6. Is 1800 too small? Evidence from the logs and the source (Always-RAG, flat prune)\n")
P("| Dataset | Tasks | RAG tasks where the prune kept fewer chunks than the 5 retrieved | Gold-complete tasks lost at the prune (top-5 → after pruning) | Median sum of gold-function sizes (est. tokens) | p90 | Tasks whose gold functions together exceed 540 / 1260 / 1800 | Largest single gold function |")
P("|---|---|---|---|---|---|---|---|")
from harness.metrics import acc_at                                          # noqa: E402
for ds in L.DATASETS:
    S = [r for r in size_rows if r["dataset"] == ds]
    ids = [r["task_id"] for r in S]
    lost = sum(1 for t in ids if acc_at(res[(ds, "ALWAYS_RAG")][t]["ranked"]["retrieved_top5"]["functions"] or [], gold[ds][t]["functions"], 10) == 1.0
               and acc_at(res[(ds, "ALWAYS_RAG")][t]["ranked"]["after_pruning"]["functions"] or [], gold[ds][t]["functions"], 10) < 1.0)
    P(f"| {L.DS_LABEL[ds]} | {len(S)} | {sum(r['rag_pruned_items'] < r['rag_top5_items'] for r in S)} | {lost} | {q([r['sum_tokens'] for r in S], .5)} | {q([r['sum_tokens'] for r in S], .9)} | "
      f"{sum(r['sum_tokens'] > 540 for r in S)} / {sum(r['sum_tokens'] > 1260 for r in S)} / {sum(r['sum_tokens'] > 1800 for r in S)} | {max(r['max_tokens'] for r in S)} |")
miss = sum(r["missing"] for r in size_rows)
P(f"\nSizes are `len(source lines of the def) // 4` from the base-commit checkout; {miss} gold functions could not be located as a function def and are excluded.\n")
P("Model context for reference: `num_ctx` is 32768 in every row; the LC arm injects up to 24000 estimated tokens. The 1800-token pruner budget is a pipeline constant (`DEFAULT_TOKEN_BUDGET`, `pruning/file.py:26`; `arms.py:30, 94`), not a model limit.\n")
P("## 7. Cases behind the numbers\n")
P("Gold functions that expansion added and the pass left out (the 5 from `pruning_trace_report.md`), with the smallest reserve that would have admitted each (`edge_budget_gold_chunks.csv`):\n")
P("| Task | Cost | Discovering edge | Rank V0 / V3 | Min reserve V0 | Min reserve V3 |")
P("|---|---|---|---|---|---|")
for r_ in rows_gold:
    if r_["gold_function"] and r_["mapped_function"] and not r_["mapped_function_already_in_retrieved_top5"]:
        P(f"| {r_['task_id']} | {r_['token_cost']} | {r_['rel_first']} | {r_['rank_V0']} / {r_['rank_V3']} | {r_['min_reserve_to_keep_V0'] or 'none within 6000'} | {r_['min_reserve_to_keep_V3'] or 'none within 6000'} |")
P("\nA sixth case: scikit-learn__scikit-learn-30128 has its gold function `_check_reg_targets` in the retrieved top 5 but not in the final context (lost in the seed pruning), and it re-enters the pool as an 859-token neighbour that needs a reserve of about 2,380 (V3 order); no V0 reserve up to 6000 admits it.\n")
(HERE / "edge_budget_report.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
print(dict(valid))
