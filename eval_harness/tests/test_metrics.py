"""Metric definitions, failure rule, sensitivity variant, gate tokens by path, merge refusal, 'not run' rows."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from harness import metrics as M

HEADER = {"config_hashes": {c: "h" for c in M.COMPAT_CONFIGS}, "ollama_version": "1", "model_digest": "d", "machine_id": "m1",
          "gpu_name": "g", "pipeline_frozen_sha": "p", "harness_commit": "c", "schema": 1}


def lists(files, modules=None, functions=None):
    return {"files": files, "modules": modules or files, "functions": functions or files, "unmapped": [], "n_items": len(files)}


def line(tid, files, status="ok", gate_error=False, route="GRAPH", gate=None, tokens=None):
    L = lists(files)
    return {"task_id": tid, "status": status, "route": route, "gate_error": gate_error, "answer_error": False, "counted_as_failed": status != "ok" or gate_error,
            "ranked": {"after_retrieval": L, "after_expansion": None, "after_pruning": L, "final_context": L},
            "tokens": tokens or {"router_prompt": 400, "router_completion": 40, "answer_prompt": 1600, "answer_completion": 50},
            "gate": gate or {}, "gate_error_events": ([{"name": "x"}] if gate_error else []), "ollama_calls": [{"role": "gapa_gate_call1"}], "latency_ms": {"task_total": 1000}}


def make_job(root: Path, arm, rows, ablation="full", n=4, dataset="swe-bench-lite", **hdr):
    d = root / f"{arm}_{ablation}"
    d.mkdir(parents=True)
    ident = {"arm": arm, "model": "m", "dataset": dataset, "ablation": ablation, "sample_sha256": "s", "pipeline_frozen_sha": "p", "harness_commit": "c",
             **{k: v for k, v in hdr.items() if k in ("pipeline_frozen_sha", "harness_commit")}}
    (d / "job.json").write_text(json.dumps({"job_id": d.name, "identity": ident, "header": {**HEADER, **hdr}, "info": {"n_tasks": n}}), encoding="utf-8")
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    (d / "datasets" / "swe-bench-lite").mkdir(parents=True)
    (d / "samples").mkdir()
    ids = ["t1", "t2", "t3", "t4"]
    gold = {"t1": ["a.py"], "t2": ["a.py", "b.py"], "t3": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"], "t4": ["z.py"]}
    (d / "datasets" / "swe-bench-lite" / "labels.jsonl").write_text("".join(json.dumps({"instance_id": i, "files": gold[i], "modules": gold[i], "functions": gold[i]}) + "\n" for i in ids), encoding="utf-8")
    (d / "samples" / "swe-bench-lite.sample.json").write_text(json.dumps({"instance_ids": ids}), encoding="utf-8")
    return d


def test_acc_and_recall_follow_the_locagent_cap_rule_and_dedupe():
    assert M.acc_at(["a", "b"], ["a", "b"], 1) == 1.0                       # need min(2, 1) = 1 hit: a is a hit
    assert M.acc_at(["a", "x"], ["a", "b"], 1) == 1.0                       # cap: one hit is enough at k=1
    assert M.acc_at(["a", "x"], ["a", "b"], 3) == 0.0                       # both gold needed at k=3
    assert M.acc_at(["a", "b", "c", "d", "e"], list("abcdefg"), 5) == 1.0   # 7 gold, k=5: 5 hits are enough
    assert M.acc_at(["a", "a", "a", "b"], ["a", "b"], 2) == 1.0             # duplicates keep the first rank only: top-2 = a, b
    assert M.acc_at(["a", "a", "b"], ["a", "b"], 2) == 1.0
    assert M.recall_at(["a", "x", "b"], ["a", "b", "c"], 5) == pytest.approx(2 / 3)
    assert M.recall_at(list("abcde"), list("abcdefg"), 5) == 1.0           # denominator min(7, 5)


def test_tables_failed_tasks_are_zero_sensitivity_keeps_gate_failures_and_unfinished_rows_are_not_run(tmp_path, data):
    rows = [line("t1", ["a.py"]), line("t2", ["a.py", "b.py"], gate_error=True, route="LC"),
            line("t3", ["a.py"], status="timeout", route=None), line("t4", ["q.py"])]
    rows[2].pop("tokens")
    run = tmp_path / "runs"
    make_job(run, "GAPA", rows)
    make_job(run, "GASR", rows[:2])                                              # unfinished: 2 of 4 tasks
    jobs = M.load_jobs([run])
    out = tmp_path / "out"
    s = M.build_tables(jobs, data, out, [])
    a = s["aggregates"]["m|swe-bench-lite|GAPA|full"]
    # primary: t1 hit, t2 gate failure -> 0, t3 timeout -> 0, t4 miss -> file Acc@5 = 1/4
    assert a["final_context.file_acc@5"] == pytest.approx(0.25) and a["n_failed_primary"] == 2 and a["n_failed_hard"] == 1 and a["n_gate_parse_failures"] == 1
    sens_tbl = list(csv.reader(open(next(out.glob("table_vi_cost_routing_m_sensitivity.csv")))))
    assert any(r[1] == "GASR" and r[2].startswith("not run (incomplete: 2/4") for r in sens_tbl)
    assert any(r[1] == "Always-GRAPH" and r[2] == "not run" for r in sens_tbl)
    primary = list(csv.reader(open(next(out.glob("table_vi_cost_routing_m.csv")))))
    gapa = next(r for r in primary if r[0] == "swe-bench-lite" and r[1] == "GAPA")
    assert gapa[9] == "2" and gapa[10] == "1"                                    # failed (primary), failed (hard)
    # sensitivity scores the gate-failed t2 on its fallback output: file Acc@5 = (t1 + t2) / 4 = 0.5
    per = {r["task_id"]: r for r in csv.DictReader(open(out / "per_task.csv"))}
    assert float(per["t2"]["primary.final_context.file_acc@5"]) == 0 and float(per["t2"]["sens.final_context.file_acc@5"]) == 1
    assert float(per["t3"]["sens.final_context.file_acc@5"]) == 0               # hard failures stay zero


def test_tokens_quality_per_cost_and_route_shares(tmp_path, data):
    rows = [line("t1", ["a.py"]), line("t2", ["a.py", "b.py"], route="RAG"), line("t3", ["x"], route="LC"), line("t4", ["z.py"])]
    run = tmp_path / "runs"
    make_job(run, "GAPA", rows)
    s = M.build_tables(M.load_jobs([run]), data, tmp_path / "out", [])
    a = s["aggregates"]["m|swe-bench-lite|GAPA|full"]
    assert a["tokens_total_mean"] == 400 + 40 + 1600
    assert a["pct_GRAPH"] == 50 and a["pct_RAG"] == 25 and a["pct_LC"] == 25
    # function Acc@10 per task: t1 1, t2 1, t3 0 (gold 7 files, only 1 pred), t4 1 -> mean of acc / 2.04 = 3 / 4 / 2.04
    assert a["quality_per_cost"] == pytest.approx(0.75 / 2.04)


def test_gate_tokens_by_path(tmp_path, data):
    g1 = {"gapa_call1": {"prompt_eval_count": 300, "eval_count": 30}, "gapa_call2": None, "post_expand_judges": []}
    g2 = {"gapa_call1": {"prompt_eval_count": 300, "eval_count": 30}, "gapa_call2": {"prompt_eval_count": 1000, "eval_count": 50}, "post_expand_judges": [{"prompt_eval_count": 500, "eval_count": 20}]}
    rows = [line("t1", ["a.py"], gate=g1), line("t2", ["a.py"], gate=g2), line("t3", ["a.py"], gate=g2), line("t4", ["a.py"], gate=g1)]
    sr = [line(t, ["a.py"], gate={"gasr_gate": {"prompt_eval_count": 900, "eval_count": 60}, "post_expand_judges": []}) for t in ("t1", "t2", "t3", "t4")]
    run = tmp_path / "runs"
    make_job(run, "GAPA", rows)
    make_job(run, "GASR", sr)
    rows_ = M.gate_token_rows(M.load_jobs([run]))
    by = {(r["arm"], r["path"]): r["mean_gate_tokens"] for r in rows_}
    assert by[("GAPA", "GAPA call 1 only")] == 330 and by[("GAPA", "GAPA call 1 + call 2")] == 330 + 1050
    assert by[("GAPA", "GAPA entry gate (all tasks)")] == (330 * 2 + 1380 * 2) / 4 and by[("GASR", "GASR merged gate (call A)")] == 960


def test_merge_refuses_mismatched_fields_and_duplicates_and_warns_across_machines(tmp_path, capsys):
    import sys
    from harness import merge
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    make_job(a, "GAPA", [line("t1", ["a.py"])])
    make_job(b, "GASR", [line("t1", ["a.py"])], harness_commit="OTHER")
    make_job(c, "GASR", [line("t1", ["a.py"])], machine_id="m2", gpu_name="other")
    with pytest.raises(M.MergeError, match="harness_commit"):
        M.check_compat(M.load_jobs([a, b]))
    with pytest.raises(M.MergeError, match="duplicate"):
        M.check_compat(M.load_jobs([a, a]))
    w = M.check_compat(M.load_jobs([a, c]))
    assert w and "several machines" in w[0]
    sys.argv = ["merge", "--src", str(a), str(b), "--out", str(tmp_path / "merged_bad")]
    assert merge.main() == 2 and "[REFUSED]" in capsys.readouterr().out and not (tmp_path / "merged_bad").exists()
    sys.argv = ["merge", "--src", str(a), str(c), "--out", str(tmp_path / "merged_ok")]
    assert merge.main() == 0 and len(list((tmp_path / "merged_ok").glob("*/job.json"))) == 2
