"""Scripted fake Ollama server for end-to-end tests of the unmodified pipeline.

The reply depends on which pipeline prompt was sent (recognised from its system prompt) and on the
scenario set through POST /__scenario. Token counts are derived from the prompt length so sums are
reproducible. GET /__log returns every request seen; POST /__reset clears it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def classify(system: str) -> str:
    if "six steps" in system:
        return "gapa_gate_call1"
    if "repository code Q&A system" in system:
        return "gapa_gate_call2"
    if "AFTER a graph-neighbor expand" in system:
        return "post_expand_judge"
    if "repository code-search RAG system" in system:
        return "gasr_gate"
    if "Your job is to decide whether the chunks are sufficient" in system:
        return "selfroute_judge"
    return "answer"


class FakeOllama:
    def __init__(self) -> None:
        self.scenario: dict = {}
        self.log: list[dict] = []
        self._post_expand_i = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/__log":
                    return self._send(outer.log)
                if self.path == "/api/version":
                    return self._send({"version": "fake-0"})
                if self.path == "/api/tags":
                    return self._send({"models": [{"name": "fake:model", "digest": "sha256:fake"}]})
                if self.path == "/api/ps":
                    return self._send({"models": [{"name": "fake:model", "size": 100, "size_vram": 100}]})
                self._send({"models": []})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/__scenario":
                    outer.scenario, outer._post_expand_i = body, 0
                    return self._send({"ok": True})
                if self.path == "/__reset":
                    outer.log.clear()
                    return self._send({"ok": True})
                try:
                    self._send(outer.reply(self.path, body))
                except RuntimeError as exc:                    # scripted server error (e.g. an OOM message)
                    self._send({"error": str(exc)}, 500)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "FakeOllama":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()

    def reply(self, path: str, body: dict) -> dict:
        msgs = body.get("messages") or []
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        user = "".join(m["content"] for m in msgs if m["role"] == "user") or body.get("prompt", "")
        kind = classify(system) if msgs else "answer"
        self.log.append({"role": kind, "path": path, "body": body})
        sc = self.scenario
        if kind == "answer" and sc.get("answer_delay_s"):
            import time
            time.sleep(sc["answer_delay_s"])
        if sc.get("http_error") and kind == "answer" and sc.get("http_error_match", "cart") in user.lower():
            raise RuntimeError(sc["http_error"])
        if kind == "gapa_gate_call1":
            content = json.dumps({"sufficient": bool(sc.get("gapa1", True)), "confidence": "high", "reason": "fake"})
        elif kind == "gapa_gate_call2":
            content = json.dumps({"need": sc.get("structure", "CONTEXT"), "confidence": "high", "reason": "fake"})
        elif kind == "post_expand_judge":
            seq = sc.get("post_expand", [True])
            ok = seq[min(self._post_expand_i, len(seq) - 1)]
            self._post_expand_i += 1
            content = json.dumps({"sufficient": bool(ok), "confidence": "high", "reason": "fake"})
        elif kind == "gasr_gate":
            g = sc.get("gasr", {"sufficient": True})
            content = json.dumps({"sufficient": g["sufficient"], "need": g.get("need"), "confidence": "high", "reason": "fake"})
        elif kind == "selfroute_judge":
            content = json.dumps({"sufficient": bool(sc.get("selfroute", True)), "confidence": "high", "reason": "fake"})
        else:
            content = "FAKE ANSWER " + hashlib.sha256((system + user).encode()).hexdigest()[:12]
        n_in = max(1, (len(system) + len(user)) // 4)
        out = {"model": body.get("model"), "done": True, "done_reason": "stop", "prompt_eval_count": n_in,
               "eval_count": 17, "total_duration": 1_000_000, "load_duration": 0, "prompt_eval_duration": 500_000,
               "eval_duration": 400_000}
        if path.endswith("/chat"):
            out["message"] = {"role": "assistant", "content": content}
        else:
            out["response"] = content
        return out
