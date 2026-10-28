"""Queue runner: one job at a time from a versioned queue file, reboot-safe, with a hard stop.

  python -m harness.queue --profile P --queue Q [--dry-run]

* state per job in <out>/queue_status.json (complete / incomplete / not_started_deadline); finished jobs are
  skipped after a restart, unfinished ones resume (harness.run resumes inside its output folder);
* heartbeat file <data>/queue/heartbeat.json every few seconds; event log <data>/queue/events.jsonl;
* ntfy: the topic is read from the environment variable NAMED in the profile (ntfy_topic_env); it is never
  logged. Without the variable the notification is skipped;
* hard stop (queue file: hard_stop, hard_stop_tz): a job whose estimate (x safety) does not fit before it is
  not started; a running job is stopped at the hard stop and marked incomplete;
* stops (marker <data>/queue/STOPPED, exit 0) after a job fails with 5 consecutive failed tasks or looks
  abnormal; delete the marker to continue;
* the job's code and commit are those of the tree this module runs from (run it from a clean worktree).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import load_toml
from .run import kill_group, pid_alive_with, safe_rmtree

REPO = Path(__file__).resolve().parents[3]
ABNORMAL_FAILED_SHARE = 0.20
STUB_SHARE_LIMIT = 0.05


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hard_stop_dt(q: dict) -> datetime:
    tz = ZoneInfo(q["queue"].get("hard_stop_tz", "UTC"))
    return datetime.fromisoformat(q["queue"]["hard_stop"]).replace(tzinfo=tz).astimezone(timezone.utc)


def read_results(out: Path) -> list[dict]:
    p = out / "results.jsonl"
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def abnormalities(rows: list[dict], n_tasks: int) -> list[str]:
    """Reasons a finished job should stop the queue (checked after every job)."""
    out = []
    sn = [r.get("stub_neighbors") or {} for r in rows]
    pm = sum((s.get("by_category") or {}).get("prefix_mismatch", 0) for s in sn)
    if pm:
        out.append(f"prefix-mismatch stub neighbours above zero ({pm})")
    other = sum(s.get("stub_chunks", 0) for s in sn) - pm
    chunks = sum(s.get("neighbor_chunks", 0) for s in sn)
    if chunks and other / chunks > STUB_SHARE_LIMIT:            # unresolved nodes without a source file / unindexed file types are expected, but rare
        out.append(f"unresolved neighbours (non-prefix) are {other / chunks:.1%} of neighbour chunks (> {STUB_SHARE_LIMIT:.0%})")
    if any((r.get("failure") or {}).get("type") == "prefix_mismatch_stub_neighbours" for r in rows):
        out.append("prefix-mismatch failures")
    if any(r.get("any_truncation_suspected") for r in rows):
        out.append("possible context truncation")
    failed = sum(1 for r in rows if r.get("counted_as_failed"))
    if rows and failed / max(1, n_tasks) > ABNORMAL_FAILED_SHARE:
        out.append(f"{failed}/{n_tasks} failed tasks (> {int(ABNORMAL_FAILED_SHARE * 100)}%)")
    return out


class Notifier:
    def __init__(self, env_name: str | None, server: str = "https://ntfy.sh") -> None:
        self.topic = os.environ.get(env_name or "", "") if env_name else ""
        self.server = server.rstrip("/")

    def send(self, title: str, message: str) -> bool:
        if not self.topic:
            return False
        try:
            req = urllib.request.Request(f"{self.server}/{self.topic}", data=message.encode("utf-8"),
                                         headers={"Title": title}, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            return True
        except Exception:                                    # a failed notification must never stop the queue
            return False


class Queue:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.profile = load_toml(a.profile)
        self.q = load_toml(a.queue)
        self.data = Path(self.profile["data_dir"]).resolve()
        self.qdir = self.data / "queue"
        self.qdir.mkdir(parents=True, exist_ok=True)
        self.hard_stop = hard_stop_dt(self.q)
        self.safety = float(self.q["queue"].get("safety_factor", 1.25))
        self.notify = Notifier(self.profile.get("ntfy_topic_env"), self.profile.get("ntfy_server", "https://ntfy.sh"))
        self.state = {"state": "starting", "current_job": None, "tasks_done": 0, "tasks_total": 0}
        self._stop_hb = threading.Event()
        self.pcfg = load_toml(a.pipeline_config)

    # ── files ────────────────────────────────────────────────────────────────
    def event(self, kind: str, **kw) -> None:
        rec = {"t_utc": now_utc().isoformat(timespec="seconds"), "event": kind, **kw}
        with (self.qdir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec) + "\n")
        print(json.dumps(rec), flush=True)

    def heartbeat(self) -> None:
        tz = ZoneInfo(self.q["queue"].get("hard_stop_tz", "UTC"))
        hb = {"t_utc": now_utc().isoformat(timespec="seconds"), "t_local": now_utc().astimezone(tz).isoformat(timespec="seconds"),
              "pid": os.getpid(), "hard_stop_utc": self.hard_stop.isoformat(timespec="seconds"), **self.state}
        tmp = self.qdir / "heartbeat.json.tmp"
        tmp.write_text(json.dumps(hb), encoding="utf-8")
        os.replace(tmp, self.qdir / "heartbeat.json")

    def _hb_loop(self) -> None:
        while not self._stop_hb.wait(self.a.heartbeat_s):
            out = self.state.get("current_out")
            if out:
                self.state["tasks_done"] = len(read_results(Path(out)))
            self.heartbeat()

    def job_out(self, job: dict) -> Path:
        return self.data / "runs" / self.pcfg["pipeline"]["frozen_sha"][:7] / job["id"]

    def status_path(self, job: dict) -> Path:
        return self.job_out(job) / "queue_status.json"

    def get_status(self, job: dict) -> dict:
        p = self.status_path(job)
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"state": "pending"}

    def set_status(self, job: dict, **kw) -> None:
        self.job_out(job).mkdir(parents=True, exist_ok=True)
        st = {**self.get_status(job), **kw}
        self.status_path(job).write_text(json.dumps(st, indent=1), encoding="utf-8")

    # ── one job ──────────────────────────────────────────────────────────────
    def cmd(self, job: dict) -> list[str]:
        c = self.a.configs
        cmd = [sys.executable, "-m", "harness.run", "--profile", str(self.a.profile), "--dataset", job["dataset"], "--model", job["model"],
               "--arm", job["arm"], "--sample", str(self.data / "samples" / f"{job['dataset']}.sample.json"),
               "--out", str(self.job_out(job)), "--experiment", str(c / "experiment.toml"), "--pipeline-config", str(self.a.pipeline_config),
               "--index-config", str(c / "index.toml"), "--run-config", str(c / "run.toml"), "--ablations-config", str(c / "ablations.toml")]
        if job.get("ablation"):
            cmd += ["--ablation", job["ablation"]]
        if job.get("answer_locations_instruction"):                  # opt-in, default off (extras.toml)
            cmd.append("--answer-locations-instruction")
        for s in job.get("answer_stop") or []:
            cmd += ["--answer-stop", s]
        return cmd

    def remaining_minutes(self, job: dict) -> float:
        done = len(read_results(self.job_out(job)))
        return job["estimated_minutes"] * max(0.0, 1 - done / job["tasks"]) if done < job["tasks"] else 0.0

    def run_job(self, job: dict) -> int:
        out = self.job_out(job)
        out.mkdir(parents=True, exist_ok=True)
        self.set_status(job, state="running", started_utc=now_utc().isoformat(timespec="seconds"))
        self.state.update(state="running", current_job=job["id"], current_out=str(out), tasks_total=job["tasks"])
        self.event("job_start", job=job["id"], tasks=job["tasks"], estimated_minutes=job["estimated_minutes"])
        self.notify.send(f"job started: {job['id']}", f"{job['tasks']} tasks")
        if self.a.dry_run:
            return 0
        log = (out / "run.log").open("a", encoding="utf-8")
        p = subprocess.Popen(self.cmd(job), stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=self.a.cwd)
        while True:
            try:
                return p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if now_utc() >= self.hard_stop:
                self.event("hard_stop_reached", job=job["id"])
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    p.wait()
                self.cleanup_inflight(out)
                return -15

    def cleanup_inflight(self, out: Path) -> None:
        f = out / "inflight.json"
        if f.is_file():
            info = json.loads(f.read_text(encoding="utf-8"))
            if pid_alive_with(info["pid"], "task_worker"):
                kill_group(info["pgid"])
            safe_rmtree(Path(info["scratch"]), self.data / "cache" / "scratch")
            f.unlink(missing_ok=True)
        (out / ".lock").unlink(missing_ok=True)

    # ── queue ────────────────────────────────────────────────────────────────
    def stop_marker(self) -> Path:
        return self.qdir / "STOPPED"

    def main(self) -> int:
        lock = self.qdir / "queue.lock"
        if lock.is_file() and pid_alive_with(int(lock.read_text() or 0), "harness.queue"):
            print("[FAIL] another queue runner is active")
            return 2
        if self.stop_marker().is_file():
            print(f"[STOPPED] {self.stop_marker().read_text(encoding='utf-8')[:300]} (delete {self.stop_marker()} to continue)")
            return 0
        if not self.q["queue"].get("enabled", False):
            print("[FAIL] queue is not enabled")
            return 2
        if self.pcfg["pipeline"]["frozen_sha"] != self.q["queue"]["frozen_sha"]:
            print("[FAIL] queue file and pipeline.toml name different frozen SHAs")
            return 2
        lock.write_text(str(os.getpid()))
        threading.Thread(target=self._hb_loop, daemon=True).start()
        self.heartbeat()
        self.event("queue_start", jobs=len(self.q["job"]), hard_stop=self.hard_stop.isoformat(), harness_commit=_head())
        rc = 0
        try:
            for job in self.q["job"]:
                st = self.get_status(job)
                if st["state"] == "complete":
                    continue
                need = self.remaining_minutes(job) * self.safety
                eta = now_utc().timestamp() + need * 60
                if eta > self.hard_stop.timestamp():
                    self.set_status(job, state="not_started_deadline" if st["state"] == "pending" else "incomplete",
                                    reason=f"estimate {need:.0f} min does not fit before the hard stop")
                    self.event("skip_deadline", job=job["id"], needed_minutes=round(need, 1))
                    continue
                code = self.run_job(job)
                rows = read_results(self.job_out(job))
                if code == 0 and len(rows) >= job["tasks"]:
                    ab = abnormalities(rows, job["tasks"])
                    self.set_status(job, state="complete", finished_utc=now_utc().isoformat(timespec="seconds"), abnormalities=ab)
                    self.event("job_complete", job=job["id"], lines=len(rows), abnormalities=ab)
                    self.notify.send(f"job complete: {job['id']}", f"{len(rows)} tasks" + (f"; ABNORMAL: {ab}" if ab else ""))
                    if ab:
                        self.stop(f"abnormal result in {job['id']}: {ab}")
                        break
                    if job.get("hold_after"):                        # checkpoint: stop cleanly, a person resumes (delete the marker, rerun)
                        self.stop(f"hold after {job['id']}: {job['hold_after']}")
                        break
                    continue
                reason = {-15: "hard stop", 3: "5 consecutive failed tasks / unrecoverable", 2: "preflight failed"}.get(code, f"exit code {code}")
                self.set_status(job, state="incomplete", reason=reason, finished_utc=now_utc().isoformat(timespec="seconds"), lines=len(rows))
                self.event("job_incomplete", job=job["id"], reason=reason, lines=len(rows))
                self.notify.send(f"job INCOMPLETE: {job['id']}", reason)
                if code == -15:
                    break
                if code in (3, 2):
                    self.stop(f"{job['id']}: {reason}")
                    break
            self.state.update(state="finished", current_job=None, current_out=None)
        except BaseException as exc:                          # crash: systemd restarts the runner and finished jobs are skipped
            self.event("queue_crash", error=repr(exc)[:300])
            rc = 1
        finally:
            self.heartbeat()
            self._stop_hb.set()
            lock.unlink(missing_ok=True)
        if rc == 0:
            self.event("queue_end", statuses={j["id"]: self.get_status(j)["state"] for j in self.q["job"]})
            self.notify.send("queue ended", json.dumps({j["id"][:2]: self.get_status(j)["state"][:4] for j in self.q["job"]}))
        return rc

    def stop(self, why: str) -> None:
        self.stop_marker().write_text(why, encoding="utf-8")
        self.event("queue_stopped", why=why)
        self.notify.send("queue STOPPED", why)


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--configs", type=Path, default=REPO / "eval_harness" / "configs")
    ap.add_argument("--pipeline-config", type=Path, default=None)
    ap.add_argument("--heartbeat-s", type=float, default=15)
    ap.add_argument("--cwd", type=Path, default=Path.cwd(), help="working directory of the jobs (the profile's relative data_dir resolves against it)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.profile, a.queue, a.configs, a.cwd = a.profile.resolve(), a.queue.resolve(), a.configs.resolve(), a.cwd.resolve()
    a.pipeline_config = (a.pipeline_config or a.configs / "pipeline.toml").resolve()
    os.chdir(a.cwd)
    return Queue(a).main()


if __name__ == "__main__":
    raise SystemExit(main())
