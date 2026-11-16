#!/usr/bin/env bash
# Quick status for SWE-QA rest generation + indexes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNS="$ROOT/eval_harness/data/runs/a2cfcec"
IDX="$ROOT/eval_harness/data/cache/indexes"
echo "=== indexes (DONE markers for rest repos) ==="
for pat in astropy conan django matplotlib pylint reflex scikit-learn sphinx sqlfluff streamlink sympy xarray; do
  n=$(find "$IDX" -maxdepth 1 -type d -iname "*${pat}*" 2>/dev/null | wc -l)
  done_n=$(find "$IDX" -maxdepth 1 -type d -iname "*${pat}*" -exec test -f '{}/DONE' \; -print 2>/dev/null | wc -l)
  echo "  $pat: dirs=$n DONE=$done_n"
done
echo "=== rest job progress ==="
shopt -s nullglob
for d in "$RUNS"/p2-sweqa-*-rest; do
  [ -d "$d" ] || continue
  if [ -f "$d/results.jsonl" ]; then
    n=$(wc -l < "$d/results.jsonl" | tr -d ' ')
  else
    n=0
  fi
  echo "  $(basename "$d"): $n / 576"
done
echo "=== queue STOPPED? ==="
if [ -f "$ROOT/eval_harness/data/queue/STOPPED" ]; then
  echo "  STOPPED: $(cat "$ROOT/eval_harness/data/queue/STOPPED")"
else
  echo "  (no STOPPED marker)"
fi
echo "=== live processes ==="
pgrep -af 'harness.queue|harness.run' | head -5 || echo "  (none)"
