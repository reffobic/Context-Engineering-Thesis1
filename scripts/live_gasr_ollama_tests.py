#!/usr/bin/env python3
"""
Live GASR tests — hit running API + Ollama (NOT unit tests).

Writes structured JSON + Markdown under results/live_gasr/.

Prerequisites:
  - ollama serve with a pulled model (see --recommend-model)
  - API process started with OLLAMA_MODEL=<same model>
  - uvicorn on BASE_URL (default http://127.0.0.1:8000)
  - repo indexed + Graphify graph available to the server
  - Do NOT set GASR_OFFLINE=1

Usage:
  python -u scripts/live_gasr_ollama_tests.py --recommend-model
  python -u scripts/live_gasr_ollama_tests.py --timeout 600
  python -u scripts/live_gasr_ollama_tests.py --cases L1_simple_ragish L2_cross_file_callees
  python -u scripts/live_gasr_ollama_tests.py --suite core
  python -u scripts/live_gasr_ollama_tests.py --suite full --timeout 900
  python -u scripts/live_gasr_ollama_tests.py --suite compare --timeout 900
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"

# Preference order for "best practical" on 4GB VRAM + ~32GB RAM (this PC profile).
# Larger / higher quality first; script filters to what is actually installed.
MODEL_PREFERENCE: list[str] = [
    "qwen3.5:9b",       # recommended: quality + runnable with RAM offload
    "qwen3.6:latest",   # strongest installed; slower on 4GB VRAM
    "qwen3.5:4b",       # fast, fits GPU
    "llama3.2:3b",      # legacy default
]

# Suites: core = thesis ladder spots; full = + mode baselines
SUITE_CORE = {
    "L1_simple_ragish",
    "L2_cross_file_callees",
    "L3_dependency_neighbors",
    "L4_same_file_context",
    "L5_ablation_self_route",
}
SUITE_FULL = SUITE_CORE | {
    "L6_baseline_rag",
    "L7_baseline_lc",
    "L8_multi_hop_imports",
}

# Same prompt × RAG / LC / GASR — compare tokens_injected_est + wall/stage speed.
COMPARE_MODES: list[str] = ["RAG", "LC", "GASR"]

COMPARE_GROUPS: list[dict[str, Any]] = [
    {
        "group": "C1_simple_return",
        "prompt": "What does create_app in superset/app.py return?",
        "intent": "Simple return-value Q — RAG should use fewer tokens than LC.",
        "expect_note": "Compare tokens + total_ms across RAG vs LC vs GASR.",
        "thesis_tag": "compare_simple",
    },
    {
        "group": "C2_cross_file_callees",
        "prompt": (
            "In superset/app.py, what does create_app call related to feature flags "
            "or encrypt init_app helpers? Name the callee symbols only."
        ),
        "intent": "STRUCTURE / neighbor question — GASR may expand graph; watch hops + tokens.",
        "expect_note": "GASR may add graph_expand/re_prune time; LC usually highest tokens.",
        "thesis_tag": "compare_structure",
    },
    {
        "group": "C3_same_file_context",
        "prompt": (
            "In superset/app.py create_app, what happens in the surrounding lines of the "
            "function body for app factory setup? Quote nearby setup steps from the same file."
        ),
        "intent": "Same-file CONTEXT pressure — LC injects parent_context (more tokens).",
        "expect_note": "LC tokens >> RAG; GASR may escalate to LC or stay RAG.",
        "thesis_tag": "compare_context",
    },
    {
        "group": "C4_config_deps",
        "prompt": (
            "Which functions does SupersetApp or create_app depend on for configuration "
            "loading across files? List neighbor symbols, not whole files."
        ),
        "intent": "Cross-file dependency — graph path vs pruned RAG vs full LC parents.",
        "expect_note": "Report hops_used for GASR; tokens and stage timings for all three.",
        "thesis_tag": "compare_deps",
    },
]


def _expand_compare_cases() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in COMPARE_GROUPS:
        for mode in COMPARE_MODES:
            out.append(
                {
                    "id": f"{g['group']}_{mode}",
                    "mode": mode,
                    "suite": "compare",
                    "compare_group": g["group"],
                    "prompt": g["prompt"],
                    "intent": g["intent"],
                    "expect_note": g["expect_note"],
                    "thesis_tag": g["thesis_tag"],
                }
            )
    return out


COMPARE_CASES: list[dict[str, Any]] = _expand_compare_cases()

CASES: list[dict[str, Any]] = [
    {
        "id": "L1_simple_ragish",
        "mode": "GASR",
        "suite": "core",
        "prompt": "What does create_app in superset/app.py return?",
        "intent": "Likely RAG or LC; checks judge + answer path end-to-end.",
        "expect_note": "Often RAG or LC; hops may be 0.",
        "thesis_tag": "ladder_simple",
    },
    {
        "id": "L2_cross_file_callees",
        "mode": "GASR",
        "suite": "core",
        "prompt": (
            "In superset/app.py, what does create_app call related to feature flags "
            "or encrypt init_app helpers? Name the callee symbols only."
        ),
        "intent": "Designed to need STRUCTURE / graph hop (GRAPH or LC after expand).",
        "expect_note": "Best chance of hops_used>=1; route GRAPH if judge is satisfied after expand.",
        "thesis_tag": "ladder_structure",
    },
    {
        "id": "L3_dependency_neighbors",
        "mode": "GASR",
        "suite": "core",
        "prompt": (
            "Which functions does SupersetApp or create_app depend on for configuration "
            "loading across files? List neighbor symbols, not whole files."
        ),
        "intent": "Cross-file dependency question → STRUCTURE expand pressure.",
        "expect_note": "GRAPH or LC with hops; not a pure snippet answer.",
        "thesis_tag": "ladder_structure",
    },
    {
        "id": "L4_same_file_context",
        "mode": "GASR",
        "suite": "core",
        "prompt": (
            "In superset/app.py create_app, what happens in the surrounding lines of the "
            "function body for app factory setup? Quote nearby setup steps from the same file."
        ),
        "intent": "Same-file CONTEXT pressure → prefer LC over graph.",
        "expect_note": "Often LC with hops_used=0 (CONTEXT need).",
        "thesis_tag": "ladder_context",
    },
    {
        "id": "L5_ablation_self_route",
        "mode": "SELF_ROUTE",
        "suite": "core",
        "prompt": "What does create_app in superset/app.py return?",
        "intent": "Binary Self-Route baseline (RAG vs LC only) with same prompt as L1.",
        "expect_note": "route RAG or LC; no GRAPH; hops_used absent or 0.",
        "thesis_tag": "ablation_self_route",
    },
    {
        "id": "L6_baseline_rag",
        "mode": "RAG",
        "suite": "full",
        "prompt": "What does create_app in superset/app.py return?",
        "intent": "Forced RAG path (no sufficiency ladder) — same prompt as L1.",
        "expect_note": "route RAG; hops_used absent or 0.",
        "thesis_tag": "baseline_rag",
    },
    {
        "id": "L7_baseline_lc",
        "mode": "LC",
        "suite": "full",
        "prompt": "What does create_app in superset/app.py return?",
        "intent": "Forced long-context path — same prompt as L1.",
        "expect_note": "route LC; hops_used absent or 0.",
        "thesis_tag": "baseline_lc",
    },
    {
        "id": "L8_multi_hop_imports",
        "mode": "GASR",
        "suite": "full",
        "prompt": (
            "Trace how create_app in superset/app.py reaches feature-flag or config helpers "
            "across imports: list the chain of callee / neighbor symbols across files."
        ),
        "intent": "Multi-hop STRUCTURE pressure (up to hard_cap=2).",
        "expect_note": "Often hops_used>=1; GRAPH if satisfied after expand else LC.",
        "thesis_tag": "ladder_multihop",
    },
]


def _post_json(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _get(url: str, timeout: float = 10.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _run_cmd(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (out.stdout or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def detect_hardware() -> dict[str, Any]:
    """Best-effort local hardware snapshot for thesis meta."""
    hw: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "hostname": platform.node(),
    }
    # RAM (Windows)
    if sys.platform.startswith("win"):
        ps = _run_cmd(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
            ]
        )
        if ps.isdigit():
            hw["ram_bytes"] = int(ps)
            hw["ram_gb"] = round(int(ps) / (1024**3), 1)
    # GPU via nvidia-smi
    if shutil.which("nvidia-smi"):
        gpu = _run_cmd(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader",
            ]
        )
        if gpu:
            lines = [ln.strip() for ln in gpu.splitlines() if ln.strip()]
            gpus = []
            for ln in lines:
                parts = [p.strip() for p in ln.split(",")]
                if len(parts) >= 2:
                    gpus.append({"name": parts[0], "memory_total": parts[1]})
                else:
                    gpus.append({"raw": ln})
            hw["gpus"] = gpus
    return hw


def list_ollama_models(tags: Optional[dict[str, Any]] = None) -> list[str]:
    if tags is None:
        tags = _get(OLLAMA_TAGS_URL, timeout=10)
    names: list[str] = []
    for m in tags.get("models") or []:
        name = m.get("name")
        if name:
            names.append(str(name))
    return names


def _normalize_model_name(name: str) -> str:
    return name.strip().lower()


def resolve_best_model(
    installed: list[str],
    *,
    preferred: Optional[str] = None,
) -> dict[str, Any]:
    """
    Pick best installed model.

    - If preferred is set and installed → use it.
    - Else walk MODEL_PREFERENCE.
    - Else fall back to first installed.
    """
    installed_norm = {_normalize_model_name(n): n for n in installed}
    recommendation = {
        "recommended_for_this_pc": "qwen3.5:9b",
        "rationale": (
            "RTX A2000 4GB VRAM + ~32GB RAM: qwen3.5:9b gives the best quality that "
            "still runs with GPU+RAM offload. Use qwen3.6:latest for max quality (slower). "
            "Use qwen3.5:4b for fastest GPU-resident runs."
        ),
        "preference_order": list(MODEL_PREFERENCE),
        "installed": installed,
    }

    if preferred:
        key = _normalize_model_name(preferred)
        if key in installed_norm:
            recommendation["selected"] = installed_norm[key]
            recommendation["selection_reason"] = f"user --model {preferred}"
            return recommendation
        # allow prefix match e.g. qwen3.5:9b vs qwen3.5:9b-instruct-q4
        for inst_key, inst_name in installed_norm.items():
            if inst_key.startswith(key) or key.startswith(inst_key.split(":")[0]):
                if preferred.split(":")[0].lower() in inst_key:
                    recommendation["selected"] = inst_name
                    recommendation["selection_reason"] = (
                        f"user --model {preferred} (fuzzy match → {inst_name})"
                    )
                    return recommendation
        recommendation["selected"] = None
        recommendation["selection_reason"] = (
            f"--model {preferred} not installed; pull it or pick from {installed}"
        )
        return recommendation

    for cand in MODEL_PREFERENCE:
        key = _normalize_model_name(cand)
        if key in installed_norm:
            recommendation["selected"] = installed_norm[key]
            recommendation["selection_reason"] = f"preference order hit: {cand}"
            return recommendation
        # tagless match: qwen3.5:9b vs qwen3.5:9b-xxx
        base = key
        for inst_key, inst_name in installed_norm.items():
            if inst_key == base or inst_key.startswith(base):
                recommendation["selected"] = inst_name
                recommendation["selection_reason"] = f"preference order hit: {cand}"
                return recommendation

    if installed:
        recommendation["selected"] = installed[0]
        recommendation["selection_reason"] = "fallback: first installed model"
    else:
        recommendation["selected"] = None
        recommendation["selection_reason"] = "no models installed"
    return recommendation


def _stage_timings(stages: dict[str, Any]) -> dict[str, Optional[int]]:
    """Pull duration_ms per pipeline stage (retrieve, prune, assess, …)."""
    keys = (
        "retrieve",
        "prune",
        "assess",
        "graph_expand",
        "re_prune",
        "inject",
        "generate",
    )
    out: dict[str, Optional[int]] = {}
    for k in keys:
        st = stages.get(k)
        if isinstance(st, dict) and "duration_ms" in st:
            try:
                out[k] = int(st["duration_ms"])
            except (TypeError, ValueError):
                out[k] = None
        else:
            out[k] = None
    return out


def _summarize(resp: dict[str, Any]) -> dict[str, Any]:
    pipeline = resp.get("pipeline") or {}
    stages = pipeline.get("stages") or {}
    assess = stages.get("assess") or {}
    expand = stages.get("graph_expand") or {}
    re_prune = stages.get("re_prune") or {}
    answer = (
        resp.get("output")
        or resp.get("answer")
        or resp.get("response")
        or resp.get("text")
        or ""
    )
    if isinstance(answer, dict):
        answer = answer.get("text") or json.dumps(answer)[:2000]
    answer_s = str(answer)
    timings = _stage_timings(stages if isinstance(stages, dict) else {})
    pipeline_stages_ms = sum(v for v in timings.values() if isinstance(v, int))
    tokens = resp.get("tokens_injected_est")
    if tokens is None:
        tokens = pipeline.get("tokens_injected_est")
    return {
        "route": pipeline.get("route") or resp.get("route"),
        "hops_used": pipeline.get("hops_used"),
        "tokens_injected_est": tokens,
        "stage_order": pipeline.get("stage_order"),
        "stage_ms": timings,
        "pipeline_stages_ms": pipeline_stages_ms,
        "assess_summary": assess.get("summary") if isinstance(assess, dict) else assess,
        "expand_summary": expand.get("summary") if isinstance(expand, dict) else expand,
        "re_prune_summary": re_prune.get("summary") if isinstance(re_prune, dict) else re_prune,
        "answer_chars": len(answer_s),
        "answer_preview": (answer_s[:1200] + ("…" if len(answer_s) > 1200 else "")),
        "answer_full": answer_s,
    }


def _mean(nums: list[float]) -> Optional[float]:
    return round(sum(nums) / len(nums), 1) if nums else None


def _mode_cost_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per forced mode: mean tokens, wall time, generate stage, hops."""
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        if not r.get("ok"):
            continue
        by_mode.setdefault(str(r.get("mode")), []).append(r)

    stats: dict[str, Any] = {}
    for mode, rows in sorted(by_mode.items()):
        tokens = [
            float(s["tokens_injected_est"])
            for r in rows
            for s in [r.get("summary") or {}]
            if s.get("tokens_injected_est") is not None
        ]
        walls = [float(r["duration_ms"]) for r in rows if isinstance(r.get("duration_ms"), int)]
        gens = [
            float(s["stage_ms"]["generate"])
            for r in rows
            for s in [r.get("summary") or {}]
            if isinstance((s.get("stage_ms") or {}).get("generate"), int)
        ]
        retrieves = [
            float(s["stage_ms"]["retrieve"])
            for r in rows
            for s in [r.get("summary") or {}]
            if isinstance((s.get("stage_ms") or {}).get("retrieve"), int)
        ]
        expands = [
            float(s["stage_ms"]["graph_expand"])
            for r in rows
            for s in [r.get("summary") or {}]
            if isinstance((s.get("stage_ms") or {}).get("graph_expand"), int)
        ]
        hops = [
            int(s["hops_used"])
            for r in rows
            for s in [r.get("summary") or {}]
            if s.get("hops_used") is not None
        ]
        stats[mode] = {
            "n": len(rows),
            "tokens_injected_est_mean": _mean(tokens),
            "tokens_injected_est_sum": int(sum(tokens)) if tokens else None,
            "wall_ms_mean": _mean(walls),
            "wall_ms_sum": int(sum(walls)) if walls else None,
            "generate_ms_mean": _mean(gens),
            "retrieve_ms_mean": _mean(retrieves),
            "graph_expand_ms_mean": _mean(expands) if expands else None,
            "hops_used_mean": _mean([float(h) for h in hops]) if hops else None,
            "ms_per_token_mean": (
                round(_mean(walls) / _mean(tokens), 3)
                if tokens and walls and _mean(tokens)
                else None
            ),
        }
    return stats


def _prompt_comparison(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per compare_group with RAG/LC/GASR side-by-side metrics."""
    groups: dict[str, dict[str, Any]] = {}
    for r in results:
        g = r.get("compare_group")
        if not g:
            continue
        slot = groups.setdefault(
            g,
            {"compare_group": g, "prompt": r.get("prompt"), "by_mode": {}},
        )
        s = r.get("summary") or {}
        slot["by_mode"][str(r.get("mode"))] = {
            "ok": r.get("ok"),
            "case_id": r.get("id"),
            "route": s.get("route"),
            "hops_used": s.get("hops_used"),
            "tokens_injected_est": s.get("tokens_injected_est"),
            "wall_ms": r.get("duration_ms"),
            "retrieve_ms": (s.get("stage_ms") or {}).get("retrieve"),
            "assess_ms": (s.get("stage_ms") or {}).get("assess"),
            "graph_expand_ms": (s.get("stage_ms") or {}).get("graph_expand"),
            "re_prune_ms": (s.get("stage_ms") or {}).get("re_prune"),
            "inject_ms": (s.get("stage_ms") or {}).get("inject"),
            "generate_ms": (s.get("stage_ms") or {}).get("generate"),
            "pipeline_stages_ms": s.get("pipeline_stages_ms"),
            "answer_chars": s.get("answer_chars"),
            "error": r.get("error"),
        }
    # Relative deltas vs RAG when present
    rows: list[dict[str, Any]] = []
    for g, slot in groups.items():
        by = slot["by_mode"]
        rag = by.get("RAG") or {}
        rag_tok = rag.get("tokens_injected_est")
        rag_ms = rag.get("wall_ms")
        deltas: dict[str, Any] = {}
        for mode, m in by.items():
            if mode == "RAG":
                continue
            d: dict[str, Any] = {}
            if rag_tok is not None and m.get("tokens_injected_est") is not None:
                d["tokens_delta_vs_rag"] = int(m["tokens_injected_est"]) - int(rag_tok)
                if rag_tok:
                    d["tokens_ratio_vs_rag"] = round(
                        float(m["tokens_injected_est"]) / float(rag_tok), 3
                    )
            if rag_ms is not None and m.get("wall_ms") is not None:
                d["wall_ms_delta_vs_rag"] = int(m["wall_ms"]) - int(rag_ms)
                if rag_ms:
                    d["wall_ms_ratio_vs_rag"] = round(float(m["wall_ms"]) / float(rag_ms), 3)
            deltas[mode] = d
        rows.append({**slot, "deltas_vs_rag": deltas})
    rows.sort(key=lambda x: str(x.get("compare_group")))
    return rows


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    routes = Counter()
    hops = Counter()
    ok_n = 0
    fail_n = 0
    durations: list[int] = []
    by_mode: dict[str, Counter] = {}
    for r in results:
        if r.get("ok"):
            ok_n += 1
        else:
            fail_n += 1
        s = r.get("summary") or {}
        route = s.get("route") or ("ERROR" if not r.get("ok") else "UNKNOWN")
        routes[str(route)] += 1
        h = s.get("hops_used")
        hops[str(h)] += 1
        if isinstance(r.get("duration_ms"), int):
            durations.append(r["duration_ms"])
        mode = str(r.get("mode") or "?")
        by_mode.setdefault(mode, Counter())[str(route)] += 1

    out: dict[str, Any] = {
        "cases_total": len(results),
        "cases_ok": ok_n,
        "cases_failed": fail_n,
        "route_counts": dict(routes),
        "hops_used_counts": dict(hops),
        "route_counts_by_mode": {m: dict(c) for m, c in by_mode.items()},
        "duration_ms": {
            "min": min(durations) if durations else None,
            "max": max(durations) if durations else None,
            "mean": int(sum(durations) / len(durations)) if durations else None,
            "total": sum(durations) if durations else None,
        },
        "cost_by_mode": _mode_cost_stats(results),
    }
    prompt_cmp = _prompt_comparison(results)
    if prompt_cmp:
        out["prompt_comparison"] = prompt_cmp
    return out


def _markdown_comparison_tables(aggregates: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    cost = aggregates.get("cost_by_mode") or {}
    if cost:
        lines.extend(
            [
                "## Mode cost summary (tokens + speed)",
                "",
                "| Mode | n | tokens mean | tokens sum | wall_ms mean | generate_ms mean | retrieve_ms mean | graph_expand_ms mean | hops mean | ms/token |",
                "|------|---|-------------|------------|--------------|------------------|------------------|----------------------|-----------|----------|",
            ]
        )
        for mode, s in cost.items():
            lines.append(
                "| {mode} | {n} | {tok} | {toks} | {wall} | {gen} | {ret} | {exp} | {hops} | {mpt} |".format(
                    mode=mode,
                    n=s.get("n"),
                    tok=s.get("tokens_injected_est_mean"),
                    toks=s.get("tokens_injected_est_sum"),
                    wall=s.get("wall_ms_mean"),
                    gen=s.get("generate_ms_mean"),
                    ret=s.get("retrieve_ms_mean"),
                    exp=s.get("graph_expand_ms_mean"),
                    hops=s.get("hops_used_mean"),
                    mpt=s.get("ms_per_token_mean"),
                )
            )
        lines.append("")

    prompt_cmp = aggregates.get("prompt_comparison") or []
    if prompt_cmp:
        lines.extend(
            [
                "## Prompt comparison (RAG vs LC vs GASR)",
                "",
                "Each group runs the **same prompt** under forced `RAG`, `LC`, and `GASR`.",
                "",
            ]
        )
        for row in prompt_cmp:
            lines.extend(
                [
                    f"### `{row.get('compare_group')}`",
                    "",
                    f"**Prompt:** {row.get('prompt')}",
                    "",
                    "| Mode | ok | route | hops | tokens | wall_ms | retrieve_ms | assess_ms | graph_expand_ms | generate_ms | Δtokens vs RAG | Δwall_ms vs RAG |",
                    "|------|----|-------|------|--------|---------|-------------|-----------|-----------------|-------------|----------------|-----------------|",
                ]
            )
            deltas = row.get("deltas_vs_rag") or {}
            for mode in COMPARE_MODES:
                m = (row.get("by_mode") or {}).get(mode) or {}
                d = deltas.get(mode) or {}
                lines.append(
                    "| {mode} | {ok} | `{route}` | {hops} | {tok} | {wall} | {ret} | {assess} | {gexp} | {gen} | {dt} | {dw} |".format(
                        mode=mode,
                        ok=m.get("ok"),
                        route=m.get("route"),
                        hops=m.get("hops_used"),
                        tok=m.get("tokens_injected_est"),
                        wall=m.get("wall_ms"),
                        ret=m.get("retrieve_ms"),
                        assess=m.get("assess_ms"),
                        gexp=m.get("graph_expand_ms"),
                        gen=m.get("generate_ms"),
                        dt=d.get("tokens_delta_vs_rag", "—"),
                        dw=d.get("wall_ms_delta_vs_rag", "—"),
                    )
                )
            lines.append("")
    return lines


def _markdown_report(
    run_meta: dict[str, Any],
    results: list[dict[str, Any]],
    aggregates: dict[str, Any],
) -> str:
    model = (run_meta.get("model") or {}).get("selected")
    lines = [
        "# Live GASR + Ollama test run",
        "",
        f"- **When (UTC):** {run_meta['timestamp_utc']}",
        f"- **Base URL:** `{run_meta['base_url']}`",
        f"- **Timeout/case:** {run_meta['timeout_sec']}s",
        f"- **Suite:** `{run_meta.get('suite', 'custom')}`",
        f"- **Ollama model (selected):** `{model}`",
        f"- **Model selection:** {(run_meta.get('model') or {}).get('selection_reason', '')}",
        f"- **Cases:** {len(results)}",
        "",
        "## Hardware",
        "",
        "```json",
        json.dumps(run_meta.get("hardware") or {}, indent=2, default=str)[:2000],
        "```",
        "",
        "## Aggregates",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| ok / fail | {aggregates.get('cases_ok')} / {aggregates.get('cases_failed')} |",
        f"| route_counts | `{json.dumps(aggregates.get('route_counts'))}` |",
        f"| hops_used_counts | `{json.dumps(aggregates.get('hops_used_counts'))}` |",
        f"| duration_ms mean | {((aggregates.get('duration_ms') or {}).get('mean'))} |",
        f"| duration_ms total | {((aggregates.get('duration_ms') or {}).get('total'))} |",
        "",
        "### Routes by mode",
        "",
        "```json",
        json.dumps(aggregates.get("route_counts_by_mode") or {}, indent=2),
        "```",
        "",
    ]
    lines.extend(_markdown_comparison_tables(aggregates))
    lines.extend(
        [
            "## Process",
            "",
            "1. Confirm Ollama + API health; record hardware + selected model.",
            "2. `POST /generate` with `{prompt, mode}` for each case.",
            "3. After graph expand (GASR), flat-merge re-prune (`prune_context` on seeds+neighbors).",
            "4. Record route, hops, tokens_injected_est, stage timings, and full answer.",
            "5. Write JSON + Markdown + LATEST.md under `results/live_gasr/`.",
            "",
            "## Results",
            "",
        ]
    )
    for r in results:
        s = r.get("summary") or {}
        stage_ms = s.get("stage_ms") or {}
        lines.extend(
            [
                f"### {r['id']} (`mode={r['mode']}`)",
                "",
                f"**Thesis tag:** `{r.get('thesis_tag', '')}`",
                "",
            ]
        )
        if r.get("compare_group"):
            lines.extend([f"**Compare group:** `{r['compare_group']}`", ""])
        lines.extend(
            [
                f"**Intent:** {r.get('intent', '')}",
                "",
                f"**Expect:** {r.get('expect_note', '')}",
                "",
                f"**Prompt:** {r['prompt']}",
                "",
                "| Field | Value |",
                "|-------|-------|",
                f"| ok | {r.get('ok')} |",
                f"| duration_ms (wall) | {r.get('duration_ms')} |",
                f"| route | `{s.get('route')}` |",
                f"| hops_used | `{s.get('hops_used')}` |",
                f"| tokens_injected_est | `{s.get('tokens_injected_est')}` |",
                f"| retrieve_ms | `{stage_ms.get('retrieve')}` |",
                f"| assess_ms | `{stage_ms.get('assess')}` |",
                f"| graph_expand_ms | `{stage_ms.get('graph_expand')}` |",
                f"| re_prune_ms | `{stage_ms.get('re_prune')}` |",
                f"| inject_ms | `{stage_ms.get('inject')}` |",
                f"| generate_ms | `{stage_ms.get('generate')}` |",
                f"| pipeline_stages_ms | `{s.get('pipeline_stages_ms')}` |",
                f"| answer_chars | `{s.get('answer_chars')}` |",
                "",
                "**Answer:**",
                "",
                "```",
                (s.get("answer_full") or r.get("error") or "(empty)")[:4000],
                "```",
                "",
            ]
        )
        if s.get("assess_summary"):
            lines.extend(
                [
                    "**Assess summary:**",
                    "",
                    "```json",
                    json.dumps(s["assess_summary"], indent=2, default=str)[:3000],
                    "```",
                    "",
                ]
            )
        if s.get("expand_summary"):
            lines.extend(
                [
                    "**Expand summary:**",
                    "",
                    "```json",
                    json.dumps(s["expand_summary"], indent=2, default=str)[:3000],
                    "```",
                    "",
                ]
            )
        if s.get("re_prune_summary"):
            lines.extend(
                [
                    "**Re-prune summary:**",
                    "",
                    "```json",
                    json.dumps(s["re_prune_summary"], indent=2, default=str)[:2000],
                    "```",
                    "",
                ]
            )
        if s.get("stage_order"):
            lines.extend(
                [
                    f"**Stage order:** `{' → '.join(s['stage_order'])}`",
                    "",
                ]
            )
    return "\n".join(lines)


def _all_case_catalog() -> list[dict[str, Any]]:
    return list(CASES) + list(COMPARE_CASES)


def _select_cases(suite: str, case_ids: Optional[list[str]]) -> list[dict[str, Any]]:
    catalog = _all_case_catalog()
    if case_ids:
        want = set(case_ids)
        selected = [c for c in catalog if c["id"] in want]
        return selected
    if suite == "core":
        return [c for c in CASES if c["id"] in SUITE_CORE]
    if suite == "full":
        return [c for c in CASES if c["id"] in SUITE_FULL]
    if suite == "compare":
        return list(COMPARE_CASES)
    if suite == "all":
        return catalog
    raise ValueError(f"Unknown suite: {suite}")


def print_recommend() -> int:
    hw = detect_hardware()
    try:
        tags = _get(OLLAMA_TAGS_URL, timeout=10)
        installed = list_ollama_models(tags)
    except Exception as exc:
        print(f"Ollama not reachable: {exc}", file=sys.stderr)
        installed = []
    rec = resolve_best_model(installed)
    print("=== Live GASR model recommendation (this PC) ===")
    print(json.dumps({"hardware": hw, "model": rec}, indent=2, default=str))
    print()
    sel = rec.get("selected")
    if not sel:
        print("No usable model. Example: ollama pull qwen3.5:9b", file=sys.stderr)
        return 2
    print("Start API with:")
    print(f'  $env:OLLAMA_MODEL="{sel}"')
    print('  $env:PYTHONPATH="backend"')
    print("  cd backend")
    print("  python -m uvicorn mcp_server.server:app --host 127.0.0.1 --port 8000")
    print()
    print("Then run:")
    print("  python -u scripts/live_gasr_ollama_tests.py --suite core --timeout 600")
    print("  python -u scripts/live_gasr_ollama_tests.py --suite compare --timeout 900")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Comprehensive live GASR tests against Ollama-backed API"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent.parent / "results" / "live_gasr"),
    )
    parser.add_argument(
        "--suite",
        choices=["core", "full", "compare", "all"],
        default="core",
        help=(
            "core=5 thesis cases; full=+RAG/LC baselines; "
            "compare=same prompts × RAG/LC/GASR (tokens+speed); all=core+full+compare"
        ),
    )
    parser.add_argument("--cases", nargs="*", help="Optional case ids (overrides --suite)")
    parser.add_argument(
        "--model",
        default=None,
        help="Preferred Ollama model name (must be installed; API must use same via OLLAMA_MODEL)",
    )
    parser.add_argument(
        "--recommend-model",
        action="store_true",
        help="Print hardware + best model recommendation and exit",
    )
    parser.add_argument(
        "--dry-preflight",
        action="store_true",
        help="Check API + Ollama + model only; do not call /generate",
    )
    args = parser.parse_args()

    if args.recommend_model:
        return print_recommend()

    base = args.base_url.rstrip("/")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    hardware = detect_hardware()

    try:
        root = _get(f"{base}/", timeout=10)
        print(f"API OK: {root}", flush=True)
    except Exception as exc:
        print(f"API not reachable at {base}: {exc}", file=sys.stderr)
        print("Start uvicorn with OLLAMA_MODEL set (see --recommend-model).", file=sys.stderr)
        return 2

    try:
        tags = _get(OLLAMA_TAGS_URL, timeout=10)
        installed = list_ollama_models(tags)
        print(f"Ollama OK: models={installed}", flush=True)
    except Exception as exc:
        print(f"Ollama not reachable: {exc}", file=sys.stderr)
        return 2

    model_info = resolve_best_model(installed, preferred=args.model)
    selected_model = model_info.get("selected")
    print(f"Model selected: {selected_model} ({model_info.get('selection_reason')})", flush=True)
    if not selected_model:
        print(model_info.get("selection_reason"), file=sys.stderr)
        return 2

    print(
        "NOTE: The API uses whatever OLLAMA_MODEL was set when uvicorn started. "
        f"Ensure it matches `{selected_model}`.",
        flush=True,
    )

    if args.dry_preflight:
        payload = {
            "preflight": "ok",
            "api": root,
            "hardware": hardware,
            "model": model_info,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    try:
        selected = _select_cases(args.suite, args.cases)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.cases and not selected:
        print(f"No cases matched {args.cases}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for case in selected:
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
        }
        body: dict[str, Any] = {"prompt": case["prompt"], "mode": case["mode"]}
        try:
            resp = _post_json(f"{base}/generate", body, timeout=args.timeout)
            ms = int((time.perf_counter() - t0) * 1000)
            summary = _summarize(resp)
            entry.update(
                {
                    "ok": True,
                    "duration_ms": ms,
                    "summary": summary,
                    "raw_pipeline": resp.get("pipeline"),
                }
            )
            print(
                f"OK route={summary.get('route')} hops={summary.get('hops_used')} ms={ms}",
                flush=True,
            )
            print((summary.get("answer_preview") or "")[:300], flush=True)
        except urllib.error.HTTPError as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            entry.update(
                {"ok": False, "duration_ms": ms, "error": f"HTTP {exc.code}: {err_body[:2000]}"}
            )
            print(entry["error"], file=sys.stderr, flush=True)
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            entry.update({"ok": False, "duration_ms": ms, "error": str(exc)})
            print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        results.append(entry)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    aggregates = _aggregate(results)
    run_meta = {
        "timestamp_utc": stamp,
        "base_url": base,
        "timeout_sec": args.timeout,
        "suite": args.suite if not args.cases else "custom",
        "case_ids": [r["id"] for r in results],
        "hardware": hardware,
        "model": model_info,
        "schema_version": "live_gasr.v2",
    }
    json_path = out_dir / f"live_gasr_run_{stamp}.json"
    md_path = out_dir / f"live_gasr_run_{stamp}.md"
    payload = {
        "meta": run_meta,
        "aggregates": aggregates,
        "results": results,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_report = _markdown_report(run_meta, results, aggregates)
    md_path.write_text(md_report, encoding="utf-8")
    (out_dir / "LATEST.md").write_text(
        f"Latest run: `{md_path.name}`\n\n" + md_report,
        encoding="utf-8",
    )
    # Compact index row for thesis tables
    index_path = out_dir / "INDEX.jsonl"
    index_row = {
        "timestamp_utc": stamp,
        "model": selected_model,
        "suite": run_meta["suite"],
        "json": json_path.name,
        "md": md_path.name,
        "aggregates": aggregates,
    }
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(index_row, default=str) + "\n")

    print(f"\nWrote {json_path}", flush=True)
    print(f"Wrote {md_path}", flush=True)
    print(f"Wrote {out_dir / 'LATEST.md'}", flush=True)
    print(f"Appended {index_path}", flush=True)
    print(f"Aggregates: {json.dumps(aggregates)}", flush=True)

    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
