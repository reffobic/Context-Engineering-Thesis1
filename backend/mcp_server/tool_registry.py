from __future__ import annotations

from typing import Any, Callable, Dict

tools: Dict[str, Callable[..., Any]] = {}


def register_tool(name: str, function: Callable[..., Any]) -> None:
    tools[name] = function


def call_tool(name: str, *args: Any, **kwargs: Any) -> Any:
    if name not in tools:
        raise ValueError(f"Tool '{name}' not registered")
    return tools[name](*args, **kwargs)

