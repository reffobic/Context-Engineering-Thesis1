"""Gate replies that quote braces inside the reason text must still parse (reason last and reason first)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from diamond import sufficiency_assessor as sa  # noqa: E402

LAST = '{"need": "CONTEXT", "confidence": "high", "reason": "How `inject` resolves `{user:first_name}` in the string."}'
FIRST = '{"reason": "see {a} and {b}", "need": "STRUCTURE", "confidence": "low"}'
GATE_FIRST = '{"reason": "see {a} and {b}", "sufficient": false, "need": "STRUCTURE", "confidence": "low"}'


def test_structure_reason_last():
    r = sa._parse_structure_response(LAST)
    assert r["need"] == "CONTEXT" and r["reason"].endswith("in the string.")


def test_structure_reason_first():
    r = sa._parse_structure_response(FIRST)
    assert r["need"] == "STRUCTURE" and r["confidence"] == "low"


def test_rag_gate_reason_first():
    r = sa._parse_rag_gate_response(GATE_FIRST)
    assert r["need"] == "STRUCTURE" and r["sufficient"] is False


def test_fenced_and_trailing_text_still_parse():
    r = sa._parse_structure_response("```json\n" + LAST + "\n```")
    assert r["confidence"] == "high"
    r = sa._parse_structure_response("Sure. " + FIRST + " Hope this helps {done}")
    assert r["need"] == "STRUCTURE"
