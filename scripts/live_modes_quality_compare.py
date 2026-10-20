#!/usr/bin/env python3
"""
Live modes quality compare: RAG / LC / GASR / SELF_ROUTE

For each prompt × mode:
  1. POST /generate → tokens, route, hops, assess/expand traces, answer
  2. DeepEval faithfulness (vs that mode's injected chunks)
  3. DeepEval answer_relevancy (accuracy proxy)
  4. File-level precision@k / recall@k vs gold relevant_files

Writes ONLY under results/live_modes_quality/ (does not touch live_gasr/ or live_modes_compare/).

Example:
  export OLLAMA_MODEL=qwen3.5:9b
  export ASSESSOR_MODEL=qwen3.5:9b
  export EVAL_JUDGE_MODEL=qwen3.5:9b
  # restart uvicorn with graph-aware re-prune, then:
  python3 -u scripts/live_modes_quality_compare.py --timeout 1200 \\
    --gen-model-label qwen3.5:9b --assessor-model-label qwen3.5:9b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
_BACKEND = _REPO / "backend"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from live_gasr_ollama_tests import (  # noqa: E402
    OLLAMA_TAGS_URL,
    _get,
    _post_json,
    _summarize,
    detect_hardware,
    list_ollama_models,
)
from live_modes_compare_suite import COMPARE_GROUPS, MODES  # noqa: E402

# Six representative prompts for the fast quality subset (thesis coverage).
FAST_GROUPS: list[str] = [
    "C1_simple_return",
    "C2_cross_file_callees",
    "C3_same_file_context",
    "C4_config_deps",
    "C7_initializer_init",
    "C12_encrypt_init",
]

# Broader set for precision/recall GASR vs SELF_ROUTE (no DeepEval needed).
PRECISION_GROUPS: list[str] = [
    "C1_simple_return",
    "C2_cross_file_callees",
    "C3_same_file_context",
    "C4_config_deps",
    "C5_approot_middleware",
    "C7_initializer_init",
    "C8_feature_flags_chain",
    "C9_create_app_type",
    "C10_local_extensions_watcher",
    "C12_encrypt_init",
    "C14_superset_config_env",
    "C16_cli_create_app",
]

# Gold files for file-level P/R (thesis IR labels).
_GOLD_BY_GROUP: dict[str, list[str]] = {
    "C1_simple_return": ["superset/app.py"],
    "C2_cross_file_callees": [
        "superset/app.py",
        "superset/initialization/__init__.py",
    ],
    "C3_same_file_context": ["superset/app.py"],
    "C4_config_deps": [
        "superset/app.py",
        "superset/initialization/__init__.py",
    ],
    "C5_approot_middleware": ["superset/app.py"],
    "C6_sync_config": ["superset/app.py"],
    "C7_initializer_init": ["superset/initialization/__init__.py"],
    "C8_feature_flags_chain": [
        "superset/initialization/__init__.py",
        "superset/app.py",
    ],
    "C9_create_app_type": ["superset/app.py"],
    "C10_local_extensions_watcher": ["superset/app.py"],
    "C11_db_up_to_date": ["superset/app.py"],
    "C12_encrypt_init": ["superset/initialization/__init__.py"],
    "C13_app_imports": ["superset/app.py"],
    "C14_superset_config_env": ["superset/app.py"],
    "C15_test_create_app": ["tests/integration_tests/base_tests.py"],
    "C16_cli_create_app": ["superset/cli/main.py", "superset/app.py"],
}


def _quality_groups() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in COMPARE_GROUPS:
        row = dict(g)
        row["relevant_files"] = list(
            _GOLD_BY_GROUP.get(g["group"], ["superset/app.py"])
        )
        out.append(row)
    return out


def _expand_cases(
    groups: list[dict[str, Any]], modes: list[str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in groups:
        for mode in modes:
            out.append(
                {
                    "id": f"{g['group']}_{mode}",
                    "mode": mode,
                    "suite": "modes_quality",
                    "compare_group": g["group"],
                    "prompt": g["prompt"],
                    "intent": g.get("intent"),
                    "expect_note": g.get("expect_note"),
                    "thesis_tag": g.get("thesis_tag"),
                    "relevant_files": list(g.get("relevant_files") or []),
                }
            )
    return out


def _inject_chunks(pipeline: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(pipeline, dict):
        return []
    stages = pipeline.get("stages") or {}
    inject = stages.get("inject") or {}
    raw = inject.get("raw")
    if isinstance(raw, dict) and isinstance(raw.get("chunks"), list):
        return list(raw["chunks"])
    if isinstance(raw, list):
        return list(raw)
    prune = stages.get("prune") or {}
    praw = prune.get("raw")
    if isinstance(praw, dict) and isinstance(praw.get("chunks"), list):
        return list(praw["chunks"])
    return []


def _score_quality(
    prompt: str,
    answer: str,
    chunks: list[dict[str, Any]],
    gold_files: list[str],
    *,
    skip_deepeval: bool,
    skip_deepeval_reason: str = "--skip-deepeval",
) -> dict[str, Any]:
    from context_metrics.run_pipeline_metrics import (
        measure_answer_relevancy_only,
        measure_faithfulness_only,
        precision_recall_at_k,
    )

    out: dict[str, Any] = {}
    k = max(1, len(chunks)) if chunks else 1
    if gold_files:
        pr = precision_recall_at_k(chunks, k, gold_files)
        pr["stage"] = "after_inject"
        pr["note"] = "P/R on mode-injected context (k=len(chunks))."
        out["precision_recall"] = pr
    else:
        out["precision_recall"] = {"skipped": True, "reason": "No gold files."}

    if skip_deepeval:
        out["faithfulness"] = {"skipped": True, "reason": skip_deepeval_reason}
        out["answer_relevancy"] = {"skipped": True, "reason": skip_deepeval_reason}
        return out

    try:
        out["faithfulness"] = measure_faithfulness_only(prompt, chunks, answer)
    except Exception as exc:
        out["faithfulness"] = {"error": str(exc)}
    try:
        out["answer_relevancy"] = measure_answer_relevancy_only(prompt, answer)
    except Exception as exc:
        out["answer_relevancy"] = {"error": str(exc)}
    return out


def _metric_score(blob: Any) -> Optional[float]:
    if not isinstance(blob, dict):
        return None
    if blob.get("skipped") or blob.get("error"):
        return None
    s = blob.get("score")
    try:
        return float(s) if s is not None else None
    except (TypeError, ValueError):
        return None


def _pr_fields(blob: Any) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(blob, dict) or blob.get("skipped"):
        return None, None
    try:
        p = blob.get("precision_at_k")
        r = blob.get("recall_at_k")
        return (
            float(p) if p is not None else None,
            float(r) if r is not None else None,
        )
    except (TypeError, ValueError):
        return None, None


def _mean(vals: list[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 4) if vals else None


def _path_label_gasr(assess_summary: Any) -> str:
    """Compact ladder path string from GASR assess trace."""
    if not isinstance(assess_summary, dict):
        return "unknown"
    route = str(assess_summary.get("route") or "?")
    hops = assess_summary.get("hops_used")
    trace = assess_summary.get("trace") or []
    steps: list[str] = []
    for t in trace:
        if not isinstance(t, dict):
            continue
        phase = str(t.get("phase") or "")
        if phase == "rag":
            steps.append(
                "rag_ok" if t.get("sufficient") else "rag_miss"
            )
        elif phase == "structure_vs_context":
            need = str(t.get("need") or "?").upper()
            steps.append(f"need_{need}")
        elif phase == "graph":
            steps.append(
                "graph_ok" if t.get("sufficient") else "graph_miss"
            )
    path = "→".join(steps) if steps else "no_trace"
    return f"{path}⇒{route}(hops={hops})"


def _path_label_self_route(assess_summary: Any) -> str:
    if not isinstance(assess_summary, dict):
        return "unknown"
    route = str(assess_summary.get("route") or "?")
    suf = assess_summary.get("sufficient")
    conf = assess_summary.get("confidence") or "?"
    return f"sufficient={suf}({conf})⇒{route}"


def _aggregate_quality(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        if not r.get("ok"):
            continue
        by_mode.setdefault(str(r.get("mode")), []).append(r)

    mode_stats: dict[str, Any] = {}
    for mode, rows in sorted(by_mode.items()):
        tokens: list[float] = []
        walls: list[float] = []
        faith: list[float] = []
        ar: list[float] = []
        prec: list[float] = []
        rec: list[float] = []
        routes: Counter[str] = Counter()
        hops: list[int] = []
        for r in rows:
            s = r.get("summary") or {}
            q = r.get("quality") or {}
            if s.get("tokens_injected_est") is not None:
                tokens.append(float(s["tokens_injected_est"]))
            if isinstance(r.get("duration_ms"), int):
                walls.append(float(r["duration_ms"]))
            fs = _metric_score(q.get("faithfulness"))
            if fs is not None:
                faith.append(fs)
            a = _metric_score(q.get("answer_relevancy"))
            if a is not None:
                ar.append(a)
            p, rc = _pr_fields(q.get("precision_recall"))
            if p is not None:
                prec.append(p)
            if rc is not None:
                rec.append(rc)
            routes[str(s.get("route") or "?")] += 1
            if s.get("hops_used") is not None:
                try:
                    hops.append(int(s["hops_used"]))
                except (TypeError, ValueError):
                    pass
        mode_stats[mode] = {
            "n": len(rows),
            "tokens_mean": _mean(tokens),
            "tokens_sum": int(sum(tokens)) if tokens else None,
            "wall_ms_mean": _mean(walls),
            "faithfulness_mean": _mean(faith),
            "answer_relevancy_mean": _mean(ar),
            "precision_mean": _mean(prec),
            "recall_mean": _mean(rec),
            "route_counts": dict(routes),
            "hops_mean": _mean([float(h) for h in hops]),
        }

    gasr_paths: Counter[str] = Counter()
    self_paths: Counter[str] = Counter()
    for r in results:
        if not r.get("ok"):
            continue
        s = r.get("summary") or {}
        assess = s.get("assess_summary")
        if r.get("mode") == "GASR":
            gasr_paths[_path_label_gasr(assess)] += 1
        elif r.get("mode") == "SELF_ROUTE":
            self_paths[_path_label_self_route(assess)] += 1

    return {
        "cases_ok": sum(1 for r in results if r.get("ok")),
        "cases_failed": sum(1 for r in results if not r.get("ok")),
        "by_mode": mode_stats,
        "gasr_path_counts": dict(gasr_paths),
        "self_route_path_counts": dict(self_paths),
    }


def _markdown_quality_report(
    meta: dict[str, Any],
    results: list[dict[str, Any]],
    aggregates: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# Modes quality compare (RAG / LC / GASR / SELF_ROUTE)")
    lines.append("")
    lines.append(f"- **When (UTC):** `{meta.get('timestamp_utc')}`")
    lines.append(f"- **Generation model:** `{meta.get('generation_model_expected')}`")
    lines.append(f"- **Assessor model:** `{meta.get('assessor_model_expected')}`")
    lines.append(f"- **DeepEval judge:** `{meta.get('judge_model_expected')}`")
    lines.append(f"- **Cases:** {len(results)}")
    lines.append(
        f"- **ok / fail:** {aggregates.get('cases_ok')} / {aggregates.get('cases_failed')}"
    )
    lines.append(f"- **re_prune (GASR expand):** `graph_aware`")
    lines.append("")
    lines.append("## Mode cost + quality (means)")
    lines.append("")
    lines.append(
        "| Mode | n | tokens mean | tokens sum | faithfulness | "
        "answer_relevancy | precision | recall | wall_ms mean | routes |"
    )
    lines.append(
        "|------|---|-------------|------------|--------------|"
        "------------------|-----------|--------|--------------|--------|"
    )
    for mode, st in (aggregates.get("by_mode") or {}).items():
        lines.append(
            "| {mode} | {n} | {tm} | {ts} | {f} | {ar} | {p} | {r} | {w} | `{routes}` |".format(
                mode=mode,
                n=st.get("n"),
                tm=st.get("tokens_mean"),
                ts=st.get("tokens_sum"),
                f=st.get("faithfulness_mean"),
                ar=st.get("answer_relevancy_mean"),
                p=st.get("precision_mean"),
                r=st.get("recall_mean"),
                w=st.get("wall_ms_mean"),
                routes=json.dumps(st.get("route_counts") or {}),
            )
        )
    lines.append("")
    lines.append("### Notes")
    lines.append("")
    lines.append(
        "- **answer_relevancy** = DeepEval AnswerRelevancy (accuracy proxy; no labeled answers)."
    )
    lines.append(
        "- **faithfulness** = DeepEval Faithfulness vs that mode's *injected* chunks."
    )
    lines.append(
        "- **precision / recall** = file-level P/R@k on injected chunks vs gold `relevant_files`."
    )
    lines.append("")

    lines.append("## Path choice: GASR ladder vs SELF_ROUTE")
    lines.append("")
    lines.append("### SELF_ROUTE (binary)")
    lines.append("")
    lines.append(
        "`assess_sufficiency` → `sufficient=true` ⇒ **RAG**, else **LC** (no GRAPH, hops=0)."
    )
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(aggregates.get("self_route_path_counts") or {}, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("### GASR (ladder)")
    lines.append("")
    lines.append(
        "RAG assess → STRUCTURE|CONTEXT → expand? → graph-aware re-prune → "
        "post-expand assess → final **RAG|GRAPH|LC**."
    )
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(aggregates.get("gasr_path_counts") or {}, indent=2))
    lines.append("```")
    lines.append("")

    # Side-by-side per group
    by_group: dict[str, dict[str, dict[str, Any]]] = {}
    for r in results:
        g = str(r.get("compare_group") or "?")
        by_group.setdefault(g, {})[str(r.get("mode"))] = r

    lines.append("## Per-prompt comparison")
    lines.append("")
    for g in sorted(by_group.keys()):
        modes_map = by_group[g]
        sample = next(iter(modes_map.values()))
        lines.append(f"### `{g}`")
        lines.append("")
        lines.append(f"**Prompt:** {sample.get('prompt')}")
        gold = sample.get("relevant_files") or []
        lines.append(f"**Gold files:** `{gold}`")
        lines.append("")
        lines.append(
            "| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |"
        )
        lines.append(
            "|------|----|-------|------|--------|-------|---------|---|---|---------|------|"
        )
        for mode in MODES:
            r = modes_map.get(mode)
            if not r:
                continue
            s = r.get("summary") or {}
            q = r.get("quality") or {}
            p, rc = _pr_fields(q.get("precision_recall"))
            if mode == "GASR":
                path = _path_label_gasr(s.get("assess_summary"))
            elif mode == "SELF_ROUTE":
                path = _path_label_self_route(s.get("assess_summary"))
            else:
                path = str(s.get("route") or "")
            lines.append(
                "| {mode} | {ok} | `{route}` | {hops} | {tok} | {f} | {ar} | {p} | {r} | {w} | `{path}` |".format(
                    mode=mode,
                    ok=r.get("ok"),
                    route=s.get("route"),
                    hops=s.get("hops_used"),
                    tok=s.get("tokens_injected_est"),
                    f=_metric_score(q.get("faithfulness")),
                    ar=_metric_score(q.get("answer_relevancy")),
                    p=p,
                    r=rc,
                    w=r.get("duration_ms"),
                    path=path.replace("|", "/"),
                )
            )
        lines.append("")
        # Short path reasons for GASR / SELF_ROUTE
        for mode in ("GASR", "SELF_ROUTE"):
            r = modes_map.get(mode)
            if not r or not r.get("ok"):
                continue
            assess = (r.get("summary") or {}).get("assess_summary") or {}
            lines.append(f"**{mode} assess:**")
            lines.append("")
            lines.append("```json")
            # Compact: drop huge fields
            if mode == "GASR":
                compact = {
                    "route": assess.get("route"),
                    "hops_used": assess.get("hops_used"),
                    "re_prune_kind": assess.get("re_prune_kind"),
                    "assessor_model": assess.get("assessor_model"),
                    "trace": assess.get("trace"),
                }
            else:
                compact = {
                    "route": assess.get("route"),
                    "sufficient": assess.get("sufficient"),
                    "confidence": assess.get("confidence"),
                    "reason": assess.get("reason"),
                    "assessor_model": assess.get("assessor_model"),
                }
            lines.append(json.dumps(compact, indent=2, default=str)[:4000])
            lines.append("```")
            lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RAG/LC/GASR/SELF_ROUTE + DeepEval quality → results/live_modes_quality/"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument(
        "--out-dir",
        default=str(_REPO / "results" / "live_modes_quality"),
    )
    parser.add_argument("--groups", nargs="*", help="Subset of group ids")
    parser.add_argument(
        "--fast",
        action="store_true",
        help=(
            f"Use the 6-group fast subset ({', '.join(FAST_GROUPS)}) and "
            "DeepEval only on GASR+SELF_ROUTE (RAG/LC: tokens+P/R only)."
        ),
    )
    parser.add_argument(
        "--precision",
        action="store_true",
        help=(
            f"Precision suite: {len(PRECISION_GROUPS)} groups × GASR/SELF_ROUTE/RAG, "
            "tokens+P/R only (no DeepEval). For IR precision compare."
        ),
    )
    parser.add_argument("--modes", nargs="*", default=None)
    parser.add_argument(
        "--deepeval-modes",
        nargs="*",
        default=None,
        help="Modes that run Faithfulness/AnswerRelevancy (default: all, or GASR SELF_ROUTE with --fast).",
    )
    parser.add_argument("--gen-model-label", default="qwen3.5:9b")
    parser.add_argument("--assessor-model-label", default="qwen3.5:9b")
    parser.add_argument("--judge-model-label", default="qwen3.5:9b")
    parser.add_argument(
        "--skip-deepeval",
        action="store_true",
        help="Only tokens + P/R (skip Faithfulness / AnswerRelevancy)",
    )
    parser.add_argument(
        "--append-json",
        default=None,
        help=(
            "Merge this run's results into an existing modes_quality_run_*.json "
            "(by case id; new wins on conflict), then rewrite aggregates + LATEST."
        ),
    )
    args = parser.parse_args()

    # Align judge env with labels when unset
    os.environ["EVAL_JUDGE_MODEL"] = (
        args.judge_model_label or os.environ.get("EVAL_JUDGE_MODEL") or "qwen3.5:9b"
    )
    base = args.base_url.rstrip("/")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.resolve().name in ("live_gasr", "live_modes_compare"):
        print(
            f"Refusing to write into {out_dir.name}/. Use results/live_modes_quality/.",
            file=sys.stderr,
        )
        return 2

    modes = [m.upper() for m in (args.modes or MODES)]
    if args.precision and not args.modes:
        modes = ["RAG", "GASR", "SELF_ROUTE"]
    for m in modes:
        if m not in MODES:
            print(f"Unknown mode {m}; allowed: {MODES}", file=sys.stderr)
            return 2

    if args.deepeval_modes is not None:
        deepeval_modes = {m.upper() for m in args.deepeval_modes}
    elif args.precision or args.skip_deepeval:
        deepeval_modes = set()
    elif args.fast:
        deepeval_modes = {"GASR", "SELF_ROUTE"}
    else:
        deepeval_modes = set(modes)

    groups = _quality_groups()
    if args.groups:
        wanted = set(args.groups)
        groups = [g for g in groups if g["group"] in wanted]
    elif args.precision:
        wanted = set(PRECISION_GROUPS)
        groups = [g for g in groups if g["group"] in wanted]
    elif args.fast:
        wanted = set(FAST_GROUPS)
        groups = [g for g in groups if g["group"] in wanted]
    cases = _expand_cases(groups, modes)

    hardware = detect_hardware()
    try:
        root = _get(f"{base}/", timeout=10)
        print(f"API OK: {root}", flush=True)
    except Exception as exc:
        print(f"API not reachable at {base}: {exc}", file=sys.stderr)
        return 2

    try:
        tags = _get(OLLAMA_TAGS_URL, timeout=10)
        installed = list_ollama_models(tags)
        print(f"Ollama OK: models={installed}", flush=True)
    except Exception as exc:
        print(f"Ollama not reachable: {exc}", file=sys.stderr)
        return 2

    print(
        f"Cases: {len(cases)} | groups={[g['group'] for g in groups]} | "
        f"skip_deepeval={args.skip_deepeval} | deepeval_modes={sorted(deepeval_modes)} | "
        f"judge={os.environ.get('EVAL_JUDGE_MODEL')}",
        flush=True,
    )

    results: list[dict[str, Any]] = []
    for case in cases:
        print(f"\n=== {case['id']} mode={case['mode']} ===", flush=True)
        print(case["prompt"][:160], flush=True)
        t0 = time.perf_counter()
        entry: dict[str, Any] = {
            "id": case["id"],
            "mode": case["mode"],
            "prompt": case["prompt"],
            "intent": case.get("intent"),
            "expect_note": case.get("expect_note"),
            "thesis_tag": case.get("thesis_tag"),
            "suite": case.get("suite"),
            "compare_group": case.get("compare_group"),
            "relevant_files": case.get("relevant_files"),
        }
        body = {"prompt": case["prompt"], "mode": case["mode"]}
        try:
            resp = _post_json(f"{base}/generate", body, timeout=args.timeout)
            ms = int((time.perf_counter() - t0) * 1000)
            summary = _summarize(resp)
            pipeline = resp.get("pipeline") or {}
            chunks = _inject_chunks(pipeline if isinstance(pipeline, dict) else {})
            answer = summary.get("answer_full") or ""
            print(
                f"OK route={summary.get('route')} hops={summary.get('hops_used')} "
                f"tokens={summary.get('tokens_injected_est')} chunks={len(chunks)} ms={ms}",
                flush=True,
            )
            t1 = time.perf_counter()
            mode_u = str(case["mode"]).upper()
            skip_de = bool(args.skip_deepeval) or (mode_u not in deepeval_modes)
            skip_reason = (
                "--skip-deepeval"
                if args.skip_deepeval
                else f"deepeval only for {sorted(deepeval_modes)}; mode={mode_u}"
            )
            quality = _score_quality(
                case["prompt"],
                answer,
                chunks,
                list(case.get("relevant_files") or []),
                skip_deepeval=skip_de,
                skip_deepeval_reason=skip_reason,
            )
            q_ms = int((time.perf_counter() - t1) * 1000)
            quality["duration_ms"] = q_ms
            quality["deepeval_ran"] = not skip_de
            print(
                f"  quality deepeval={not skip_de} "
                f"faith={_metric_score(quality.get('faithfulness'))} "
                f"ar={_metric_score(quality.get('answer_relevancy'))} "
                f"P/R={_pr_fields(quality.get('precision_recall'))} q_ms={q_ms}",
                flush=True,
            )
            for key in ("faithfulness", "answer_relevancy"):
                blob = quality.get(key) or {}
                if isinstance(blob, dict) and blob.get("error"):
                    print(f"  {key}_err={str(blob['error'])[:240]}", flush=True)
            entry.update(
                {
                    "ok": True,
                    "duration_ms": ms,
                    "summary": summary,
                    "quality": quality,
                    "inject_chunk_count": len(chunks),
                    # Keep assess/expand for path analysis; omit full inject prompt to shrink JSON.
                    "raw_pipeline": {
                        "route": pipeline.get("route")
                        if isinstance(pipeline, dict)
                        else None,
                        "hops_used": pipeline.get("hops_used")
                        if isinstance(pipeline, dict)
                        else None,
                        "tokens_injected_est": pipeline.get("tokens_injected_est")
                        if isinstance(pipeline, dict)
                        else None,
                        "stages": {
                            k: v
                            for k, v in (
                                (pipeline.get("stages") or {}).items()
                                if isinstance(pipeline, dict)
                                else []
                            )
                            if k in ("assess", "graph_expand", "re_prune", "prune")
                        },
                        "inject_chunks": chunks,
                    },
                }
            )
        except urllib.error.HTTPError as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            entry.update(
                {
                    "ok": False,
                    "duration_ms": ms,
                    "error": f"HTTP {exc.code}: {err_body[:2000]}",
                }
            )
            print(entry["error"], file=sys.stderr, flush=True)
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            entry.update({"ok": False, "duration_ms": ms, "error": str(exc)})
            print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        results.append(entry)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prior_path: Path | None = None
    prior_meta: dict[str, Any] = {}
    if args.append_json:
        prior_path = Path(args.append_json)
        if not prior_path.is_file():
            print(f"--append-json not found: {prior_path}", file=sys.stderr)
            return 2
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        prior_meta = dict(prior.get("meta") or {})
        by_id: dict[str, dict[str, Any]] = {}
        for r in prior.get("results") or []:
            rid = str(r.get("id") or "")
            if rid:
                by_id[rid] = r
        for r in results:
            rid = str(r.get("id") or "")
            if rid:
                by_id[rid] = r
        results = list(by_id.values())
        results.sort(key=lambda r: str(r.get("id") or ""))
        print(
            f"Appended/merged into prior {prior_path.name}: "
            f"{len(by_id)} cases total ({len(cases)} new this run)",
            flush=True,
        )

    aggregates = _aggregate_quality(results)
    merged_groups = sorted(
        {
            str(r.get("compare_group") or "")
            for r in results
            if r.get("compare_group")
        }
        | {g["group"] for g in groups}
    )
    merged_modes = sorted({str(r.get("mode") or "") for r in results if r.get("mode")})
    run_meta = {
        "timestamp_utc": stamp,
        "base_url": base,
        "timeout_sec": args.timeout,
        "suite": "modes_quality",
        "case_ids": [r["id"] for r in results],
        "hardware": hardware,
        "generation_model_expected": args.gen_model_label,
        "assessor_model_expected": args.assessor_model_label,
        "judge_model_expected": args.judge_model_label
        or os.environ.get("EVAL_JUDGE_MODEL"),
        "ollama_installed": installed,
        "modes": merged_modes or modes,
        "skip_deepeval": args.skip_deepeval,
        "deepeval_modes": sorted(deepeval_modes),
        "fast": bool(args.fast),
        "groups": merged_groups,
        "schema_version": "live_modes_quality.v1",
        "re_prune_kind": "graph_aware",
        "note": "Does not write to results/live_gasr/ or live_modes_compare/",
        "appended_from": prior_path.name if prior_path else None,
        "prior_timestamp_utc": prior_meta.get("timestamp_utc"),
        "this_run_groups": [g["group"] for g in groups],
        "this_run_case_ids": [c["id"] for c in cases],
    }
    json_path = out_dir / f"modes_quality_run_{stamp}.json"
    md_path = out_dir / f"modes_quality_run_{stamp}.md"
    payload = {"meta": run_meta, "aggregates": aggregates, "results": results}
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_report = _markdown_quality_report(run_meta, results, aggregates)
    md_path.write_text(md_report, encoding="utf-8")
    (out_dir / "LATEST.md").write_text(
        f"Latest modes-quality run: `{md_path.name}`\n\n" + md_report,
        encoding="utf-8",
    )
    with (out_dir / "INDEX.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp_utc": stamp,
                    "json": json_path.name,
                    "md": md_path.name,
                    "cases": len(results),
                    "aggregates": {
                        "ok": aggregates.get("cases_ok"),
                        "fail": aggregates.get("cases_failed"),
                        "by_mode": {
                            m: {
                                "tokens_mean": st.get("tokens_mean"),
                                "faithfulness_mean": st.get("faithfulness_mean"),
                                "answer_relevancy_mean": st.get(
                                    "answer_relevancy_mean"
                                ),
                                "precision_mean": st.get("precision_mean"),
                                "recall_mean": st.get("recall_mean"),
                                "route_counts": st.get("route_counts"),
                            }
                            for m, st in (aggregates.get("by_mode") or {}).items()
                        },
                    },
                },
                default=str,
            )
            + "\n"
        )

    print(f"\nWrote {json_path}", flush=True)
    print(f"Wrote {md_path}", flush=True)
    print(f"Wrote {out_dir / 'LATEST.md'}", flush=True)
    print(
        f"Aggregates by_mode: {json.dumps(aggregates.get('by_mode'), default=str)[:2000]}",
        flush=True,
    )
    return 0 if aggregates.get("cases_failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
