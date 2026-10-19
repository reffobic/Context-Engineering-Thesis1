"""Shared helpers: config loading/hash, GPU info, minimal Ollama HTTP client (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


def load_toml(path: Path) -> dict[str, Any]:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def config_hash(path: Path) -> str:
    """sha256 of the canonicalised parsed TOML (insensitive to whitespace/comments)."""
    blob = json.dumps(load_toml(path), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _smi(args: list[str]) -> Optional[str]:
    try:
        out = subprocess.run(
            ["nvidia-smi", *args], capture_output=True, text=True, timeout=30, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def gpu_info() -> Optional[dict[str, Any]]:
    raw = _smi(
        ["--query-gpu=name,driver_version,memory.total,memory.used",
         "--format=csv,noheader,nounits"]
    )
    if not raw:
        return None
    name, driver, total, used = [x.strip() for x in raw.splitlines()[0].split(",")]
    return {"name": name, "driver": driver, "total_mib": int(total), "used_mib": int(used)}


def gpu_processes() -> list[dict[str, str]]:
    raw = _smi(["--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits"])
    rows = []
    for line in (raw or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            rows.append({"pid": parts[0], "name": parts[1], "used_mib": parts[2]})
    return rows


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, timeout_s: float):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise OllamaError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError(f"connection/timeout: {exc}") from exc

    def version(self) -> str:
        return self._request("GET", "/api/version").get("version", "")

    def tags(self) -> list[dict]:
        return self._request("GET", "/api/tags").get("models", [])

    def ps(self) -> list[dict]:
        return self._request("GET", "/api/ps").get("models", [])

    def show(self, model: str) -> dict:
        return self._request("POST", "/api/show", {"model": model})

    def unload(self, model: str) -> None:
        self._request("POST", "/api/generate", {"model": model, "keep_alive": 0})

    def chat(self, model: str, messages: list[dict], options: dict,
             think: Optional[bool] = False, keep_alive: str = "2m") -> tuple[dict, float]:
        body: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            "options": options, "keep_alive": keep_alive,
        }
        if think is not None:
            body["think"] = think
        t0 = time.perf_counter()
        resp = self._request("POST", "/api/chat", body)
        return resp, time.perf_counter() - t0
