"""The CHROMA_PATH edit to retrieval_tool.py (patches/0001) must not change any pipeline output.

Three runs of the same cases: unpatched; patched with CHROMA_PATH unset (identical default);
patched with CHROMA_PATH pointing at an index outside backend/ (backend/chroma_db absent).
"""
from __future__ import annotations

import pytest

from conftest import HARNESS, run_cases
from test_pipeline_e2e import CASES, norm, request_view

pytestmark = pytest.mark.e2e
PATCH = HARNESS / "patches" / "0001-retrieval-tool-chroma-path-env.patch"
PICK = {"gapa_sufficient", "gapa_expand_1hop", "gasr_expand_1hop", "self_route_lc", "lc", "rag"}


@pytest.fixture(scope="module")
def three(snapshots, index_dir, work, fake, pcfg, exp):
    cases = [c for c in CASES if c["id"] in PICK]
    kw = dict(index_dir=index_dir, work=work, fake=fake, cases=cases, pcfg=pcfg, exp=exp, instrument=False)
    return {
        "unpatched": run_cases(snapshots["aligned"], tag="cp_unpatched", chroma_mode="default", **kw),
        "patched_default": run_cases(snapshots["aligned"], tag="cp_default", chroma_mode="default", patch=PATCH, **kw),
        "patched_external": run_cases(snapshots["aligned"], tag="cp_external", chroma_mode="external", patch=PATCH, **kw),
    }


def test_patch_touches_one_line_only():
    lines = PATCH.read_text(encoding="utf-8").splitlines()
    assert [x for x in lines if x.startswith("-") and not x.startswith("---")] == [
        '-CHROMA_PATH     = str(Path(__file__).resolve().parent.parent / "chroma_db")']
    assert len([x for x in lines if x.startswith("+") and not x.startswith("+++")]) == 1


def test_outputs_identical_with_default_and_with_external_path(three):
    for tag in ("patched_default", "patched_external"):
        for cid, base in three["unpatched"].items():
            o = three[tag][cid]
            assert base["error"] is None and o["error"] is None, (tag, cid)
            assert norm(base["result"]) == norm(o["result"]), (tag, cid)
            assert request_view(base["fake_log"], (), ()) == request_view(o["fake_log"], (), ()), (tag, cid)


def test_external_run_really_read_the_external_index(three):
    # the empty database the pipeline creates under backend/ would give no hits; identical non-empty
    # retrieval proves the external index was the one opened
    o = three["patched_external"]["gapa_sufficient"]["result"]["pipeline"]["stages"]["retrieve"]
    assert o["raw"] and len(o["raw"]) == 5


def test_negative_control_unpatched_pipeline_ignores_the_variable(snapshots, index_dir, work, fake, pcfg, exp, three):
    """Without the patch, an external CHROMA_PATH is ignored and the (empty) local database is read:
    the same comparison must then fail, proving the test can detect a wrong index."""
    cases = [c for c in CASES if c["id"] == "gapa_sufficient"]
    out = run_cases(snapshots["aligned"], index_dir, work, fake, cases, "cp_neg", pcfg, exp, False, chroma_mode="external")
    o, base = out["gapa_sufficient"], three["unpatched"]["gapa_sufficient"]
    assert o["error"] is not None or norm(o["result"]) != norm(base["result"])
