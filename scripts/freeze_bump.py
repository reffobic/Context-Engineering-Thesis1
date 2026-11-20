"""Bump the Phase 2 freeze in pipeline_phase2.toml to a new pipeline commit.

Moves the current frozen_sha to previous_frozen_sha (and the old previous into earlier_frozen_shas),
revokes the old frozen SHA for runs, and sets frozen_sha and record_results_pipeline_sha to the new SHA.
Edits only those lines, checks each edit landed exactly once, and prints the resulting values.
Usage (from the repo root, tracked files clean):  python3 scripts/freeze_bump.py [--new-sha <40-hex>]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

TOML = Path("eval_harness/configs/pipeline_phase2.toml")


def git(*a: str) -> str:
    """Run a git command and return its stripped output."""
    return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()


def one(pattern: str, text: str) -> re.Match:
    """Return the single regex match for pattern in text, or stop the script."""
    m = list(re.finditer(pattern, text, re.M))
    if len(m) != 1:
        sys.exit(f"expected exactly one match for {pattern!r}, found {len(m)}")
    return m[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new-sha", default=None, help="default: git HEAD")
    ap.add_argument("--toml", type=Path, default=TOML)
    a = ap.parse_args()
    new = a.new_sha or git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", new):
        sys.exit("new SHA must be 40 hex characters")
    if a.new_sha is None and git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("tracked files have uncommitted changes; commit them first (the freeze SHA is HEAD)")
    s = a.toml.read_text(encoding="utf-8")
    old = one(r'^frozen_sha = "([0-9a-f]{40})"', s).group(1)
    prev = one(r'^previous_frozen_sha = "([0-9a-f]{40})"', s).group(1)
    if new == old:
        sys.exit("new SHA equals the current frozen_sha; nothing to do")
    # earlier_frozen_shas: append the old previous
    m = one(r'^earlier_frozen_shas = \[(.*)\]', s)
    s = s[:m.start(1)] + m.group(1) + f', "{prev}"' + s[m.end(1):]
    # previous_frozen_sha <- old frozen
    m = one(r'^previous_frozen_sha = "[0-9a-f]{40}"[^\n]*', s)
    s = s[:m.start()] + f'previous_frozen_sha = "{old}"   # Phase 2 Wave 1 freeze (local qwen runs)' + s[m.end():]
    # frozen_sha and record_results_pipeline_sha <- new
    m = one(r'^frozen_sha = "[0-9a-f]{40}"', s)
    s = s[:m.start()] + f'frozen_sha = "{new}"' + s[m.end():]
    m = one(r'^record_results_pipeline_sha = "[0-9a-f]{40}"', s)
    s = s[:m.start()] + f'record_results_pipeline_sha = "{new}"' + s[m.end():]
    # revoke the old frozen SHA for runs
    m = one(r'^revoked_pipeline_shas = \[(.*)\]', s)
    s = s[:m.start(1)] + m.group(1) + f', "{old}"' + s[m.end(1):]
    a.toml.write_text(s, encoding="utf-8", newline="\n")
    print(f"frozen_sha      {old[:10]} -> {new[:10]}")
    print(f"previous        {prev[:10]} -> {old[:10]}")
    print(f"revoked_pipeline_shas now ends with {old[:10]}")


if __name__ == "__main__":
    main()