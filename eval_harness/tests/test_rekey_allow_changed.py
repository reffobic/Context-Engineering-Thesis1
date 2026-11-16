"""index_cache.prove_indexer_identity: an explicitly allowed changed file is recorded, anything else still refuses."""

import subprocess
from pathlib import Path

import pytest

from harness import index_cache as ic


def _git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    r = tmp_path / "r"
    (r / "backend" / "indexing").mkdir(parents=True)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "backend/indexing/a.py").write_text("a = 1\n")
    (r / "backend/indexing/b.py").write_text("b = 1\n")
    (r / "requirements.txt").write_text("x\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "old")
    old = _git(r, "rev-parse", "HEAD")
    (r / "backend/indexing/a.py").write_text("a = 2\n")
    _git(r, "commit", "-q", "-am", "new")
    new = _git(r, "rev-parse", "HEAD")
    import harness.pipeline_src as ps

    monkeypatch.setattr(ps, "snapshot", lambda sha, d: (None, tmp_path))
    monkeypatch.setattr(ic, "indexer_import_closure", lambda root: ["backend/indexing/a.py", "backend/indexing/b.py"])
    monkeypatch.setattr(ic, "INDEXER_PATHS", [])
    return r, old, new, tmp_path


def test_changed_closure_file_refuses_by_default(repo):
    r, old, new, d = repo
    with pytest.raises(RuntimeError, match="a.py differs"):
        ic.prove_indexer_identity(r, old, new, d)


def test_allowed_file_is_recorded_not_refused(repo):
    r, old, new, d = repo
    p = ic.prove_indexer_identity(r, old, new, d, ("backend/indexing/a.py",))
    assert set(p["allowed_changed_files"]) == {"backend/indexing/a.py"}
    assert set(p["object_hashes"]) == {"backend/indexing/b.py"}          # b.py is proven identical


def test_allow_list_does_not_cover_other_files(repo):
    r, old, new, d = repo
    (r / "backend/indexing/b.py").write_text("b = 2\n")
    _git(r, "commit", "-q", "-am", "b changed")
    with pytest.raises(RuntimeError, match="b.py differs"):
        ic.prove_indexer_identity(r, old, _git(r, "rev-parse", "HEAD"), d, ("backend/indexing/a.py",))


def test_allowing_a_file_that_did_not_change_is_an_error(repo):
    r, old, new, d = repo
    with pytest.raises(RuntimeError, match="did not change"):
        ic.prove_indexer_identity(r, old, new, d, ("backend/indexing/a.py", "backend/indexing/b.py"))


def test_evidence_is_required_with_an_allow_list(repo):
    r, old, new, d = repo
    with pytest.raises(RuntimeError, match="needs --evidence"):
        ic.rekey(d, r, old, new, {}, None, True, ("backend/indexing/a.py",), "")
