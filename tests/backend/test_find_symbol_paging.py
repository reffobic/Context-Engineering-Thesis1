"""find_symbol pages through the collection (chroma 'too many SQL variables' on very large indexes) and returns the same matches."""
from retrieval import retrieval_tool as rt


class FakeCollection:
    def __init__(self, metas, max_rows_per_get=None):
        self.metas, self.max, self.calls = metas, max_rows_per_get, 0

    def get(self, include=None, limit=None, offset=0, **_):
        self.calls += 1
        if limit is None and self.max is not None and len(self.metas) > self.max:
            raise RuntimeError("too many SQL variables")
        end = None if limit is None else offset + limit
        return {"metadatas": self.metas[offset:end]}


def _metas(n):
    metas = [{"name": f"f{i}", "qualified_name": f"a.py::f{i}", "chunk_type": "function", "file_path": "a.py", "start_line": i, "end_line": i + 1} for i in range(n)]
    metas.append({"name": "target", "qualified_name": "b.py::target", "chunk_type": "function", "file_path": "b.py", "start_line": 1, "end_line": 2})
    return metas


def test_find_symbol_works_where_one_unpaged_get_would_fail(monkeypatch):
    fake = FakeCollection(_metas(5000), max_rows_per_get=1000)
    monkeypatch.setattr(rt, "collection", fake)
    monkeypatch.setattr(rt, "_METADATA_PAGE", 700, raising=False)
    out = rt.find_symbol("target")
    assert [m["file"] for m in out] == ["b.py"] and fake.calls > 1


def test_paged_iteration_visits_every_row_exactly_once(monkeypatch):
    fake = FakeCollection(_metas(2001))
    monkeypatch.setattr(rt, "collection", fake)
    assert len(list(rt._iter_metadatas(500))) == 2002
