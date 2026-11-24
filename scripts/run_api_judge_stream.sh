#!/usr/bin/env bash
# Score finished arms while gen continues. Strict: never score a partial arm.
#
# Rules:
#   - SWE-QA arm scored only when swe-qa-w1/<ARM>/results.jsonl has >=144 lines
#     AND that arm dir has no inflight.json (writer idle).
#   - RepoProbe arm scored only when repoprobe-py8/<ARM>/results.jsonl has >=108
#     AND no inflight.json.
#   - Done markers under $SCORES/markers/ prevent re-spend if restarted.
#   - Exits when run.log has ALL DONE and every arm is marked done (or CANCEL file).
#
# Cancel:
#   touch ~/haya/api_runs/CANCEL_JUDGE
#   # or: kill "$(cat ~/haya/api_runs/judge_stream.pid)"
#
# Usage:
#   nohup bash scripts/run_api_judge_stream.sh >> ~/haya/api_runs/judge_stream.log 2>&1 &
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUNS="${API_RUNS:-$HOME/haya/api_runs}"
SCORES="${API_SCORES:-$HOME/haya/api_scores}"
RP_LINKS="${API_RP_LINKS:-$HOME/haya/api_rp_runs}"
ENVF="${API_JUDGE_ENV:-$HOME/haya/api_judge.env}"
LOG="${RUNS}/run.log"
PIDFILE="${RUNS}/judge_stream.pid"
CANCEL="${RUNS}/CANCEL_JUDGE"
MARKERS="${SCORES}/markers"
ARMS=(GAPA GASR SELF_ROUTE ALWAYS_RAG ALWAYS_GRAPH PREASSESS ALWAYS_LC)
W1_N=144
RP_N=108
POLL_S="${JUDGE_POLL_S:-45}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

mkdir -p "$SCORES" "$MARKERS" "$RP_LINKS"
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

if [[ -f "$CANCEL" ]]; then
  log "CANCEL_JUDGE already present — remove it to run"; exit 0
fi

# refuse to run alongside the full waiter (double-spend risk)
if pgrep -af 'scripts/run_api_judge.sh' | grep -v 'run_api_judge_stream' | grep -v pgrep >/dev/null 2>&1; then
  log "full run_api_judge.sh still running — cancel it first to avoid double spend"
  exit 1
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
  : "${OPENAI_SCORING_API_KEY:?}"
  : "${SCORING_MODEL:?}"
}

lines() {
  local f="$1"
  [[ -f "$f" ]] || { echo 0; return; }
  wc -l < "$f" | tr -d ' '
}

# True only when file has enough lines AND harness is not mid-write on that arm.
arm_ready() {
  local ds="$1" arm="$2" need="$3"
  local dir="$RUNS/$ds/$arm"
  local f="$dir/results.jsonl"
  local n
  n=$(lines "$f")
  if [[ "$n" -lt "$need" ]]; then
    return 1
  fi
  if [[ -e "$dir/inflight.json" ]]; then
    return 1
  fi
  # mtime stable ~15s (avoid scoring the exact moment the last line lands mid-flush)
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
    log "building reference.jsonl (W1 gold)"
    python3 scripts/export_test_answers.py \
      --runs "$RUNS" --out "$SCORES" --datasets swe-qa-w1 --arms GAPA >/dev/null
    # export above also wrote GAPA answers; fine — SWE judge still gated by marker
  fi
}

score_swe() {
  local arm="$1"
  local marker="$MARKERS/swe_${arm}.done"
  local score="$SCORES/${arm}_answers_score.jsonl"
  if [[ -f "$marker" ]]; then
    return 0
  fi
  if [[ -f "$score" ]]; then
    local ns
    ns=$(lines "$score")
    if [[ "$ns" -ge "$W1_N" ]]; then
      log "SWE $arm already has $ns score lines — marking done (no re-spend)"
      echo "preexisting score lines=$ns" > "$marker"
      return 0
    fi
  fi
  if ! arm_ready swe-qa-w1 "$arm" "$W1_N"; then
    return 1
  fi

  ensure_reference
  log "SWE-QA judge START $arm (strict ready: ${W1_N}+ lines, no inflight, mtime≥15s)"
  python3 scripts/export_test_answers.py \
    --runs "$RUNS" --out "$SCORES" --datasets swe-qa-w1 --arms "$arm" \
    || { log "export failed $arm"; return 1; }

  local nans
  nans=$(lines "$SCORES/${arm}_answers.jsonl")
  if [[ "$nans" -lt "$W1_N" ]]; then
    log "REFUSING $arm SWE: exported $nans < $W1_N"; return 1
  fi

  EVAL_REFERENCE_PATH="$SCORES/reference.jsonl" \
  EVAL_CANDIDATE_PATH="$SCORES/${arm}_answers.jsonl" \
  EVAL_OUTPUT_PATH="$score" \
    python3 scripts/phase2_sweqa_ollama_judge.py 2>&1 | tee -a "$SCORES/${arm}_judge.log" | tail -15

  local ns
  ns=$(lines "$score")
  if [[ "$ns" -lt "$W1_N" ]]; then
    log "SWE $arm partial/incomplete scores=$ns < $W1_N — not marking done (resume next poll; sleep 70s for RPM)"
    sleep 70
    return 1
  fi
  echo "scored_lines=$ns model=$EVAL_LLM_MODEL_NAME ts=$(date -Is)" > "$marker"
  log "SWE-QA judge END $arm scored_lines=$ns"
  return 0
}

score_rp() {
  local arm="$1"
  local marker="$MARKERS/rp_${arm}.done"
  local score="$SCORES/repoprobe/${arm}_score.jsonl"
  if [[ -f "$marker" ]]; then
    return 0
  fi
  if [[ -f "$score" ]]; then
    local ns
    ns=$(lines "$score")
    if [[ "$ns" -ge "$RP_N" ]]; then
      log "RP $arm already has $ns score lines — marking done (no re-spend)"
      echo "preexisting score lines=$ns" > "$marker"
      return 0
    fi
  fi
  if ! arm_ready repoprobe-py8 "$arm" "$RP_N"; then
    return 1
  fi

  ln -sfn "$RUNS/repoprobe-py8/$arm" "$RP_LINKS/p2-repoprobe-$arm-py8"
  mkdir -p "$SCORES/repoprobe"
  log "RepoProbe score START $arm (strict ready: ${RP_N}+ lines, no inflight, mtime≥15s)"
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase2_repoprobe_score_pipeline.py \
    --arms "$arm" \
    --runs-root "$RP_LINKS" \
    --labels eval_harness/data/datasets/repoprobe-py8/labels.jsonl \
    --out-dir "$SCORES/repoprobe" \
    --scoring-workers "${EVAL_MAX_WORKERS:-1}" \
    2>&1 | tee -a "$SCORES/repoprobe/${arm}_judge.log" | grep -v "^  scored" || true

  local ns
  ns=$(lines "$score")
  if [[ "$ns" -lt 1 ]]; then
    log "RP $arm produced no scores — not marking done (will retry)"
    return 1
  fi
  echo "scored_lines=$ns model=$SCORING_MODEL ts=$(date -Is)" > "$marker"
  log "RepoProbe score END $arm scored_lines=$ns"
  return 0
}

all_done_markers() {
  local arm
  for arm in "${ARMS[@]}"; do
    [[ -f "$MARKERS/swe_${arm}.done" ]] || return 1
    [[ -f "$MARKERS/rp_${arm}.done" ]] || return 1
  done
  return 0
}

load_judge_env
log "STREAM judge started pid=$$ poll=${POLL_S}s scores=$SCORES"
log "cancel: touch $CANCEL   OR   kill \$(cat $PIDFILE)"

while true; do
  if [[ -f "$CANCEL" ]]; then
    log "CANCEL_JUDGE seen — exiting cleanly (markers preserved; no further spend)"
    exit 0
  fi

  for arm in "${ARMS[@]}"; do
    if [[ -f "$CANCEL" ]]; then
      log "CANCEL_JUDGE during loop — stop"
      exit 0
    fi
    score_swe "$arm" || true
    score_rp "$arm" || true
  done

  if all_done_markers; then
    log "all 7 arms SWE+RP marked done — STREAM SCORING ALL DONE"
    exit 0
  fi

  if [[ -f "$LOG" ]] && grep -q '^ALL DONE$' "$LOG"; then
    # gen finished: keep polling until markers catch up (or stuck incomplete)
    pending=0
    for arm in "${ARMS[@]}"; do
      [[ -f "$MARKERS/swe_${arm}.done" ]] || pending=1
      [[ -f "$MARKERS/rp_${arm}.done" ]] || pending=1
    done
    if [[ "$pending" -eq 0 ]]; then
      log "STREAM SCORING ALL DONE"
      exit 0
    fi
    log "gen ALL DONE; still waiting on remaining markers (incomplete arms will keep retrying readiness)"
  fi

  sleep "$POLL_S"
done
