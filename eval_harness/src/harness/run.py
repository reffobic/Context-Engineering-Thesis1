"""Run one job: one arm x one model x one dataset sample, one JSON line per task.

  python -m harness.run --profile P --dataset D --model M --arm A --sample S.json --out DIR
        --experiment E --pipeline-config PC --index-config IC --run-config RC [--smoke] [--inject-timeout ID]

Each task runs in its own subprocess (harness.task_worker) against a verified scratch copy of its index.
Subprocess start-up, index copy/verify and model warm-up are logged separately and are NOT part of the
per-task latency. A task that times out, hits OOM or crashes is written as a failed line and the job
goes on; the job stops after max_consecutive_failures. Re-running with the same --out resumes.
The script never reads the gold-label file.
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from .common import OllamaClient, OllamaError, config_hash, gpu_info, load_toml
from .envinfo import _graphify_version, environment_info
from .index_cache import index_dir, make_key
from .instrument import JobFailure, check_prefix_mismatch_stubs
from .locmap import Mapper
from .pipeline_src import snapshot
from .pipelineenv import (EnvError, assert_snapshot_root, build_env, effective_record, forbidden_chroma_paths,
                          git_identity, require_runnable)
from .sampling import load_sample

OOM_RE = re.compile(r"out of memory|cuda error|cudamalloc|oom|requires more (system )?memory|failed to allocate", re.I)
SCHEMA = 1


class JobAbort(RuntimeError):
    pass


# ── index checks ─────────────────────────────────────────────────────────────

def sqlite_fingerprint(chroma_dir: Path) -> dict:
    """Chunk counts and an ID hash per collection, read through SQLite read-only/immutable (never via Chroma)."""
    con = sqlite3.connect(f"file:{chroma_dir / 'chroma.sqlite3'}?mode=ro&immutable=1", uri=True)
    try:
        out = {}
        for name, seg in con.execute("select c.name, s.id from segments s join collections c on c.id = s.collection "
                                     "where s.scope = 'METADATA'"):
            ids = sorted(r[0] for r in con.execute("select embedding_id from embeddings where segment_id = ?", (seg,)))
            out[name] = {"count": len(ids), "id_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}
        return out
    finally:
        con.close()


def check_index_files(chroma_dir: Path, graph_dir: Path) -> list[str]:
    problems = []
    if not (chroma_dir / "chroma.sqlite3").is_file():
        return ["chroma.sqlite3 missing"]
    try:
        fp = sqlite_fingerprint(chroma_dir)
    except Exception as exc:
        return [f"chroma.sqlite3 unreadable: {exc!r}"]
    for coll in ("code_embeddings", "code_meta_embeddings"):
        if coll not in fp:
            problems.append(f"collection {coll} missing")
        elif fp[coll]["count"] <= 0:
            problems.append(f"collection {coll} has no chunks")
    for f in ("call_graph.json", "bm25_index.json"):
        p = chroma_dir / f
        if not p.is_file():
            problems.append(f"{f} missing")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            problems.append(f"{f} is not valid JSON")
    if not (graph_dir / "graph.json").is_file():
        problems.append("graphify graph.json missing")
    return problems


def original_fingerprint(idx: Path) -> dict:
    """Once per job: counts + ID hash of the cached original; compared with the build report and with the
    fingerprint stored the first time (written next to, not inside, the Chroma files)."""
    fp = sqlite_fingerprint(idx / "chroma")
    rep = json.loads((idx / "build_report.json").read_text(encoding="utf-8"))
    for coll, key in (("code_embeddings", "chunks_code"), ("code_meta_embeddings", "chunks_meta")):
        if fp.get(coll, {}).get("count") != rep["counts"][key]:
            raise JobAbort(f"{idx.name}: {coll} has {fp.get(coll, {}).get('count')} chunks, build report says {rep['counts'][key]}")
    stored = idx / "fingerprint.json"
    if stored.is_file():
        if json.loads(stored.read_text(encoding="utf-8")) != fp:
            raise JobAbort(f"{idx.name}: chunk count / ID hash changed since the first check (cached index was modified)")
    else:
        stored.write_text(json.dumps(fp, indent=1), encoding="utf-8")
    return fp


def safe_rmtree(path: Path, root: Path) -> None:
    if root.resolve() in path.resolve().parents and path.exists():
        shutil.rmtree(path)


# ── job set-up ───────────────────────────────────────────────────────────────

class Job:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.repo = Path(__file__).resolve().parents[3]
        self.profile = load_toml(a.profile)
        self.exp, self.pcfg = load_toml(a.experiment), load_toml(a.pipeline_config)
        self.icfg, self.rcfg = load_toml(a.index_config), load_toml(a.run_config)
        self.data = Path(self.profile["data_dir"]).resolve()
        self.out = a.out
        self.client = OllamaClient(self.rcfg["run"]["ollama_base_url"], 120)
        self.arm = a.arm
        if self.arm not in self.rcfg["arms"]:
            raise JobAbort(f"unknown arm {self.arm}")
        self.arm_impl = "harness" if self.rcfg["arms"][self.arm] == "HARNESS" else "pipeline"
        # Table VIII ablation setting: every switch gets an explicit "0"/"1"; only GAPA may carry one
        self.abl_cfg = load_toml(a.ablations_config)
        settings = self.abl_cfg["settings"]
        if a.ablation not in settings:
            raise JobAbort(f"unknown ablation setting {a.ablation!r}")
        switches = list(self.abl_cfg["switches"])
        on = dict(settings[a.ablation]["env"])
        if set(on) - set(switches) or any(v not in ("0", "1") for v in on.values()):
            raise JobAbort(f"ablation {a.ablation}: only the declared switches with values 0/1 are allowed")
        if on and self.arm != "GAPA":
            raise JobAbort(f"ablation {a.ablation} is only allowed on the GAPA arm, not {self.arm}")
        self.abl_env = {k: "0" for k in switches} | on
        self.ablation = {"key": a.ablation, "label": settings[a.ablation]["label"], "env": self.abl_env}

    # all start-of-job checks; anything wrong aborts before a single task runs
    def preflight(self) -> dict:
        t0 = time.perf_counter()
        a, p, rc = self.a, self.pcfg["pipeline"], self.rcfg["run"]
        if not a.smoke and not p["record_results"]:
            raise JobAbort("record_results is false in pipeline.toml; only --smoke runs are allowed until it is confirmed")
        self.ident = git_identity(self.repo, self.pcfg)
        if not a.allow_dirty:
            require_runnable(self.ident)
        elif self.ident["revoked"]:
            raise EnvError("revoked harness/pipeline commit")
        env_probe = build_env(self.pcfg, a.model, self.exp, extra=self.abl_env)
        self.sha, self.root = snapshot(p["frozen_sha"], self.data)
        assert_snapshot_root(self.root, self.data, self.pcfg, self.repo)
        self.gver = _graphify_version()
        # sample integrity: the sample must belong to the tasks file in use; gold-label files are never opened here
        tasks_file = self.data / "datasets" / a.dataset / "tasks.jsonl"
        try:
            ids = load_sample(a.sample, tasks_file)                  # refuses a sample made from other tasks
        except RuntimeError as exc:
            raise JobAbort(str(exc))
        tasks = [json.loads(x) for x in tasks_file.read_text(encoding="utf-8").splitlines()]
        byid = {t["instance_id"]: t for t in tasks}
        self.tasks = [byid[i] for i in ids]
        if a.limit:
            self.tasks = self.tasks[: a.limit]
        # every index: present, DONE, fingerprint
        self.indexes, self.fingerprints = {}, {}
        for t in self.tasks:
            parts = make_key(t["repo"], t["base_commit"], self.sha, self.icfg, self.gver)
            d = index_dir(self.data, parts)
            if not (d / "DONE").is_file():
                raise JobAbort(f"index missing for {t['instance_id']}: {d.name}")
            self.indexes[t["instance_id"]] = d
            self.fingerprints[t["instance_id"]] = original_fingerprint(d)
        # disk
        scratch_root = self.data / rc["scratch_subdir"]
        scratch_root.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(scratch_root).free / 1e9
        if free_gb < rc["min_free_disk_gb"]:
            raise JobAbort(f"only {free_gb:.0f} GB free for scratch copies")
        self.scratch_root = scratch_root
        # Ollama, model, GPU
        try:
            version = self.client.version()
            tags = {m["name"]: m for m in self.client.tags()}
        except OllamaError as exc:
            raise JobAbort(f"Ollama not reachable: {exc}")
        if a.model not in tags:
            raise JobAbort(f"model {a.model} not present in Ollama")
        self.model_digest = tags[a.model].get("digest")
        for m in self.client.ps():                                   # one model resident at a time
            if m.get("name") != a.model:
                self.client.unload(m["name"])
        warm = self.warmup()
        self.gpu = gpu_info()
        env_rec = effective_record(self.pcfg, env_probe)
        cfgs = {n: config_hash(Path(getattr(a, n))) for n in ("experiment", "pipeline_config", "index_config", "run_config",
                                                              "ablations_config")}
        combined = hashlib.sha256(json.dumps({**cfgs, "ablation": self.ablation}, sort_keys=True).encode()).hexdigest()[:16]
        self.header = {
            "schema": SCHEMA, "smoke": a.smoke, "machine_id": self.profile["machine_id"],
            "gpu_name": (self.gpu or {}).get("name"), "gpu_driver": (self.gpu or {}).get("driver"),
            "ollama_version": version, "model": a.model, "model_digest": self.model_digest,
            "config_hashes": cfgs, "config_hash": combined,
            "harness_commit": self.ident["harness_commit"], "harness_clean": self.ident["clean"],
            "pipeline_frozen_sha": p["frozen_sha"], "pipeline_base_commit": p["base_commit"],
            "pipeline_seed_alignment_commit": p["seed_alignment_commit"],
            "ablation": self.ablation, "record_results": p["record_results"], "env": env_rec,
            "python_packages": environment_info(), "num_ctx": self.exp["ollama"]["num_ctx"]}
        self.job_info = {"arm": self.arm, "arm_impl": self.arm_impl, "dataset": a.dataset, "model": a.model,
                         "n_tasks": len(self.tasks), "warmup": warm, "preflight_s": round(time.perf_counter() - t0, 2),
                         "sample": str(a.sample), "sample_sha256": hashlib.sha256(Path(a.sample).read_bytes()).hexdigest()}
        return self.job_info

    def warmup(self) -> dict:
        """Load the model with the run's num_ctx and check it is fully VRAM-resident. Not part of task latency."""
        t = time.perf_counter()
        o = self.exp["ollama"]
        try:
            self.client.chat(self.a.model, [{"role": "user", "content": self.rcfg["run"]["warmup_prompt"]}],
                             {"num_ctx": o["num_ctx"], "num_predict": 8, "temperature": 0.0, "seed": o["seed"]},
                             think=False, keep_alive=o["keep_alive"])
        except OllamaError as exc:
            raise JobAbort(f"warm-up failed: {exc}")
        ps = [m for m in self.client.ps() if m.get("name") == self.a.model]
        split = {"size": ps[0].get("size"), "size_vram": ps[0].get("size_vram")} if ps else None
        if self.rcfg["run"]["require_full_vram"] and (not split or split["size_vram"] != split["size"]):
            raise JobAbort(f"model not fully resident in VRAM: {split}")
        return {"seconds": round(time.perf_counter() - t, 2), "ollama_ps": split}

    # ── results file ─────────────────────────────────────────────────────────
    def results_path(self) -> Path:
        return self.out / "results.jsonl"

    def done_ids(self) -> set[str]:
        p = self.results_path()
        if not p.is_file():
            return set()
        good, keep = set(), []
        raw = p.read_bytes().split(b"\n")
        for i, line in enumerate(raw):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                if i == len(raw) - 1 or all(not x.strip() for x in raw[i + 1:]):     # torn last line from a kill
                    break
                raise JobAbort("results.jsonl is corrupt in the middle")
            good.add(rec["task_id"])
            keep.append(line)
        clean = b"\n".join(keep) + (b"\n" if keep else b"")
        if clean != p.read_bytes():
            p.write_bytes(clean)
        return good

    def append(self, rec: dict) -> None:
        with self.results_path().open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


# ── one task ─────────────────────────────────────────────────────────────────

def kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def pid_alive_with(pid: int, needle: str) -> bool:
    try:
        return needle in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False


def recover_inflight(job: Job) -> None:
    """After a kill: stop an orphaned worker of the previous run and remove its scratch copy."""
    f = job.out / "inflight.json"
    if f.is_file():
        info = json.loads(f.read_text(encoding="utf-8"))
        if pid_alive_with(info["pid"], "task_worker"):
            kill_group(info["pgid"])
        safe_rmtree(Path(info["scratch"]), job.scratch_root)
        f.unlink()


def classify(out: dict | None, returncode: int | None, timed_out: bool, stderr: str) -> tuple[str, str | None]:
    if timed_out:
        return "timeout", "task exceeded the per-task timeout; subprocess killed"
    if out is None:
        if returncode in (-9, 137):
            return "oom", "worker killed by SIGKILL (system OOM killer suspected)"
        return "error", f"worker exited {returncode} without a result: {stderr[-400:]}"
    texts = [f"{c.get('error') or ''} {c.get('error_text') or ''}" for c in out["record"]["ollama_calls"]]
    if any(OOM_RE.search(t) for t in texts) or (out.get("error") and OOM_RE.search(out["error"])):
        return "oom", next((t.strip()[:300] for t in texts if OOM_RE.search(t)), (out.get("error") or "")[-300:])
    if out["status"] == "error":
        return "error", (out.get("error") or "")[-400:]
    if out["record"].get("answer_error"):
        return "error", "Ollama error returned in place of an answer"
    return "ok", None


def build_lists(job: Job, task: dict, raw: dict) -> dict:
    m = Mapper(job.data / "cache" / "checkouts" / task["repo"].replace("/", "__") / task["base_commit"])
    exp = None
    if raw.get("neighbors"):                                  # at least one expansion hop happened
        exp = (raw.get("seeds") or []) + [c for hop in raw["neighbors"] for c in hop]
    final = raw.get("re_pruned_last") or raw.get("pruned")
    def lists(items):
        r = m.rank_lists(items)
        r["unmapped_n"] = len(r["unmapped"])
        r["unmapped"] = r["unmapped"][:20]
        return r
    return {"after_retrieval": lists(raw["ranked_candidates"]), "retrieved_top5": lists(raw["retrieved"]),
            "after_expansion": lists(exp) if exp is not None else None,
            "after_pruning": lists(final) if final else None, "final_context": lists(raw["final_context"])}


def run_task(job: Job, task: dict, force_timeout: bool) -> dict:
    a, rc = job.a, job.rcfg["run"]
    tid = task["instance_id"]
    idx = job.indexes[tid]
    scratch = job.scratch_root / f"{job.job_id}__{tid}"
    overhead: dict[str, Any] = {}
    line = {**job.header, "job_id": job.job_id, "task_id": tid, "dataset": a.dataset, "repo": task["repo"],
            "base_commit": task["base_commit"], "arm": job.arm, "arm_impl": job.arm_impl, "model": a.model,
            "index_dir": str(idx), "query_sha256": hashlib.sha256(task["query"].encode()).hexdigest(), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "forced_timeout": force_timeout}
    proc = None
    try:
        # per-task preflight on a verified scratch copy
        t = time.perf_counter()
        safe_rmtree(scratch, job.scratch_root)
        scratch.mkdir(parents=True)
        shutil.copytree(idx / "chroma", scratch / "chroma")
        shutil.copytree(idx / "graphify-out", scratch / "graphify-out")
        overhead["index_copy_s"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter()
        for src, dst in ((idx / "chroma", scratch / "chroma"), (idx / "graphify-out", scratch / "graphify-out")):
            for f in src.rglob("*"):
                if f.is_file() and not filecmp.cmp(f, dst / f.relative_to(src), shallow=False):
                    raise JobAbort(f"scratch copy differs: {f.name}")
        problems = check_index_files(scratch / "chroma", scratch / "graphify-out")
        if problems:
            raise JobAbort("index preflight: " + "; ".join(problems))
        if sqlite_fingerprint(scratch / "chroma") != job.fingerprints[tid]:
            raise JobAbort("scratch copy fingerprint differs from the cached original")
        overhead["index_verify_s"] = round(time.perf_counter() - t, 3)
    except JobAbort as exc:
        safe_rmtree(scratch, job.scratch_root)
        return {**line, "status": "preflight_failed", "failure": {"type": "preflight_failed", "detail": str(exc)},
                "counted_as_failed": True, "overhead_s": overhead}

    spec = {"pipeline_root": str(job.root), "arm": job.arm, "arm_impl": job.arm_impl, "mode": job.rcfg["arms"][job.arm],
            "prompt": task["query"], "hops": job.rcfg["arms"]["always_graph"]["hops"], "pruning_method": rc["pruning_method"],
            "experiment": str(a.experiment), "forbid": [str(p) for p in forbidden_chroma_paths(job.repo)],
            "ready": str(scratch / "ready.json"), "out": str(scratch / "out.json"),
            "expect_structure_rerank": job.pcfg["pipeline"]["structure_rerank_expected"]}
    (scratch / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    env = build_env(job.pcfg, a.model, job.exp, extra={
        **job.abl_env, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "CHROMA_PATH": str(scratch / "chroma"),
        "GRAPHIFY_OUT": str(scratch / "graphify-out"), **({"HARNESS_OLLAMA_REDIRECT_PORT": os.environ["HARNESS_OLLAMA_REDIRECT_PORT"]}
                                                           if os.environ.get("HARNESS_OLLAMA_REDIRECT_PORT") else {})})
    timeout = 1.0 if force_timeout else rc["task_timeout_s"]
    t_spawn = time.time()
    timed_out, out = False, None
    try:
        proc = subprocess.Popen([sys.executable, "-m", "harness.task_worker", str(scratch / "spec.json")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        (job.out / "inflight.json").write_text(json.dumps({"pid": proc.pid, "pgid": os.getpgid(proc.pid), "task": tid,
                                                           "scratch": str(scratch)}), encoding="utf-8")
        try:
            _stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_group(os.getpgid(proc.pid))
            _stdout, stderr = proc.communicate()
        ready = scratch / "ready.json"
        if ready.is_file():
            overhead["subprocess_startup_s"] = json.loads(ready.read_text())["startup_s"]
            overhead["spawn_to_ready_s"] = round(json.loads(ready.read_text())["t"] - t_spawn, 3)
        if (scratch / "out.json").is_file():
            out = json.loads((scratch / "out.json").read_text(encoding="utf-8"))
        status, detail = classify(out, proc.returncode, timed_out, stderr or "")
    finally:
        (job.out / "inflight.json").unlink(missing_ok=True)
        if proc is not None and proc.poll() is None:
            kill_group(os.getpgid(proc.pid))
        safe_rmtree(scratch, job.scratch_root)                    # scratch copy deleted afterwards
    line.update({"status": status, "failure": {"type": status, "detail": detail} if status != "ok" else None,
                 "overhead_s": overhead, "timeout_s": timeout})
    if out is not None:
        rec = out["record"]
        gate_err, ans_err = rec["gate_error"], rec["answer_error"]
        line.update({
            "route": out["route"], "hops_used": out["hops_used"], "answer": out["answer"],
            "gate_error": gate_err, "answer_error": ans_err,
            "latency_ms": {"task_total": rec["wall_ms"], **rec["stage_ms"]},
            "tokens": {**rec["tokens"], "injected_est_len4": out["tokens_injected_est"]},
            "gate": rec["gate"], "gate_inputs": rec.get("gate_inputs"), "seed_pool": rec["seed_pool"],
            "stub_neighbors": rec.get("stub_neighbors"), "contains_only": rec.get("contains_only"),
            "structure_rerank": rec["structure_rerank"],
            "inject": rec["inject"], "answer_call": rec["answer_call"], "expansions": rec["expansions"],
            "any_truncation_suspected": rec["any_truncation_suspected"], "token_reconciliation": rec["token_reconciliation"],
            "ollama_calls": [{k: v for k, v in c.items()} for c in rec["ollama_calls"]],
            "ranked": build_lists(job, task, out["lists_raw"]) if out["status"] == "ok" else None,
            "pipeline_error": out.get("error")})
        line["counted_as_failed"] = status != "ok" or gate_err or ans_err
        if status == "ok":
            try:
                check_prefix_mismatch_stubs(rec)
            except JobFailure as exc:
                line.update({"status": "error", "failure": {"type": "prefix_mismatch_stub_neighbours", "detail": str(exc)},
                             "counted_as_failed": True})
        if status == "ok" and line["structure_rerank"]["expected"] and not line["structure_rerank"]["applied"]:
            line.update({"status": "error", "failure": {"type": "structure_rerank_not_applied", "detail": json.dumps(line["structure_rerank"])},
                         "counted_as_failed": True})
    else:
        line["counted_as_failed"] = True
    try:
        ps = [m for m in job.client.ps() if m.get("name") == a.model]
        line["ollama_ps"] = {"size": ps[0].get("size"), "size_vram": ps[0].get("size_vram")} if ps else None
    except OllamaError:
        line["ollama_ps"] = None
    return line


# ── main loop ────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--sample", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--experiment", type=Path, required=True)
    ap.add_argument("--pipeline-config", type=Path, required=True)
    ap.add_argument("--index-config", type=Path, required=True)
    ap.add_argument("--run-config", type=Path, required=True)
    ap.add_argument("--ablation", default="full", help="Table VIII setting from configs/ablations.toml (GAPA only)")
    ap.add_argument("--ablations-config", type=Path, default=Path(__file__).resolve().parents[2] / "configs" / "ablations.toml")
    ap.add_argument("--smoke", action="store_true", help="not a result run: allowed while record_results is false")
    ap.add_argument("--allow-dirty", action="store_true", help="development only")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--inject-timeout", action="append", default=[], help="smoke test: force a timeout for this task id")
    a = ap.parse_args()
    for n in ("profile", "sample", "out", "experiment", "pipeline_config", "index_config", "run_config", "ablations_config"):
        setattr(a, n, getattr(a, n).resolve())             # the worker changes directory; every path must be absolute
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        job = Job(a)
    except (JobAbort, EnvError) as exc:
        print(f"[FAIL] {exc}")
        return 2
    lock = a.out / ".lock"
    if lock.is_file():
        old = int(lock.read_text() or 0)
        if old and pid_alive_with(old, "harness.run"):
            print(f"[FAIL] another run (pid {old}) is using {a.out}")
            return 2
    lock.write_text(str(os.getpid()))
    try:
        info = job.preflight()
    except (JobAbort, EnvError) as exc:
        print(f"[FAIL] preflight: {exc}")
        lock.unlink(missing_ok=True)
        return 2
    jf = a.out / "job.json"
    ident = {"arm": job.arm, "model": a.model, "dataset": a.dataset, "ablation": a.ablation, "config_hash": job.header["config_hash"],
             "pipeline_frozen_sha": job.header["pipeline_frozen_sha"], "sample_sha256": info["sample_sha256"]}
    if jf.is_file():
        prev = json.loads(jf.read_text(encoding="utf-8"))
        if prev["identity"] != ident:
            print(f"[FAIL] {a.out} holds a job with different settings; refusing to resume into it")
            lock.unlink(missing_ok=True)
            return 2
        job.job_id = prev["job_id"]
    else:
        abl = "" if a.ablation == "full" else f"__{a.ablation}"
        job.job_id = f"{job.arm}{abl}__{a.model.replace(':', '-').replace('/', '-')}__{a.dataset}__{job.sha[:7]}__{time.strftime('%Y%m%dT%H%M%S')}"
    jf.write_text(json.dumps({"job_id": job.job_id, "identity": ident, "info": info, "header": job.header}, indent=1, default=str), encoding="utf-8")
    recover_inflight(job)
    done = job.done_ids()
    todo = [t for t in job.tasks if t["instance_id"] not in done]
    print(f"[job {job.job_id}] {len(done)} done, {len(todo)} to run; warm-up {info['warmup']['seconds']}s "
          f"ps={info['warmup']['ollama_ps']}", flush=True)
    consecutive = 0
    try:
        for i, task in enumerate(todo, 1):
            try:
                line = run_task(job, task, task["instance_id"] in a.inject_timeout)
            except Exception:                                   # a harness bug must cost one task, not the whole job
                line = {**job.header, "job_id": job.job_id, "task_id": task["instance_id"], "dataset": a.dataset,
                        "repo": task["repo"], "base_commit": task["base_commit"], "arm": job.arm, "arm_impl": job.arm_impl,
                        "model": a.model, "status": "error", "counted_as_failed": True, "overhead_s": {},
                        "failure": {"type": "harness_exception", "detail": traceback.format_exc()[-1500:]}}
                (job.out / "inflight.json").unlink(missing_ok=True)
            job.append(line)
            lat = (line.get("latency_ms") or {}).get("task_total")
            print(f"[{len(done) + i}/{len(job.tasks)}] {task['instance_id']} {line['status']} route={line.get('route')} "
                  f"latency_ms={lat} startup_s={line['overhead_s'].get('subprocess_startup_s')} "
                  f"copy_s={line['overhead_s'].get('index_copy_s')}", flush=True)
            if (line.get("failure") or {}).get("type") == "prefix_mismatch_stub_neighbours":
                print(f"[ABORT] {line['failure']['detail'][:300]}")
                return 3
            consecutive = consecutive + 1 if line["status"] in ("timeout", "oom", "error", "preflight_failed") else 0
            if line["status"] in ("timeout", "oom"):                       # a stuck/overloaded runner: reset and re-warm
                try:
                    job.client.unload(a.model)
                    time.sleep(3)
                    job.warmup()
                except (OllamaError, JobAbort) as exc:
                    print(f"[ABORT] could not recover Ollama after {line['status']}: {exc}")
                    return 3
            if consecutive >= job.rcfg["run"]["max_consecutive_failures"]:
                print(f"[ABORT] {consecutive} consecutive failed tasks")
                return 3
    finally:
        lock.unlink(missing_ok=True)
    rows = [json.loads(x) for x in job.results_path().read_text(encoding="utf-8").splitlines() if x.strip()]
    summary = {"job_id": job.job_id, "tasks": len(job.tasks), "lines": len(rows),
               "status_counts": {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})},
               "counted_as_failed": sum(1 for r in rows if r["counted_as_failed"])}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
