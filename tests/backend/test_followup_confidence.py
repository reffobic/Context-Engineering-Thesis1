from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agent.ollama_agent import OllamaAgent


class MemoryStore:
    def add(self, *_args: Any, **_kwargs: Any) -> dict[str, list]:
        return {"results": []}


@pytest.fixture
def agent(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> OllamaAgent:
    monkeypatch.setenv("MEM0_DIR", str(tmp_path))
    instance = OllamaAgent(memory_store=MemoryStore(), memory_user_id="test")
    instance.messages = [
        {"role": "user", "content": "Explain the API handler."},
        {"role": "assistant", "content": "It validates the request and dispatches it."},
    ]
    instance._last_code_context = "def handle_request(request): return dispatch(request)"
    return instance


def _draft(content: str, *, logprobs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"message": {"content": content}}
    if logprobs is not None:
        result["message"]["logprobs"] = logprobs
    return result


def test_regex_prefilter_avoids_draft_for_normal_questions(agent: OllamaAgent) -> None:
    agent._post_json_raw = lambda *_args, **_kwargs: pytest.fail("unexpected model call")  # type: ignore[method-assign]

    decision = agent.assess_cached_followup("Add an authorization check to the endpoint.")

    assert decision["candidate"] is False
    assert decision["method"] == "regex_prefilter"


def test_chat_continues_response_when_ollama_reports_length(agent: OllamaAgent) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {"message": {"content": "The answer is incom"}, "done_reason": "length"},
            {"message": {"content": "plete."}, "done_reason": "stop"},
        ]
    )

    def post(_url: str, body: dict[str, Any]) -> dict[str, Any]:
        requests.append(body.copy())
        return next(responses)

    agent._post_json_raw = post  # type: ignore[method-assign]

    response = agent._chat(
        messages=[{"role": "user", "content": "Give a complete answer."}]
    )

    assert response == "The answer is incomplete."
    assert len(requests) == 2
    assert requests[1]["messages"][-2:] == [
        {"role": "assistant", "content": "The answer is incom"},
        {
            "role": "user",
            "content": (
                "Continue the answer from its exact ending. Do not "
                "repeat earlier text. Complete the current thought "
                "and finish the answer."
            ),
        },
    ]


def test_confident_logprob_draft_uses_cached_context(agent: OllamaAgent) -> None:
    captured: list[dict[str, Any]] = []
    token_scores = [
        {
            "token": "It",
            "logprob": -0.1,
            "top_logprobs": [
                {"token": "It", "logprob": -0.1},
                {"token": "They", "logprob": -2.0},
            ],
        }
    ]

    def post(_url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured.append(body)
        if "strict evidence checker" in body["messages"][0]["content"].lower():
            return _draft('{"supported": true, "reason": "The cached code confirms it."}')
        return _draft("Its dependencies are request and dispatch.", logprobs=token_scores)

    agent._post_json_raw = post  # type: ignore[method-assign]
    decision = agent.assess_cached_followup("What are its dependencies?")

    assert decision["candidate"] is True
    assert decision["confident"] is True
    assert decision["method"] == "logprob_margin"
    assert "Cached-source support check passed" in decision["reason"]
    assert len(captured) == 2
    cached_prompt = captured[0]["messages"][1]["content"]
    assert "Previous assistant reply:\nIt validates the request and dispatches it." in cached_prompt
    assert "Code context retrieved for that previous topic:" in cached_prompt


def test_varying_samples_continue_to_retrieval(agent: OllamaAgent) -> None:
    answers = iter(
        [
            "The handler checks the request.",
            "Its caller is the router module.",
            "It returns the result of dispatch.",
        ]
    )
    def post(_url: str, body: dict[str, Any]) -> dict[str, Any]:
        if "strict evidence checker" in body["messages"][0]["content"].lower():
            return _draft('{"supported": true, "reason": "Supported."}')
        return _draft(next(answers))

    agent._post_json_raw = post  # type: ignore[method-assign]

    decision = agent.assess_cached_followup("What does it return?")

    assert decision["candidate"] is True
    assert decision["confident"] is False
    assert decision["method"] == "multi_sample_similarity"


def test_consistent_samples_skip_retrieval(agent: OllamaAgent) -> None:
    def post(_url: str, body: dict[str, Any]) -> dict[str, Any]:
        if "strict evidence checker" in body["messages"][0]["content"].lower():
            return _draft('{"supported": true, "reason": "Supported."}')
        return _draft("It validates the request, then dispatches it.")

    agent._post_json_raw = post  # type: ignore[method-assign]

    decision = agent.assess_cached_followup("What does it do?")

    assert decision["confident"] is True
    assert decision["method"] == "multi_sample_similarity"
    assert "Cached-source support check passed" in decision["reason"]


def test_unsupported_logprobs_fall_back_to_sampling(agent: OllamaAgent) -> None:
    calls: list[dict[str, Any]] = []

    def post(_url: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        if "strict evidence checker" in body["messages"][0]["content"].lower():
            return _draft('{"supported": true, "reason": "Supported."}')
        if body.get("logprobs"):
            raise RuntimeError("Ollama HTTP error 400: unknown field 'logprobs'")
        return _draft("It validates the request, then dispatches it.")

    agent._post_json_raw = post  # type: ignore[method-assign]
    decision = agent.assess_cached_followup("What does it do?")

    assert decision["confident"] is True
    assert decision["method"] == "multi_sample_similarity"
    assert len(calls) == 5
    assert calls[0]["logprobs"] is True
    assert all("logprobs" not in call for call in calls[1:])


def test_high_logprob_but_unsupported_draft_continues_to_retrieval(
    agent: OllamaAgent,
) -> None:
    token_scores = [
        {
            "token": "The",
            "logprob": -0.01,
            "top_logprobs": [
                {"token": "The", "logprob": -0.01},
                {"token": "A", "logprob": -4.0},
            ],
        }
    ]

    def post(_url: str, body: dict[str, Any]) -> dict[str, Any]:
        if "strict evidence checker" in body["messages"][0]["content"].lower():
            return _draft(
                '{"supported": false, "reason": "The answer contradicts the cached source."}'
            )
        return _draft(
            "Meta-memory questions trigger a fresh Mem0 search.",
            logprobs=token_scores,
        )

    agent._post_json_raw = post  # type: ignore[method-assign]
    decision = agent.assess_cached_followup(
        "What does it do with meta-memory questions?"
    )

    assert decision["candidate"] is True
    assert decision["confident"] is False
    assert "contradicts the cached source" in decision["reason"]


def test_cached_followup_answer_uses_normal_response_generation(agent: OllamaAgent) -> None:
    captured: list[list[dict[str, str]]] = []

    def chat(messages: list[dict[str, str]] | None = None) -> str:
        assert messages is not None
        captured.append(messages)
        return "A complete answer generated with the normal response budget."

    agent._chat = chat  # type: ignore[method-assign]
    response = agent.generate_response("What does it do?")

    assert response == "A complete answer generated with the normal response budget."
    assert "Previous assistant reply:\nIt validates the request and dispatches it." in captured[0][1]["content"]
    assert agent.messages[-2:] == [
        {"role": "user", "content": "What does it do?"},
        {"role": "assistant", "content": response},
    ]


def test_normal_retrieval_context_is_cached(agent: OllamaAgent) -> None:
    agent.cache_code_context(
        [{"path": "api.py", "symbol": "handle", "text": "return dispatch(request)"}],
        user_task="Explain the API handler.",
    )

    assert "[api.py::handle]\nreturn dispatch(request)" in agent._last_code_context
    assert agent._load_last_code_context() == agent._last_code_context


def test_followup_does_not_replace_cached_code_context(agent: OllamaAgent) -> None:
    original = agent._last_code_context

    agent.cache_code_context(
        [{"path": "unrelated.py", "text": "irrelevant()"}],
        user_task="What does it do?",
    )

    assert agent._last_code_context == original


def test_uncertain_followup_retrieval_query_includes_original_topic(
    agent: OllamaAgent,
) -> None:
    query = agent.build_retrieval_query(
        "What can it use to answer those questions, and what must it avoid?"
    )

    assert query.startswith("Original topic: Explain the API handler.")
    assert "Follow-up question: What can it use to answer those questions" in query


def test_normal_retrieval_query_is_unchanged(agent: OllamaAgent) -> None:
    task = "Explain how cache keys are generated."

    assert agent.build_retrieval_query(task) == task


def test_uncertain_followup_can_use_fresh_retrieval(agent: OllamaAgent) -> None:
    agent._augment_user_prompt = lambda prompt: prompt  # type: ignore[method-assign]
    agent._chat = lambda messages=None: "Answer from fresh retrieval."  # type: ignore[method-assign]

    answer = agent.generate_response(
        "What does it do?\n\n<CONTEXT>fresh retrieved code</CONTEXT>",
        force_retrieval=True,
    )

    assert answer == "Answer from fresh retrieval."
