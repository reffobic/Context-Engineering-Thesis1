"""
Ollama-backed judge for DeepEval metrics.

Fixes common failures with thinking models (e.g. Qwen) and structured ``format``:

1. **Empty ``message.content``** — Ollama may put reasoning in ``message.thinking``; we merge
   ``content`` + ``thinking`` before JSON extraction.
2. **Trailing prose after the JSON** — we extract the first valid JSON value.
3. **Strict JSON schema** — round 1 uses the pydantic schema; if that is unparseable we retry
   once with Ollama's simpler ``format="json"``.

Env knobs:
  JUDGE_THINK=1          keep the model's reasoning trace (default: off, much faster on Qwen)
  JUDGE_STRICT_SCHEMA=0  use format="json" in round 1 instead of the pydantic schema
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional, Tuple, Union, get_args, get_origin

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


def _log(msg: str) -> None:
    """stderr only: stdout is reserved for the pipeline's JSON payload."""
    print(f"[ollama_judge] {msg}", file=sys.stderr)


def _think_kwargs() -> dict[str, Any]:
    return {} if os.environ.get("JUDGE_THINK", "0") == "1" else {"think": False}


def _strict_schema() -> bool:
    return os.environ.get("JUDGE_STRICT_SCHEMA", "1") != "0"


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


def _first_json_value(text: str) -> Optional[str]:
    """Return the first JSON object/array in ``text``, ignoring trailing prose."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            _obj, end = decoder.raw_decode(text[i:])
            return text[i : i + end].strip()
        except json.JSONDecodeError:
            continue
    return None


def normalize_ollama_metric_json(raw: Optional[str]) -> str:
    """Turn raw assistant text into a JSON string for ``model_validate_json``."""
    s = (raw or "").strip()
    if not s:
        return s

    for marker in _THINK_END_MARKERS:
        if marker in s:
            s = s.split(marker, 1)[-1].strip()

    s = _strip_leading_fenced_json(s)
    extracted = _first_json_value(s)
    return extracted if extracted is not None else s.strip()


def _list_field_names(schema: type[BaseModel]) -> list[str]:
    names: list[str] = []
    for name, info in schema.model_fields.items():
        ann = info.annotation
        origin = get_origin(ann)
        if origin is list:
            names.append(name)
            continue
        if origin is Union and any(get_origin(a) is list for a in get_args(ann)):
            names.append(name)
    # Fallback when annotations are unresolved strings under future annotations.
    if not names:
        for candidate in ("verdicts", "claims", "truths", "statements"):
            if candidate in schema.model_fields:
                names.append(candidate)
    return names


def _coerce_to_schema_dict(data: Any, schema: type[BaseModel]) -> Any:
    """
    Lossless repair only: a bare JSON list becomes {"<the schema's one list field>": list}.
    Anything else is returned unchanged and will fail validation on purpose. A lone verdict
    object must NOT be turned into a one-item list: faithfulness scores the share of
    non-"no" verdicts, so one repaired "yes" would read as a perfect 1.0.
    """
    if isinstance(data, list):
        list_fields = _list_field_names(schema)
        if len(list_fields) == 1:
            _log(f"repaired bare list -> {{'{list_fields[0]}': [...]}} for {schema.__name__}")
            return {list_fields[0]: data}
    return data


class OllamaJudgeModel(OllamaModel):
    """DeepEval ``OllamaModel`` with thinking-field merge + JSON-schema fallback."""

    @staticmethod
    def _combined_assistant_text(message: Any) -> str:
        """Prefer ``content`` when it already holds JSON; else merge thinking."""
        if message is None:
            return ""
        content = getattr(message, "content", None)
        thinking = getattr(message, "thinking", None)
        c = content.strip() if isinstance(content, str) else ""
        t = thinking.strip() if isinstance(thinking, str) else ""
        if c and _first_json_value(c) is not None:
            return c
        if t and _first_json_value(t) is not None:
            return t
        return "\n\n".join(p for p in (c, t) if p)

    def _chat_options(self) -> dict[str, Any]:
        return {
            **{"temperature": self.temperature},
            **self.generation_kwargs,
        }

    def _parse_schema_text(self, raw: Optional[str], schema: BaseModel) -> BaseModel:
        cleaned = normalize_ollama_metric_json(raw)
        if not cleaned:
            raise ValueError("empty assistant text after normalize")
        schema_cls = schema if isinstance(schema, type) else type(schema)
        try:
            return schema_cls.model_validate_json(cleaned)
        except ValidationError:
            data = _coerce_to_schema_dict(json.loads(cleaned), schema_cls)
            return schema_cls.model_validate(data)

    def _chat(self, chat_model: Any, messages: list, fmt: Any, opts: dict) -> Any:
        kwargs = dict(model=self.name, messages=messages, format=fmt, options=opts)
        try:
            return chat_model.chat(**kwargs, **_think_kwargs())
        except TypeError:
            _log("ollama client does not accept think=...; run: pip install -U ollama")
            return chat_model.chat(**kwargs)

    async def _a_chat(self, chat_model: Any, messages: list, fmt: Any, opts: dict) -> Any:
        kwargs = dict(model=self.name, messages=messages, format=fmt, options=opts)
        try:
            return await chat_model.chat(**kwargs, **_think_kwargs())
        except TypeError:
            _log("ollama client does not accept think=...; run: pip install -U ollama")
            return await chat_model.chat(**kwargs)

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
        strict = _strict_schema()
        fmt1 = (schema.model_json_schema() if strict else "json") if schema else None
        response = self._chat(chat_model, messages, fmt1, opts)
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

        if not strict:
            raise RuntimeError(
                f"Ollama judge returned no parseable JSON (format=json). round1={first_err!s}"
            ) from first_err

        response2 = self._chat(chat_model, messages, "json", opts)
        raw2 = self._combined_assistant_text(response2.message)
        try:
            return self._parse_schema_text(raw2, schema), 0.0
        except (ValidationError, ValueError, json.JSONDecodeError) as exc2:
            raise RuntimeError(
                "Ollama judge returned no parseable JSON (tried schema format and json format). "
                "Try: export EVAL_OLLAMA_NUM_CTX=16384. "
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
        strict = _strict_schema()
        fmt1 = (schema.model_json_schema() if strict else "json") if schema else None
        response = await self._a_chat(chat_model, messages, fmt1, opts)
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

        if not strict:
            raise RuntimeError(
                f"Ollama judge returned no parseable JSON (format=json). round1={first_err!s}"
            ) from first_err

        response2 = await self._a_chat(chat_model, messages, "json", opts)
        raw2 = self._combined_assistant_text(response2.message)
        try:
            return self._parse_schema_text(raw2, schema), 0.0
        except (ValidationError, ValueError, json.JSONDecodeError) as exc2:
            raise RuntimeError(
                "Ollama judge returned no parseable JSON (tried schema format and json format). "
                "Try: export EVAL_OLLAMA_NUM_CTX=16384. "
                f"schema_round={first_err!s}; json_round={exc2!s}"
            ) from exc2