"""Scorer v2 for LC routes: module / function ids come from the retrieved chunk each LC chunk came from."""
import pytest

from harness.locmap import Mapper, rescore_lc_final, resolve_lc_item

RET = {"functions": ["a.py:Foo.get", "a.py:Bar", "b.py:run"],
       "unmapped": [{"file": "c.py", "name": "CONST", "reason": "block_outside_any_definition"}]}


def test_unique_join_gives_function_and_module():
    r = resolve_lc_item("a.py", "get", "block_outside_any_definition", RET, None)
    assert (r["function"], r["module"], r["how"]) == ("a.py:Foo.get", "a.py:Foo", "join")
    r = resolve_lc_item("b.py", "run", "block_outside_any_definition", RET, None)
    assert (r["function"], r["module"]) == ("b.py:run", "b.py:run")


def test_originating_chunk_unmappable_stays_file_only():
    r = resolve_lc_item("c.py", "CONST", "block_outside_any_definition", RET, None)
    assert r["function"] is None and r["how"] == "join_unmapped"


def test_non_python_and_missing_without_mapper_do_not_resolve():
    assert resolve_lc_item("x.yml", None, "not_python", RET, None)["how"] == "not_python"
    assert resolve_lc_item("a.py", "nope", "block_outside_any_definition", RET, None)["how"] == "unresolved"


def test_ast_fallback_picks_nested_def_under_retrieved_function(tmp_path):
    (tmp_path / "a.py").write_text("def outer():\n    def inner():\n        pass\n    return inner\n\ndef other():\n    pass\n")
    ret = {"functions": ["a.py:outer"], "unmapped": []}
    r = resolve_lc_item("a.py", "inner", "block_outside_any_definition", ret, Mapper(tmp_path))
    assert (r["function"], r["how"]) == ("a.py:outer", "ast_retrieved")


def test_ambiguous_join_is_not_guessed(tmp_path):
    (tmp_path / "a.py").write_text("class X:\n    def get(self): pass\nclass Y:\n    def get(self): pass\n")
    ret = {"functions": ["a.py:X.get", "a.py:Y.get"], "unmapped": []}
    r = resolve_lc_item("a.py", "get", "block_outside_any_definition", ret, Mapper(tmp_path))
    assert r["function"] is None and r["how"] == "unresolved"


def test_rescore_keeps_files_dedupes_and_rejects_non_lc_lists():
    final = {"files": ["a.py", "b.py"], "modules": [], "functions": [], "n_items": 3, "unmapped_n": 3,
             "unmapped": [{"file": "a.py", "name": "get", "reason": "block_outside_any_definition"},
                          {"file": "a.py", "name": "get", "reason": "block_outside_any_definition"},
                          {"file": "b.py", "name": "run", "reason": "block_outside_any_definition"}]}
    new, recs = rescore_lc_final(final, RET, None)
    assert new["files"] == final["files"] and new["functions"] == ["a.py:Foo.get", "b.py:run"]
    assert new["modules"] == ["a.py:Foo", "b.py:run"] and len(recs) == 3
    with pytest.raises(ValueError):
        rescore_lc_final({**final, "functions": ["a.py:x"]}, RET, None)
