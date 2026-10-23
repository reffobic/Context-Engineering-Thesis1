"""Ablation switches and the Graphify prefix variable: off = unchanged, on = the stated change only.

Runs the pipeline snapshot of the previous frozen SHA (before the switches existed) and of the new frozen
SHA against the fake Ollama server and the fixture index.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from conftest import run_cases
from harness.common import load_toml
from harness.instrument import STUB_PREFIX
from harness.pipeline_src import snapshot
from test_pipeline_e2e import CASES, Q, norm, request_view

pytestmark = pytest.mark.e2e
ABL = load_toml(__import__("pathlib").Path(__file__).resolve().parent.parent / "configs" / "ablations.toml")
SWITCHES = list(ABL["switches"])
EXPAND = {"id": "x_expand", "mode": "GAPA", "prompt": Q,
          "scenario": {"gapa1": False, "structure": "STRUCTURE", "post_expand": [True]}}
EXPAND2 = {"id": "x_expand2", "mode": "GAPA", "prompt": Q,
           "scenario": {"gapa1": False, "structure": "STRUCTURE", "post_expand": [False, True]}}
SUFF = {"id": "x_suff", "mode": "GAPA", "prompt": Q, "scenario": {"gapa1": True}}
GAPA_CASES = [SUFF, EXPAND, EXPAND2]


def without(pcfg, *names):
    p = copy.deepcopy(pcfg)
    for n in names:
        p["env_pinned"].pop(n, None)
    return p


@pytest.fixture(scope="module")
def snaps(pcfg, work):
    p = pcfg["pipeline"]
    return {"previous": snapshot(p["previous_frozen_sha"], work)[1], "new": snapshot(p["frozen_sha"], work)[1]}


def kw(index_dir, work, fake, exp, cases=CASES):
    return dict(index_dir=index_dir, work=work, fake=fake, cases=cases, exp=exp)


def setting_env(key):
    return {k: "0" for k in SWITCHES} | ABL["settings"][key]["env"]


def user_msg(o, role="gapa_gate_call1"):
    e = next(x for x in o["fake_log"] if x["role"] == role)
    return next(m["content"] for m in e["body"]["messages"] if m["role"] == "user")


# ── off = unchanged ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def off_runs(snaps, index_dir, work, fake, pcfg, exp):
    k = kw(index_dir, work, fake, exp)
    default = without(pcfg, "GRAPHIFY_PATH_PREFIX")                       # prefix variable unset: its code default
    bare = without(pcfg, "GRAPHIFY_PATH_PREFIX", *SWITCHES)               # no switch variable in the environment at all
    return {
        "previous": run_cases(snaps["previous"], tag="off_prev", pcfg=default, instrument=False, **k),
        "new_zero": run_cases(snaps["new"], tag="off_zero", pcfg=default, instrument=False, **k),
        "new_unset": run_cases(snaps["new"], tag="off_unset", pcfg=bare, instrument=False, **k),
        "new_wrapped": run_cases(snaps["new"], tag="off_wrapped", pcfg=default, instrument=True, **k),
    }


def test_switches_off_leave_every_output_and_request_unchanged(off_runs):
    base = off_runs["previous"]
    for tag in ("new_zero", "new_unset", "new_wrapped"):
        for cid, b in base.items():
            o = off_runs[tag][cid]
            assert b["error"] is None and o["error"] is None, (tag, cid)
            assert norm(b["result"]) == norm(o["result"]), (tag, cid)
            drop = () if tag != "new_wrapped" else ("num_ctx", "seed", "temperature")
            top = () if tag != "new_wrapped" else ("think", "keep_alive")
            assert request_view(b["fake_log"], drop, top) == request_view(o["fake_log"], drop, top) or tag == "new_wrapped", (tag, cid)


def test_gate_prompt_text_is_byte_identical_when_off(off_runs):
    a, b = user_msg(off_runs["previous"]["gapa_sufficient"]), user_msg(off_runs["new_zero"]["gapa_sufficient"])
    assert a == b and hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()


# ── Graphify prefix ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def prefix_runs(snaps, index_dir, work, fake, pcfg, exp):
    k = kw(index_dir, work, fake, exp, [EXPAND])
    return {
        "default": run_cases(snaps["new"], tag="pfx_default", pcfg=without(pcfg, "GRAPHIFY_PATH_PREFIX"), instrument=True, **k)["x_expand"],
        "empty": run_cases(snaps["new"], tag="pfx_empty", pcfg=pcfg, instrument=True, **k)["x_expand"],
    }


def test_negative_control_stub_neighbours_without_the_fix_and_none_with_it(prefix_runs):
    d, e = prefix_runs["default"]["record"]["stub_neighbors"], prefix_runs["empty"]["record"]["stub_neighbors"]
    assert d["stub_chunks"] > 0 and d["by_category"]["prefix_mismatch"] == d["stub_chunks"], d
    assert e["stub_chunks"] == 0 and e["neighbor_chunks"] > 0, e


# ── each switch on ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def on_runs(snaps, index_dir, work, fake, pcfg, exp):
    out = {}
    for key in ABL["settings"]:
        out[key] = run_cases(snaps["new"], tag=f"abl_{key}", pcfg=pcfg, instrument=True, extra_env=setting_env(key),
                             **kw(index_dir, work, fake, exp, GAPA_CASES))
    return out


def test_no_graph_expansion_returns_nothing_and_ends_in_lc(on_runs):
    o = on_runs["no_graph_expansion"]["x_expand"]
    assert o["error"] is None and o["result"]["route"] == "LC"
    assert [e["role"] for e in o["fake_log"]] == ["gapa_gate_call1", "gapa_gate_call2", "answer"]
    assert o["record"]["expansions"][0]["new_chunk_count"] == 0 and not o["record"]["expansions"][0]["neighbor_ids"]


def test_contains_only_logs_neighbours_against_full_expansion(on_runs):
    full = on_runs["full"]["x_expand"]["record"]["expansions"][0]
    c = on_runs["contains_only"]["x_expand"]["record"]["expansions"][0]
    co = c["contains_only"]
    assert co["full_expansion_total_pre_cap"] >= co["contains_total_pre_cap"] and co["returned"] == min(co["contains_total_pre_cap"], 8)
    assert co["full_expansion_after_cap"] == min(co["full_expansion_total_pre_cap"], 8)
    assert on_runs["contains_only"]["x_expand"]["record"]["contains_only"]["returned"] == co["returned"]
    assert len(c["neighbor_ids"]) <= len(full["neighbor_ids"])                                   # same graph, fewer edge types
    assert on_runs["full"]["x_expand"]["record"]["contains_only"] is None


def test_truncation_replaces_pruning_with_same_budget(on_runs):
    o = on_runs["truncation"]["x_expand"]["result"]["pipeline"]["stages"]
    kept = sum(max(1, len(c["text"]) // 4) for c in o["prune"]["raw"]["chunks"])
    assert 0 < kept <= 1800
    assert o["prune"]["raw"]["stats"]["method"] == "truncate" and o["prune"]["raw"]["stats"]["kept_est_tokens"] <= 1800
    full = on_runs["full"]["x_expand"]["result"]["pipeline"]["stages"]["prune"]["raw"]["stats"]
    assert full["method"] == "heuristic"


def test_no_keyword_channel_uses_only_the_vector_stream(on_runs):
    def sources(o):
        return sorted(o["result"]["pipeline"]["stages"]["retrieve"]["raw"][0]["retrieval_diagnostics"]["candidate_source_counts"])
    assert sources(on_runs["full"]["x_suff"]) == ["bm25", "exact", "vector"]
    assert sources(on_runs["no_keyword_channel"]["x_suff"]) == ["vector"]


def has_line(text, prefix):
    return any(l.startswith(prefix) for l in text.split("\n"))


def strip_lines(text, *prefixes):
    return "\n".join(l for l in text.split("\n") if not l.startswith(prefixes))


def test_gate_without_scores_only_removes_those_input_lines(on_runs):
    full, abl = user_msg(on_runs["full"]["x_suff"]), user_msg(on_runs["gate_no_scores"]["x_suff"])
    assert all(has_line(full, x) for x in ("top_score:", "score_margin:", "candidate_files:"))
    assert not any(has_line(abl, x) for x in ("top_score:", "score_margin:", "candidate_files:"))
    assert strip_lines(full, "top_score:", "score_margin:", "candidate_files:") == abl
    assert has_line(abl, "dependencies:") and ("sim_code:" in abl) == ("sim_code:" in full)          # dual-embedding fields untouched
    assert "## Step 3 — distribution pattern" in abl                                           # headings stay by default


def test_gate_without_dependency_count_only_removes_that_line(on_runs):
    full, abl = user_msg(on_runs["full"]["x_suff"]), user_msg(on_runs["gate_no_deps"]["x_suff"])
    assert has_line(full, "dependencies:") and not has_line(abl, "dependencies:")
    assert strip_lines(full, "dependencies:") == abl
    assert has_line(abl, "top_score:") and "## Step 4 — relationships" in abl
    assert "Imports/dependencies" in abl or "Dependencies:" in abl      # the chunk description still carries dependency text


def test_heading_control_removes_only_the_matching_empty_headings(on_runs):
    abl, ctl = user_msg(on_runs["gate_no_deps"]["x_suff"]), user_msg(on_runs["gate_no_deps_headings"]["x_suff"])
    assert "## Step 4 — relationships" in abl and "## Step 4 — relationships" not in ctl
    assert "## Step 5 — decision rule" in ctl and "Step 3" in ctl
    s = user_msg(on_runs["gate_no_scores_headings"]["x_suff"])
    full = user_msg(on_runs["full"]["x_suff"])
    # Step 3 keeps its heading while the dual-embedding lines (sim_*) are present under it
    assert ("## Step 3 — distribution pattern" in s) == ("sim_code:" in full)


def test_heading_control_alone_changes_nothing(snaps, index_dir, work, fake, pcfg, exp, on_runs):
    o = run_cases(snaps["new"], tag="abl_headings_only", pcfg=pcfg, instrument=True,
                  extra_env={**setting_env("full"), "ABL_GATE_DROP_HEADINGS": "1"}, **kw(index_dir, work, fake, exp, [SUFF]))
    assert user_msg(o["x_suff"]) == user_msg(on_runs["full"]["x_suff"])


def signature(o):
    r = o["result"]
    return json.dumps({"route": r["route"], "inject": norm(r["pipeline"]["stages"].get("inject", {}).get("raw")),
                       "gate": user_msg(o) if any(e["role"] == "gapa_gate_call1" for e in o["fake_log"]) else None,
                       "roles": [e["role"] for e in o["fake_log"]],
                       "expansions": norm(r["pipeline"]["stages"].get("graph_expand", {}).get("summary"))},
                      sort_keys=True, default=str)


def test_every_ablation_differs_from_full_gapa(on_runs):
    full = {cid: signature(o) for cid, o in on_runs["full"].items()}
    differs = {}
    for key, s in ABL["settings"].items():
        if key == "full" or s.get("control"):
            continue
        differs[key] = [cid for cid, o in on_runs[key].items() if signature(o) != full[cid]]
    assert all(differs.values()), differs
