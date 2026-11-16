#!/usr/bin/env bash
# CP9 smoke: score all 7 arms × browser-use (n=35) with local qwen-7B checklist scorer.
# Run in a normal host terminal (not the sandboxed agent shell).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

set -a
# shellcheck disable=SC1091
source "$ROOT/docs/phase2_runs/judge_ollama.env"
set +a

export OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:11434/v1}"
export OPENAI_SCORING_BASE_URL="${OPENAI_SCORING_BASE_URL:-http://127.0.0.1:11434/v1}"

echo "=== preflight ==="
curl -sf "${OLLAMA_HOST%/}/api/tags" >/dev/null || {
  echo "Ollama not reachable at $OLLAMA_HOST — start with: ollama serve"
  exit 1
}
if ! ollama list 2>/dev/null | grep -qiE 'qwen-7B|qwen-7b'; then
  echo "Missing judge model qwen-7B:latest"
  echo "If ~/.ollama models were wiped, recreate e.g.:"
  echo "  ollama pull qwen2.5:7b"
  echo "  printf 'FROM qwen2.5:7b\\n' | ollama create qwen-7B:latest -f -"
  exit 1
fi
echo "judge model OK; starting score (7 arms × browser-use)…"

OUT="$ROOT/eval_harness/data/reports/phase2_repoprobe_py/smoke_browser_use"
mkdir -p "$OUT"
LOG="$ROOT/eval_harness/data/logs/phase2-cp9-smoke-browser-use.log"

python3 "$ROOT/scripts/phase2_repoprobe_score_pipeline.py" \
  --arms all \
  --repo-filter browser-use \
  --runs-root "$ROOT/eval_harness/data/runs/a2cfcec" \
  --labels "$ROOT/eval_harness/data/datasets/repoprobe-py8/labels.jsonl" \
  --out-dir "$OUT" \
  --num-iterations 1 \
  --scoring-workers 1 \
  2>&1 | tee "$LOG"

echo "=== done ==="
echo "summary: $OUT/summary.json"
echo "log: $LOG"
