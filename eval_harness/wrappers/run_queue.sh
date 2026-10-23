#!/usr/bin/env bash
# Start (or resume) the queue from the worktree this script lives in. Safe to run repeatedly and after a reboot:
# finished jobs are skipped, an unfinished job resumes, and the runner exits at the hard stop.
# Jobs run with the main tree as working directory (that is where the gitignored data lives); the code is the worktree's.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$HERE/../.." && pwd)"
MAIN="$(cd "$(git -C "$WT" rev-parse --git-common-dir)/.." && pwd)"
PY="${EVAL_PYTHON:-$MAIN/.venv/bin/python}"
cd "$MAIN"
# a systemd user service has a minimal PATH; the graphify CLI version is part of every index key
export PATH="$HOME/.local/bin:$MAIN/.venv/bin:$PATH"
export PYTHONPATH="$WT/eval_harness/src"
exec "$PY" -m harness.queue --profile "$WT/eval_harness/profiles/ubuntu-4080.toml" \
  --queue "$WT/eval_harness/queues/ubuntu-4080.toml" --configs "$WT/eval_harness/configs" --cwd "$MAIN" "$@"
