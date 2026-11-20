"""Lookup shortcuts fire only for short single-line lookup questions, not for issue text containing a trigger phrase."""
import pytest
pytest.importorskip("chromadb")
from mcp_server.server import _lightweight_request as lw

ISSUE = ("Fix the retry bug\\nThe handler crashes.\\n\\nSteps: we saw references to `outside` and calls to postgres "
         "in the traceback.\\n```\\nTraceback...\\n```")


@pytest.mark.parametrize("q", ["Where is class Foo defined?", "who calls run_pipeline?", "How is retrieve_context used?",
                               "find the definition of HybridRetriever", "References to Config.load",
                               "List all .py files in backend/retrieval"])
def test_short_lookup_still_uses_tool(q):
    assert lw(q) is not None


def test_issue_text_is_not_hijacked():
    assert lw(ISSUE.replace("\\\\n", "\\n")) is None
    assert lw("x" * 250 + " where is Foo defined") is None


def test_stopword_symbol_rejected():
    assert lw("definition of the") is None
