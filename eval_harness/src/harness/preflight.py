"""Preflight: PASS/FAIL checks before any experiment run.

Usage:
  PYTHONPATH=eval_harness/src python -m harness.preflight \
      --profile eval_harness/profiles/ubuntu-4080.toml \
      --config eval_harness/configs/preflight.toml
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from harness.common import (
    OllamaClient, OllamaError, config_hash, gpu_info, gpu_processes, load_toml,
)

PASS, FAIL, INFO, LIMIT = "PASS", "FAIL", "INFO", "LIMIT"  # LIMIT = search boundary, not a failure

_VOCAB = (
    "alpha beta gamma delta value index buffer render query model cache token "
    "parse node edge chunk score route patch commit field array merge split "
    "handler config socket stream filter module symbol import return yield"
).split()


def make_filler(n_words: int, seed: int) -> str:
    """Deterministic pseudo-text; distinct seeds give distinct prompts (defeats prefix cache)."""
    rng = random.Random(seed)
    return " ".join(rng.choice(_VOCAB) + str(rng.randrange(1000)) for _ in range(n_words))


class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, check: str, status: str, detail: str, **extra: Any) -> None:
        self.rows.append({"check": check, "status": status, "detail": detail, **extra})
        print(f"[{status}] {check}: {detail}", flush=True)

    @property
    def failed(self) -> bool:
        return any(r["status"] == FAIL for r in self.rows)


def _opts(cfg: dict, num_ctx: int, num_predict: Optional[int] = None) -> dict:
    return {
        "num_ctx": num_ctx,
        "num_predict": num_predict or cfg["probe_num_predict"],
        "temperature": cfg["temperature"],
        "seed": cfg["seed"],
    }


def check_gpu(rep: Report) -> Optional[dict]:
    info = gpu_info()
    if info is None:
        rep.add("gpu", FAIL, "nvidia-smi unavailable or no NVIDIA GPU")
        return None
    rep.add("gpu", PASS,
            f"{info['name']}, driver {info['driver']}, {info['total_mib']} MiB total, "
            f"{info['used_mib']} MiB used", **info)
    return info


def check_disk(rep: Report, cfg: dict, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(data_dir).free / 1e9
    ok = free_gb >= cfg["min_free_disk_gb"]
    rep.add("disk", PASS if ok else FAIL,
            f"{free_gb:.0f} GB free at {data_dir.as_posix()} (need >= {cfg['min_free_disk_gb']})")


def check_ollama_idle(rep: Report, cfg: dict, cli: OllamaClient) -> None:
    loaded = cli.ps()
    names = [m["name"] for m in loaded]
    for n in names:
        cli.unload(n)
    time.sleep(3)
    still = [m["name"] for m in cli.ps()]
    gpu = gpu_info() or {}
    procs = gpu_processes()
    used = gpu.get("used_mib", -1)
    ok = not still and 0 <= used <= cfg["max_idle_vram_used_mib"]
    rep.add("ollama_ps_idle", PASS if ok else FAIL,
            f"was loaded: {names or 'none'}; after unload: {still or 'none'}; "
            f"GPU used {used} MiB (limit {cfg['max_idle_vram_used_mib']}); "
            f"non-Ollama compute procs: {procs or 'none reported'}",
            unloaded=names, gpu_used_mib=used)


def check_models(rep: Report, cfg: dict, cli: OllamaClient) -> dict[str, dict]:
    tags = {m["name"]: m for m in cli.tags()}
    out: dict[str, dict] = {}
    for model in cfg["models"]:
        if model not in tags:
            rep.add(f"model_present[{model}]", FAIL, "tag not found in /api/tags")
            continue
        digest = tags[model].get("digest", "")
        show = cli.show(model)
        native = next((v for k, v in show.get("model_info", {}).items()
                       if k.endswith(".context_length")), None)
        out[model] = {"digest": digest, "native_ctx": native,
                      "capabilities": show.get("capabilities", [])}
        rep.add(f"model_present[{model}]", PASS,
                f"digest {digest[:12]}, native ctx {native}", digest=digest, native_ctx=native)
        try:
            resp, secs = cli.chat(
                model, [{"role": "user", "content": "Reply with the single word: ready"}],
                _opts(cfg, cfg["num_ctx_candidates"][0]), think=False, keep_alive="30s")
            content = (resp.get("message", {}).get("content") or "").strip()
            ok = bool(content) and resp.get("done") is True
            rep.add(f"model_responds[{model}]", PASS if ok else FAIL,
                    f"{secs:.1f}s, reply {content[:40]!r}, prompt_eval_count="
                    f"{resp.get('prompt_eval_count')}, eval_count={resp.get('eval_count')}")
        except OllamaError as exc:
            rep.add(f"model_responds[{model}]", FAIL, str(exc))
    return out


def _fit_probe(cli: OllamaClient, cfg: dict, model: str, num_ctx: int,
               tokens_per_word: float, native: Optional[int]) -> dict[str, Any]:
    res: dict[str, Any] = {"model": model, "num_ctx": num_ctx}
    if native and num_ctx > native:
        res.update(ok=False, reason=f"exceeds native context {native}")
        return res
    try:
        cli.unload(model)
        time.sleep(1)
        target = int(num_ctx * cfg["fill_fraction"])
        words = int(target / tokens_per_word)
        prompt = make_filler(words, seed=num_ctx) + "\n\nReply with the single word: ok"
        resp, secs = cli.chat(model, [{"role": "user", "content": prompt}],
                              _opts(cfg, num_ctx), think=False, keep_alive="1m")
        pec = resp.get("prompt_eval_count") or 0
        loaded = next((m for m in cli.ps() if m["name"] == model), {})
        size, vram = loaded.get("size", 0), loaded.get("size_vram", 0)
        gpu = gpu_info() or {}
        tol = cfg["fill_tolerance"]
        in_range = target * (1 - tol) <= pec <= target * (1 + tol) and pec < num_ctx
        full_gpu = size > 0 and vram >= size
        res.update(
            seconds=round(secs, 1), prompt_eval_count=pec, target_tokens=target,
            truncated=not in_range and pec >= num_ctx * 0.97,
            model_size_gib=round(size / 2**30, 2), vram_gib=round(vram / 2**30, 2),
            fully_on_gpu=full_gpu, gpu_used_mib=gpu.get("used_mib"),
            has_content=bool((resp.get("message", {}).get("content") or "").strip()),
            ok=bool(in_range and full_gpu and resp.get("done")),
        )
        if not res["ok"]:
            res["reason"] = (
                "prompt_eval_count outside tolerance/truncated" if not in_range
                else "not fully resident in VRAM (CPU offload)" if not full_gpu
                else "incomplete response")
    except OllamaError as exc:
        res.update(ok=False, reason=f"error: {exc}")
    finally:
        try:
            cli.unload(model)
        except OllamaError:
            pass
    return res


def check_ctx_fit(rep: Report, cfg: dict, cli: OllamaClient,
                  models: dict[str, dict]) -> Optional[int]:
    per_model_max: dict[str, int] = {}
    probes: list[dict] = []
    for model, meta in models.items():
        # calibrate tokens per filler word for this tokenizer
        cli.unload(model)
        words = 800
        resp, _ = cli.chat(model, [{"role": "user", "content": make_filler(words, 99991)}],
                           _opts(cfg, cfg["num_ctx_candidates"][0]), think=False,
                           keep_alive="30s")
        tpw = max(0.5, (resp.get("prompt_eval_count") or words) / words)
        best = 0
        for ctx in cfg["num_ctx_candidates"]:
            r = _fit_probe(cli, cfg, model, ctx, tpw, meta.get("native_ctx"))
            probes.append(r)
            status = PASS if r["ok"] else LIMIT
            rep.add(f"ctx_fit[{model}@{ctx}]", status,
                    (f"{r['prompt_eval_count']} prompt tok, {r['seconds']}s, "
                     f"{r['vram_gib']}/{r['model_size_gib']} GiB in VRAM, GPU used "
                     f"{r['gpu_used_mib']} MiB") if "prompt_eval_count" in r
                    else r.get("reason", ""), **{k: v for k, v in r.items()
                                                  if k not in ("model", "num_ctx")})
            if not r["ok"]:
                break
            best = ctx
        per_model_max[model] = best
    if len(per_model_max) < len(cfg["models"]) or not all(per_model_max.values()):
        rep.add("recommended_num_ctx", FAIL, f"no common fitting value: {per_model_max}",
                per_model_max=per_model_max)
        return None
    chosen = min(per_model_max.values())
    rep.add("recommended_num_ctx", PASS,
            f"{chosen} = largest value fitting all three models fully on GPU "
            f"(per-model max: {per_model_max}); must be fixed in the experiment config "
            f"and shared by every machine", chosen=chosen, per_model_max=per_model_max)
    return chosen


def probe_cache_and_thinking(rep: Report, cfg: dict, cli: OllamaClient) -> None:
    """INFO only (D17 log-only, D15 thinking state)."""
    model = cfg["models"][0]
    ctx = cfg["num_ctx_candidates"][0]
    cli.unload(model)
    prompt = make_filler(1500, 4242) + "\n\nReply with the single word: ok"
    counts = []
    for _ in range(2):
        resp, secs = cli.chat(model, [{"role": "user", "content": prompt}],
                              _opts(cfg, ctx), think=False)
        counts.append((resp.get("prompt_eval_count"), round(secs, 2)))
    count_drop = bool(counts[0][0] and counts[1][0] is not None and counts[1][0] < 0.5 * counts[0][0])
    time_drop = counts[1][1] < 0.5 * counts[0][1]
    verdict = (
        "prefix cache ACTIVE" + (": repeat prompt_eval_count under-reports" if count_drop
                                 else "; prompt_eval_count still reports the FULL count, "
                                      "but latency is reduced (latency comparisons are affected)")
        if (count_drop or time_drop) else "prefix cache not observed")
    rep.add("prompt_cache_probe", INFO,
            f"identical prompt twice on {model}: (prompt_eval_count, secs) = {counts}; {verdict}",
            counts=counts, cache_active=count_drop or time_drop,
            count_under_reported=count_drop, latency_reduced=time_drop)
    q = [{"role": "user", "content": "What is 17 times 3? Answer briefly."}]
    for label, think in (("think=unset", None), ("think=false", False)):
        resp, _ = cli.chat(model, q, _opts(cfg, ctx, num_predict=2048), think=think)
        msg = resp.get("message", {})
        rep.add(f"thinking_probe[{label}]", INFO,
                f"thinking field chars={len((msg.get('thinking') or ''))}, "
                f"content={((msg.get('content') or '').strip()[:40])!r}, "
                f"eval_count={resp.get('eval_count')}",
                thinking_chars=len(msg.get("thinking") or ""), eval_count=resp.get("eval_count"))
    cli.unload(model)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--skip-ctx-fit", action="store_true", help="debug only")
    args = ap.parse_args()

    profile, cfg = load_toml(args.profile), load_toml(args.config)
    cli = OllamaClient(cfg["ollama_base_url"], cfg["request_timeout_s"])
    rep = Report()
    started = datetime.now(timezone.utc)

    try:
        rep.add("ollama_version", PASS, cli.version(), version=cli.version())
    except OllamaError as exc:
        rep.add("ollama_version", FAIL, f"Ollama not reachable: {exc}")
        return 1

    gpu = check_gpu(rep)
    check_disk(rep, cfg, Path(profile["data_dir"]))
    check_ollama_idle(rep, cfg, cli)
    models = check_models(rep, cfg, cli)
    chosen = None
    if len(models) == len(cfg["models"]) and not args.skip_ctx_fit:
        chosen = check_ctx_fit(rep, cfg, cli, models)
        probe_cache_and_thinking(rep, cfg, cli)
    else:
        rep.add("ctx_fit", FAIL, "skipped: missing models or --skip-ctx-fit")
    check_ollama_idle_after = [m["name"] for m in cli.ps()]
    rep.add("final_unload", PASS if not check_ollama_idle_after else FAIL,
            f"models still loaded: {check_ollama_idle_after or 'none'}")

    out = {
        "machine_id": profile["machine_id"], "gpu": gpu,
        "ollama_version": cli.version(), "config_hash": config_hash(args.config),
        "started_utc": started.isoformat(), "recommended_num_ctx": chosen,
        "models": models, "checks": rep.rows,
    }
    out_dir = Path(profile["data_dir"]) / "preflight"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{profile['machine_id']}-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    n_fail = sum(r["status"] == FAIL for r in rep.rows)
    print(f"\nOVERALL: {'FAIL' if rep.failed else 'PASS'} "
          f"({n_fail} failing checks). Report: {path.as_posix()}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
