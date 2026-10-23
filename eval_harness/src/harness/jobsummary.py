"""One-job summary from a run folder (logs only): tasks ok/failed, routes, gate call 2 share, gate parse failures,
seconds per task by stage, stub counts by category, seed tier shares, tokens, truncation."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from .instrument import gapa_gate_summary, seed_tier_shares, stub_shares

STAGES = ["retrieve", "rank", "structure_rerank", "prune", "parent_context", "gates_and_judges", "graph_expand", "re_prune", "inject", "generate", "task_total"]


def summarize(d: Path) -> dict:
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = [r for r in rows if r["status"] == "ok"]
    m = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    s = {"job": d.name, "lines": len(rows), "status_counts": dict(Counter(r["status"] for r in rows)),
         "counted_as_failed": sum(1 for r in rows if r["counted_as_failed"]), "gate_parse_failures": [r["task_id"] for r in rows if r.get("gate_error")],
         "routes": dict(Counter(r.get("route") for r in ok)),
         "seconds_per_task_by_stage_mean": {k: m([r["latency_ms"][k] / 1000 for r in ok if k in r["latency_ms"]]) for k in STAGES},
         "seconds_per_task_task_total_median": round(statistics.median([r["latency_ms"]["task_total"] / 1000 for r in ok]), 2) if ok else None,
         "overhead_seconds_mean": {k: m([r["overhead_s"].get(k) for r in rows if r["overhead_s"].get(k) is not None]) for k in ("subprocess_startup_s", "index_copy_s", "index_verify_s")},
         "stub_neighbors": stub_shares(ok), "seed_tier_shares": seed_tier_shares(ok),
         "truncation_suspected": sum(1 for r in rows if r.get("any_truncation_suspected")),
         "structure_rerank_applied_all": all(r["structure_rerank"]["applied"] for r in ok),
         "tokens_mean": {k: m([r["tokens"][k] for r in ok if r["tokens"].get(k) is not None]) for k in ("router_prompt", "router_completion", "answer_prompt", "answer_completion")},
         "token_reconciliation_unexplained_mismatches": [r["task_id"] for r in ok if r["token_reconciliation"]["match"] is False and not r["token_reconciliation"]["explained_by_gate_error"]]}
    if rows and rows[0]["arm"] == "GAPA":
        s["gapa_gate"] = gapa_gate_summary(ok)
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    print(json.dumps(summarize(ap.parse_args().dir), indent=1, default=str))
