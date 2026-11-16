"""LocAgent baseline adapter: MRR merge identical to LocAgent's, scoring through the harness acc_at, missing tasks count as 0."""

import json

from harness.locagent_baseline import merge_mrr, run, score_task

GOLD = {"instance_id": "t1", "files": ["a.py", "b.py"], "modules": ["a.py:A"], "functions": ["a.py:A.f"]}


def test_merge_mrr_matches_locagent_weighting_and_tie_order():
    # a.py: 1 + 1/2 = 1.5 ; b.py: 1/2 + 1 = 1.5 ; c.py: 1/3. Tie keeps first-seen order (a before b).
    assert merge_mrr([["a.py", "b.py", "c.py"], ["b.py", "a.py"]]) == ["a.py", "b.py", "c.py"]
    assert merge_mrr([["x"], ["y", "x"]]) == ["x", "y"]            # x: 1 + 1/2, y: 1
    assert merge_mrr([]) == []


def test_score_task_uses_the_cap_rule_and_normalises_init():
    row = {"found_files": [["c.py", "a.py", "b.py"]], "found_modules": [["a.py:A"]], "found_entities": [["a.py:A.__init__", "a.py:A.f"]]}
    s = score_task(row, GOLD)
    assert s["file_acc@1"] == 0.0 and s["file_acc@3"] == 1.0     # cap rule: @1 needs min(2,1)=1 hit (c.py is wrong), @3 needs both gold files
    assert s["module_acc@5"] == 1.0
    assert s["function_acc@5"] == 1.0                              # ".__init__" is folded into the class entry; gold function still found


def test_missing_output_scores_zero():
    assert set(score_task(None, GOLD).values()) == {0.0}


def test_run_reports_missing_and_means(tmp_path):
    (tmp_path / "samples").mkdir()
    (tmp_path / "datasets" / "swe-bench-lite").mkdir(parents=True)
    (tmp_path / "samples" / "swe-bench-lite.sample.json").write_text(json.dumps({"instance_ids": ["t1", "t2"]}), encoding="utf-8")
    gold2 = dict(GOLD, instance_id="t2")
    (tmp_path / "datasets" / "swe-bench-lite" / "labels.jsonl").write_text(json.dumps(GOLD) + "\n" + json.dumps(gold2) + "\n", encoding="utf-8")
    out = tmp_path / "loc.jsonl"
    out.write_text(json.dumps({"instance_id": "t1", "found_files": [["a.py", "b.py"]], "found_modules": [["a.py:A"]],
                               "found_entities": [["a.py:A.f"]]}) + "\n", encoding="utf-8")
    res = run(out, tmp_path, "label")
    r = res["swe-bench-lite"]
    assert r["n_tasks"] == 2 and r["n_with_output"] == 1 and r["missing"] == ["t2"]
    assert r["metrics"]["file_acc@5"] == 0.5 and r["metrics"]["function_acc@10"] == 0.5
    assert "loc-bench" not in res
