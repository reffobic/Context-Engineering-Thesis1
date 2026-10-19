import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.datasets import build_records, derive_labels, safe_name  # noqa: E402


def _row(**kw):
    base = {"instance_id": "a__b-1", "repo": "a/b", "base_commit": "c" * 40,
            "problem_statement": "the issue", "patch": "SECRET PATCH", "hints_text": "HINT",
            "edit_functions": ["pkg/m.py:Cls.meth", "pkg/m.py:top", "pkg/n.py:A.B.inner"],
            "added_functions": ["pkg/new.py:f"], "category": "Bug Report"}
    base.update(kw)
    return base


def test_derive_labels():
    lab = derive_labels(_row())
    assert lab["files"] == ["pkg/m.py", "pkg/n.py"]
    # LocAgent: module = first dotted component; a top-level function is its own module
    assert lab["modules"] == ["pkg/m.py:Cls", "pkg/m.py:top", "pkg/n.py:A"]
    assert lab["functions"] == ["pkg/m.py:Cls.meth", "pkg/m.py:top", "pkg/n.py:A.B.inner"]


def test_init_stripped_and_deduplicated():
    lab = derive_labels(_row(edit_functions=["p.py:C.__init__", "p.py:C", "p.py:C.m"]))
    assert lab["functions"] == ["p.py:C", "p.py:C.m"]
    assert lab["modules"] == ["p.py:C"]
    assert lab["functions_raw"] == ["p.py:C.__init__", "p.py:C", "p.py:C.m"]


def test_tasks_never_contain_gold_or_patch():
    tasks, labels = build_records([_row()])
    blob = str(tasks)
    for leak in ("SECRET PATCH", "HINT", "pkg/m.py", "Cls", "Bug Report"):
        assert leak not in blob
    assert set(tasks[0]) == {"instance_id", "repo", "base_commit", "query"}
    assert labels[0]["files"]


def test_safe_name_is_portable():
    assert safe_name("owner/repo") == "owner__repo"
    assert "/" not in safe_name("a/b") and "\\" not in safe_name("a/b")
