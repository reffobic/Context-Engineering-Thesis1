"""gate_no_usage failure class: flags any call with missing or zero usage; silent when the count is zero."""
import json
from pathlib import Path

import pytest

from harness import metrics as M

OCT23 = Path(__file__).resolve().parents[1] / "data" / "runs" / "2350c0b"


def call(role="gapa_gate_call1", pe=10, ev=5):
    return {"role": role, "seq": 0, "prompt_eval_count": pe, "eval_count": ev}


@pytest.mark.parametrize("pe,ev", [(None, 5), (10, None), (0, 5), (10, 0), (None, None)])
def test_missing_or_zero_usage_is_flagged(pe, ev):
    line = {"status": "ok", "gate_error": False, "answer_error": False, "ollama_calls": [call(), call("answer", pe, ev)]}
    assert [c["role"] for c in M.no_usage_calls(line)] == ["answer"]
    assert M.failed_kind(line) == "gate_no_usage"


def test_clean_line_is_not_flagged_and_precedence():
    ok = {"status": "ok", "gate_error": False, "answer_error": False, "ollama_calls": [call(), call("answer", 3, 4)]}
    assert M.no_usage_calls(ok) == [] and M.failed_kind(ok) is None
    both = {**ok, "gate_error": True, "ollama_calls": [call(pe=None)]}
    assert M.failed_kind(both) == "gate"                                  # an existing class wins
    assert M.failed_kind({**ok, "status": "timeout", "ollama_calls": [call(pe=0)]}) == "hard"


@pytest.mark.skipif(not OCT23.is_dir(), reason="Oct 23 run logs not present")
def test_oct23_jobs_have_zero_calls_without_usage():
    n = tasks = 0
    for rp in sorted(OCT23.glob("[0-9]*/results.jsonl")):
        for ln in rp.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                tasks += 1
                n += len(M.no_usage_calls(json.loads(ln)))
    assert tasks == 386 and n == 0
