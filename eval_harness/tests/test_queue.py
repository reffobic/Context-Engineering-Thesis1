"""Queue runner: status per job, resume, hard stop, deadline fit, stop markers, heartbeat, ntfy."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest import HARNESS
from harness import queue as Q

STUB = """
import json, sys, time, pathlib
a = sys.argv
out = pathlib.Path(a[a.index('--out') + 1]); out.mkdir(parents=True, exist_ok=True)
mode = {MODE!r}
n = {N}
(out / 'results.jsonl').write_text(''.join(json.dumps({{'task_id': str(i), 'status': 'ok', 'counted_as_failed': False, 'stub_neighbors': {STUBS}}}) + '\\n' for i in range(n)))
if mode == 'sleep':
    time.sleep(120)
sys.exit({RC})
"""


def make(tmp_path, jobs, hard_stop, mode="ok", rc=0, stubs=None, topic_env=None):
    data = tmp_path / "data"
    prof = tmp_path / "profile.toml"
    prof.write_text(f'machine_id = "t"\ndata_dir = "{data.as_posix()}"\nqueue_file = "q"\n' + (f'ntfy_topic_env = "{topic_env}"\n' if topic_env else ""), encoding="utf-8")
    qf = tmp_path / "queue.toml"
    body = f'[queue]\nenabled = true\nfrozen_sha = "{Q.load_toml(HARNESS / "configs" / "pipeline.toml")["pipeline"]["frozen_sha"]}"\nhard_stop = "{hard_stop}"\nhard_stop_tz = "UTC"\nsafety_factor = 1.0\n'
    for j in jobs:
        body += f'\n[[job]]\nid = "{j[0]}"\nmodel = "m"\narm = "GAPA"\ndataset = "d"\ntasks = {j[1]}\nestimated_minutes = {j[2]}\n'
    qf.write_text(body, encoding="utf-8")
    stub = tmp_path / "stub.py"
    stub.write_text(STUB.format(MODE=mode, N=jobs[0][1], RC=rc, STUBS=json.dumps(stubs or {"stub_chunks": 0})), encoding="utf-8")
    a = argparse.Namespace(profile=prof, queue=qf, configs=HARNESS / "configs", pipeline_config=HARNESS / "configs" / "pipeline.toml",
                           heartbeat_s=0.2, cwd=tmp_path, dry_run=False)
    q = Q.Queue(a)
    q.cmd = lambda job: [sys.executable, str(stub), "--out", str(q.job_out(job))]
    return q


def future(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def statuses(q):
    return {j["id"]: q.get_status(j)["state"] for j in q.q["job"]}


def test_jobs_run_in_order_status_heartbeat_and_resume(tmp_path):
    q = make(tmp_path, [("a", 3, 1), ("b", 3, 1)], future(60))
    assert q.main() == 0
    assert statuses(q) == {"a": "complete", "b": "complete"}
    hb = json.loads((q.qdir / "heartbeat.json").read_text())
    assert hb["state"] == "finished" and hb["pid"] and "t_local" in hb and "hard_stop_utc" in hb
    ev = [json.loads(x)["event"] for x in (q.qdir / "events.jsonl").read_text().splitlines()]
    assert ev.count("job_start") == 2 and ev[-1] == "queue_end"
    n_before = len(ev)
    q2 = make(tmp_path, [("a", 3, 1), ("b", 3, 1)], future(60))                  # restart: finished jobs are skipped
    assert q2.main() == 0
    ev2 = [json.loads(x)["event"] for x in (q.qdir / "events.jsonl").read_text().splitlines()][n_before:]
    assert "job_start" not in ev2


def test_job_that_cannot_finish_before_the_hard_stop_is_not_started(tmp_path):
    q = make(tmp_path, [("big", 3, 60), ("small", 3, 1)], future(10))
    assert q.main() == 0
    assert statuses(q) == {"big": "not_started_deadline", "small": "complete"}


def test_hard_stop_kills_a_running_job_and_marks_it_incomplete(tmp_path):
    q = make(tmp_path, [("slow", 3, 0.01), ("after", 3, 0.01)], (datetime.now(timezone.utc) + timedelta(seconds=4)).strftime("%Y-%m-%dT%H:%M:%S"), mode="sleep")
    assert q.main() == 0
    st = statuses(q)
    assert st["slow"] == "incomplete" and st["after"] == "pending"
    assert q.get_status(q.q["job"][0])["reason"] == "hard stop"


def test_five_consecutive_failures_stop_the_queue_until_the_marker_is_removed(tmp_path):
    q = make(tmp_path, [("bad", 3, 1), ("next", 3, 1)], future(60), rc=3)
    assert q.main() == 0
    assert statuses(q)["bad"] == "incomplete" and statuses(q)["next"] == "pending" and q.stop_marker().is_file()
    q2 = make(tmp_path, [("bad", 3, 1), ("next", 3, 1)], future(60))
    assert q2.main() == 0 and statuses(q2)["next"] == "pending"                  # STOPPED marker: nothing runs
    q.stop_marker().unlink()
    assert make(tmp_path, [("bad", 3, 1), ("next", 3, 1)], future(60)).main() == 0


def test_prefix_mismatch_stubs_stop_the_queue_after_the_job(tmp_path):
    stubs = {"stub_chunks": 2, "neighbor_chunks": 30, "by_category": {"prefix_mismatch": 2, "unindexed_file_type": 0, "other": 0}}
    q = make(tmp_path, [("a", 3, 1), ("b", 3, 1)], future(60), stubs=stubs)
    assert q.main() == 0
    assert statuses(q) == {"a": "complete", "b": "pending"} and "prefix-mismatch" in q.stop_marker().read_text()


def test_rare_non_prefix_stubs_do_not_stop_the_queue_but_many_do():
    few = {"stub_chunks": 1, "neighbor_chunks": 300, "by_category": {"prefix_mismatch": 0, "unindexed_file_type": 0, "other": 1}}
    many = {"stub_chunks": 30, "neighbor_chunks": 300, "by_category": {"prefix_mismatch": 0, "unindexed_file_type": 30, "other": 0}}
    ok = {"counted_as_failed": False}
    assert Q.abnormalities([{**ok, "stub_neighbors": few}], 1) == []
    assert any("non-prefix" in x for x in Q.abnormalities([{**ok, "stub_neighbors": many}], 1))


def test_abnormalities_rules():
    ok = {"counted_as_failed": False}
    assert Q.abnormalities([ok] * 10, 10) == []
    assert any("failed" in x for x in Q.abnormalities([{"counted_as_failed": True}] * 3 + [ok] * 7, 10))
    assert any("truncation" in x for x in Q.abnormalities([{**ok, "any_truncation_suspected": True}], 1))
    assert any("prefix" in x for x in Q.abnormalities([{**ok, "failure": {"type": "prefix_mismatch_stub_neighbours"}}], 1))
    assert Q.abnormalities([{"counted_as_failed": True}] * 2 + [ok] * 8, 10) == []          # 20% is not above 20%


def test_ntfy_topic_comes_from_the_named_variable_and_is_never_logged(tmp_path, monkeypatch):
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.path, self.headers.get("Title"), self.rfile.read(int(self.headers["Content-Length"])).decode()))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("TEST_NTFY_TOPIC", "secret-topic-123")
    q = make(tmp_path, [("a", 3, 1)], future(60), topic_env="TEST_NTFY_TOPIC")
    q.notify.server = f"http://127.0.0.1:{srv.server_address[1]}"
    assert q.main() == 0
    srv.shutdown()
    assert got and all(p == "/secret-topic-123" for p, _, _ in got) and any("job complete" in t for _, t, _ in got)
    assert "secret-topic-123" not in (q.qdir / "events.jsonl").read_text() and "secret-topic-123" not in json.dumps(statuses(q))


def test_notifier_without_the_variable_is_silent(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_NTFY_TOPIC", raising=False)
    assert Q.Notifier("TEST_NTFY_TOPIC").send("t", "m") is False
