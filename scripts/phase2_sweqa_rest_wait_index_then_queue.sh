#!/usr/bin/env bash
# Wait until swe-qa-rest indexes exist with DONE, then launch the 7-arm queue.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH=eval_harness/src
unset GAPA_COMPACT_PROMPT GAPA_NEED GAPA_GRAPHMETA GAPA_META INDEX_CONTEXT_OLLAMA \
      GASR_OFFLINE RERANKER_ENABLED PREROUTE_DEBUG OLLAMA_SKIP
LOG="$ROOT/docs/phase2_runs/sweqa_rest_queue.log"
STATUS="$ROOT/docs/phase2_runs/sweqa_rest_progress.txt"
IDX="$ROOT/eval_harness/data/cache/indexes"

count_done() {
  local n=0
  local d
  for d in \
    "$IDX"/astropy__astropy__* \
    "$IDX"/conan-io__conan__* \
    "$IDX"/django__django__* \
    "$IDX"/matplotlib__matplotlib__* \
    "$IDX"/pylint-dev__pylint__* \
    "$IDX"/reflex-dev__reflex__* \
    "$IDX"/scikit-learn__scikit-learn__* \
    "$IDX"/sphinx-doc__sphinx__* \
    "$IDX"/sqlfluff__sqlfluff__* \
    "$IDX"/streamlink__streamlink__* \
    "$IDX"/sympy__sympy__* \
    "$IDX"/pydata__xarray__*
  do
    [ -d "$d" ] || continue
    case "$d" in *.partial*) continue ;; esac
    if [ -f "$d/DONE" ]; then
      n=$((n + 1))
    fi
  done
  echo "$n"
}

echo "$(date -Is) waiting for 12 rest indexes..." | tee -a "$STATUS"
while true; do
  done_n=$(count_done)
  echo "$(date -Is) indexes DONE=$done_n / 12" | tee -a "$STATUS"
  if [ "$done_n" -ge 12 ]; then
    break
  fi
  if ! pgrep -f 'harness.index_cache build.*swe-qa-rest' >/dev/null \
     && ! pgrep -f 'harness.index_worker' >/dev/null; then
    echo "$(date -Is) index processes exited early DONE=$done_n" | tee -a "$STATUS"
    exit 2
  fi
  sleep 60
done

echo "$(date -Is) launching queue phase2-sweqa-rest.toml" | tee -a "$STATUS"
curl -sf http://127.0.0.1:11434/api/tags >/dev/null || { sudo systemctl start ollama || true; sleep 3; }

nohup python3 -m harness.queue \
  --profile eval_harness/profiles/ubuntu-4080.toml \
  --queue eval_harness/queues/phase2-sweqa-rest.toml \
  --pipeline-config eval_harness/configs/pipeline_phase2.toml \
  --configs eval_harness/configs \
  --cwd . \
  >> "$LOG" 2>&1 &
echo $! > "$ROOT/docs/phase2_runs/sweqa_rest_queue.pid"
echo "$(date -Is) queue pid=$(cat "$ROOT/docs/phase2_runs/sweqa_rest_queue.pid")" | tee -a "$STATUS"

# Chain: when generation finishes, export + judge
nohup bash "$ROOT/scripts/phase2_sweqa_rest_wait_queue_then_judge.sh" \
  >> "$ROOT/docs/phase2_runs/sweqa_rest_judge_watcher.log" 2>&1 &
echo $! > "$ROOT/docs/phase2_runs/sweqa_rest_judge_watcher.pid"
echo "$(date -Is) judge-watcher pid=$(cat "$ROOT/docs/phase2_runs/sweqa_rest_judge_watcher.pid")" | tee -a "$STATUS"
