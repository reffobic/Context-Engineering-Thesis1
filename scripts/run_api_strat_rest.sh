#!/usr/bin/env bash
# Paid API gen: ONLY swe-qa-rest strat2 (96 Q × 7 arms).
# Does NOT touch swe-qa-w1 or repoprobe-py8 (already paid — no rebill).
#
# Out: ~/haya/api_runs/swe-qa-rest/<ARM>/
# Log: ~/haya/api_runs/run_strat_rest.log
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENVF="${API_GEN_ENV:-$HOME/haya/api_gen.env}"
if [[ -f "$ENVF" ]]; then set -a; # shellcheck disable=SC1090
  source "$ENVF"; set +a; fi
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY or source $HOME/haya/api_gen.env}"
export HARNESS_PROVIDER_CONFIG="${HARNESS_PROVIDER_CONFIG:-$ROOT/eval_harness/configs/provider_openrouter.toml}"
OUT="${1:-$HOME/haya/api_runs}"
CFG=eval_harness/configs
SAMPLE=eval_harness/samples_strat/swe-qa-rest.strat2.sample.json
LOG="$OUT/run_strat_rest.log"
REST_N=96
mkdir -p "$OUT"
exec > >(tee -a "$LOG") 2>&1

echo "[$(date -Is)] STRAT-REST gen start out=$OUT sample=$SAMPLE (rest only; skip W1+RP)"

# Hard refuse if sample is missing or wrong size
if [[ ! -f "$SAMPLE" ]]; then
  echo "[FAIL] missing $SAMPLE"; exit 1
fi
n=$(python3 -c "import json; print(len(json.load(open('$SAMPLE'))['instance_ids']))")
if [[ "$n" -ne "$REST_N" ]]; then
  echo "[FAIL] sample n=$n expected $REST_N"; exit 1
fi

run() {
  local arm="$1"
  local dest="$OUT/swe-qa-rest/$arm"
  # Safety: never write into W1/RP trees
  case "$dest" in
    */swe-qa-w1/*|*/repoprobe-py8/*) echo "[FAIL] refused path $dest"; exit 1 ;;
  esac
  echo "[$(date +%H:%M:%S)] START swe-qa-rest/$arm"
  PYTHONPATH=eval_harness/src python3 -m harness.run \
    --profile eval_harness/profiles/ubuntu-4080.toml --dataset swe-qa-rest --model qwen3.5:9b --arm "$arm" \
    --sample "$SAMPLE" --out "$dest" \
    --experiment $CFG/experiment.toml --pipeline-config $CFG/pipeline_phase2.toml \
    --index-config $CFG/index.toml --run-config $CFG/run.toml \
    --allow-dirty 2>&1 | tail -12
  echo "[$(date +%H:%M:%S)] END   swe-qa-rest/$arm"
}

for arm in GAPA GASR SELF_ROUTE ALWAYS_RAG ALWAYS_GRAPH PREASSESS ALWAYS_LC; do
  run "$arm"
done
echo "ALL DONE"
echo "[$(date -Is)] STRAT-REST gen finished"
