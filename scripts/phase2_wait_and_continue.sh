#!/usr/bin/env bash
# Wait for Phase 2 SWE-QA Wave 1 queue to finish, then run CP5→CP7 (+ kick CP8).
set -euo pipefail
ROOT="$(cd "$(dirname "$BASH_SOURCE[0]")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$ROOT/eval_harness/src"
LOG="$ROOT/eval_harness/data/logs/phase2-continue.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

echo "=== wait_and_continue start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 1) Wait until queue process gone AND all 7 jobs complete
ARMS=(ALWAYS_LC ALWAYS_RAG ALWAYS_GRAPH SELF_ROUTE PREASSESS GASR GAPA)
RUNS="$ROOT/eval_harness/data/runs/a2cfcec"
while true; do
  done_n=0
  for a in "${ARMS[@]}"; do
    st="$RUNS/p2-sweqa-${a}-w1/queue_status.json"
    if [[ -f "$st" ]] && python3 -c "import json; d=json.load(open('$st')); raise SystemExit(0 if d.get('state')=='complete' else 1)"; then
      done_n=$((done_n+1))
    fi
  done
  hb="$ROOT/eval_harness/data/queue/heartbeat.json"
  state="unknown"
  [[ -f "$hb" ]] && state=$(python3 -c "import json; print(json.load(open('$hb')).get('state',''))" 2>/dev/null || echo unknown)
  echo "$(date -u +%H:%M:%S) arms_complete=$done_n/7 heartbeat_state=$state"
  if [[ "$done_n" -eq 7 ]]; then
    echo "All 7 arms complete"
    break
  fi
  # if queue died early with STOPPED, abort
  if [[ -f "$ROOT/eval_harness/data/queue/STOPPED" ]]; then
    echo "STOPPED marker present:"; cat "$ROOT/eval_harness/data/queue/STOPPED"
    exit 3
  fi
  sleep 60
done

echo "=== CP5 export + judge $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
set -a
# shellcheck disable=SC1091
source "$ROOT/docs/phase2_runs/judge_ollama.env"
set +a

python3 "$ROOT/scripts/phase2_export_sweqa_candidates.py" \
  --runs-root "$RUNS" \
  --tasks "$ROOT/eval_harness/data/datasets/swe-qa-w1/tasks.jsonl" \
  --out-dir "$ROOT/eval_harness/data/reports/phase2_sweqa_candidates"

# Combined reference JSONL
REF="$ROOT/eval_harness/data/reports/phase2_sweqa_candidates/reference_w1.jsonl"
: > "$REF"
for repo in flask requests pytest; do
  cat "$ROOT/../external/SWE-QA-Bench/Benchmark/${repo}.jsonl" >> "$REF"
done

OUT_SCORES="$ROOT/eval_harness/data/reports/phase2_sweqa_scores"
mkdir -p "$OUT_SCORES"
export EVAL_REFERENCE_PATH="$REF"
export EVAL_CANDIDATE_DIR="$ROOT/eval_harness/data/reports/phase2_sweqa_candidates"
export EVAL_OUTPUT_DIR="$OUT_SCORES"
export EVAL_MAX_WORKERS=1

# Unload generator if possible so 27B fits
curl -s http://127.0.0.1:11434/api/generate -d '{"model":"qwen3.5:9b","keep_alive":0}' >/dev/null || true
sleep 2

python3 "$ROOT/scripts/phase2_sweqa_ollama_judge.py"

echo "=== CP6–CP7 tables $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
python3 "$ROOT/scripts/phase2_fill_table_b.py"

echo "=== CP8 kick RepoProbe materialize/index/queue $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
# Materialize already done; clone+index Python-8 then start queue
python3 -m harness.phase2_datasets \
  --profile "$ROOT/eval_harness/profiles/ubuntu-4080.toml" \
  --config "$ROOT/eval_harness/configs/datasets_phase2.toml" \
  --only repoprobe-py8 \
  --resolve-commits || true

python3 -m harness.index_cache build \
  --profile "$ROOT/eval_harness/profiles/ubuntu-4080.toml" \
  --experiment "$ROOT/eval_harness/configs/experiment.toml" \
  --pipeline-config "$ROOT/eval_harness/configs/pipeline_phase2.toml" \
  --index-config "$ROOT/eval_harness/configs/index.toml" \
  --dataset repoprobe-py8 \
  --continue-on-error || true

# Enable RepoProbe queue and start
python3 - <<'PY'
from pathlib import Path
p = Path("eval_harness/queues/phase2-repoprobe-py.toml")
t = p.read_text()
t = t.replace("enabled = false", "enabled = true", 1)
p.write_text(t)
print("repoprobe queue enabled")
PY

# Refresh record auth for current HEAD
python3 - <<'PY'
import json, subprocess
from pathlib import Path
from datetime import datetime, timezone
head = subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip()
auth = {
  "pipeline_sha": "a2cfcec312626b4b50f743c4af91a6d118c79d0e",
  "harness_commit": head,
  "authorized_utc": datetime.now(timezone.utc).isoformat(),
  "note": "Phase 2 continue after Wave1",
}
Path("eval_harness/data/queue/record_authorisation.json").write_text(json.dumps(auth, indent=1))
print(auth)
PY
rm -f "$ROOT/eval_harness/data/queue/STOPPED" "$ROOT/eval_harness/data/queue/queue.lock" || true

nohup python3 -m harness.queue \
  --profile "$ROOT/eval_harness/profiles/ubuntu-4080.toml" \
  --queue "$ROOT/eval_harness/queues/phase2-repoprobe-py.toml" \
  --pipeline-config "$ROOT/eval_harness/configs/pipeline_phase2.toml" \
  --configs "$ROOT/eval_harness/configs" \
  --cwd "$ROOT" \
  > "$ROOT/eval_harness/data/logs/phase2-repoprobe-queue.log" 2>&1 &
echo "repoprobe queue pid=$!"

echo "=== wait_and_continue finished kickoff $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "RepoProbe generation running; CP9 after that queue completes."
