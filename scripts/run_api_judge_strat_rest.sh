#!/usr/bin/env bash
# Stream-score paid swe-qa-rest strat2 only (96/arm). Never touches W1/RP scores or markers.
#
# Reads:  $API_RUNS/swe-qa-rest/<ARM>/results.jsonl
# Writes: $API_SCORES_REST/ (default ~/haya/api_scores_strat_rest)
# Markers: $API_SCORES_REST/markers/swe_rest_<ARM>.done
#
# Cancel: touch ~/haya/api_runs/CANCEL_JUDGE_STRAT_REST
#         or kill $(cat ~/haya/api_runs/judge_strat_rest.pid)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUNS="${API_RUNS:-$HOME/haya/api_runs}"
SCORES="${API_SCORES_REST:-$HOME/haya/api_scores_strat_rest}"
ENVF="${API_JUDGE_ENV:-$HOME/haya/api_judge.env}"
LOG="${RUNS}/run_strat_rest.log"
PIDFILE="${RUNS}/judge_strat_rest.pid"
CANCEL="${RUNS}/CANCEL_JUDGE_STRAT_REST"
MARKERS="${SCORES}/markers"
ARMS=(GAPA GASR SELF_ROUTE ALWAYS_RAG ALWAYS_GRAPH PREASSESS ALWAYS_LC)
REST_N=96
POLL_S="${JUDGE_POLL_S:-45}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

mkdir -p "$SCORES" "$MARKERS"
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

if [[ -f "$CANCEL" ]]; then
  log "CANCEL_JUDGE_STRAT_REST already present — remove it to run"; exit 0
fi

# Refuse if this would write into the original W1/RP score tree
if [[ "$(realpath -m "$SCORES")" == "$(realpath -m "$HOME/haya/api_scores")" ]]; then
  log "REFUSE: SCORES must not be ~/haya/api_scores (W1/RP already billed)"; exit 1
fi

load_judge_env() {
  unset HARNESS_PROVIDER_CONFIG || true
  unset OPENROUTER_API_KEY || true
  if [[ ! -f "$ENVF" ]]; then
    log "missing $ENVF"; exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENVF"
  set +a
  if env | cut -d= -f1 | grep -i -E 'openrouter|paid'; then
    log "paid-looking env *name* set — abort"; exit 1
  fi
  : "${EVAL_LLM_API_KEY:?}"
  : "${EVAL_LLM_MODEL_NAME:?}"
  : "${EVAL_LLM_BASE_URL:?}"
}

lines() {
  local f="$1"
  [[ -f "$f" ]] || { echo 0; return; }
  wc -l < "$f" | tr -d ' '
}

arm_ready() {
  local arm="$1"
  local dir="$RUNS/swe-qa-rest/$arm"
  local f="$dir/results.jsonl"
  local n
  n=$(lines "$f")
  if [[ "$n" -lt "$REST_N" ]]; then
    return 1
  fi
  if [[ -e "$dir/inflight.json" ]]; then
    return 1
  fi
  if [[ -f "$f" ]]; then
    local age
    age=$(( $(date +%s) - $(stat -c %Y "$f") ))
    if [[ "$age" -lt 15 ]]; then
      return 1
    fi
  fi
  return 0
}

ensure_reference() {
  if [[ ! -f "$SCORES/reference.jsonl" ]]; then
    log "building rest-only reference.jsonl"
    python3 scripts/export_test_answers.py \
      --runs "$RUNS" --out "$SCORES" --datasets swe-qa-rest --arms GAPA >/dev/null
  fi
}

score_rest() {
  local arm="$1"
  local marker="$MARKERS/swe_rest_${arm}.done"
  local score="$SCORES/${arm}_answers_score.jsonl"
  if [[ -f "$marker" ]]; then
    return 0
  fi
  if [[ -f "$score" ]]; then
    local ns
    ns=$(lines "$score")
    if [[ "$ns" -ge "$REST_N" ]]; then
      log "REST $arm already has $ns score lines — marking done (no re-spend)"
      echo "preexisting score lines=$ns" > "$marker"
      return 0
    fi
  fi
  if ! arm_ready "$arm"; then
    return 1
  fi

  ensure_reference
  log "SWE-QA-REST judge START $arm (need ${REST_N})"
  python3 scripts/export_test_answers.py \
    --runs "$RUNS" --out "$SCORES" --datasets swe-qa-rest --arms "$arm" \
    || { log "export failed $arm"; return 1; }

  local nans
  nans=$(lines "$SCORES/${arm}_answers.jsonl")
  if [[ "$nans" -lt "$REST_N" ]]; then
    log "REFUSING $arm REST: exported $nans < $REST_N"; return 1
  fi

  EVAL_REFERENCE_PATH="$SCORES/reference.jsonl" \
  EVAL_CANDIDATE_PATH="$SCORES/${arm}_answers.jsonl" \
  EVAL_OUTPUT_PATH="$score" \
    python3 scripts/phase2_sweqa_ollama_judge.py 2>&1 | tee -a "$SCORES/${arm}_judge.log" | tail -15

  local ns
  ns=$(lines "$score")
  if [[ "$ns" -lt "$REST_N" ]]; then
    log "REST $arm partial scores=$ns < $REST_N — resume next poll; sleep 70s for RPM"
    sleep 70
    return 1
  fi
  echo "scored_lines=$ns model=$EVAL_LLM_MODEL_NAME ts=$(date -Is)" > "$marker"
  log "SWE-QA-REST judge END $arm scored_lines=$ns"
  return 0
}

all_done_markers() {
  local arm
  for arm in "${ARMS[@]}"; do
    [[ -f "$MARKERS/swe_rest_${arm}.done" ]] || return 1
  done
  return 0
}

load_judge_env
log "STRAT-REST STREAM judge started pid=$$ poll=${POLL_S}s scores=$SCORES"
log "cancel: touch $CANCEL   OR   kill \$(cat $PIDFILE)"

while true; do
  if [[ -f "$CANCEL" ]]; then
    log "CANCEL seen — exit (markers kept; no further spend)"
    exit 0
  fi

  for arm in "${ARMS[@]}"; do
    if [[ -f "$CANCEL" ]]; then
      log "CANCEL during loop — stop"
      exit 0
    fi
    score_rest "$arm" || true
  done

  if all_done_markers; then
    log "all 7 REST arms marked done — STRAT-REST SCORING ALL DONE"
    exit 0
  fi

  if [[ -f "$LOG" ]] && grep -q '^ALL DONE$' "$LOG"; then
    pending=0
    for arm in "${ARMS[@]}"; do
      [[ -f "$MARKERS/swe_rest_${arm}.done" ]] || pending=1
    done
    if [[ "$pending" -eq 0 ]]; then
      log "STRAT-REST SCORING ALL DONE"
      exit 0
    fi
    log "gen ALL DONE; waiting on remaining REST markers"
  fi

  sleep "$POLL_S"
done
