"""Write and enable a systemd --user unit that runs wrappers/run_queue.sh from a worktree (restarts after a crash or reboot).

  python -m harness.install_service --worktree W [--env-file F] [--no-enable]

The unit lives in ~/.config/systemd/user (outside the repository; it contains this machine's paths). The ntfy topic
is NOT written into it: put `EVAL_NTFY_TOPIC=...` into an environment file readable only by you and pass --env-file.
For a start at boot without a login session, run once: loginctl enable-linger $USER
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

UNIT = """[Unit]
Description=Evaluation queue (%(wt)s)
After=network-online.target ollama.service

[Service]
Type=simple
ExecStart=%(wt)s/eval_harness/wrappers/run_queue.sh
Restart=on-failure
RestartSec=30
%(envline)s
[Install]
WantedBy=default.target
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worktree", type=Path, required=True)
    ap.add_argument("--env-file", type=Path, default=None)
    ap.add_argument("--name", default="eval-queue")
    ap.add_argument("--no-enable", action="store_true")
    a = ap.parse_args()
    unit = Path.home() / ".config" / "systemd" / "user" / f"{a.name}.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(UNIT % {"wt": a.worktree.resolve(), "envline": f"EnvironmentFile={a.env_file.resolve()}\n" if a.env_file else ""},
                    encoding="utf-8")
    print(f"wrote {unit}")
    if not a.no_enable:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{a.name}.service"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
