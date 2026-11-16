#!/usr/bin/env bash
# After rest generation queue finishes (7×576), export + judge with qwen-7B.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH=eval_harness/src
STATUS="$ROOT/docs/phase2_runs/sweqa_rest_progress.txt"
RUNS="$ROOT/eval_harness/data/runs/a2cfcec"
ARMS=(ALWAYS_LC ALWAYS_RAG ALWAYS_GRAPH SELF_ROUTE PREASSESS GASR GAPA)

count_complete() {
  local ok=0 arm f n
  for arm in "${ARMS[@]}"; do
    f="$RUNS/p2-sweqa-${arm}-rest/results.jsonl"
    if [ -f "$f" ]; then
      n=$(wc -l < "$f" | tr -d ' ')
      if [ "$n" -ge 576 ]; then
        ok=$((ok + 1))
      fi
    fi
  done
  echo "$ok"
}

echo "$(date -Is) waiting for 7 rest generation jobs (576 each)..." | tee -a "$STATUS"
while true; do
  n=$(count_complete)
  echo "$(date -Is) gen arms complete=$n / 7" | tee -a "$STATUS"
  for arm in "${ARMS[@]}"; do
    f="$RUNS/p2-sweqa-${arm}-rest/results.jsonl"
    if [ -f "$f" ]; then
      echo "  $arm: $(wc -l < "$f" | tr -d ' ') / 576" | tee -a "$STATUS"
    else
      echo "  $arm: (no results yet)" | tee -a "$STATUS"
    fi
  done
  if [ "$n" -ge 7 ]; then
    break
  fi
  if [ -f "$ROOT/eval_harness/data/queue/STOPPED" ]; then
    echo "$(date -Is) queue STOPPED marker present" | tee -a "$STATUS"
  fi
  if ! pgrep -f 'harness.queue' >/dev/null && ! pgrep -f 'harness.run' >/dev/null; then
    sleep 30
    n2=$(count_complete)
    if [ "$n2" -lt 7 ]; then
      echo "$(date -Is) queue/run exited early complete=$n2 — attempting resume" | tee -a "$STATUS"
      curl -sf http://127.0.0.1:11434/api/tags >/dev/null || { sudo systemctl start ollama || true; sleep 3; }
      nohup python3 -m harness.queue \
        --profile eval_harness/profiles/ubuntu-4080.toml \
        --queue eval_harness/queues/phase2-sweqa-rest.toml \
        --pipeline-config eval_harness/configs/pipeline_phase2.toml \
        --configs eval_harness/configs \
        --cwd . \
        >> "$ROOT/docs/phase2_runs/sweqa_rest_queue.log" 2>&1 &
      echo $! > "$ROOT/docs/phase2_runs/sweqa_rest_queue.pid"
    fi
  fi
  sleep 120
done

echo "$(date -Is) generation complete — export + judge" | tee -a "$STATUS"
set -a
# shellcheck disable=SC1091
source docs/phase2_runs/judge_ollama.env
set +a
export OLLAMA_MODELS="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}"

CAND_DIR="$ROOT/eval_harness/data/reports/phase2_sweqa_candidates_rest"
OUT_SCORES="$ROOT/eval_harness/data/reports/phase2_sweqa_scores_rest"
REF="$ROOT/eval_harness/data/reports/phase2_sweqa_candidates_rest/reference.jsonl"
mkdir -p "$CAND_DIR" "$OUT_SCORES"

python3 - <<'PY'
import json
from pathlib import Path
labels = Path("eval_harness/data/datasets/swe-qa-rest/labels.jsonl")
out = Path("eval_harness/data/reports/phase2_sweqa_candidates_rest/reference.jsonl")
with out.open("w", encoding="utf-8") as f:
    for ln in labels.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        o = json.loads(ln)
        f.write(json.dumps({
            "question": o["question"],
            "answer": o.get("gold_answer") or o.get("answer") or "",
            "task_id": o.get("instance_id"),
        }, ensure_ascii=False) + "\n")
print(f"wrote {out}")
PY

python3 scripts/phase2_export_sweqa_candidates.py \
  --runs-root "$RUNS" \
  --tasks eval_harness/data/datasets/swe-qa-rest/tasks.jsonl \
  --out-dir "$CAND_DIR" \
  --job-suffix rest \
  2>&1 | tee -a "$ROOT/docs/phase2_runs/sweqa_rest_judge.log"

export EVAL_REFERENCE_PATH="$REF"
export EVAL_CANDIDATE_DIR="$CAND_DIR"
export EVAL_OUTPUT_DIR="$OUT_SCORES"
export EVAL_MAX_WORKERS=1

python3 scripts/phase2_sweqa_ollama_judge.py \
  2>&1 | tee -a "$ROOT/docs/phase2_runs/sweqa_rest_judge.log"

echo "$(date -Is) judge finished — writing INDEXES_REST + progress note" | tee -a "$STATUS"
python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
p = Path("docs/phase2_runs/SWE_QA_REST_RUN.md")
p.write_text(f"""# SWE-QA rest run

**Stamp (UTC):** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
**Dataset:** swe-qa-rest · 576 Q · 12 repos
**Generator:** qwen3.5:9b · **Judge:** qwen-7B:latest
**Jobs:** `eval_harness/data/runs/a2cfcec/p2-sweqa-*-rest`
**Scores:** `eval_harness/data/reports/phase2_sweqa_scores_rest/`

Merge with Wave 1 (`phase2_sweqa_scores/`) for full n=720 Table B.
""", encoding="utf-8")
print("wrote", p)
PY

echo "$(date -Is) C4–C5 automation done (C6 merge table may still need fill_table_b update)" | tee -a "$STATUS"
