#!/usr/bin/env bash
# 1-task paid smoke: ALWAYS_RAG · flask-000 → ~/haya/api_smoke
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENVF="${API_GEN_ENV:-$HOME/haya/api_gen.env}"
if [[ -f "$ENVF" ]]; then set -a; # shellcheck disable=SC1090
  source "$ENVF"; set +a
fi
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY or source $HOME/haya/api_gen.env}"
: "${HARNESS_PROVIDER_CONFIG:=$ROOT/eval_harness/configs/provider_openrouter.toml}"
export HARNESS_PROVIDER_CONFIG
OUT="${1:-$HOME/haya/api_smoke/ALWAYS_RAG_flask000}"
mkdir -p "$(dirname "$OUT")"
CFG=eval_harness/configs
echo "[$(date +%H:%M:%S)] API smoke ALWAYS_RAG flask-000 → $OUT"
PYTHONPATH=eval_harness/src python3 -m harness.run \
  --profile eval_harness/profiles/ubuntu-4080.toml \
  --dataset swe-qa-w1 --model qwen3.5:9b --arm ALWAYS_RAG \
  --sample eval_harness/data/samples/swe-qa-w1.smoke.json \
  --out "$OUT" \
  --experiment $CFG/experiment.toml --pipeline-config $CFG/pipeline_phase2.toml \
  --index-config $CFG/index.toml --run-config $CFG/run.toml \
  --smoke --allow-dirty --limit 1
echo "[$(date +%H:%M:%S)] SMOKE DONE"
# quick check
python3 - <<'PY'
import json, os, sys
from pathlib import Path
out=Path(os.environ.get("OUT_DIR","")) 
PY
python3 - "$OUT" <<'PY'
import json,sys
from pathlib import Path
out=Path(sys.argv[1])
rp=out/"results.jsonl"
if not rp.exists():
  print("FAIL: no results.jsonl"); sys.exit(1)
o=json.loads(rp.read_text().splitlines()[0])
ans=(o.get("answer") or "").strip()
calls=o.get("ollama_calls") or []
pu=None
for c in calls:
  if c.get("role")=="answer":
    pu=(c.get("provider_usage") or {})
print("status", o.get("status"), "route", o.get("route"), "answer_len", len(ans))
print("model_logged", (calls[-1].get("model") if calls else None))
print("provider_usage", pu)
print("inject_tok", (o.get("inject") or {}).get("est_tokens_div4"))
if not ans:
  print("FAIL: empty answer"); sys.exit(1)
print("SMOKE OK")
PY
