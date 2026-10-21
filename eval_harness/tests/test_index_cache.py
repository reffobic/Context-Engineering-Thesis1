import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.index_cache import index_dir, make_key, sampled_pairs, verdict  # noqa: E402

ICFG = {"embedding": {"model": "m"}, "chunking": {"max_chunk_tokens": 512}, "index": {"A": "0"},
        "build": {"max_files": 10, "index_device": "cuda"}}


def test_key_depends_on_pipeline_commit_and_settings_but_not_device():
    a = make_key("o/r", "c" * 40, "p1", ICFG, "g1")
    assert a["key"] == make_key("o/r", "c" * 40, "p1", ICFG, "g1")["key"]
    assert a["key"] != make_key("o/r", "c" * 40, "p2", ICFG, "g1")["key"]
    assert a["key"] != make_key("o/r", "d" * 40, "p1", ICFG, "g1")["key"]
    assert a["key"] != make_key("o/r", "c" * 40, "p1", ICFG, "g2")["key"]
    other = {**ICFG, "chunking": {"max_chunk_tokens": 256}}
    assert a["key"] != make_key("o/r", "c" * 40, "p1", other, "g1")["key"]
    cpu = {**ICFG, "build": {"max_files": 10, "index_device": "cpu"}}
    assert a["key"] == make_key("o/r", "c" * 40, "p1", cpu, "g1")["key"]


def test_index_dir_is_portable_name():
    k = make_key("o/r", "c" * 40, "p1", ICFG, "g1")
    name = index_dir(Path("d"), k).name
    assert "/" not in name and "\\" not in name and name.startswith("o__r__cccccccccccc__")


def test_sampled_pairs_dedupes_repo_commit(tmp_path):
    d = tmp_path / "datasets" / "x"
    d.mkdir(parents=True)
    (tmp_path / "samples").mkdir()
    tasks = [{"instance_id": f"i{n}", "repo": "o/r", "base_commit": "c", "query": "q"} for n in range(3)]
    (d / "tasks.jsonl").write_text("\n".join(map(json.dumps, tasks)), encoding="utf-8")
    (tmp_path / "samples" / "x.sample.json").write_text(json.dumps({"instance_ids": ["i0", "i1"]}), encoding="utf-8")
    assert len(sampled_pairs(tmp_path, ["x"])) == 1


def test_verdict_flags_each_failure():
    good = {"count": 5, "ids_equal": True, "max_embedding_abs_diff": 1e-8, "self_recall": 1.0, "golden_overlap_mean": 1.0}
    man = {"counts": {"code_embeddings": 5, "code_meta_embeddings": 5},
           "verify": {"max_embedding_abs_diff": 1e-6, "min_self_recall": 0.97, "min_golden_overlap_mean": 0.98}}
    v = {"checks": {"code_embeddings": good, "code_meta_embeddings": dict(good),
                    "call_graph_json": True, "graphify_json": True}}
    assert verdict(v, man) == []
    v["checks"]["call_graph_json"] = False
    assert any("call_graph.json" in f for f in verdict(v, man))
    v["checks"]["call_graph_json"] = True
    v["checks"]["code_meta_embeddings"] = {**good, "self_recall": 0.9, "golden_overlap_mean": 0.5, "count": 4}
    assert len(verdict(v, man)) == 3
