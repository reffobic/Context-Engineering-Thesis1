"""Local test of the OpenRouter provider hook against a fake server (no network, no key)."""
import json, os, sys, threading, tempfile, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
seen, fail_first = [], {"n": 0}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append((self.headers.get("Authorization"), body))
        if body["messages"][-1]["content"] == "flaky" and fail_first["n"] < 1:
            fail_first["n"] += 1; code, out = 429, {"error": "rate"}
        elif body["messages"][-1]["content"] == "reasonbad" and "reasoning" in body:
            code, out = 400, {"error": "Unsupported parameter: reasoning"}
        elif body["messages"][-1]["content"] == "short" and body.get("max_tokens", 0) < 100:
            code, out = 200, {"id": "g", "model": "m", "choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {"prompt_tokens": 5, "completion_tokens": body["max_tokens"]}}
        elif body["messages"][-1]["content"] == "bad":
            code, out = 400, {"error": "bad request"}
        else:
            code, out = 200, {"id": "gen-1", "model": body["model"], "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "cost": 0.0012,
                          "completion_tokens_details": {"reasoning_tokens": 0}, "prompt_tokens_details": {"cached_tokens": 3}}}
        b = json.dumps(out).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg = f'''[provider]
base_url = "http://127.0.0.1:{srv.server_port}/v1"
api_key_env = "FAKE_KEY"
reasoning_effort = "none"
max_retries = 2
[provider.models]
default = "openai/gpt-5.1"
answer = "openai/gpt-5.1"
'''
d = tempfile.mkdtemp(); open(f"{d}/p.toml", "w").write(cfg)
os.environ.update(HARNESS_PROVIDER_CONFIG=f"{d}/p.toml", FAKE_KEY="sk-test-123")
import time; time.sleep(0)
import harness.provider as P; P.time.sleep = lambda s: None          # no real waiting in the test
from harness.ollama_shim import OllamaShim, Recorder, role
import requests
rec = Recorder()
exp = {"ollama": {"num_ctx": 32768, "think": False, "answer_temperature": 0.0, "keep_alive": "10m", "seed": 42, "truncation_margin_tokens": 8}}
sh = OllamaShim(exp, rec); sh.install()
# 1 requests.post chat
with role("gapa_gate_call1"):
    r = requests.post("http://localhost:11434/api/chat", json={"model": "qwen3.5:9b", "messages": [{"role": "user", "content": "hi"}], "options": {"num_predict": 64}})
j = r.json(); assert j["message"]["content"] == "hello" and j["prompt_eval_count"] == 11 and j["eval_count"] == 5, j
b = seen[-1][1]; assert b["model"] == "openai/gpt-5.1" and b["reasoning"] == {"effort": "none"} and b["seed"] == 42 and b["temperature"] == 0.0 and b["max_tokens"] == 64, b
assert "think" not in b and "keep_alive" not in b and seen[-1][0] == "Bearer sk-test-123"
# 2 urlopen generate
req = urllib.request.Request("http://localhost:11434/api/generate", data=json.dumps({"model": "x", "prompt": "p", "system": "s", "options": {}}).encode(), method="POST")
out = json.loads(urllib.request.urlopen(req).read()); assert out["response"] == "hello"
assert seen[-1][1]["messages"][0] == {"role": "system", "content": "s"}
# 3 retry on 429
r = requests.post("http://localhost:11434/api/chat", json={"model": "x", "messages": [{"role": "user", "content": "flaky"}], "options": {}})
assert r.status_code == 200 and rec.calls[-1]["provider_retries"] == 1, rec.calls[-1]
# 4 400 is not retried and is recorded as an error status
n = len(seen); r = requests.post("http://localhost:11434/api/chat", json={"model": "x", "messages": [{"role": "user", "content": "bad"}], "options": {}})
assert r.status_code == 400 and len(seen) == n + 1 and rec.calls[-1]["http_status"] == 400
# 4b safeguards
r = requests.post("http://localhost:11434/api/chat", json={"model": "x", "messages": [{"role": "user", "content": "reasonbad"}], "options": {}})
assert r.status_code == 200 and "reasoning" not in seen[-1][1] and rec.calls[-1]["provider_usage"]["safeguards"] == ["dropped_reasoning"], rec.calls[-1]
r = requests.post("http://localhost:11434/api/chat", json={"model": "x", "messages": [{"role": "user", "content": "short"}], "options": {"num_predict": 64}})
assert r.json()["message"]["content"] == "hello" and seen[-1][1]["max_tokens"] == 128 and rec.calls[-1]["provider_usage"]["safeguards"] == ["doubled_max_tokens"]
# 5 record contents + no key leak
c = rec.calls[0]; assert c["prompt_eval_count"] == 11 and c["eval_count"] == 5 and c["provider_usage"]["cost"] == 0.0012 and c["provider_usage"]["cached_tokens"] == 3
assert "sk-test-123" not in json.dumps(rec.calls)
# 6 missing key fails loudly
os.environ.pop("FAKE_KEY")
try: P.provider_from_env(); raise SystemExit("no error")
except P.ProviderError as e: print("missing key ->", e)
print("ALL PROVIDER TESTS PASSED")
