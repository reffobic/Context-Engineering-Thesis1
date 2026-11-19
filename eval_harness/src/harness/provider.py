"""Paid-API provider (OpenRouter) behind the Ollama-shaped calls the unmodified pipeline makes.

The pipeline sends Ollama requests (``/api/chat`` or ``/api/generate``) and reads Ollama-shaped replies.
``harness.ollama_shim`` hands every such request to ``OpenRouterProvider.call`` when a provider is active.
The provider
  * maps the Ollama request to an OpenAI-style chat completion request (model chosen per role),
  * sends it with retries on rate limits and server errors,
  * maps the reply back to the Ollama shape, filling ``prompt_eval_count`` and ``eval_count`` from the
    API's usage fields, so token counts keep coming from the shim only,
  * keeps the extra usage fields (reasoning tokens, cost, cached tokens) under ``provider_usage``.

Settings live in a TOML file named by the environment variable HARNESS_PROVIDER_CONFIG; the key is read
from the environment variable named in that file (never stored in a file or written to a log).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

try:                                              # tomllib is in Python 3.11+, tomli is the backport
    import tomllib
except ModuleNotFoundError:                       # pragma: no cover
    import tomli as tomllib


class ProviderError(RuntimeError):
    """Raised for a provider misconfiguration (missing key, bad config)."""


def provider_from_env() -> "OpenRouterProvider | None":
    """Build the provider named by HARNESS_PROVIDER_CONFIG, or return None when it is not set."""
    path = os.environ.get("HARNESS_PROVIDER_CONFIG")
    if not path:
        return None
    with Path(path).open("rb") as fh:
        return OpenRouterProvider(tomllib.load(fh)["provider"])


class OpenRouterProvider:
    """Translate Ollama-shaped calls into OpenAI-compatible chat completions and back."""

    RETRY_STATUS = (429, 500, 502, 503, 504)

    def __init__(self, cfg: dict) -> None:
        self.url = cfg["base_url"].rstrip("/") + "/chat/completions"
        self.key = os.environ.get(cfg["api_key_env"], "")
        if not self.key:
            raise ProviderError(f"environment variable {cfg['api_key_env']} is not set")
        self.models: dict[str, str] = dict(cfg["models"])            # role -> model id, plus "default"
        if "default" not in self.models:
            raise ProviderError("[provider.models] needs a 'default' model id")
        self.reasoning_effort = cfg.get("reasoning_effort")           # e.g. "none"; absent = send nothing
        self.send_temperature = bool(cfg.get("send_temperature", True))
        self.send_seed = bool(cfg.get("send_seed", True))
        self.timeout = float(cfg.get("timeout_s", 300))
        self.max_retries = int(cfg.get("max_retries", 3))
        self.extra_headers = dict(cfg.get("headers", {}))             # e.g. HTTP-Referer / X-Title (optional)

    # ── request translation ──────────────────────────────────────────────────
    def model_for(self, role: str) -> str:
        """Model id for this role; roles without their own entry use the default."""
        return self.models.get(role, self.models["default"])

    def translate_request(self, path: str, body: dict, role: str) -> dict:
        """Ollama request body -> OpenAI-style chat completion body."""
        if path.endswith("/api/generate"):                            # single prompt, optional system text
            msgs = ([{"role": "system", "content": body["system"]}] if body.get("system") else [])
            msgs.append({"role": "user", "content": body.get("prompt", "")})
        else:
            msgs = body["messages"]
        opts = body.get("options") or {}
        out: dict[str, Any] = {"model": self.model_for(role), "messages": msgs, "stream": False,
                               "usage": {"include": True}}            # asks for cost and token details
        if self.send_temperature and "temperature" in opts:
            out["temperature"] = opts["temperature"]
        if self.send_seed and "seed" in opts:
            out["seed"] = opts["seed"]
        if opts.get("num_predict") and opts["num_predict"] > 0:       # Ollama's output cap
            out["max_tokens"] = opts["num_predict"]
        if opts.get("stop"):
            out["stop"] = opts["stop"]
        if self.reasoning_effort:
            out["reasoning"] = {"effort": self.reasoning_effort}
        return out                                                    # think, keep_alive, num_ctx have no API meaning

    # ── reply translation ────────────────────────────────────────────────────
    @staticmethod
    def translate_reply(j: dict) -> dict:
        """OpenAI-style reply -> Ollama-shaped reply (message, response, counters, done_reason)."""
        choice = (j.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = j.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        return {
            "model": j.get("model"), "message": {"role": "assistant", "content": text}, "response": text,
            "done": True, "done_reason": choice.get("finish_reason"),
            "prompt_eval_count": usage.get("prompt_tokens"), "eval_count": usage.get("completion_tokens"),
            "provider_usage": {"reasoning_tokens": details.get("reasoning_tokens"), "cached_tokens": cached,
                               "cost": usage.get("cost"), "generation_id": j.get("id")},
        }

    # ── the call ─────────────────────────────────────────────────────────────
    def call(self, path: str, body: dict, role: str) -> tuple[int, bytes, int]:
        """Send one request. Returns (HTTP status, reply bytes, retries used).

        On success the reply bytes are an Ollama-shaped JSON document; on failure they are the provider's
        error text, so the shim can record it and the pipeline's own raise_for_status() fires as before.
        Two single-use safeguards replace a paid pilot: an empty reply cut off by the token cap is retried
        once with double the cap, and a 400 that names reasoning/temperature/seed is retried once without
        that parameter. Both are listed under provider_usage["safeguards"].
        """
        payload = self.translate_request(path, body, role)
        safeguards: list[str] = []
        status, raw, retries = self._send(payload)
        if status == 400:                                             # unsupported parameter: drop it once
            text = raw.decode("utf-8", "replace").lower()
            for key in ("reasoning", "temperature", "seed"):
                if key in text and key in payload:
                    payload = {k: v for k, v in payload.items() if k != key}
                    safeguards.append(f"dropped_{key}")
                    status, raw, more = self._send(payload)
                    retries += more
                    break
        if status < 400:
            j = json.loads(raw.decode("utf-8"))
            ch = (j.get("choices") or [{}])[0]
            empty = not ((ch.get("message") or {}).get("content") or "").strip()
            if empty and ch.get("finish_reason") == "length" and payload.get("max_tokens"):
                payload = {**payload, "max_tokens": payload["max_tokens"] * 2}
                safeguards.append("doubled_max_tokens")
                status, raw, more = self._send(payload)
                retries += more
                if status < 400:
                    j = json.loads(raw.decode("utf-8"))
            if status < 400:
                out = self.translate_reply(j)
                out["provider_usage"]["safeguards"] = safeguards
                return status, json.dumps(out).encode("utf-8"), retries
        return status, raw, retries

    def _send(self, payload: dict) -> tuple[int, bytes, int]:
        """POST one payload; retry rate limits, 5xx and network errors with a growing pause."""
        import requests                                               # patched by the shim, but this URL is not Ollama's
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", **self.extra_headers}
        status, raw, retries = 0, b"", 0
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
                status, raw = r.status_code, r.content
            except requests.RequestException as exc:                  # network error: retry like a 5xx
                status, raw = 599, repr(exc).encode("utf-8")
            if status < 400 or (status not in self.RETRY_STATUS and status != 599):
                break                                                 # success, or 400/401/402/404: retrying cannot help
            if attempt < self.max_retries:
                retries += 1
                time.sleep(min(2 ** attempt, 20))                     # 1 s, 2 s, 4 s ...
        return status, raw, retries


def provider_env() -> dict[str, str]:
    """Environment variables a pipeline worker needs in paid-API mode (empty dict otherwise).

    The worker is started with a pinned environment, so the config path and the API key variable
    named in that config are passed on explicitly. The key's value is never written to disk or logs.
    """
    path = os.environ.get("HARNESS_PROVIDER_CONFIG")
    if not path:
        return {}
    with Path(path).open("rb") as fh:
        key_env = tomllib.load(fh)["provider"]["api_key_env"]
    out = {"HARNESS_PROVIDER_CONFIG": str(Path(path).resolve())}
    if os.environ.get(key_env):
        out[key_env] = os.environ[key_env]
    return out
