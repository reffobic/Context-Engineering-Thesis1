from __future__ import annotations

from typing import Any, Union

from .ollama_agent import get_shared_agent

# Process-wide singleton — do NOT construct OllamaAgent per request.
agent = get_shared_agent(model="qwen3.5:9b")  # must match: ollama list


def ollama_generate(payload: Union[str, dict[str, Any]]) -> str:
    """
    Accept either a single string (legacy) or ``{"system": ..., "user": ...}`` from
    ``inject_context`` so we use chat API with separate roles.
    """
    if isinstance(payload, dict):
        system = payload.get("system")
        user = payload.get("user")
        if isinstance(user, str) and user:
            return agent.generate_response(user, system=system if isinstance(system, str) else None)
    if isinstance(payload, str):
        return agent.generate_response(payload)
    return agent.generate_response(str(payload))
