#!/usr/bin/env python3
"""CP6–CP7: aggregate SWE-QA Wave 1 scores + routing metrics → Table B CSV + paper note."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAND = ROOT / "eval_harness/data/reports/phase2_sweqa_candidates"
SCORES = ROOT / "eval_harness/data/reports/phase2_sweqa_scores"
OUT_DIR = ROOT / "docs/phase2_runs"
ARMS = ["ALWAYS_LC", "ALWAYS_RAG", "ALWAYS_GRAPH", "SELF_ROUTE", "PREASSESS", "GASR", "GAPA"]


def load_join() -> list[dict]:
    p = CAND / "join_metrics.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_scores_by_arm() -> dict[str, dict[str, dict]]:
    """arm -> task_id -> score dict with overall."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for arm in ARMS:
        # export names answers as ALWAYS_LC_answers.jsonl → score ALWAYS_LC_answers_score.jsonl
        sp = SCORES / f"{arm}_answers_score.jsonl"
        if not sp.is_file():
            # try alternate
            alts = list(SCORES.glob(f"{arm}*_score.jsonl"))
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


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def main() -> int:
    join = load_join()
    scores = load_scores_by_arm()
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for row in join:
        by_arm[row["arm"]].append(row)

    # Always-LC mean injected tokens (prefer est_tokens_div4, else prompt_eval_count)
    def tok(r):
        if r.get("est_tokens_div4") is not None:
            return r["est_tokens_div4"]
        return r.get("prompt_eval_count")

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
        n = len(rs)
        n_ok = sum(1 for r in rs if r.get("status") == "ok")
        rec = {
            "arm": arm,
            "n": n,
            "n_ok": n_ok,
            "n_scored": len(overalls),
            "overall_mean": mean(overalls),
            "correctness_mean": mean(dims["correctness"]),
            "completeness_mean": mean(dims["completeness"]),
            "relevance_mean": mean(dims["relevance"]),
            "clarity_mean": mean(dims["clarity"]),
            "reasoning_mean": mean(dims["reasoning"]),
            "tokens_mean": mean(toks),
            "token_pct_vs_LC": round(100.0 * mean(toks) / lc_mean, 1) if (mean(toks) and lc_mean) else None,
            "pct_RAG": pct(sum(1 for x in routes if x == "RAG"), len(routes)),
            "pct_GRAPH": pct(sum(1 for x in routes if x == "GRAPH"), len(routes)),
            "pct_LC": pct(sum(1 for x in routes if x == "LC"), len(routes)),
            "wall_ms_mean": mean([r.get("task_total_ms") for r in rs]),
            "hops_mean": mean([r.get("hops_used") for r in rs if r.get("hops_used") is not None]),
        }
        rows_out.append(rec)
        print(json.dumps(rec))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "table_b_sweqa_w1.csv"
    cols = list(rows_out[0].keys()) if rows_out else ["arm"]
    with csv_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(cols) + "\n")
        for r in rows_out:
            f.write(",".join("" if r[c] is None else str(r[c]) for c in cols) + "\n")

    # Markdown Table B
    md = OUT_DIR / "TABLE_B_SWEQA_W1.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Table B — SWE-QA Wave 1 (Phase 2)",
        "",
        f"*n = 144 · generator = qwen3.5:9b · judge = qwen-7B:latest (local-judge, single-judge) · stamp = {stamp}*",
        "",
        "| Arm | Overall/100 | tokens mean | token% vs LC | %RAG | %GRAPH | %LC | wall_ms mean |",
        "|-----|-------------|-------------|--------------|------|--------|-----|--------------|",
    ]
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

    # CP7 short paper note
    by = {r["arm"]: r for r in rows_out}
    note = ["", "## CP7 — vs SWE-QA paper (anchors only)", ""]
    note.append(
        "Paper systems (Direct / FC-RAG / agents) use different scaffolding and often larger models; "
        "primary claim is **routing cost–quality** among our 7 arms, not beating Cursor absolute /100."
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
        note.append(
            f"- GASR %GRAPH={g.get('pct_GRAPH')} %LC={g.get('pct_LC')}; "
            f"Self-Route %RAG={s.get('pct_RAG')} %LC={s.get('pct_LC')}."
        )
    if by.get("ALWAYS_RAG"):
        note.append(
            f"- Our Always-RAG Overall={by['ALWAYS_RAG']['overall_mean']} "
            f"(paper FC-RAG example ~63 with Qwen3-Coder-30B — different model/scaffold)."
        )
    lines += note
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[PASS] wrote {csv_path}")
    print(f"[PASS] wrote {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
