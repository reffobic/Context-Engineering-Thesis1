import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.instrument import JobFailure, check_structure_rerank, reconcile, trace_tokens  # noqa: E402
from harness.ollama_shim import OllamaShim, Recorder  # noqa: E402
from harness.pipelineenv import EnvError, assert_unset, build_env, effective_record  # noqa: E402

EXP = {"ollama": {"num_ctx": 32768, "think": False, "seed": 42, "answer_temperature": 0.0, "keep_alive": "10m",
                  "override_num_ctx": True, "truncation_margin_tokens": 8}}
PCFG = {"env_pinned": {"OLLAMA_NUM_CTX": "32768", "OLLAMA_MODEL": "@model", "PYTHONHASHSEED": "0"},
        "assert_unset": {"vars": ["GAPA_COMPACT_PROMPT", "INDEX_CONTEXT_OLLAMA"]},
        "known_env": {"OLLAMA_NUM_CTX": "0", "MAX_CONTEXT_TOKENS": "24000", "GRAPHIFY_OUT": ""}}


def test_shim_prepare_overrides_num_ctx_and_fills_gaps():
    s = OllamaShim(EXP, Recorder())
    body, ch = s.prepare({"model": "m", "options": {"temperature": 0.0, "num_ctx": 4096}})
    assert body["options"]["num_ctx"] == 32768 and ch["num_ctx"] == {"asked": 4096, "set": 32768}
    assert body["think"] is False and body["options"]["seed"] == 42 and body["keep_alive"] == "10m"
    assert body["options"]["temperature"] == 0.0 and "temperature" not in ch       # call site value kept
    body2, ch2 = s.prepare({"model": "m", "think": True, "options": {"seed": 7}})
    assert body2["think"] is True and body2["options"]["seed"] == 7 and "think" not in ch2


def test_assert_unset_and_env_building():
    with pytest.raises(EnvError):
        assert_unset(PCFG, {"GAPA_COMPACT_PROMPT": "1"})
    with pytest.raises(EnvError):
        build_env(PCFG, "m", EXP, environ={"INDEX_CONTEXT_OLLAMA": "0"})
    env = build_env(PCFG, "qwen3.5:9b", EXP, environ={"PATH": "/bin", "SECRET_TOKEN": "x"})
    assert env["OLLAMA_MODEL"] == "qwen3.5:9b" and "SECRET_TOKEN" not in env and env["PYTHONHASHSEED"] == "0"
    with pytest.raises(EnvError):
        build_env({**PCFG, "env_pinned": {**PCFG["env_pinned"], "OLLAMA_NUM_CTX": "4096"}}, "m", EXP, environ={})
    rec = effective_record(PCFG, env)
    assert rec["OLLAMA_NUM_CTX"] == {"value": "32768", "source": "pinned"}
    assert rec["MAX_CONTEXT_TOKENS"] == {"value": "24000", "source": "code_default"}
    assert rec["GRAPHIFY_OUT"]["value"] is None


def test_reconcile_and_trace_tokens():
    calls = [{"role": "gapa_gate_call1", "prompt_eval_count": 10, "eval_count": 2},
             {"role": "gapa_gate_call2", "prompt_eval_count": 20, "eval_count": 3},
             {"role": "answer", "prompt_eval_count": 999, "eval_count": 9}]
    result = {"pipeline": {"stages": {"assess": {"summary": {"trace": [
        {"phase": "rag_gate", "token_usage": {"prompt_tokens": 30, "completion_tokens": 5}}],
        "entry_gate_token_usage": {"prompt_tokens": 10, "completion_tokens": 2}}}}}}
    assert trace_tokens(result)["prompt"] == 30                 # entry_gate_token_usage not added
    r = reconcile(calls, result)
    assert r["match"] is True and r["shim_prompt"] == 30
    calls[0]["prompt_eval_count"] = 11
    assert reconcile(calls, result)["match"] is False
    assert reconcile(calls, {"pipeline": {"stages": {}}})["match"] is None


def test_check_structure_rerank():
    ok = {"structure_rerank": {"expected": True, "applied": True, "flag": True, "called": True, "raised": False,
                               "call_graph_json_exists": True}}
    check_structure_rerank(ok)
    with pytest.raises(JobFailure):
        check_structure_rerank({"structure_rerank": {**ok["structure_rerank"], "applied": False,
                                                     "call_graph_json_exists": False}})


def test_git_identity_flags(tmp_path):
    import subprocess
    from harness.pipelineenv import git_identity, require_runnable
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (tmp_path / "backend").mkdir(); (tmp_path / "backend" / "a.py").write_text("x=1\n")
    run("add", "."); run("commit", "-qm", "frozen")
    frozen = run("rev-parse", "HEAD")
    pcfg = {"pipeline": {"frozen_sha": frozen, "base_commit": "b", "seed_alignment_commit": "s",
                         "revoked_harness_commits": ["deadbeef"]}}
    ident = git_identity(tmp_path, pcfg)
    assert ident["clean"] and ident["backend_matches_frozen"] and not ident["revoked"]
    require_runnable(ident)
    (tmp_path / "backend" / "a.py").write_text("x=2\n")
    assert not git_identity(tmp_path, pcfg)["clean"]
    with pytest.raises(EnvError):
        require_runnable(git_identity(tmp_path, pcfg))
    run("commit", "-qam", "change backend")
    assert not git_identity(tmp_path, pcfg)["backend_matches_frozen"]
    pcfg["pipeline"]["revoked_harness_commits"] = [run("rev-parse", "HEAD")[:10]]
    assert git_identity(tmp_path, pcfg)["revoked"]
