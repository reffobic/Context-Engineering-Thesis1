#!/usr/bin/env bash
# Paid API gen: SWE-QA Wave 1 (144) + RepoProbe py8 (108) × 7 arms. Resume-safe per out dir.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENVF="${API_GEN_ENV:-$HOME/haya/api_gen.env}"
if [[ -f "$ENVF" ]]; then set -a; source "$ENVF"; set +a; fi
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY or source $HOME/haya/api_gen.env}"
export HARNESS_PROVIDER_CONFIG="${HARNESS_PROVIDER_CONFIG:-$ROOT/eval_harness/configs/provider_openrouter.toml}"
OUT="${1:-$HOME/haya/api_runs}"
CFG=eval_harness/configs
LOG="$OUT/run.log"
mkdir -p "$OUT"
exec > >(tee -a "$LOG") 2>&1
run() {
  echo "[$(date +%H:%M:%S)] START $4"
  PYTHONPATH=eval_harness/src python3 -m harness.run \
    --profile eval_harness/profiles/ubuntu-4080.toml --dataset "$1" --model qwen3.5:9b --arm "$2" \
    --sample "$3" --out "$OUT/$4" \
    --experiment $CFG/experiment.toml --pipeline-config $CFG/pipeline_phase2.toml \
    --index-config $CFG/index.toml --run-config $CFG/run.toml \
    --allow-dirty 2>&1 | tail -8
  echo "[$(date +%H:%M:%S)] END   $4"
}
for arm in GAPA GASR SELF_ROUTE ALWAYS_RAG ALWAYS_GRAPH PREASSESS ALWAYS_LC; do
  run swe-qa-w1     "$arm" eval_harness/data/samples/swe-qa-w1.sample.json   "swe-qa-w1/$arm"
  run repoprobe-py8 "$arm" eval_harness/data/samples/repoprobe-py8.sample.json "repoprobe-py8/$arm"
done
echo "ALL DONE"
