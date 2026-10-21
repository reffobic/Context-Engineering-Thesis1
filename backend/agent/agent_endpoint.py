from __future__ import annotations

import os
from typing import Any, Union

from .ollama_agent import OllamaAgent

agent = OllamaAgent(model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"))


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
