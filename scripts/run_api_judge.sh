#!/usr/bin/env bash
# Wait for paid W1+RP gen to finish, then Sonnet-judge SWE-QA + score RepoProbe.
#
# Usage (from repo root, or via absolute path):
#   bash scripts/run_api_judge.sh [gen_pid]
#
# Env:
#   API_JUDGE_ENV   default ~/haya/api_judge.env
#   API_RUNS        default ~/haya/api_runs
#   API_SCORES      default ~/haya/api_scores
#   SKIP_WAIT=1     skip PID / ALL DONE wait (manual re-score)
#   JUDGE_SMOKE=1   only verify Sonnet with one smoke call, then exit
#                   (skips wait + completeness; still needs api_judge.env)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GEN_PID="${1:-}"
RUNS="${API_RUNS:-$HOME/haya/api_runs}"
SCORES="${API_SCORES:-$HOME/haya/api_scores}"
RP_LINKS="${API_RP_LINKS:-$HOME/haya/api_rp_runs}"
LOG="${RUNS}/run.log"
ENVF="${API_JUDGE_ENV:-$HOME/haya/api_judge.env}"
ARMS=(GAPA ALWAYS_RAG ALWAYS_GRAPH GASR SELF_ROUTE PREASSESS ALWAYS_LC)
W1_N=144
RP_N=108

log() { echo "[$(date +%H:%M:%S)] $*"; }

load_judge_env() {
  unset HARNESS_PROVIDER_CONFIG || true
  unset OPENROUTER_API_KEY || true
  if [[ ! -f "$ENVF" ]]; then
    log "missing $ENVF — copy docs/phase2_runs/judge_openrouter.env.example and paste the judge key"
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENVF"
  set +a
  if env | cut -d= -f1 | grep -i -E 'openrouter|paid'; then
    log "paid-looking env *name* still set — unset OPENROUTER_* / *PAID* and use EVAL_LLM_API_KEY in $ENVF"
    env | cut -d= -f1 | grep -i -E 'openrouter|paid' || true
    exit 1
  fi
  : "${EVAL_LLM_API_KEY:?EVAL_LLM_API_KEY missing in $ENVF}"
  : "${EVAL_LLM_MODEL_NAME:?EVAL_LLM_MODEL_NAME missing in $ENVF}"
  : "${EVAL_LLM_BASE_URL:?EVAL_LLM_BASE_URL missing in $ENVF}"
  : "${OPENAI_SCORING_API_KEY:?OPENAI_SCORING_API_KEY missing in $ENVF}"
  : "${SCORING_MODEL:?SCORING_MODEL missing in $ENVF}"
}

# ── smoke: key check only (no wait / no results required) ───────────────────
if [[ "${JUDGE_SMOKE:-0}" == "1" ]]; then
  load_judge_env
  log "JUDGE_SMOKE: one Sonnet call"
  PHASE2_JUDGE_SMOKE=1 python3 scripts/phase2_sweqa_ollama_judge.py --smoke
  exit $?
fi

# ── wait for generation ─────────────────────────────────────────────────────
if [[ "${SKIP_WAIT:-0}" != "1" ]]; then
  if [[ -n "$GEN_PID" ]]; then
    log "waiting for gen PID $GEN_PID to exit"
    while kill -0 "$GEN_PID" 2>/dev/null; do sleep 60; done
  else
    log "no gen PID given — waiting for ALL DONE in $LOG"
  fi
  while true; do
    if [[ -f "$LOG" ]] && grep -q '^ALL DONE$' "$LOG"; then
      break
    fi
    if [[ -n "$GEN_PID" ]] && ! kill -0 "$GEN_PID" 2>/dev/null; then
      if [[ -f "$LOG" ]] && grep -q '^ALL DONE$' "$LOG"; then
        break
      fi
      log "gen PID exited but $LOG has no ALL DONE — abort"
      exit 1
    fi
    sleep 60
  done
  log "gen finished (ALL DONE)"
fi

# ── expected line counts ────────────────────────────────────────────────────
missing=0
for arm in "${ARMS[@]}"; do
  for pair in "swe-qa-w1:$W1_N" "repoprobe-py8:$RP_N"; do
    ds="${pair%%:*}"; need="${pair##*:}"
    f="$RUNS/$ds/$arm/results.jsonl"
    if [[ ! -f "$f" ]]; then
      log "MISSING $f"; missing=1; continue
    fi
    n=$(wc -l < "$f" | tr -d ' ')
    if [[ "$n" -lt "$need" ]]; then
      log "SHORT $f: $n < $need"; missing=1
    fi
  done
done
if [[ "$missing" -ne 0 ]]; then
  log "incomplete results — NOT judging"; exit 1
fi
log "all 14 result files present ($W1_N + $RP_N)×7"

load_judge_env

# ── export SWE-QA W1 answers only ───────────────────────────────────────────
mkdir -p "$SCORES"
log "export SWE-QA answers → $SCORES"
python3 scripts/export_test_answers.py \
  --runs "$RUNS" --out "$SCORES" --datasets swe-qa-w1 \
  || { log "export failed"; exit 1; }

# ── RepoProbe symlink layout expected by scorer ─────────────────────────────
mkdir -p "$RP_LINKS"
for arm in "${ARMS[@]}"; do
  ln -sfn "$RUNS/repoprobe-py8/$arm" "$RP_LINKS/p2-repoprobe-$arm-py8"
done

export EVAL_REFERENCE_PATH="$SCORES/reference.jsonl"
export EVAL_OUTPUT_DIR="$SCORES"

MARKERS="${SCORES}/markers"
mkdir -p "$MARKERS"

# ── SWE-QA Sonnet judge per arm (skip if stream marker / full score exists) ─
for arm in "${ARMS[@]}"; do
  if [[ -f "$MARKERS/swe_${arm}.done" ]]; then
    log "SWE-QA SKIP $arm (marker exists — no re-spend)"
    continue
  fi
  sp="$SCORES/${arm}_answers_score.jsonl"
  if [[ -f "$sp" ]] && [[ "$(wc -l < "$sp" | tr -d ' ')" -ge "$W1_N" ]]; then
    log "SWE-QA SKIP $arm (existing score file)"
    echo "preexisting" > "$MARKERS/swe_${arm}.done"
    continue
  fi
  log "SWE-QA judge START $arm"
  EVAL_CANDIDATE_PATH="$SCORES/${arm}_answers.jsonl" \
  EVAL_OUTPUT_PATH="$sp" \
    python3 scripts/phase2_sweqa_ollama_judge.py 2>&1 | tail -20
  echo "scored ts=$(date -Is)" > "$MARKERS/swe_${arm}.done"
  log "SWE-QA judge END   $arm"
done

# ── RepoProbe Sonnet checklist (per-arm skip) ───────────────────────────────
log "RepoProbe scoring START"
mkdir -p "$SCORES/repoprobe"
rp_arms=()
for arm in "${ARMS[@]}"; do
  if [[ -f "$MARKERS/rp_${arm}.done" ]]; then
    log "RepoProbe SKIP $arm (marker exists)"
    continue
  fi
  sp="$SCORES/repoprobe/${arm}_score.jsonl"
  if [[ -f "$sp" ]] && [[ "$(wc -l < "$sp" | tr -d ' ')" -ge "$RP_N" ]]; then
    log "RepoProbe SKIP $arm (existing score file)"
    echo "preexisting" > "$MARKERS/rp_${arm}.done"
    continue
  fi
  rp_arms+=("$arm")
done
if [[ ${#rp_arms[@]} -eq 0 ]]; then
  log "RepoProbe nothing left to score"
else
  IFS=,
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase2_repoprobe_score_pipeline.py \
    --arms "${rp_arms[*]}" \
    --runs-root "$RP_LINKS" \
    --labels eval_harness/data/datasets/repoprobe-py8/labels.jsonl \
    --out-dir "$SCORES/repoprobe" \
    --scoring-workers "${EVAL_MAX_WORKERS:-1}" \
    2>&1 | grep -v "^  scored" || true
  unset IFS
  for arm in "${rp_arms[@]}"; do
    echo "scored ts=$(date -Is)" > "$MARKERS/rp_${arm}.done"
  done
fi
log "SCORING ALL DONE → $SCORES"
