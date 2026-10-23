import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.locmap import Mapper, collect_defs  # noqa: E402

SRC = '''import os

X = 1


def top(a):
    def inner(b):
        return b
    return inner(a)


class A:
    def __init__(self):
        pass

    @staticmethod
    def m(x):
        return x

    class B:
        def deep(self):
            return 1


async def co():
    return 2
'''


def mapper(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(SRC, encoding="utf-8")
    (tmp_path / "pkg" / "bad.py").write_text("def (:\n", encoding="utf-8")
    return Mapper(tmp_path)


def test_collect_defs_qualnames():
    q = {d.qualname for d in collect_defs(SRC)}
    assert q == {"top", "A", "A.__init__", "A.m", "A.B", "A.B.deep", "co"}      # nested 'inner' is not its own def


def test_function_module_mapping(tmp_path):
    m = mapper(tmp_path)
    it = lambda n, s, t="function": {"file": "pkg/m.py", "name": n, "start_line": s, "chunk_type": t}
    assert m.map_item(it("top", 6))["function"] == "pkg/m.py:top"
    assert m.map_item(it("top", 6))["module"] == "pkg/m.py:top"                # top-level function is its own module
    inner = m.map_item(it("inner", 7))
    assert inner["function"] == "pkg/m.py:top"                                  # nested -> enclosing function
    meth = m.map_item(it("m", 17))
    assert meth["function"] == "pkg/m.py:A.m" and meth["module"] == "pkg/m.py:A"
    init = m.map_item(it("__init__", 13))
    assert init["function"] == "pkg/m.py:A" and init["module"] == "pkg/m.py:A"  # .__init__ stripped
    deep = m.map_item(it("deep", 21))
    assert deep["function"] == "pkg/m.py:A.B.deep" and deep["module"] == "pkg/m.py:A"
    cls = m.map_item(it("A", 12, "class"))
    assert cls["function"] == "pkg/m.py:A" and cls["module"] == "pkg/m.py:A"


def test_unmappable_chunks_are_reported_and_files_kept(tmp_path):
    m = mapper(tmp_path)
    items = [{"file": "pkg/m.py", "name": "", "start_line": 1, "end_line": 3, "chunk_type": "block"},
             {"file": "README.md", "name": "", "start_line": 1, "chunk_type": "block"},
             {"file": "pkg/bad.py", "name": "f", "start_line": 1, "chunk_type": "function"},
             {"file": "pkg/gone.py", "name": "f", "start_line": 1, "chunk_type": "function"},
             {"file": "pkg/m.py", "name": "co", "start_line": 25, "chunk_type": "function"},
             {"file": "pkg/m.py", "name": "top", "start_line": 6, "chunk_type": "function"},
             {"file": "pkg/m.py", "name": "inner", "start_line": 7, "chunk_type": "function"}]
    r = m.rank_lists(items)
    assert r["files"] == ["pkg/m.py", "README.md", "pkg/bad.py", "pkg/gone.py"]
    assert r["functions"] == ["pkg/m.py:co", "pkg/m.py:top"]                    # de-duplicated, first rank kept
    assert r["modules"] == ["pkg/m.py:co", "pkg/m.py:top"]
    assert [u["reason"] for u in r["unmapped"]] == ["block_outside_any_definition", "not_python", "syntax_error", "file_not_in_checkout"]
