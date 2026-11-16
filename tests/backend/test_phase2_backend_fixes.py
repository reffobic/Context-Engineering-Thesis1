"""Tests for the Phase 2 backend fixes B (per-file chunk cap), C (LC truncation)
and D (graph expansion: edge types + oversized neighbours).

Pure/offline: no Chroma, no models, no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.injection.prompt_builder import (  # noqa: E402
    CONTEXT_TRUNCATED_TAG,
    PromptBuilder,
)
from backend.indexing.graphify_adapter import CodeGraphAdapter  # noqa: E402
from backend.pruning import file as pruning  # noqa: E402


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


chunk_select = _load("chunk_select_under_test", "backend/retrieval/chunk_select.py")
select_chunks = chunk_select._select_chunks_for_file


# ── B: per-file chunk selection ──────────────────────────────────────────────

def _file(n: int, size: int = 10):
    """n chunks, chunk i spans lines [i*size+1, (i+1)*size]."""
    return [
        (f"doc{i}", {"start_line": i * size + 1, "end_line": (i + 1) * size, "name": f"f{i}"})
        for i in range(n)
    ]


def _names(sel):
    return [m["name"] for _d, m in sel]


def test_small_file_kept_whole_in_line_order():
    paired = list(reversed(_file(4)))
    assert _names(select_chunks(paired, [(1, 10)], cap=10)) == ["f0", "f1", "f2", "f3"]


def test_retrieved_chunk_beyond_first_n_is_kept():
    """The old behaviour (first N in line order) lost chunk 30 of 40."""
    paired = _file(40)
    hit = (301, 310)  # chunk f30
    sel = select_chunks(paired, [hit], cap=10)
    names = _names(sel)
    assert len(names) == 10
    assert "f30" in names
    # nearest siblings surround it
    assert {"f29", "f31"} <= set(names)
    # output stays in line order
    assert names == sorted(names, key=lambda n: int(n[1:]))


def test_all_retrieved_kept_before_neighbours():
    paired = _file(40)
    hits = [(1, 10), (201, 210), (391, 400)]  # f0, f20, f39
    names = set(_names(select_chunks(paired, hits, cap=10)))
    assert {"f0", "f20", "f39"} <= names


def test_more_hits_than_cap_keeps_best_ranked():
    paired = _file(40)
    hits = [(i * 10 + 1, (i + 1) * 10) for i in (35, 3, 20, 7)]  # rank order
    names = _names(select_chunks(paired, hits, cap=2))
    assert set(names) == {"f35", "f3"}


def test_no_hits_degrades_to_first_n():
    paired = _file(40)
    assert _names(select_chunks(paired, [], cap=3)) == ["f0", "f1", "f2"]
    # hit spans that match nothing also degrade gracefully
    assert _names(select_chunks(paired, [(9999, 9999)], cap=3)) == ["f0", "f1", "f2"]


def test_garbled_metadata_does_not_raise():
    paired = [("a", {"start_line": "x"}), ("b", {}), ("c", {"start_line": 5, "end_line": None})]
    assert len(select_chunks(paired, [(5, 5)], cap=2)) == 2


def test_cap_never_exceeded_and_zero_cap_empty():
    paired = _file(25)
    assert len(select_chunks(paired, [(1, 10), (11, 20)], cap=10)) == 10
    assert select_chunks(paired, [(1, 10)], cap=0) == []


# ── C: PromptBuilder rank order + skip-not-stop ──────────────────────────────

def _c(path, text, **kw):
    return {"path": path, "text": text, **kw}


def _est(block: str) -> int:
    return max(1, len(block) // 4)


def test_default_order_is_rank_not_alphabetical():
    pb = PromptBuilder()
    out = pb.format_context_body([_c("z.py", "zz"), _c("a.py", "aa")])
    assert out.index("z.py") < out.index("a.py")


def test_sorted_order_still_available():
    pb = PromptBuilder(file_order="sorted")
    out = pb.format_context_body([_c("z.py", "zz"), _c("a.py", "aa")])
    assert out.index("a.py") < out.index("z.py")


def test_unknown_path_goes_last():
    pb = PromptBuilder()
    out = pb.format_context_body([{"text": "orphan"}, _c("a.py", "aa")])
    assert out.index("a.py") < out.index("(unknown path)")


def test_nonfitting_file_is_skipped_not_stop():
    pb = PromptBuilder()
    chunks = [
        _c("first.py", "small first"),
        _c("big.py", "x" * 4000),
        _c("third.py", "small third"),
    ]
    budget = _est(pb._file_block("first.py", chunks[:1])) + _est(
        pb._file_block("third.py", chunks[2:])
    ) + 2
    out = pb.format_context_body(chunks, max_context_tokens=budget)
    assert "first.py" in out and "third.py" in out
    assert "big.py" not in out
    assert CONTEXT_TRUNCATED_TAG in out


def test_high_rank_gold_file_survives_over_alphabetically_early_files():
    """Regression for the Nov 3 failure: alphabetical order dropped the gold file."""
    pb = PromptBuilder()
    chunks = [_c("zzz_gold.py", "g" * 200)] + [_c(f"a{i}.py", "f" * 200) for i in range(10)]
    budget = _est(pb._file_block("zzz_gold.py", chunks[:1])) * 3
    out = pb.format_context_body(chunks, max_context_tokens=budget)
    assert "zzz_gold.py" in out
    legacy = PromptBuilder(file_order="sorted").format_context_body(chunks, max_context_tokens=budget)
    assert "zzz_gold.py" not in legacy  # documents what the fix changes


def test_oversized_first_file_still_hard_truncated():
    pb = PromptBuilder()
    out = pb.format_context_body(
        [_c("big.py", "\n".join("line %d" % i for i in range(500))), _c("s.py", "s")],
        max_context_tokens=50,
    )
    assert "[... truncated ...]" in out and CONTEXT_TRUNCATED_TAG in out


def test_no_notice_when_everything_fits():
    pb = PromptBuilder()
    out = pb.format_context_body([_c("a.py", "a"), _c("b.py", "b")], max_context_tokens=10_000)
    assert CONTEXT_TRUNCATED_TAG not in out


def test_result_never_exceeds_budget_except_notice():
    pb = PromptBuilder()
    chunks = [_c(f"f{i}.py", "x" * (100 + 37 * i)) for i in range(15)]
    out = pb.format_context_body(chunks, max_context_tokens=300)
    body = out.split("</CONTEXT_TRUNCATED>\n\n", 1)[-1]
    assert _est(body) <= 300


# ── C: parity with the harness copy (eval_harness/src/harness/arms.py) ───────

def test_harness_ranked_selection_agrees_with_backend_builder():
    """If the harness's _select_lc_chunks_by_rank and PromptBuilder drift apart,
    this fails. Harness is imported only if available."""
    arms_path = ROOT / "eval_harness" / "src" / "harness" / "arms.py"
    if not arms_path.exists():
        return
    sys.path.insert(0, str(ROOT / "eval_harness" / "src"))
    try:
        from harness.arms import _lc_file_block, _select_lc_chunks_by_rank
    except Exception:  # harness deps unavailable in this env
        return
    pb = PromptBuilder()
    chunks = [
        {"path": "a.py", "text": "alpha " * 40, "symbol": "fa", "language": "python"},
        {"path": "b.py", "text": "beta " * 400, "symbol": "fb", "language": "python"},
        {"path": "c.py", "text": "gamma " * 40, "symbol": "fc", "language": "python"},
        {"path": "a.py", "text": "alpha2 " * 20, "symbol": "fa2", "language": "python"},
    ]
    # block rendering identical
    assert _lc_file_block("a.py", [chunks[0], chunks[3]]) == pb._file_block("a.py", [chunks[0], chunks[3]])
    budget = 200
    selected = _select_lc_chunks_by_rank(chunks, max_tokens=budget)
    harness_paths = []
    for c in selected:
        if c["path"] not in harness_paths:
            harness_paths.append(c["path"])
    out = pb.format_context_body(chunks, max_context_tokens=budget)
    backend_paths = [p for p in ("a.py", "b.py", "c.py") if f"=== File: {p} ===" in out]
    assert harness_paths == backend_paths


# ── D: edge types ────────────────────────────────────────────────────────────

def _adapter():
    ad = CodeGraphAdapter()
    for k in ("seed", "callee", "caller", "base", "mod", "parent", "far"):
        ad._register_node(f"p/{k}.py:{k}", {"id": f"p/{k}.py:{k}", "label": k, "file": f"p/{k}.py"})
    key = lambda k: f"p/{k}.py:{k}"
    ad._add_edge(key("seed"), key("callee"), "calls", "EXTRACTED")
    ad._add_edge(key("caller"), key("seed"), "calls", "EXTRACTED")
    ad._add_edge(key("seed"), key("base"), "inherits_from", "EXTRACTED")
    ad._add_edge(key("seed"), key("mod"), "imports", "EXTRACTED")
    ad._add_edge(key("parent"), key("seed"), "contains", "EXTRACTED")
    ad._add_edge(key("callee"), key("far"), "calls", "EXTRACTED")
    return ad, key


def test_normalize_relation():
    n = CodeGraphAdapter.normalize_relation
    assert n("calls") == "callee" and n("rev_calls") == "caller"
    assert n("inherits_from") == "inherits" and n("rev_inherits_from") == "default"
    assert n("imports") == "imports" and n("rev_imports") == "default"
    assert n("contains") == "contains" and n("rev_contains") == "contains"
    assert n("weird") == "default"


def test_neighbors_with_relations_keeps_edge_types_and_hops():
    ad, key = _adapter()
    got = {n: (r, d) for n, r, d in ad.neighbors_with_relations([key("seed")], hops=2)}
    assert got[key("callee")] == ("callee", 1)
    assert got[key("caller")] == ("caller", 1)
    assert got[key("base")] == ("inherits", 1)
    assert got[key("mod")] == ("imports", 1)
    assert got[key("parent")] == ("contains", 1)
    assert got[key("far")] == ("callee", 2)


def test_neighbors_unchanged_for_existing_callers():
    ad, key = _adapter()
    new = [n for n, _r, _d in ad.neighbors_with_relations([key("seed")], hops=2)]
    assert ad.neighbors([key("seed")], hops=2) == new
    assert ad.neighbors([], hops=1) == [] and ad.neighbors([key("seed")], hops=0) == []


def test_rel_order_consistent_with_pruner_priority():
    # graph_expand has package-relative imports, so read the table from source.
    src = (ROOT / "backend" / "retrieval" / "graph_expand.py").read_text()
    start = src.index("_REL_ORDER: dict[str, float] = {")
    end = src.index("}", start)
    table = eval(src[start:end + 1].split("=", 1)[1])  # noqa: S307 (literal dict from our own source)
    for k, v in table.items():
        assert pruning._RELATION_PRIORITY[k] == v, k


# ── D: oversized neighbour is truncated, not dropped ─────────────────────────

def _neighbor(name: str, n_lines: int, relation: str = "callee") -> dict:
    code = f"def {name}():\n" + "\n".join(f"    x{i} = {i}" for i in range(n_lines))
    return {
        "code": code,
        "metadata": {"file": f"p/{name}.py", "function": name},
        "relation_type": relation,
        "source": "graph",
    }


def test_truncate_chunk_helper_fits_budget():
    chunk = {"text": "\n".join(f"line {i}" for i in range(400)), "path": "p.py"}
    out = pruning._truncate_chunk_to_tokens(chunk, 50)
    assert pruning.estimate_tokens(out["text"]) <= 50
    assert out["text"].startswith("line 0")
    assert out["text"].endswith(pruning._TRUNC_MARKER)
    assert out is not chunk and chunk["text"].startswith("line 0\n")  # input untouched
    small = {"text": "tiny"}
    assert pruning._truncate_chunk_to_tokens(small, 50) is small


def test_truncate_chunk_single_giant_line():
    chunk = {"text": "x" * 5000}
    out = pruning._truncate_chunk_to_tokens(chunk, 40)
    assert pruning.estimate_tokens(out["text"]) <= 40


def test_oversized_neighbor_kept_truncated_in_reserve():
    big = _neighbor("big_gold", 600)  # far larger than the reserve
    budget = 1300
    reserve = int(budget * 0.30)
    assert pruning.estimate_tokens(big["code"]) > reserve
    kept, _stats = pruning.prune_context_graph_aware(
        "where is big_gold defined", seeds=[], neighbors=[big], token_budget=budget
    )
    assert kept, "oversized neighbour must not be dropped"
    texts = "\n".join(c["text"] for c in kept)
    assert "big_gold" in texts
    assert sum(pruning.estimate_tokens(c["text"]) for c in kept) <= reserve


def test_neighbor_reserve_never_exceeded_with_mixed_sizes():
    neighbors = [_neighbor(f"n{i}", 10 + 40 * i) for i in range(8)]
    budget = 1300
    kept, _ = pruning.prune_context_graph_aware(
        "q", seeds=[], neighbors=neighbors, token_budget=budget
    )
    assert sum(pruning.estimate_tokens(c["text"]) for c in kept) <= int(budget * 0.30)


def test_tiny_remaining_room_does_not_produce_stub():
    first = _neighbor("fits", 5)
    first_tokens = pruning.estimate_tokens(first["code"])
    budget = int((first_tokens + pruning.NEIGHBOR_MIN_TRUNC_TOKENS - 10) / 0.30)
    kept, _ = pruning.prune_context_graph_aware(
        "q", seeds=[], neighbors=[first, _neighbor("huge", 500, "default")], token_budget=budget
    )
    assert all("truncated ..." not in c["text"] for c in kept)


def test_edge_type_changes_neighbor_priority():
    callee, imp = _neighbor("callee_fn", 20, "callee"), _neighbor("import_fn", 20, "imports")
    assert pruning._neighbor_structural_score(callee) > pruning._neighbor_structural_score(imp)
    no_edge = {k: v for k, v in callee.items() if k != "relation_type"}
    assert pruning._neighbor_structural_score(no_edge) < pruning._neighbor_structural_score(callee)
