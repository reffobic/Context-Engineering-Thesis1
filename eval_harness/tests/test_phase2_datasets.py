"""Unit tests for Phase 2 dataset adapters (no network)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.phase2_datasets import build_repoprobe, build_sweqa, write_dataset


def test_build_sweqa_wave1_counts(tmp_path: Path):
    bench = tmp_path / "SWE-QA-Bench"
    bdir = bench / "Benchmark"
    bdir.mkdir(parents=True)
    for name, n in [("flask", 2), ("requests", 2), ("pytest", 2)]:
        lines = [
            json.dumps({"question": f"Q-{name}-{i}", "answer": f"A-{name}-{i}"})
            for i in range(n)
        ]
        (bdir / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ds = {
        "name": "swe-qa-w1",
        "kind": "swe-qa",
        "repos": ["flask", "requests", "pytest"],
        "expected_count": 6,
        "repo_map": {
            "flask": "pallets/flask",
            "requests": "psf/requests",
            "pytest": "pytest-dev/pytest",
        },
        "commits": {"flask": "85c5d93", "requests": "46e939b", "pytest": "5989efe"},
    }
    tasks, labels, meta = build_sweqa(ds, bench)
    assert len(tasks) == 6
    assert len(labels) == 6
    assert tasks[0]["instance_id"] == "flask-000"
    assert tasks[0]["repo"] == "pallets/flask"
    assert tasks[0]["query"].startswith("Q-flask")
    assert labels[0]["gold_answer"].startswith("A-flask")
    assert meta["counts_by_repo"] == {"flask": 2, "requests": 2, "pytest": 2}


def test_build_sweqa_rejects_wrong_count(tmp_path: Path):
    bench = tmp_path / "SWE-QA-Bench"
    (bench / "Benchmark").mkdir(parents=True)
    (bench / "Benchmark" / "flask.jsonl").write_text(
        json.dumps({"question": "q", "answer": "a"}) + "\n", encoding="utf-8"
    )
    (bench / "Benchmark" / "requests.jsonl").write_text(
        json.dumps({"question": "q", "answer": "a"}) + "\n", encoding="utf-8"
    )
    (bench / "Benchmark" / "pytest.jsonl").write_text(
        json.dumps({"question": "q", "answer": "a"}) + "\n", encoding="utf-8"
    )
    ds = {
        "repos": ["flask", "requests", "pytest"],
        "expected_count": 144,
        "repo_map": {"flask": "a/b", "requests": "c/d", "pytest": "e/f"},
        "commits": {"flask": "1", "requests": "2", "pytest": "3"},
    }
    with pytest.raises(RuntimeError, match="expected 144"):
        build_sweqa(ds, bench)


def test_build_repoprobe_checklist_fields(tmp_path: Path):
    root = tmp_path / "RepoProbe"
    (root / "dataset").mkdir(parents=True)
    info = [{
        "name": "org/demo",
        "primary_language": "Python",
        "snapshot": {"git": {"head": "abc123def456"}},
    }]
    (root / "repos_info.json").write_text(json.dumps(info), encoding="utf-8")
    csv = (
        "repo_name,question_id,discussion_id,taxonomy,difficulty,question,answer,checklist\n"
        "demo,q1,d1,Architecture,hard,What is X?,Gold A,\"item1|item2\"\n"
    )
    (root / "dataset" / "demo.csv").write_text(csv, encoding="utf-8")
    ds = {"repos": ["demo"]}
    tasks, labels, meta = build_repoprobe(ds, root)
    assert len(tasks) == 1
    assert tasks[0]["instance_id"] == "demo__q1"
    assert tasks[0]["repo"] == "org/demo"
    assert tasks[0]["base_commit"] == "abc123def456"
    assert labels[0]["checklist"] == "item1|item2"
    assert labels[0]["gold_answer"] == "Gold A"
    assert meta["total"] == 1


def test_write_dataset_sample_sha(tmp_path: Path):
    tasks = [
        {"instance_id": "flask-000", "repo": "pallets/flask", "base_commit": "85c5d93",
         "query": "q0", "suite": "swe-qa"},
        {"instance_id": "flask-001", "repo": "pallets/flask", "base_commit": "85c5d93",
         "query": "q1", "suite": "swe-qa"},
    ]
    labels = [
        {"instance_id": "flask-000", "gold_answer": "a0", "files": [], "suite": "swe-qa"},
        {"instance_id": "flask-001", "gold_answer": "a1", "files": [], "suite": "swe-qa"},
    ]
    m = write_dataset(tmp_path, "swe-qa-w1", tasks, labels, {"kind": "swe-qa"})
    assert m["count"] == 2
    sample = json.loads((tmp_path / "samples" / "swe-qa-w1.sample.json").read_text())
    assert sample["instance_ids"] == ["flask-000", "flask-001"]
    assert sample["fraction"] == 1.0
    tasks_blob = (tmp_path / "datasets" / "swe-qa-w1" / "tasks.jsonl").read_bytes()
    import hashlib
    assert sample["source_tasks_sha256"] == hashlib.sha256(tasks_blob).hexdigest()
    # slim tasks only
    line = json.loads(tasks_blob.splitlines()[0])
    assert set(line) == {"instance_id", "repo", "base_commit", "query"}
