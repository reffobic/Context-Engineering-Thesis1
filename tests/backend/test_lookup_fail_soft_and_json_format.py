import pytest
pytest.importorskip("chromadb")
from mcp_server import server
from diamond.judge_json import judge_format_field


def test_failed_lookup_tool_falls_back_to_retrieve_context(monkeypatch):
    calls = []

    def fake_call_tool(name, *a, **k):
        calls.append(name)
        if name == "find_symbol":
            raise RuntimeError("too many SQL variables")
        return [{"code": "x", "metadata": {}}]

    monkeypatch.setattr(server, "call_tool", fake_call_tool)
    ctx, tool = server._retrieve_for_prompt("where is Foo defined?", use_lightweight_tools=True)
    assert calls == ["find_symbol", "retrieve_context"]
    assert tool is None and ctx


def test_judge_format_field_default_on_and_switchable(monkeypatch):
    monkeypatch.delenv("JUDGE_JSON_FORMAT", raising=False)
    assert judge_format_field() == {"format": "json"}
    monkeypatch.setenv("JUDGE_JSON_FORMAT", "0")
    assert judge_format_field() == {}
