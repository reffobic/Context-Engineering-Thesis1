"""End-to-end tests of the unmodified pipeline against a fake Ollama server.

Covers: GAPA (sufficient / call 2 / expansion paths), GASR and Self-Route unchanged by the seed
alignment, GAPA differing only in its expansion seeds, wrappers not changing outputs, the HTTP-shim
token sum against the pipeline's trace, call_graph.json handling and MAX_CONTEXT_TOKENS logging.
"""
from __future__ import annotations

import copy
import importlib.util
import json

import pytest

from conftest import run_cases
from harness.instrument import JobFailure, check_structure_rerank, gapa_gate_summary

pytestmark = pytest.mark.e2e

Q = "test_cart_total_applies_discount and test_apply_discount_cart_total: what do these tests check about the cart total discount?"
CASES = [
    {"id": "gapa_sufficient", "mode": "GAPA", "prompt": Q, "scenario": {"gapa1": True}},
    {"id": "gapa_call2_context", "mode": "GAPA", "prompt": Q, "scenario": {"gapa1": False, "structure": "CONTEXT"}},
    {"id": "gapa_expand_1hop", "mode": "GAPA", "prompt": Q,
     "scenario": {"gapa1": False, "structure": "STRUCTURE", "post_expand": [True]}},
    {"id": "gapa_expand_2hop", "mode": "GAPA", "prompt": Q,
     "scenario": {"gapa1": False, "structure": "STRUCTURE", "post_expand": [False, True]}},
    {"id": "gapa_no_call_graph", "mode": "GAPA", "prompt": Q, "scenario": {"gapa1": True}, "hide_call_graph": True},
    {"id": "gasr_sufficient", "mode": "GASR", "prompt": Q, "scenario": {"gasr": {"sufficient": True}}},
    {"id": "gasr_context", "mode": "GASR", "prompt": Q, "scenario": {"gasr": {"sufficient": False, "need": "CONTEXT"}}},
    {"id": "gasr_expand_1hop", "mode": "GASR", "prompt": Q,
     "scenario": {"gasr": {"sufficient": False, "need": "STRUCTURE"}, "post_expand": [True]}},
    {"id": "self_route_sufficient", "mode": "SELF_ROUTE", "prompt": Q, "scenario": {"selfroute": True}},
    {"id": "self_route_lc", "mode": "SELF_ROUTE", "prompt": Q, "scenario": {"selfroute": False}},
    {"id": "rag", "mode": "RAG", "prompt": Q, "scenario": {}},
    {"id": "lc", "mode": "LC", "prompt": Q, "scenario": {}},
]
UNCHANGED = [c["id"] for c in CASES if not c["id"].startswith("gapa_")]
NO_EXPANSION = ["gapa_sufficient", "gapa_call2_context", "gapa_no_call_graph"]
EXPANSION = ["gapa_expand_1hop", "gapa_expand_2hop"]


# server.py adds re_prune_kept / re_prune_kind to the last expansion entry only when the integer
# millisecond timer of the re-prune is non-zero, so these keys appear or not depending on speed
# (observed without any wrapper). They are therefore excluded from output comparison; the harness
# reads the re-pruned chunks from its own wrapper instead.
TIMING_CONDITIONAL = ("re_prune_kept", "re_prune_kind")


def norm(o):
    """Drop timings, which differ between runs."""
    if isinstance(o, dict):
        return {k: norm(v) for k, v in o.items()
                if not (k.endswith("_ms") or k in ("duration_ms", "ms", "wall_ms", "time", "timestamp") + TIMING_CONDITIONAL)}
    if isinstance(o, list):
        return [norm(v) for v in o]
    return o


def diffs(a, b, path="", out=None):
    """Paths where two normalised structures differ (for readable failures)."""
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            diffs(a.get(k), b.get(k), f"{path}/{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            diffs(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append((path, str(a)[:150], str(b)[:150]))
    return out


def request_view(log, drop_options=("num_ctx", "seed", "temperature"), drop_top=("think", "keep_alive")):
    """The requests the pipeline sent, minus the options the shim is allowed to set."""
    out = []
    for e in log:
        b = copy.deepcopy(e["body"])
        for k in drop_top:
            b.pop(k, None)
        for k in drop_options:
            b.get("options", {}).pop(k, None)
        out.append({"role": e["role"], "path": e["path"], "body": b})
    return out


@pytest.fixture(scope="module")
def runs(snapshots, index_dir, work, fake, pcfg, exp):
    kw = dict(index_dir=index_dir, work=work, fake=fake, cases=CASES, pcfg=pcfg, exp=exp)
    return {
        "base": run_cases(snapshots["base"], tag="base", instrument=False, **kw),
        "aligned": run_cases(snapshots["aligned"], tag="aligned", instrument=False, **kw),
        "wrapped": run_cases(snapshots["aligned"], tag="wrapped", instrument=True, **kw),
    }


def stages(o):
    return o["result"]["pipeline"]["stages"]


def seeds(o):
    return stages(o)["graph_expand"]["summary"]["expansions"][0]["seed_keys"]


def roles(o):
    return [e["role"] for e in o["fake_log"]]


# ── GAPA end to end ──────────────────────────────────────────────────────────

def test_gapa_sufficient_path(runs):
    for tag in ("aligned", "wrapped"):
        o = runs[tag]["gapa_sufficient"]
        assert o["error"] is None and o["result"]["route"] == "RAG"
        assert roles(o) == ["gapa_gate_call1", "answer"]


def test_gapa_call2_path_goes_to_lc(runs):
    for tag in ("aligned", "wrapped"):
        o = runs[tag]["gapa_call2_context"]
        assert o["error"] is None and o["result"]["route"] == "LC"
        assert roles(o) == ["gapa_gate_call1", "gapa_gate_call2", "answer"]


def test_gapa_expansion_paths(runs):
    for tag in ("aligned", "wrapped"):
        o1, o2 = runs[tag]["gapa_expand_1hop"], runs[tag]["gapa_expand_2hop"]
        assert o1["error"] is None and o1["result"]["route"] == "GRAPH" and o1["result"]["pipeline"]["hops_used"] == 1
        assert roles(o1) == ["gapa_gate_call1", "gapa_gate_call2", "post_expand_judge", "answer"]
        assert o2["result"]["route"] == "GRAPH" and o2["result"]["pipeline"]["hops_used"] == 2
        assert roles(o2) == ["gapa_gate_call1", "gapa_gate_call2", "post_expand_judge", "post_expand_judge", "answer"]


# ── seed alignment: nothing else changed ─────────────────────────────────────

def test_gasr_self_route_rag_lc_outputs_unchanged(runs):
    for cid in UNCHANGED:
        a, b = runs["base"][cid], runs["aligned"][cid]
        assert a["error"] is None and b["error"] is None, cid
        assert norm(a["result"]) == norm(b["result"]), cid
        assert request_view(a["fake_log"], (), ()) == request_view(b["fake_log"], (), ()), cid


def test_gapa_without_expansion_unchanged(runs):
    for cid in NO_EXPANSION:
        a, b = runs["base"][cid], runs["aligned"][cid]
        assert norm(a["result"]) == norm(b["result"]), cid
        assert request_view(a["fake_log"], (), ()) == request_view(b["fake_log"], (), ()), cid


def test_gapa_differs_only_in_expansion_seeds(runs):
    for cid in EXPANSION:
        a, b = runs["base"][cid], runs["aligned"][cid]
        sa, sb = stages(a), stages(b)
        # everything before the expansion is identical, including the first gate decision
        for st in ("retrieve", "prune"):
            assert norm(sa[st]) == norm(sb[st]), (cid, st)
        assert norm(sa["assess"]["summary"]["trace"][0]) == norm(sb["assess"]["summary"]["trace"][0])
        assert [e["role"] for e in a["fake_log"][:2]] == [e["role"] for e in b["fake_log"][:2]]
        assert a["fake_log"][0] == b["fake_log"][0] and a["fake_log"][1] == b["fake_log"][1]
        # the seeds differ: baseline expands from raw hits (tests included), aligned from pruned non-test chunks
        assert any("/tests/" in k for k in seeds(a)), seeds(a)
        assert not any("/tests/" in k for k in seeds(b)), seeds(b)
        assert set(seeds(b)) < set(seeds(a))
        differing = {k for k in sa if norm(sa[k]) != norm(sb[k])}
        assert differing <= {"assess", "graph_expand", "re_prune", "inject", "generate"}, differing
        assert "graph_expand" in differing


def test_gapa_seeds_equal_gasr_seeds(runs):
    gasr = runs["aligned"]["gasr_expand_1hop"]
    for cid in EXPANSION:
        assert seeds(runs["aligned"][cid]) == seeds(gasr)


# ── wrappers do not change behaviour ─────────────────────────────────────────

def test_wrapped_outputs_equal_plain_outputs(runs):
    for cid in [c["id"] for c in CASES]:
        a, b = runs["aligned"][cid], runs["wrapped"][cid]
        assert a["error"] is None and b["error"] is None, cid
        assert norm(a["result"]) == norm(b["result"]), (cid, diffs(norm(a["result"]), norm(b["result"]))[:6])
        assert request_view(a["fake_log"]) == request_view(b["fake_log"]), cid


def test_shim_sets_uniform_options_and_logs_originals(runs):
    asked = {"gapa_gate_call1": 2048, "gapa_gate_call2": 4096, "post_expand_judge": 4096, "gasr_gate": 4096,
             "selfroute_judge": 4096}
    for cid, o in runs["wrapped"].items():
        for e in o["fake_log"]:
            opt = e["body"]["options"]
            assert opt["num_ctx"] == 32768 and opt["seed"] == 42 and e["body"]["think"] is False
            assert e["body"]["keep_alive"] == "10m"
        for c in o["record"]["ollama_calls"]:
            if c["role"] in asked:
                assert c["shim_changed"]["num_ctx"] == {"asked": asked[c["role"]], "set": 32768}, (cid, c["role"])
            assert c["num_ctx"] == 32768 and c["truncation_suspected"] is False


# ── token reconciliation ─────────────────────────────────────────────────────

def test_shim_token_sum_equals_trace_token_usage(runs):
    mismatches, unreported = [], []
    for cid, o in runs["wrapped"].items():
        r = o["record"]["token_reconciliation"]
        if not r["trace_reported"]:
            unreported.append(cid)
        elif not r["match"]:
            mismatches.append((cid, r))
    assert mismatches == [], mismatches
    # modes whose pipeline output carries no per-call usage cannot be reconciled; the shim is the only source
    assert set(unreported) == {"self_route_sufficient", "self_route_lc", "rag", "lc"}


def test_gapa_gate_summary_call1_call2(runs):
    recs = [o["record"] for cid, o in runs["wrapped"].items() if cid.startswith("gapa_")]
    s = gapa_gate_summary(recs)
    assert s["n_gapa"] == 5 and s["share_needing_call2"] == pytest.approx(3 / 5)
    assert s["mean_tokens_sufficient_path"] is not None and s["mean_tokens_call2_path"] > s["mean_tokens_sufficient_path"]


# ── per-task logging ─────────────────────────────────────────────────────────

def test_record_contents(runs):
    r = runs["wrapped"]["gapa_expand_1hop"]["record"]
    assert r["stage_ms"]["retrieve"] > 0 and r["retrieval"]["ms"] == r["stage_ms"]["retrieve"]
    assert r["retrieval"]["retrieved"] and r["retrieval"]["ranked_candidates"]
    assert r["retrieval"]["parent_context_on_retrieval_output"] == [False] * 5   # lazy parent context: not present
    assert r["seed_pool"]["tier"] == "pruned_non_test" and r["seed_pool"]["size"] == len(r["seed_pool"]["seeds"]) > 0
    assert r["seed_pool"]["seed_has_test_chunk"] is False
    assert r["gate"]["gapa_call1"] and r["gate"]["gapa_call2"] and len(r["gate"]["post_expand_judges"]) == 1
    assert r["structure_rerank"]["applied"] is True
    assert r["answer_call"]["num_ctx"] == 32768 and r["answer_call"]["prompt_eval_over_num_ctx"] < 1
    assert r["inject"]["hit_max_context_tokens"] is False and r["inject"]["max_context_tokens"] == 24000
    lc = runs["wrapped"]["lc"]["record"]
    assert lc["stage_ms"]["parent_context"] > 0                                 # reconstruct_file_context is timed
    assert lc["retrieval"]["parent_context_on_retrieval_output"] == [False] * 5
    assert runs["wrapped"]["gapa_sufficient"]["record"]["stage_ms"]["parent_context"] == 0


def test_missing_call_graph_is_detected_and_fails_the_job(runs):
    ok = runs["wrapped"]["gapa_sufficient"]["record"]
    check_structure_rerank(ok)
    bad = runs["wrapped"]["gapa_no_call_graph"]["record"]
    assert bad["structure_rerank"]["called"] and bad["structure_rerank"]["call_graph_json_exists"] is False
    assert bad["structure_rerank"]["applied"] is False
    with pytest.raises(JobFailure):
        check_structure_rerank(bad)


def test_max_context_tokens_hit_is_logged(snapshots, index_dir, work, fake, pcfg, exp):
    out = run_cases(snapshots["aligned"], index_dir, work, fake, [CASES[0], CASES[-1]], "trunc", pcfg, exp, True,
                    extra_env={"MAX_CONTEXT_TOKENS": "50"})
    for cid in ("gapa_sufficient", "lc"):
        inj = out[cid]["record"]["inject"]
        assert inj["max_context_tokens"] == 50 and inj["hit_max_context_tokens"] is True


# ── build_gasr_seed_pool fallbacks (pure function) ───────────────────────────

def _load_seeds(snapshots):
    path = snapshots["aligned"] / "backend" / "diamond" / "gasr_seeds.py"
    spec = importlib.util.spec_from_file_location("gasr_seeds_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_seed_pool_fallback_order(snapshots):
    from harness.instrument import seed_tier
    m = _load_seeds(snapshots)
    t = lambda f: {"metadata": {"file": f}}
    src, tst = t("pkg/a.py"), t("tests/test_a.py")
    cases = [
        (([src, tst], [src]), [src], "pruned_non_test"),
        (([tst], [src, tst]), [src], "raw_non_test"),        # no non-test pruned chunk: falls back to RAW non-test hits
        (([tst], [tst]), [tst], "pruned_incl_tests"),         # nothing non-test anywhere: pruned chunks, tests included
        (([], [tst]), [tst], "raw_incl_tests"),
        (([], []), [], "empty"),
    ]
    for (pruned, raw), expected, tier in cases:
        assert m.build_gasr_seed_pool(pruned, raw) == expected
        assert seed_tier(pruned, raw, m) == tier


def test_seed_tier_and_working_tree_database_never_opened(runs, index_dir, snapshots, work, fake, pcfg, exp):
    from harness.instrument import seed_tier_shares
    recs = []
    for cid, o in runs["wrapped"].items():
        r = dict(o["record"], arm=o["result"]["pipeline"]["mode"], instance_id=cid)
        recs.append(r)
    sh = seed_tier_shares(recs)
    assert sh["GAPA"]["share"] == {"pruned_non_test": 1.0} and sh["GASR"]["share"] == {"pruned_non_test": 1.0}
    # the runner installed the guard for the working-tree databases; opening one must fail loudly
    import subprocess
    import sys
    from conftest import HARNESS, SRC
    from harness.pipelineenv import forbidden_chroma_paths
    code = ("import sys; sys.path.insert(0, %r); from harness.pipelineenv import install_chroma_guard, forbidden_chroma_paths;"
            "from pathlib import Path; import chromadb; install_chroma_guard(forbidden_chroma_paths(Path(%r)));"
            "chromadb.PersistentClient(path=%r)") % (str(SRC), str(HARNESS.parent), str(HARNESS.parent / "backend" / "chroma_db"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode != 0 and "refusing to open working-tree Chroma database" in r.stderr
