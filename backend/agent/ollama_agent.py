"""
Ollama-backed agent for code generation.

Calls local Ollama (same pattern as src/file_context/selector.py OllamaClient).
Supports:
  - /api/chat when system + user are separate (recommended)
  - /api/generate for a single combined prompt

You only need Ollama installed. Optional models: pull any coding model, e.g.
  ollama pull llama3.2:3b
  ollama pull deepseek-coder:6.7b   # optional; Ollama is enough without it
"""

from __future__ import annotations

import json
from typing import List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from injection.prompt_builder import (
    DEFAULT_SYSTEM_PROMPT,
    ContextChunk,
    PromptBuilder,
    build_coding_prompt,
)


class OllamaAgent:
    """
    Minimal agent interface for P1 integration:
      - generate_response(prompt, system=None)
      - generate_with_context(user_task, chunks, ...)
    """

    def __init__(
        self,
        model: str = "llama3.2:3b",
        base_url: str = "http://localhost:11434",
        timeout: int = 600,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate_response(self, prompt: str, system: Optional[str] = None) -> str:
        """
        Low-level: send prompt to Ollama. If system is set, uses /api/chat; else /api/generate.
        """
        if system is not None:
            return self._chat(system, prompt)
        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        url = f"{self.base_url}/api/generate"
        body = {"model": self.model, "prompt": prompt, "stream": False}
        return self._post_json(url, body, response_key="response")

    def _chat(self, system: str, user: str) -> str:
        url = f"{self.base_url}/api/chat"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        raw = self._post_json_raw(url, body)
        msg = raw.get("message") or {}
        return (msg.get("content") or "").strip()

    def _post_json(self, url: str, body: dict, response_key: str) -> str:
        raw = self._post_json_raw(url, body)
        return (raw.get(response_key) or "").strip()

    def _post_json_raw(self, url: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except URLError as e:
            return {"error": str(e), "response": "", "message": {"content": f"[Ollama error: {e}]"}}

    def generate_with_context(
        self,
        user_task: str,
        chunks: List[ContextChunk],
        *,
        system_prompt: Optional[str] = None,
        extra_dependencies: Optional[str] = None,
        max_context_tokens: Optional[int] = None,
        prompt_builder: Optional[PromptBuilder] = None,
    ) -> str:
        """
        Full P5 path: build structured prompt (injection) → Ollama chat.

        Pass ``prompt_builder`` to reuse a configured instance (token budget, line numbers, etc.).
        """
        builder = prompt_builder or PromptBuilder(
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT
        )
        system, user = builder.build(
            user_task,
            chunks,
            extra_dependencies=extra_dependencies,
            max_context_tokens=max_context_tokens,
        )
        return self.generate_response(user, system=system)


def generate_response(prompt: str, *, model: str = "llama3.2:3b", system: Optional[str] = None) -> str:
    """Functional shortcut."""
    return OllamaAgent(model=model).generate_response(prompt, system=system)


if __name__ == "__main__":
    # Smoke test: no Ollama required to validate prompt build; Ollama optional for full run.
    import sys

    demo_chunks: List[ContextChunk] = [
        {
            "path": "example.py",
            "symbol": "hello",
            "language": "python",
            "text": "def hello():\n    return 'world'\n",
        }
    ]
    agent = OllamaAgent(model=sys.argv[1] if len(sys.argv) > 1 else "llama3.2:3b")
    print("--- Built user message (preview) ---")
    sys_p, user = build_coding_prompt("Add type hints to hello().", demo_chunks)
    print("SYSTEM (first 200 chars):", sys_p[:200], "...")
    print("USER (first 500 chars):", user[:500], "...")
    print("\n--- Calling Ollama (set OLLAMA_SKIP=1 to skip) ---")
    import os

    if os.environ.get("OLLAMA_SKIP") == "1":
        print("Skipped (OLLAMA_SKIP=1)")
    else:
        out = agent.generate_with_context("Add type hints to hello().", demo_chunks)
        print(out[:2000] if out else "(empty response)")