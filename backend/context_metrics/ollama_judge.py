"""
Ollama-backed judge for DeepEval metrics.

Fixes common failures with thinking models (e.g. Qwen) and structured ``format``:

1. **Empty ``message.content``** — Ollama may put reasoning in ``message.thinking``; we merge
   ``content`` + ``thinking`` before JSON extraction.
2. **Strict JSON schema** — some models return nothing usable; we retry once with
   Ollama's simpler ``format=\"json\"`` and parse the same pydantic schema.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple, Union

from pydantic import BaseModel, ValidationError

from deepeval.constants import ProviderSlug as PS
from deepeval.models.llms.ollama_model import OllamaModel
from deepeval.models.retry_policy import create_retry_decorator
from deepeval.utils import check_if_multimodal, convert_to_multi_modal_array

retry_ollama = create_retry_decorator(PS.OLLAMA)

_THINK_END_MARKERS = (
    "<" + "think" + ">",
    "<" + "/" + "think" + ">",
    "<" + "Think" + ">",
    "<" + "/" + "Think" + ">",
)


def _strip_leading_fenced_json(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def normalize_ollama_metric_json(raw: Optional[str]) -> str:
    """Turn raw assistant text into a JSON string for ``model_validate_json``."""
    s = (raw or "").strip()
    if not s:
        return s

    for marker in _THINK_END_MARKERS:
        if marker in s:
            s = s.split(marker, 1)[-1].strip()

    if not s.startswith("{") and not s.startswith("["):
        m = re.search(r"[\[{][\s\S]*[\]}]", s)
        if m:
            s = m.group(0).strip()

    s = _strip_leading_fenced_json(s)
    return s.strip()


class OllamaJudgeModel(OllamaModel):
    """DeepEval ``OllamaModel`` with thinking-field merge + JSON-schema fallback."""

    @staticmethod
    def _combined_assistant_text(message: Any) -> str:
        if message is None:
            return ""
        parts: list[str] = []
        for attr in ("content", "thinking"):
            val = getattr(message, attr, None)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
        return "\n\n".join(parts)

    def _chat_options(self) -> dict[str, Any]:
        return {
            **{"temperature": self.temperature},
            **self.generation_kwargs,
        }

    def _parse_schema_text(self, raw: Optional[str], schema: BaseModel) -> BaseModel:
        cleaned = normalize_ollama_metric_json(raw)
        if not cleaned:
            raise ValueError("empty assistant text after normalize")
        return schema.model_validate_json(cleaned)

    @retry_ollama
    def generate(  # type: ignore[override]
        self, prompt: str, schema: Optional[BaseModel] = None
    ) -> Tuple[Union[str, BaseModel], float]:
        chat_model = self.load_model()

        if check_if_multimodal(prompt):
            prompt = convert_to_multi_modal_array(prompt)
            messages = self.generate_messages(prompt)
        else:
            messages = [{"role": "user", "content": prompt}]

        opts = self._chat_options()
        response = chat_model.chat(
            model=self.name,
            messages=messages,
            format=schema.model_json_schema() if schema else None,
            options=opts,
        )
        msg = response.message

        if schema is None:
            raw = getattr(msg, "content", None) if msg else None
            return (raw or ""), 0.0

        raw = self._combined_assistant_text(msg)
        first_err: Optional[BaseException] = None
        try:
            return self._parse_schema_text(raw, schema), 0.0
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            first_err = exc

        response2 = chat_model.chat(
            model=self.name,
            messages=messages,
            format="json",
            options=opts,
        )
        raw2 = self._combined_assistant_text(response2.message)
        try:
            return self._parse_schema_text(raw2, schema), 0.0
        except (ValidationError, ValueError, json.JSONDecodeError) as exc2:
            raise RuntimeError(
                "Ollama judge returned no parseable JSON (tried schema format and json format). "
                "Try: export EVAL_JUDGE_MODEL=llama3.2:3b  or  export EVAL_OLLAMA_NUM_CTX=16384. "
                f"schema_round={first_err!s}; json_round={exc2!s}"
            ) from exc2

    @retry_ollama
    async def a_generate(  # type: ignore[override]
        self, prompt: str, schema: Optional[BaseModel] = None
    ) -> Tuple[Union[str, BaseModel], float]:
        chat_model = self.load_model(async_mode=True)

        if check_if_multimodal(prompt):
            prompt = convert_to_multi_modal_array(prompt)
            messages = self.generate_messages(prompt)
        else:
            messages = [{"role": "user", "content": prompt}]

        opts = self._chat_options()
        response = await chat_model.chat(
            model=self.name,
            messages=messages,
            format=schema.model_json_schema() if schema else None,
            options=opts,
        )
        msg = response.message

        if schema is None:
            raw = getattr(msg, "content", None) if msg else None
            return (raw or ""), 0.0

        raw = self._combined_assistant_text(msg)
        first_err: Optional[BaseException] = None
        try:
            return self._parse_schema_text(raw, schema), 0.0
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            first_err = exc

        response2 = await chat_model.chat(
            model=self.name,
            messages=messages,
            format="json",
            options=opts,
        )
        raw2 = self._combined_assistant_text(response2.message)
        try:
            return self._parse_schema_text(raw2, schema), 0.0
        except (ValidationError, ValueError, json.JSONDecodeError) as exc2:
            raise RuntimeError(
                "Ollama judge returned no parseable JSON (tried schema format and json format). "
                "Try: export EVAL_JUDGE_MODEL=llama3.2:3b  or  export EVAL_OLLAMA_NUM_CTX=16384. "
                f"schema_round={first_err!s}; json_round={exc2!s}"
            ) from exc2
