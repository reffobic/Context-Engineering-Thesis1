"""Judge replies seen in the cacecedb ablation queue that json.loads rejected (gate-parse failures); each carries a usable decision."""
import json

import pytest

from diamond.judge_json import loads_lenient
from diamond.preroute_gate import _parse_generative_response
from diamond import sufficiency_assessor as sa

UNESCAPED_QUOTES = '{\n "sufficient": false,\n "confidence": "high",\n "reason": "needs set_config(transform_output="pandas") wrapper."\n}'
RAW_NEWLINE = '{"sufficient": false, "confidence": "high", "reason": "line one\nline two"}'
MISSING_QUOTE = '{"sufficient": false, "confidence": "high", "reason": "lacks execute_many, which returns CursorResult.}'
STRUCT = '{\n "need": "STRUCTURE",\n "confidence": "medium",\n "reason": "callers are shown as groupby().mean(method="cohorts") here."\n}'


def test_valid_json_is_unchanged():
    s = '{"sufficient": true, "confidence": "low", "reason": "ok"}'
    assert loads_lenient(s) == json.loads(s)


@pytest.mark.parametrize("raw", [UNESCAPED_QUOTES, RAW_NEWLINE, MISSING_QUOTE])
def test_sufficiency_replies_that_broke_json_loads_keep_their_decision(raw):
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw) if raw != RAW_NEWLINE else json.loads(raw)       # strict loads really fails on all three
    d = loads_lenient(raw)
    assert d["sufficient"] is False and d["confidence"] == "high" and d["reason"]
    assert _parse_generative_response(raw)["route"] == "LC"             # preroute parser path
    assert sa._parse_response(raw)["sufficient"] is False


def test_structure_reply_keeps_need():
    assert loads_lenient(STRUCT)["need"] == "STRUCTURE"
    assert sa._parse_structure_response(STRUCT)["need"] == "STRUCTURE"


def test_nothing_recognisable_still_fails_so_the_fallback_applies():
    with pytest.raises(ValueError):
        sa._parse_response("I think the context is probably fine.")
    with pytest.raises(json.JSONDecodeError):
        loads_lenient('{"foo": "bar", oops')


def test_reason_first_reply_keeps_reason_clean_and_decision():
    from diamond.judge_json import loads_lenient
    d = loads_lenient('{"reason": "calls "foo" twice", "sufficient": false, "confidence": "high", "need": "STRUCTURE"}')
    assert d["sufficient"] is False and d["need"] == "STRUCTURE" and d["confidence"] == "high"
    assert d["reason"] == 'calls "foo" twice'
