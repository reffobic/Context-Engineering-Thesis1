#!/usr/bin/env python3
"""
Expanded live mode comparison: RAG vs LC vs GASR vs SELF_ROUTE.

Writes ONLY under results/live_modes_compare/ (does NOT overwrite results/live_gasr/).
Uses whatever models the API was started with (OLLAMA_MODEL + ASSESSOR_MODEL).

Example:
  export OLLAMA_MODEL=qwen3.8:27b
  export ASSESSOR_MODEL=ornith:9b
  # restart uvicorn, then:
  python3 -u scripts/live_modes_compare_suite.py --timeout 1200
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse helpers from the existing live GASR harness (same directory).
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from live_gasr_ollama_tests import (  # noqa: E402
    OLLAMA_TAGS_URL,
    _aggregate,
    _get,
    _markdown_report,
    _post_json,
    _summarize,
    detect_hardware,
    list_ollama_models,
)

MODES: list[str] = ["RAG", "LC", "GASR", "SELF_ROUTE", "GAPA", "PREASSESS"]

# Larger prompt bank — each group × 4 modes.
COMPARE_GROUPS: list[dict[str, Any]] = [
    {
        "group": "C1_simple_return",
        "prompt": "What does create_app in superset/app.py return?",
        "intent": "Simple return-value Q.",
        "expect_note": "RAG fewest tokens; GASR may GRAPH or RAG.",
        "thesis_tag": "modes_simple",
    },
    {
        "group": "C2_cross_file_callees",
        "prompt": (
            "In superset/app.py, what does create_app call related to feature flags "
            "or encrypt init_app helpers? Name the callee symbols only."
        ),
        "intent": "STRUCTURE / neighbor question.",
        "expect_note": "GASR may expand; SELF_ROUTE is RAG|LC only.",
        "thesis_tag": "modes_structure",
    },
    {
        "group": "C3_same_file_context",
        "prompt": (
            "In superset/app.py create_app, what happens in the surrounding lines of the "
            "function body for app factory setup? Quote nearby setup steps from the same file."
        ),
        "intent": "Same-file CONTEXT pressure.",
        "expect_note": "LC / SELF_ROUTE→LC likely highest tokens.",
        "thesis_tag": "modes_context",
    },
    {
        "group": "C4_config_deps",
        "prompt": (
            "Which functions does SupersetApp or create_app depend on for configuration "
            "loading across files? List neighbor symbols, not whole files."
        ),
        "intent": "Cross-file dependency.",
        "expect_note": "GASR hops vs SELF_ROUTE binary.",
        "thesis_tag": "modes_deps",
    },
    {
        "group": "C5_approot_middleware",
        "prompt": (
            "What is AppRootMiddleware in superset/app.py for, and when does create_app "
            "install it? Cite the condition from the code."
        ),
        "intent": "Same-file + conditional setup.",
        "expect_note": "RAG may suffice if chunk includes middleware.",
        "thesis_tag": "modes_middleware",
    },
    {
        "group": "C6_sync_config",
        "prompt": (
            "When is sync_config_to_db on SupersetApp invoked, and what does it do "
            "at a high level? Name key helper calls if present."
        ),
        "intent": "Method body + helpers.",
        "expect_note": "STRUCTURE or CONTEXT depending on retrieval.",
        "thesis_tag": "modes_sync",
    },
    {
        "group": "C7_initializer_init",
        "prompt": (
            "In SupersetAppInitializer (superset/initialization), what major steps does "
            "init_app perform? List method names in order if possible."
        ),
        "intent": "Cross-file initializer STRUCTURE.",
        "expect_note": "GASR expand likely useful.",
        "thesis_tag": "modes_init",
    },
    {
        "group": "C8_feature_flags_chain",
        "prompt": (
            "Trace configure_feature_flags (or similar) from the app initialization path: "
            "list callee / neighbor symbols across files."
        ),
        "intent": "Multi-hop STRUCTURE.",
        "expect_note": "GASR hops; SELF_ROUTE cannot GRAPH.",
        "thesis_tag": "modes_flags",
    },
    {
        "group": "C9_create_app_type",
        "prompt": (
            "Does create_app in superset/app.py return a plain Flask app or a SupersetApp "
            "subclass instance? Quote the construction line."
        ),
        "intent": "Precise return-type Q.",
        "expect_note": "RAG should win on tokens if retrieval hits app.py.",
        "thesis_tag": "modes_type",
    },
    {
        "group": "C10_local_extensions_watcher",
        "prompt": (
            "When does create_app start the local extensions watcher thread, and what "
            "function does it call?"
        ),
        "intent": "Conditional call in create_app body.",
        "expect_note": "Same-file; RAG/GRAPH possible.",
        "thesis_tag": "modes_watcher",
    },
    {
        "group": "C11_db_up_to_date",
        "prompt": (
            "What does _is_database_up_to_date in superset/app.py check, and which "
            "libraries or helpers does it use?"
        ),
        "intent": "Helper method detail.",
        "expect_note": "RAG likely; watch LC token cost.",
        "thesis_tag": "modes_dbcheck",
    },
    {
        "group": "C12_encrypt_init",
        "prompt": (
            "Where in Superset initialization is database encryption / encrypt.init_app "
            "(or equivalent) configured? Name the enclosing method and nearby callees."
        ),
        "intent": "STRUCTURE across initialization.",
        "expect_note": "GASR vs SELF_ROUTE contrast.",
        "thesis_tag": "modes_encrypt",
    },
    {
        "group": "C13_app_imports",
        "prompt": (
            "Which imports at the top of superset/app.py are most relevant to create_app "
            "and SupersetApp? List symbols only."
        ),
        "intent": "File-header CONTEXT.",
        "expect_note": "LC may help; RAG if imports in chunk.",
        "thesis_tag": "modes_imports",
    },
    {
        "group": "C14_superset_config_env",
        "prompt": (
            "How does create_app choose the config module (SUPERSET_CONFIG / "
            "superset_config_module)? Quote the relevant lines."
        ),
        "intent": "Config loading detail.",
        "expect_note": "RAG if create_app chunk kept.",
        "thesis_tag": "modes_config_env",
    },
    {
        "group": "C15_test_create_app",
        "prompt": (
            "In tests/integration_tests/base_tests.py, what does create_app on the test "
            "case return, and how does it relate to the Flask/Superset app under test?"
        ),
        "intent": "Test harness vs production create_app.",
        "expect_note": "Retrieval may prefer tests; compare modes.",
        "thesis_tag": "modes_test_factory",
    },
    {
        "group": "C16_cli_create_app",
        "prompt": (
            "What does create_app in superset/cli/main.py do compared to "
            "superset/app.py:create_app? Name differences in one short list."
        ),
        "intent": "Two create_app symbols — disambiguation.",
        "expect_note": "GRAPH neighbors may help; RAG may confuse.",
        "thesis_tag": "modes_cli",
    },
]


def _expand_cases() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in COMPARE_GROUPS:
        for mode in MODES:
            out.append(
                {
                    "id": f"{g['group']}_{mode}",
                    "mode": mode,
                    "suite": "modes_compare",
                    "compare_group": g["group"],
                    "prompt": g["prompt"],
                    "intent": g["intent"],
                    "expect_note": g["expect_note"],
                    "thesis_tag": g["thesis_tag"],
                }
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RAG/LC/GASR/SELF_ROUTE/GAPA live compare → results/live_modes_compare/"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument(
        "--out-dir",
        default=str(
            Path(__file__).resolve().parent.parent / "results" / "live_modes_compare"
        ),
        help="Output directory (default: results/live_modes_compare — not live_gasr)",
    )
    parser.add_argument(
        "--groups",
        nargs="*",
        help="Optional compare group ids (e.g. C1_simple_return C7_initializer_init)",
    )
    parser.add_argument(
        "--modes",
        nargs="*",
        default=None,
        help="Subset of modes (default: all four)",
    )
    parser.add_argument(
        "--gen-model-label",
        default=None,
        help="Record expected OLLAMA_MODEL in meta (informational)",
    )
    parser.add_argument(
        "--assessor-model-label",
        default=None,
        help="Record expected ASSESSOR_MODEL in meta (informational)",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Refuse accidental overwrite of the original live_gasr tree via mis-set --out-dir
    if out_dir.resolve().name == "live_gasr":
        print(
            "Refusing to write into results/live_gasr/. "
            "Use results/live_modes_compare/ (default) or another new folder.",
            file=sys.stderr,
        )
        return 2

    modes = [m.upper() for m in (args.modes or MODES)]
    for m in modes:
        if m not in MODES:
            print(f"Unknown mode {m}; allowed: {MODES}", file=sys.stderr)
            return 2

    cases = _expand_cases()
    if args.groups:
        wanted = set(args.groups)
        cases = [c for c in cases if c["compare_group"] in wanted]
    cases = [c for c in cases if c["mode"] in modes]

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

    gen_label = args.gen_model_label or ""
    assessor_label = args.assessor_model_label or ""
    print(
        f"Cases: {len(cases)} (groups={len({c['compare_group'] for c in cases})} "
        f"× modes={modes})",
        flush=True,
    )
    print(
        f"Labels (ensure API env matches): gen={gen_label or '(unset)'} "
        f"assessor={assessor_label or '(unset)'}",
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
        }
        body = {"prompt": case["prompt"], "mode": case["mode"]}
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
            print((summary.get("answer_preview") or "")[:240], flush=True)
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
        "suite": "modes_compare",
        "case_ids": [r["id"] for r in results],
        "hardware": hardware,
        "generation_model_expected": gen_label,
        "assessor_model_expected": assessor_label,
        "ollama_installed": installed,
        "modes": modes,
        "schema_version": "live_modes_compare.v1",
        "note": "Does not write to results/live_gasr/",
    }
    json_path = out_dir / f"modes_compare_run_{stamp}.json"
    md_path = out_dir / f"modes_compare_run_{stamp}.md"
    payload = {"meta": run_meta, "aggregates": aggregates, "results": results}
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_report = _markdown_report(run_meta, results, aggregates)
    # Prefix header so thesis readers see model split
    header = (
        f"# Modes compare (RAG / LC / GASR / SELF_ROUTE / GAPA)\n\n"
        f"- **Generation model (expected):** `{gen_label or 'see API OLLAMA_MODEL'}`\n"
        f"- **Assessor model (expected):** `{assessor_label or 'see API ASSESSOR_MODEL'}`\n"
        f"- **Out dir:** `{out_dir}`\n\n"
    )
    md_path.write_text(header + md_report, encoding="utf-8")
    # Folder-local LATEST only (never touches results/live_gasr/LATEST.md)
    (out_dir / "LATEST.md").write_text(
        f"Latest modes-compare run: `{md_path.name}`\n\n" + header + md_report,
        encoding="utf-8",
    )
    index_path = out_dir / "INDEX.jsonl"
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp_utc": stamp,
                    "generation_model": gen_label,
                    "assessor_model": assessor_label,
                    "json": json_path.name,
                    "md": md_path.name,
                    "cases": len(results),
                    "aggregates": {
                        "ok": aggregates.get("cases_ok"),
                        "fail": aggregates.get("cases_failed"),
                        "route_counts": aggregates.get("route_counts"),
                        "route_counts_by_mode": aggregates.get("route_counts_by_mode"),
                    },
                },
                default=str,
            )
            + "\n"
        )

    print(f"\nWrote {json_path}", flush=True)
    print(f"Wrote {md_path}", flush=True)
    print(f"Wrote {out_dir / 'LATEST.md'} (folder-local only)", flush=True)
    print(f"Aggregates: {json.dumps(aggregates.get('route_counts_by_mode'), default=str)}", flush=True)
    return 0 if aggregates.get("cases_failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
