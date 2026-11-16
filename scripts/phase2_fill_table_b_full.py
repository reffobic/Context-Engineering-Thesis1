#!/usr/bin/env python3
"""Aggregate SWE-QA Wave 1 + rest scores → Table B full (n=720) and rest-only (n=576)."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs/phase2_runs"
ARMS = ["ALWAYS_LC", "ALWAYS_RAG", "ALWAYS_GRAPH", "SELF_ROUTE", "PREASSESS", "GASR", "GAPA"]

SPLITS = {
    "w1": {
        "cand": ROOT / "eval_harness/data/reports/phase2_sweqa_candidates",
        "scores": ROOT / "eval_harness/data/reports/phase2_sweqa_scores",
        "n_expect": 144,
        "title": "Table B — SWE-QA Wave 1 (Phase 2)",
        "csv": "table_b_sweqa_w1.csv",
        "md": "TABLE_B_SWEQA_W1.md",
        "label": "n = 144 (flask+requests+pytest)",
    },
    "rest": {
        "cand": ROOT / "eval_harness/data/reports/phase2_sweqa_candidates_rest",
        "scores": ROOT / "eval_harness/data/reports/phase2_sweqa_scores_rest",
        "n_expect": 576,
        "title": "Table B — SWE-QA rest (12 repos beyond Wave 1)",
        "csv": "table_b_sweqa_rest.csv",
        "md": "TABLE_B_SWEQA_REST.md",
        "label": "n = 576 (12 repos)",
    },
}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def load_join(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_scores(scores_dir: Path) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for arm in ARMS:
        sp = scores_dir / f"{arm}_answers_score.jsonl"
        if not sp.is_file():
            alts = list(scores_dir.glob(f"{arm}*_score.jsonl"))
            sp = alts[0] if alts else sp
        if not sp.is_file():
            print(f"[WARN] missing scores for {arm}: {sp}")
            continue
        for line in sp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            tid = r.get("task_id")
            if not tid:
                continue
            sc = r.get("score") or {}
            overall = r.get("overall")
            if overall is None and sc:
                overall = sum(sc.values())
            out[arm][tid] = {"overall": overall, **sc}
    return out


def tok(r: dict):
    if r.get("est_tokens_div4") is not None:
        return r["est_tokens_div4"]
    return r.get("prompt_eval_count")


def aggregate(join: list[dict], scores: dict[str, dict[str, dict]]) -> list[dict]:
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for row in join:
        by_arm[row["arm"]].append(row)

    lc_tokens = [tok(r) for r in by_arm.get("ALWAYS_LC", []) if tok(r) is not None]
    lc_mean = mean(lc_tokens)

    rows_out = []
    for arm in ARMS:
        rs = by_arm.get(arm, [])
        scmap = scores.get(arm, {})
        overalls = []
        dims = {k: [] for k in ("correctness", "completeness", "relevance", "clarity", "reasoning")}
        for r in rs:
            s = scmap.get(r["task_id"])
            if not s or s.get("overall") is None:
                continue
            overalls.append(s["overall"])
            for k in dims:
                if s.get(k) is not None:
                    dims[k].append(s[k])
        toks = [tok(r) for r in rs if tok(r) is not None]
        routes = [r.get("route") for r in rs if r.get("status") == "ok"]
        tm = mean(toks)
        rec = {
            "arm": arm,
            "n": len(rs),
            "n_ok": sum(1 for r in rs if r.get("status") == "ok"),
            "n_scored": len(overalls),
            "overall_mean": mean(overalls),
            "correctness_mean": mean(dims["correctness"]),
            "completeness_mean": mean(dims["completeness"]),
            "relevance_mean": mean(dims["relevance"]),
            "clarity_mean": mean(dims["clarity"]),
            "reasoning_mean": mean(dims["reasoning"]),
            "tokens_mean": tm,
            "token_pct_vs_LC": round(100.0 * tm / lc_mean, 1) if (tm and lc_mean) else None,
            "pct_RAG": pct(sum(1 for x in routes if x == "RAG"), len(routes)),
            "pct_GRAPH": pct(sum(1 for x in routes if x == "GRAPH"), len(routes)),
            "pct_LC": pct(sum(1 for x in routes if x == "LC"), len(routes)),
            "wall_ms_mean": mean([r.get("task_total_ms") for r in rs]),
            "hops_mean": mean([r.get("hops_used") for r in rs if r.get("hops_used") is not None]),
        }
        rows_out.append(rec)
    return rows_out


def write_table(rows_out: list[dict], title: str, label: str, csv_name: str, md_name: str, extra_note: list[str] | None = None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / csv_name
    cols = list(rows_out[0].keys()) if rows_out else ["arm"]
    with csv_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(cols) + "\n")
        for r in rows_out:
            f.write(",".join("" if r[c] is None else str(r[c]) for c in cols) + "\n")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# {title}",
        "",
        f"*generator = qwen3.5:9b · judge = qwen-7B:latest (local-judge, single-judge) · {label} · stamp = {stamp}*",
        "",
        "| Arm | Overall/100 | tokens mean | token% vs LC | %RAG | %GRAPH | %LC | wall_ms mean |",
        "|-----|-------------|-------------|--------------|------|--------|-----|--------------|",
    ]
    # Sort display by overall desc for readability (keep ALWAYS_* order? Keep ARMS order for consistency with W1)
    for r in rows_out:
        lines.append(
            f"| {r['arm']} | {r['overall_mean']} | {r['tokens_mean']} | {r['token_pct_vs_LC']} | "
            f"{r['pct_RAG']} | {r['pct_GRAPH']} | {r['pct_LC']} | {r['wall_ms_mean']} |"
        )
    lines += [
        "",
        "## Dim means",
        "",
        "| Arm | Correctness | Completeness | Relevance | Clarity | Reasoning |",
        "|-----|-------------|--------------|-----------|---------|-----------|",
    ]
    for r in rows_out:
        lines.append(
            f"| {r['arm']} | {r['correctness_mean']} | {r['completeness_mean']} | "
            f"{r['relevance_mean']} | {r['clarity_mean']} | {r['reasoning_mean']} |"
        )

    by = {r["arm"]: r for r in rows_out}
    note = ["", "## vs SWE-QA paper (anchors only)", ""]
    note.append(
        "Paper Table 3 uses Claude Sonnet 4.5 · 5-vote and stronger scaffolds. "
        "This table is **suite-scope** routing cost–quality under our freeze — not absolute paper parity."
    )
    if by.get("GASR") and by.get("SELF_ROUTE"):
        g, s = by["GASR"], by["SELF_ROUTE"]
        if g["tokens_mean"] and s["tokens_mean"]:
            cut = round(100.0 * (1 - g["tokens_mean"] / s["tokens_mean"]), 1)
            note.append(
                f"- GASR vs Self-Route token cut ≈ **{cut}%** "
                f"(GASR {g['tokens_mean']} vs Self-Route {s['tokens_mean']}); "
                f"Overall {g['overall_mean']} vs {s['overall_mean']}."
            )
    if by.get("ALWAYS_RAG"):
        note.append(f"- Always-RAG Overall={by['ALWAYS_RAG']['overall_mean']} (top under this 9B/local-judge setup).")
    if extra_note:
        note += ["", *extra_note]
    lines += note
    md_path = OUT_DIR / md_name
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[PASS] wrote {csv_path}")
    print(f"[PASS] wrote {md_path}")


def main() -> int:
    # Rest-only
    rest_join = load_join(SPLITS["rest"]["cand"] / "join_metrics.jsonl")
    rest_scores = load_scores(SPLITS["rest"]["scores"])
    rest_rows = aggregate(rest_join, rest_scores)
    write_table(
        rest_rows,
        SPLITS["rest"]["title"],
        SPLITS["rest"]["label"],
        SPLITS["rest"]["csv"],
        SPLITS["rest"]["md"],
    )

    # Full = W1 + rest
    w1_join = load_join(SPLITS["w1"]["cand"] / "join_metrics.jsonl")
    w1_scores = load_scores(SPLITS["w1"]["scores"])
    full_join = w1_join + rest_join
    full_scores: dict[str, dict[str, dict]] = defaultdict(dict)
    for arm in ARMS:
        full_scores[arm].update(w1_scores.get(arm, {}))
        full_scores[arm].update(rest_scores.get(arm, {}))
    full_rows = aggregate(full_join, full_scores)

    w1_by = {r["arm"]: r for r in aggregate(w1_join, w1_scores)}
    rest_by = {r["arm"]: r for r in rest_rows}
    extra = [
        "### Split means (Overall/100)",
        "",
        "| Arm | Wave 1 (144) | Rest (576) | Full (720) |",
        "|-----|-------------:|-----------:|-----------:|",
    ]
    full_by = {r["arm"]: r for r in full_rows}
    for arm in ARMS:
        extra.append(
            f"| {arm} | {w1_by.get(arm, {}).get('overall_mean')} | "
            f"{rest_by.get(arm, {}).get('overall_mean')} | {full_by.get(arm, {}).get('overall_mean')} |"
        )

    write_table(
        full_rows,
        "Table B — SWE-QA full (Phase 2)",
        "n = 720 (Wave 1 144 ∪ rest 576)",
        "table_b_sweqa_full.csv",
        "TABLE_B_SWEQA_FULL.md",
        extra_note=extra,
    )

    for r in full_rows:
        print(json.dumps({"split": "full", **r}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
