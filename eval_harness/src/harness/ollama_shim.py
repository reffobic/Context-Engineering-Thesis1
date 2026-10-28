"""Transport shim for Ollama calls made by the unmodified pipeline.

Patches requests.post and urllib.request.urlopen. For each request to an Ollama endpoint it
  * sets the configured options where the call site left them out (think, seed, temperature,
    keep_alive) and sets the uniform num_ctx (the original value is logged, D15),
  * records the request and Ollama's own counters (prompt_eval_count, eval_count, durations,
    done_reason) for the role set by the instrumentation layer,
  * flags possible context truncation (prompt_eval_count close to num_ctx).
Token counts come only from here. The pipeline's own `token_usage` fields are never summed.
`redirect` maps a host:port to another one (tests only).
"""
from __future__ import annotations

import contextlib
import contextvars
import io
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

LOG_REQUEST_ROLES = ("gapa_gate_call1", "gapa_gate_call2", "gasr_gate")
ROLE: contextvars.ContextVar[str] = contextvars.ContextVar("ollama_role", default="unknown")


@contextlib.contextmanager
def role(name: str):
    tok = ROLE.set(name)
    try:
        yield
    finally:
        ROLE.reset(tok)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, rec: dict[str, Any]) -> None:
        with self._lock:
            rec["seq"] = len(self.calls)
            self.calls.append(rec)

    def reset(self) -> list[dict[str, Any]]:
        with self._lock:
            out, self.calls = self.calls, []
        return out


class OllamaShim:
    def __init__(self, cfg: dict, recorder: Recorder, redirect: dict[str, str] | None = None,
                 ollama_ports: tuple[int, ...] = (11434,), answer_stop: list[str] | None = None) -> None:
        self.cfg, self.rec, self.redirect = cfg, recorder, redirect or {}
        self.answer_stop = list(answer_stop or [])                # opt-in (extras.toml / --answer-stop); empty = requests unchanged
        self.ports = ollama_ports
        self._orig_post = None
        self._orig_urlopen = None

    # ── option handling ──────────────────────────────────────────────────────
    def _is_ollama(self, url: str) -> bool:
        u = urlparse(url)
        return (u.port in self.ports or u.netloc in self.redirect) and u.path.startswith("/api/")

    def _rewrite(self, url: str) -> str:
        u = urlparse(url)
        tgt = self.redirect.get(u.netloc)
        return url.replace(u.netloc, tgt, 1) if tgt else url

    def prepare(self, body: dict) -> tuple[dict, dict]:
        """Return (new_body, record of what was changed)."""
        o = self.cfg["ollama"]
        b = json.loads(json.dumps(body))
        opts = b.setdefault("options", {})
        changed: dict[str, Any] = {}
        asked = opts.get("num_ctx")
        if o.get("override_num_ctx", True) or asked is None:
            if asked != o["num_ctx"]:
                changed["num_ctx"] = {"asked": asked, "set": o["num_ctx"]}
            opts["num_ctx"] = o["num_ctx"]
        if "think" not in b:
            b["think"] = bool(o["think"])
            changed["think"] = b["think"]
        if "seed" not in opts:
            opts["seed"] = int(o["seed"])
            changed["seed"] = opts["seed"]
        if "temperature" not in opts:
            opts["temperature"] = float(o["answer_temperature"])
            changed["temperature"] = opts["temperature"]
        if "keep_alive" not in b:
            b["keep_alive"] = o["keep_alive"]
            changed["keep_alive"] = o["keep_alive"]
        if self.answer_stop and ROLE.get() == "answer" and "stop" not in opts:
            opts["stop"] = list(self.answer_stop)
            changed["stop"] = opts["stop"]
        return b, changed

    def _record(self, url: str, orig: dict, sent: dict, changed: dict, resp: dict | None,
                wall_ms: float, error: str | None, http_status: int | None = None,
                error_text: str | None = None) -> None:
        o = self.cfg["ollama"]
        num_ctx = sent["options"]["num_ctx"]
        pe = (resp or {}).get("prompt_eval_count")
        msgs = sent.get("messages")
        text = "".join(m.get("content", "") for m in msgs) if msgs else sent.get("prompt", "")
        self.rec.add({
            "role": ROLE.get(), "path": urlparse(url).path, "model": sent.get("model"),
            "options_sent": sent["options"], "think_sent": sent.get("think"), "keep_alive": sent.get("keep_alive"),
            "shim_changed": changed, "num_predict": sent["options"].get("num_predict"),
            "num_gpu": sent["options"].get("num_gpu"),
            "prompt_eval_count": pe, "eval_count": (resp or {}).get("eval_count"),
            "prompt_chars": len(text), "prompt_chars_div4": len(text) // 4,
            "total_duration_ns": (resp or {}).get("total_duration"), "load_duration_ns": (resp or {}).get("load_duration"),
            "prompt_eval_duration_ns": (resp or {}).get("prompt_eval_duration"),
            "eval_duration_ns": (resp or {}).get("eval_duration"),
            "done_reason": (resp or {}).get("done_reason"), "wall_ms": round(wall_ms, 2),
            "num_ctx": num_ctx,
            "truncation_suspected": bool(pe is not None and pe >= num_ctx - int(o["truncation_margin_tokens"])),
            "error": error, "http_status": http_status, "error_text": error_text,
            # first words of judge/gate replies (not of answers) to diagnose unparseable verdicts
            # exact request and full reply of the gate calls (small), for diagnosing routing decisions
            "request": ({"messages": msgs, "options": sent["options"], "think": sent.get("think"),
                         "keep_alive": sent.get("keep_alive"), "model": sent.get("model")}
                        if ROLE.get() in LOG_REQUEST_ROLES else None),
            "reply": ((((resp or {}).get("message") or {}).get("content") or (resp or {}).get("response") or "")[:4000]
                      if ROLE.get() in LOG_REQUEST_ROLES else None),
            "content_head": (None if ROLE.get() == "answer" else
                             (((resp or {}).get("message") or {}).get("content") or (resp or {}).get("response") or "")[:240]),
        })

    # ── patches ──────────────────────────────────────────────────────────────
    def install(self) -> None:
        import requests
        self._orig_post, self._orig_urlopen = requests.post, urllib.request.urlopen
        shim = self

        def post(url, data=None, json=None, **kw):                # noqa: A002  (mirror requests.post)
            if not isinstance(url, str) or not shim._is_ollama(url) or json is None:
                return shim._orig_post(url, data=data, json=json, **kw)
            sent, changed = shim.prepare(json)
            t = time.perf_counter()
            try:
                r = shim._orig_post(shim._rewrite(url), json=sent, **kw)
                try:
                    body = r.json()
                except ValueError:
                    body = None
                bad = r.status_code >= 400
                shim._record(url, json, sent, changed, body, (time.perf_counter() - t) * 1000, None,
                             r.status_code, (r.text or "")[:400] if bad else None)
                return r
            except Exception as exc:
                shim._record(url, json, sent, changed, None, (time.perf_counter() - t) * 1000, repr(exc))
                raise

        def urlopen(req, *a, **kw):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if not shim._is_ollama(url) or getattr(req, "data", None) is None:
                return shim._orig_urlopen(req, *a, **kw)
            import json as _json
            orig = _json.loads(req.data.decode("utf-8"))
            sent, changed = shim.prepare(orig)
            new = urllib.request.Request(shim._rewrite(url), data=_json.dumps(sent).encode("utf-8"),
                                         method=req.get_method(), headers=dict(req.header_items()))
            t = time.perf_counter()
            try:
                resp = shim._orig_urlopen(new, *a, **kw)
                raw = resp.read()
                shim._record(url, orig, sent, changed, _json.loads(raw.decode("utf-8")),
                             (time.perf_counter() - t) * 1000, None)
                return _Replay(raw)
            except urllib.error.HTTPError as exc:
                # read the body for classification, then put it back so the pipeline's own read still works
                body = b""
                try:
                    body = exc.read()
                    exc.fp = io.BytesIO(body)
                except Exception:
                    pass
                shim._record(url, orig, sent, changed, None, (time.perf_counter() - t) * 1000, repr(exc),
                             exc.code, body.decode("utf-8", "replace")[:400])
                raise
            except Exception as exc:
                shim._record(url, orig, sent, changed, None, (time.perf_counter() - t) * 1000, repr(exc))
                raise

        requests.post = post
        urllib.request.urlopen = urlopen

    def uninstall(self) -> None:
        import requests
        if self._orig_post:
            requests.post, urllib.request.urlopen = self._orig_post, self._orig_urlopen


class _Replay(io.BytesIO):
    """Response object the pipeline can read(); already consumed once by the shim."""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def redirect_only(redirect: dict[str, str]) -> None:
    """Tests: send Ollama calls to a fake server without touching options or recording."""
    import requests
    orig_post, orig_urlopen = requests.post, urllib.request.urlopen

    def rw(url):
        u = urlparse(url)
        return url.replace(u.netloc, redirect[u.netloc], 1) if u.netloc in redirect else url

    def post(url, data=None, json=None, **kw):                    # noqa: A002
        return orig_post(rw(url) if isinstance(url, str) else url, data=data, json=json, **kw)

    def urlopen(req, *a, **kw):
        if hasattr(req, "full_url") and req.full_url != rw(req.full_url):
            req = urllib.request.Request(rw(req.full_url), data=req.data, method=req.get_method(),
                                         headers=dict(req.header_items()))
        return orig_urlopen(req, *a, **kw)

    requests.post, urllib.request.urlopen = post, urlopen
