"""Instrumentation of the unmodified pipeline: wraps functions, never replaces their behaviour.

install(server_module, ...) wraps (by attribute, so call sites keep working):
  tool registry: retrieve_context, inject_context, ollama_generate     (stages + LC prompt check)
  retrieval_tool: rank_chunks, enhance_ranking_with_structure, reconstruct_file_context
  server: prune_context_with_stats, prune_context_graph_aware, reconstruct_file_context (lazy parent
          context), assess_sufficiency, assess_rag_gate, assess_structure_vs_context
  diamond.preroute_gate.preroute_structured_gate, retrieval.graph_expand.graph_expand,
  diamond.gasr_seeds.build_gasr_seed_pool
Every wrapper returns exactly what the original returned. Token counts come from the HTTP shim only.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .ollama_shim import Recorder, role

ROUTER_ROLES_EXCLUDED = {"answer"}


def _item(c: dict) -> dict:
    """Uniform small view of a retrieved / pruned / neighbour chunk (no code text)."""
    m = c.get("metadata") or {}
    return {
        "file": c.get("path") or m.get("file") or m.get("file_path"),
        "name": c.get("symbol") or m.get("function") or m.get("name"),
        "qualified_name": m.get("qualified_name"),
        "chunk_type": m.get("chunk_type"),
        "start_line": c.get("start_line") if c.get("start_line") is not None else m.get("start_line"),
        "end_line": m.get("end_line"),
        "score": c.get("final_score", c.get("pruning_score", c.get("hybrid_score"))),
        "source": c.get("source"),
        "has_parent_context": ("parent_context" in c) if "parent_context" in c or "code" in c else None,
    }


def seed_tier(pruned: list, raw: list, gasr_seeds) -> str:
    """Which fallback of build_gasr_seed_pool produced the pool (mirrors that function's order)."""
    if gasr_seeds.filter_non_test_chunks(pruned):
        return "pruned_non_test"
    if gasr_seeds.filter_non_test_chunks(raw):
        return "raw_non_test"
    if pruned:
        return "pruned_incl_tests"
    if raw:
        return "raw_incl_tests"
    return "empty"


class Instrument:
    def __init__(self, recorder: Recorder, expected_structure_rerank: bool = True) -> None:
        self.rec = recorder
        self.expected_structure_rerank = expected_structure_rerank
        self.events: list[dict] = []
        self._undo: list[tuple[Any, str, Any]] = []
        self.srv = None
        self.rt = None
        self._t0 = 0.0

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _wrap(self, obj: Any, attr: str, make: Callable[[Callable], Callable]) -> None:
        orig = obj[attr] if isinstance(obj, dict) else getattr(obj, attr)
        new = make(orig)
        new.__wrapped__ = orig
        if isinstance(obj, dict):
            obj[attr] = new
        else:
            setattr(obj, attr, new)
        self._undo.append((obj, attr, orig))

    def uninstall(self) -> None:
        for obj, attr, orig in reversed(self._undo):
            if isinstance(obj, dict):
                obj[attr] = orig
            else:
                setattr(obj, attr, orig)
        self._undo.clear()

    def _with_result(self, name: str, summarize, ollama_role=None):
        """Wrapper that also stores summarize(args, kwargs, result) in the event."""
        def make(fn):
            def wrapper(*a, **k):
                s0 = len(self.rec.calls)
                t = time.perf_counter()
                ev = {"name": name, "error": None}
                try:
                    if ollama_role is not None:
                        with role(ollama_role(a, k) if callable(ollama_role) else ollama_role):
                            res = fn(*a, **k)
                    else:
                        res = fn(*a, **k)
                    try:
                        ev.update(summarize(a, k, res))
                    except Exception as exc:                   # instrumentation must never break a run
                        ev["summary_error"] = repr(exc)
                    return res
                except Exception as exc:
                    ev["error"] = repr(exc)
                    raise
                finally:
                    ev["ms"] = round((time.perf_counter() - t) * 1000, 3)
                    ev["shim_seq"] = [s0, len(self.rec.calls)]
                    self.events.append(ev)
            return wrapper
        return make

    # ── install ──────────────────────────────────────────────────────────────
    def install(self, server) -> None:
        self.srv = server
        reg = sys.modules.get("tool_registry") or sys.modules.get("mcp_server.tool_registry")
        rt = importlib.import_module("retrieval.retrieval_tool")
        self.rt = rt
        ge = importlib.import_module("retrieval.graph_expand")
        pg = importlib.import_module("diamond.preroute_gate")
        gs = importlib.import_module("diamond.gasr_seeds")
        pb = importlib.import_module("injection.prompt_builder")
        pbe = importlib.import_module("injection.prompt_builder_endpoint")
        self.gs, self.pb, self.pbe = gs, pb, pbe

        # retrieval (own stage); parent context is NOT assumed to be present on the output
        self._wrap(reg.tools, "retrieve_context", self._with_result(
            "retrieve", lambda a, k, r: {"retrieved": [_item(c) for c in r], "n": len(r),
                                         "top_k": k.get("top_k", a[1] if len(a) > 1 else 5)}))
        self._wrap(rt, "rank_chunks", self._with_result(
            "rank", lambda a, k, r: {"ranked_candidates": [_item(c) for c in r[:50]], "n_ranked": len(r)}))
        if getattr(rt, "enhance_ranking_with_structure", None) is not None:
            def srr(a, k, r):
                before = [(_item(c)["file"], _item(c)["start_line"]) for c in a[0]]
                after = [(_item(c)["file"], _item(c)["start_line"]) for c in r]
                cg = Path(rt.CHROMA_PATH) / "call_graph.json"
                return {"order_changed": before != after, "call_graph_json_exists": cg.is_file()}
            self._wrap(rt, "enhance_ranking_with_structure", self._with_result("structure_rerank", srr))
        for mod in (rt, server):
            if hasattr(mod, "reconstruct_file_context"):
                self._wrap(mod, "reconstruct_file_context", self._with_result(
                    "parent_context", lambda a, k, r: {"n": len(r), "files": sorted({(_item(c)["file"] or "") for c in r})}))

        # pruning
        self._wrap(server, "prune_context_with_stats", self._with_result(
            "prune", lambda a, k, r: {"kept": [_item(c) for c in r[0]], "n_in": len(a[1]), "n_out": len(r[0]),
                                      "method": k.get("method")}))
        self._wrap(server, "prune_context_graph_aware", self._with_result(
            "re_prune", lambda a, k, r: {"kept": [_item(c) for c in r[0]], "n_seeds": len(a[1]), "n_neighbors": len(a[2])}))

        # gates and judges (role decides how the shim labels the HTTP call)
        dec = lambda a, k, r: {k2: r.get(k2) for k2 in ("sufficient", "need", "route", "confidence", "stage")} | {
            "pipeline_token_usage": r.get("token_usage")}
        self._wrap(server, "assess_sufficiency", self._with_result(
            "assess_sufficiency", lambda a, k, r: dec(a, k, r) | {"phase": k.get("phase", "rag")},
            ollama_role=lambda a, k: "post_expand_judge" if k.get("phase") == "graph" else "selfroute_judge"))
        self._wrap(server, "assess_rag_gate", self._with_result("assess_rag_gate", dec, ollama_role="gasr_gate"))
        self._wrap(server, "assess_structure_vs_context", self._with_result(
            "assess_structure_vs_context", dec, ollama_role="gapa_gate_call2"))
        self._wrap(pg, "preroute_structured_gate", self._with_result(
            "gapa_gate_call1", dec, ollama_role="gapa_gate_call1"))

        # graph expansion and seeds
        self._wrap(ge, "graph_expand", self._with_result(
            "graph_expand", lambda a, k, r: {
                "n_seeds": len(a[0]), "hops": k.get("hops", a[1] if len(a) > 1 else None),
                "max_neighbors": k.get("max_neighbors"), "seed_keys": r.get("seed_keys"),
                "neighbor_ids": (r.get("neighbor_ids") or [])[:30], "new_chunk_count": r.get("new_chunk_count"),
                "graph_backend": r.get("graph_backend"), "edge_counts": r.get("edge_counts"),
                "neighbors": [_item(c) for c in (r.get("chunks") or [])]}))
        self._wrap(gs, "build_gasr_seed_pool", self._with_result(
            "seed_pool", lambda a, k, r: {"pruned_n": len(a[0]), "raw_n": len(a[1]), "size": len(r),
                                         "tier": seed_tier(a[0], a[1], gs), "seeds": [_item(c) for c in r],
                                         "seed_has_test_chunk": any(gs.is_test_path(gs.chunk_file_path(c)) for c in r)}))

        # injection (LC prompt vs MAX_CONTEXT_TOKENS) and generation
        def inj(a, k, r):
            user = r.get("user", "") if isinstance(r, dict) else str(r)
            system = r.get("system", "") if isinstance(r, dict) else ""
            return {"prompt_chars": len(user) + len(system), "est_tokens_div4": (len(user) + len(system)) // 4,
                    "max_context_tokens": pbe.DEFAULT_MAX_CONTEXT_TOKENS,
                    "hit_max_context_tokens": pb.CONTEXT_TRUNCATED_TAG in user, "n_chunks": len(a[1])}
        self._wrap(reg.tools, "inject_context", self._with_result("inject", inj))
        self._wrap(reg.tools, "ollama_generate", self._with_result(
            "generate", lambda a, k, r: {"output_chars": len(r) if isinstance(r, str) else len(str(r))},
            ollama_role="answer"))

    # ── per task ─────────────────────────────────────────────────────────────
    def begin_task(self) -> None:
        self.events = []
        self.rec.reset()
        self._t0 = time.perf_counter()

    def end_task(self, result: dict | None) -> dict:
        calls = self.rec.reset()
        ev = self.events
        by = lambda n: [e for e in ev if e["name"] == n]
        ms = lambda n: round(sum(e["ms"] for e in by(n)), 3)
        tok = lambda cs, f: sum((c.get(f) or 0) for c in cs)
        router = [c for c in calls if c["role"] not in ROUTER_ROLES_EXCLUDED]
        answer = [c for c in calls if c["role"] == "answer"]
        rr = by("structure_rerank")
        retrieve = by("retrieve")
        rec = {
            "wall_ms": round((time.perf_counter() - self._t0) * 1000, 3),
            "stage_ms": {"retrieve": ms("retrieve"), "rank": ms("rank"), "structure_rerank": ms("structure_rerank"),
                         "prune": ms("prune"), "parent_context": ms("parent_context"), "graph_expand": ms("graph_expand"),
                         "re_prune": ms("re_prune"), "inject": ms("inject"), "generate": ms("generate"),
                         "gates_and_judges": round(sum(e["ms"] for e in ev if e["name"].startswith(("assess", "gapa_gate"))), 3)},
            "retrieval": {"ms": ms("retrieve"), "retrieved": retrieve[0]["retrieved"] if retrieve else [],
                          "ranked_candidates": by("rank")[0]["ranked_candidates"] if by("rank") else [],
                          "parent_context_on_retrieval_output": [r["has_parent_context"] for r in (retrieve[0]["retrieved"] if retrieve else [])]},
            "structure_rerank": {"flag": getattr(self.rt, "_STRUCTURE_RERANK", None),
                                 "expected": self.expected_structure_rerank, "called": bool(rr),
                                 "raised": bool(rr and rr[0]["error"]),
                                 "call_graph_json_exists": bool(rr and rr[0].get("call_graph_json_exists")),
                                 "order_changed": bool(rr and rr[0].get("order_changed")),
                                 "applied": bool(rr and not rr[0]["error"] and rr[0].get("call_graph_json_exists"))},
            "pruned": by("prune")[0]["kept"] if by("prune") else [],
            "seed_pool": ({k: v for k, v in by("seed_pool")[0].items() if k not in ("name", "shim_seq", "ms", "error")}
                          if by("seed_pool") else None),
            "expansions": [{k: v for k, v in e.items() if k not in ("name", "shim_seq", "error")} for e in by("graph_expand")],
            "re_pruned": [e["kept"] for e in by("re_prune")],
            "gate": self._gate(calls),
            "inject": ({k: v for k, v in by("inject")[-1].items() if k in ("prompt_chars", "est_tokens_div4", "max_context_tokens", "hit_max_context_tokens", "n_chunks")}
                       if by("inject") else None),
            "answer_call": ({"prompt_eval_count": answer[-1]["prompt_eval_count"], "eval_count": answer[-1]["eval_count"],
                             "num_ctx": answer[-1]["num_ctx"], "prompt_eval_over_num_ctx":
                             (answer[-1]["prompt_eval_count"] / answer[-1]["num_ctx"]) if answer[-1]["prompt_eval_count"] else None,
                             "truncation_suspected": answer[-1]["truncation_suspected"], "done_reason": answer[-1]["done_reason"]}
                            if answer else None),
            "tokens": {"router_prompt": tok(router, "prompt_eval_count"), "router_completion": tok(router, "eval_count"),
                       "answer_prompt": tok(answer, "prompt_eval_count"), "answer_completion": tok(answer, "eval_count")},
            "ollama_calls": calls,
            "any_truncation_suspected": any(c["truncation_suspected"] for c in calls),
        }
        rec["token_reconciliation"] = reconcile(calls, result)
        return rec

    def _gate(self, calls: list[dict]) -> dict:
        c1 = next((c for c in calls if c["role"] == "gapa_gate_call1"), None)
        c2 = next((c for c in calls if c["role"] == "gapa_gate_call2"), None)
        g = next((c for c in calls if c["role"] == "gasr_gate"), None)
        s = next((c for c in calls if c["role"] == "selfroute_judge"), None)
        slim = lambda c: None if c is None else {k: c[k] for k in ("prompt_eval_count", "eval_count", "wall_ms", "num_ctx", "error")}
        out: dict = {"gapa_call1": slim(c1), "gapa_call2": slim(c2), "gasr_gate": slim(g), "selfroute_judge": slim(s),
                     "post_expand_judges": [slim(c) for c in calls if c["role"] == "post_expand_judge"]}
        if c1 is not None or c2 is not None:
            out["gapa_path"] = "call2" if c2 is not None else "sufficient_or_shortcut"
        return out


class JobFailure(RuntimeError):
    """The job must stop and be reported as failed (not counted as a model failure)."""


def check_structure_rerank(rec: dict) -> None:
    """The pipeline silently skips the structure re-rank when call_graph.json is missing; we do not."""
    r = rec["structure_rerank"]
    if r["expected"] and not r["applied"]:
        raise JobFailure(f"structure re-rank did not apply (flag={r['flag']}, called={r['called']}, "
                         f"raised={r['raised']}, call_graph_json_exists={r['call_graph_json_exists']})")


def trace_tokens(result: dict | None) -> dict:
    """Sum of token_usage over assess-trace entries. entry_gate_token_usage is NOT added (it is
    already contained in the first trace entry for GAPA)."""
    p = c = 0
    n = 0
    pipe = (result or {}).get("pipeline") or {}
    stage = (pipe.get("stages") or {}).get("assess") or {}
    for t in (stage.get("summary") or {}).get("trace") or []:
        u = t.get("token_usage") or {}
        p += int(u.get("prompt_tokens") or 0)
        c += int(u.get("completion_tokens") or 0)
        n += 1
    return {"prompt": p, "completion": c, "entries": n, "reported": n > 0}


def reconcile(calls: list[dict], result: dict | None) -> dict:
    """Shim token sum over judge/gate calls vs pipeline token_usage over trace entries."""
    router = [c for c in calls if c["role"] not in ROUTER_ROLES_EXCLUDED]
    sp = sum((c.get("prompt_eval_count") or 0) for c in router)
    sc = sum((c.get("eval_count") or 0) for c in router)
    t = trace_tokens(result)
    return {"shim_prompt": sp, "shim_completion": sc, "trace_prompt": t["prompt"], "trace_completion": t["completion"],
            "trace_entries": t["entries"], "trace_reported": t["reported"],
            "match": (sp == t["prompt"] and sc == t["completion"]) if t["reported"] else None}


def gapa_gate_summary(records: list[dict]) -> dict:
    """Share of GAPA tasks that needed call 2, and mean tokens on each path."""
    g = [r["gate"] for r in records if r.get("gate", {}).get("gapa_call1")]
    sufficient = [x for x in g if x["gapa_call2"] is None]
    second = [x for x in g if x["gapa_call2"] is not None]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None
    tk = lambda x, k: (x[k]["prompt_eval_count"] or 0) + (x[k]["eval_count"] or 0)
    return {
        "n_gapa": len(g), "share_needing_call2": (len(second) / len(g)) if g else None,
        "mean_tokens_sufficient_path": mean([tk(x, "gapa_call1") for x in sufficient]),
        "mean_tokens_call2_path": mean([tk(x, "gapa_call1") + tk(x, "gapa_call2") for x in second]),
        "mean_call1_tokens_on_call2_path": mean([tk(x, "gapa_call1") for x in second]),
        "mean_call2_tokens": mean([tk(x, "gapa_call2") for x in second]),
    }


def seed_tier_rows(records: list[dict]) -> list[dict]:
    """Per-task seed pool facts for the metrics output (GASR and GAPA; others have no seed pool)."""
    rows = []
    for r in records:
        sp = r.get("seed_pool")
        rows.append({"instance_id": r.get("instance_id"), "arm": r.get("arm"),
                     "seed_tier": sp["tier"] if sp else None, "seed_pool_size": sp["size"] if sp else None,
                     "seed_pool_pruned_n": sp["pruned_n"] if sp else None, "seed_pool_raw_n": sp["raw_n"] if sp else None})
    return rows


def seed_tier_shares(records: list[dict]) -> dict:
    """Share of tasks per fallback tier, per arm, over tasks that reached the seed-pool step."""
    out: dict = {}
    for row in seed_tier_rows(records):
        if row["seed_tier"] is None:
            continue
        a = out.setdefault(row["arm"], {"n": 0, "tiers": {}, "mean_size": 0.0})
        a["n"] += 1
        a["tiers"][row["seed_tier"]] = a["tiers"].get(row["seed_tier"], 0) + 1
        a["mean_size"] += row["seed_pool_size"]
    for a in out.values():
        a["mean_size"] = a["mean_size"] / a["n"]
        a["share"] = {t: c / a["n"] for t, c in a["tiers"].items()}
    return out
