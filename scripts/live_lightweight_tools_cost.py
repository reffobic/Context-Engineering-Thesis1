#!/usr/bin/env python3
"""
A/B cost proof: hybrid retrieve_context vs list_files / find_symbol / find_references.

Measures context size (tokens_injected_est, final prompt tokens) and retrieve latency
on the same lookup prompts, with lightweight tools ON vs OFF.

Does NOT call Ollama by default (context cost is the claim). Use --with-generate
for end-to-end numbers (much slower).

Usage:
  cd /path/to/Context-Engineering-Thesis1
  PYTHONPATH=backend python -u scripts/live_lightweight_tools_cost.py
  PYTHONPATH=backend python -u scripts/live_lightweight_tools_cost.py --with-generate --timeout 600
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUT_DIR = ROOT / "results" / "lightweight_tools_cost"

CASES: list[dict[str, Any]] = [
    {
        "id": "S1_find_symbol_create_app",
        "bucket": "lookup_symbol",
        "expect_tool": "find_symbol",
        "prompt": "Where is function create_app defined?",
    },
    {
        "id": "S2_find_symbol_SupersetApp",
        "bucket": "lookup_symbol",
        "expect_tool": "find_symbol",
        "prompt": "Where is class SupersetApp defined?",
    },
    {
        "id": "R1_find_references_create_app",
        "bucket": "lookup_references",
        "expect_tool": "find_references",
        "prompt": "Where is create_app used or called?",
    },
    {
        "id": "R2_find_references_init_app",
        "bucket": "lookup_references",
        "expect_tool": "find_references",
        "prompt": "Who calls init_app?",
    },
    {
        "id": "L1_list_files_cli",
        "bucket": "lookup_files",
        "expect_tool": "list_files",
        "prompt": "List files under superset/cli with .py",
    },
    {
        "id": "L2_list_files_mcp",
        "bucket": "lookup_files",
        "expect_tool": "list_files",
        "prompt": "List .py files under superset/mcp_service",
    },
    {
        "id": "X1_explain_create_app",
        "bucket": "explanation_control",
        "expect_tool": None,
        "prompt": "How does create_app in superset/app.py work? Summarize the main setup steps.",
    },
]


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 2) if xs else None


def _pct_drop(baseline: Optional[float], treatment: Optional[float]) -> Optional[float]:
    if baseline is None or treatment is None or baseline == 0:
        return None
    return round(100.0 * (1.0 - (treatment / baseline)), 1)


def _run_arm(
    prompt: str,
    *,
    use_lightweight_tools: bool,
    with_generate: bool,
) -> dict[str, Any]:
    # Import inside so PYTHONPATH/cwd mistakes surface clearly.
    from mcp_server.server import run_pipeline, _retrieve_for_prompt, _time_stage, _estimate_tokens
    from pruning.file import prune_context as prune_context_with_stats, estimate_tokens
    from mcp_server.tool_registry import call_tool

    if with_generate:
        t0 = time.perf_counter()
        resp = run_pipeline(
            prompt,
            mode="RAG",
            pruning_method="heuristic",
            use_lightweight_tools=use_lightweight_tools,
        )
        wall_ms = round((time.perf_counter() - t0) * 1000)
        pipeline = resp.get("pipeline") or {}
        retrieve = (pipeline.get("stages") or {}).get("retrieve") or {}
        prune = (pipeline.get("stages") or {}).get("prune") or {}
        inject = (pipeline.get("stages") or {}).get("inject") or {}
        generate = (pipeline.get("stages") or {}).get("generate") or {}
        return {
            "use_lightweight_tools": use_lightweight_tools,
            "retrieval_path": (retrieve.get("summary") or {}).get("lightweight_tool") or "hybrid",
            "candidate_chunks": (retrieve.get("summary") or {}).get("candidate_chunks"),
            "unique_files": (retrieve.get("summary") or {}).get("unique_files"),
            "retrieve_ms": retrieve.get("duration_ms"),
            "prune_ms": prune.get("duration_ms"),
            "kept_est_tokens": (prune.get("summary") or {}).get("kept_est_tokens"),
            "tokens_injected_est": resp.get("tokens_injected_est")
            or pipeline.get("tokens_injected_est"),
            "prompt_estimated_tokens": (inject.get("summary") or {}).get("estimated_tokens"),
            "system_chars": (inject.get("summary") or {}).get("system_chars"),
            "user_chars": (inject.get("summary") or {}).get("user_chars"),
            "generate_ms": generate.get("duration_ms"),
            "wall_ms": wall_ms,
            "route": resp.get("route") or pipeline.get("route"),
        }

    # Fast path: retrieve → prune → inject only (no Ollama).
    (context, lightweight_tool), retrieve_ms = _time_stage(
        lambda: _retrieve_for_prompt(prompt, use_lightweight_tools=use_lightweight_tools)
    )
    (pruned, stats), prune_ms = _time_stage(
        lambda: prune_context_with_stats(
            prompt, context, token_budget=1800, method="heuristic"
        )
    )
    final_prompt, inject_ms = _time_stage(
        lambda: call_tool("inject_context", prompt, pruned)
    )
    tokens_injected = sum(
        estimate_tokens(c.get("text") or c.get("code") or "") for c in pruned
    )
    system = (final_prompt or {}).get("system") or ""
    user = (final_prompt or {}).get("user") or ""
    return {
        "use_lightweight_tools": use_lightweight_tools,
        "retrieval_path": lightweight_tool or "hybrid",
        "candidate_chunks": len(context),
        "unique_files": len(
            {
                ((c.get("metadata") or {}).get("file") or "")
                for c in context
                if (c.get("metadata") or {}).get("file")
            }
        ),
        "retrieve_ms": retrieve_ms,
        "prune_ms": prune_ms,
        "inject_ms": inject_ms,
        "kept_est_tokens": getattr(stats, "kept_est_tokens", None),
        "tokens_injected_est": tokens_injected,
        "prompt_estimated_tokens": _estimate_tokens(system) + _estimate_tokens(user),
        "system_chars": len(system),
        "user_chars": len(user),
        "generate_ms": None,
        "wall_ms": retrieve_ms + prune_ms + inject_ms,
        "route": "RAG",
    }


def _pair_row(case: dict[str, Any], off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": case["id"],
        "bucket": case["bucket"],
        "expect_tool": case["expect_tool"],
        "prompt": case["prompt"],
        "hybrid": off,
        "lightweight": on,
        "delta": {
            "tokens_injected_est": _delta(off.get("tokens_injected_est"), on.get("tokens_injected_est")),
            "prompt_estimated_tokens": _delta(
                off.get("prompt_estimated_tokens"), on.get("prompt_estimated_tokens")
            ),
            "retrieve_ms": _delta(off.get("retrieve_ms"), on.get("retrieve_ms")),
            "wall_ms": _delta(off.get("wall_ms"), on.get("wall_ms")),
            "tokens_pct_drop": _pct_drop(
                _as_float(off.get("tokens_injected_est")),
                _as_float(on.get("tokens_injected_est")),
            ),
            "prompt_tokens_pct_drop": _pct_drop(
                _as_float(off.get("prompt_estimated_tokens")),
                _as_float(on.get("prompt_estimated_tokens")),
            ),
        },
        "routing_ok": (
            case["expect_tool"] is None and on.get("retrieval_path") == "hybrid"
        )
        or (case["expect_tool"] == on.get("retrieval_path")),
    }


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _delta(a: Any, b: Any) -> Optional[float]:
    fa, fb = _as_float(a), _as_float(b)
    if fa is None or fb is None:
        return None
    return round(fb - fa, 2)


def _aggregate(pairs: list[dict[str, Any]], bucket: Optional[str] = None) -> dict[str, Any]:
    rows = [p for p in pairs if bucket is None or p["bucket"] == bucket]
    if not rows:
        return {}

    def col(arm: str, key: str) -> list[float]:
        out = []
        for r in rows:
            v = _as_float((r.get(arm) or {}).get(key))
            if v is not None:
                out.append(v)
        return out

    hybrid_tok = col("hybrid", "tokens_injected_est")
    light_tok = col("lightweight", "tokens_injected_est")
    hybrid_prompt = col("hybrid", "prompt_estimated_tokens")
    light_prompt = col("lightweight", "prompt_estimated_tokens")
    hybrid_ret = col("hybrid", "retrieve_ms")
    light_ret = col("lightweight", "retrieve_ms")
    return {
        "n": len(rows),
        "routing_ok": sum(1 for r in rows if r.get("routing_ok")),
        "hybrid_tokens_mean": _mean(hybrid_tok),
        "lightweight_tokens_mean": _mean(light_tok),
        "tokens_pct_drop_mean": _pct_drop(_mean(hybrid_tok), _mean(light_tok)),
        "hybrid_prompt_tokens_mean": _mean(hybrid_prompt),
        "lightweight_prompt_tokens_mean": _mean(light_prompt),
        "prompt_tokens_pct_drop_mean": _pct_drop(_mean(hybrid_prompt), _mean(light_prompt)),
        "hybrid_retrieve_ms_mean": _mean(hybrid_ret),
        "lightweight_retrieve_ms_mean": _mean(light_ret),
        "retrieve_ms_delta_mean": (
            round(_mean(light_ret) - _mean(hybrid_ret), 2)
            if hybrid_ret and light_ret
            else None
        ),
    }


def _md(payload: dict[str, Any]) -> str:
    lines = [
        "# Lightweight tools cost ablation",
        "",
        f"- **timestamp:** `{payload['timestamp']}`",
        f"- **with_generate:** `{payload['with_generate']}`",
        f"- **token_budget:** `1800` (RAG prune)",
        "",
        "## Summary (lookup prompts only)",
        "",
    ]
    lookup = payload["aggregates"].get("lookup_all") or {}
    if lookup:
        lines.extend(
            [
                "| Arm | n | tokens_injected mean | prompt tokens mean | retrieve_ms mean |",
                "|-----|---|----------------------|--------------------|------------------|",
                (
                    f"| Hybrid (tools OFF) | {lookup.get('n')} | "
                    f"{lookup.get('hybrid_tokens_mean')} | "
                    f"{lookup.get('hybrid_prompt_tokens_mean')} | "
                    f"{lookup.get('hybrid_retrieve_ms_mean')} |"
                ),
                (
                    f"| Lightweight (tools ON) | {lookup.get('n')} | "
                    f"{lookup.get('lightweight_tokens_mean')} | "
                    f"{lookup.get('lightweight_prompt_tokens_mean')} | "
                    f"{lookup.get('lightweight_retrieve_ms_mean')} |"
                ),
                "",
                (
                    f"**Token reduction (injected):** "
                    f"`{lookup.get('tokens_pct_drop_mean')}%`  \n"
                    f"**Token reduction (final prompt):** "
                    f"`{lookup.get('prompt_tokens_pct_drop_mean')}%`  \n"
                    f"**Routing correct:** "
                    f"`{lookup.get('routing_ok')}/{lookup.get('n')}`"
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Per-case pairs",
            "",
            "| ID | expect | path ON | hybrid tokens | light tokens | Δ tokens | % drop | hybrid retrieve_ms | light retrieve_ms | routing_ok |",
            "|----|--------|---------|---------------|--------------|----------|--------|--------------------|-------------------|------------|",
        ]
    )
    for p in payload["pairs"]:
        h, l, d = p["hybrid"], p["lightweight"], p["delta"]
        lines.append(
            "| {id} | {exp} | {path} | {ht} | {lt} | {dt} | {pct}% | {hr} | {lr} | {ok} |".format(
                id=p["id"],
                exp=p["expect_tool"] or "hybrid",
                path=l.get("retrieval_path"),
                ht=h.get("tokens_injected_est"),
                lt=l.get("tokens_injected_est"),
                dt=d.get("tokens_injected_est"),
                pct=d.get("tokens_pct_drop"),
                hr=h.get("retrieve_ms"),
                lr=l.get("retrieve_ms"),
                ok="yes" if p.get("routing_ok") else "NO",
            )
        )

    lines.extend(["", "## Aggregates by bucket", ""])
    for name, agg in (payload.get("aggregates") or {}).items():
        if not agg:
            continue
        lines.append(f"### `{name}`")
        lines.append("```json")
        lines.append(json.dumps(agg, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-generate",
        action="store_true",
        help="Run full RAG pipeline including Ollama (slow).",
    )
    parser.add_argument(
        "--cases",
        nargs="*",
        default=None,
        help="Optional case ids to run (default: all).",
    )
    args = parser.parse_args()

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    cases = CASES
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in CASES if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            print(f"Unknown case ids: {sorted(missing)}", file=sys.stderr)
            return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs: list[dict[str, Any]] = []

    print("Loading retrieval stack (Chroma / embeddings)…", flush=True)
    # Warm imports / model once.
    from mcp_server import tool_registry  # noqa: F401

    for case in cases:
        print(f"\n=== {case['id']} ===", flush=True)
        print(f"prompt: {case['prompt']}", flush=True)
        print("  arm=OFF (hybrid forced)…", flush=True)
        off = _run_arm(
            case["prompt"],
            use_lightweight_tools=False,
            with_generate=args.with_generate,
        )
        print(
            f"    path={off['retrieval_path']} tokens={off['tokens_injected_est']} "
            f"prompt_tok={off['prompt_estimated_tokens']} retrieve_ms={off['retrieve_ms']}",
            flush=True,
        )
        print("  arm=ON (lightweight routing)…", flush=True)
        on = _run_arm(
            case["prompt"],
            use_lightweight_tools=True,
            with_generate=args.with_generate,
        )
        print(
            f"    path={on['retrieval_path']} tokens={on['tokens_injected_est']} "
            f"prompt_tok={on['prompt_estimated_tokens']} retrieve_ms={on['retrieve_ms']}",
            flush=True,
        )
        pair = _pair_row(case, off, on)
        pairs.append(pair)
        print(
            f"  Δtokens={pair['delta']['tokens_injected_est']} "
            f"({pair['delta']['tokens_pct_drop']}% drop) "
            f"routing_ok={pair['routing_ok']}",
            flush=True,
        )

    lookup_pairs = [p for p in pairs if p["bucket"] != "explanation_control"]
    aggregates = {
        "lookup_all": _aggregate(lookup_pairs),
        "lookup_symbol": _aggregate(pairs, "lookup_symbol"),
        "lookup_references": _aggregate(pairs, "lookup_references"),
        "lookup_files": _aggregate(pairs, "lookup_files"),
        "explanation_control": _aggregate(pairs, "explanation_control"),
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "timestamp": ts,
        "with_generate": args.with_generate,
        "pairs": pairs,
        "aggregates": aggregates,
    }

    json_path = OUT_DIR / f"lightweight_cost_{ts}.json"
    md_path = OUT_DIR / f"lightweight_cost_{ts}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_md(payload), encoding="utf-8")
    latest_json = OUT_DIR / "latest.json"
    latest_md = OUT_DIR / "latest.md"
    latest_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_md.write_text(_md(payload), encoding="utf-8")

    print("\n=== LOOKUP SUMMARY ===", flush=True)
    print(json.dumps(aggregates.get("lookup_all"), indent=2), flush=True)
    print(f"\nWrote {json_path}", flush=True)
    print(f"Wrote {md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
