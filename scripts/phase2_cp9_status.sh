#!/usr/bin/env bash
LOG=/home/g7/Documents/HODA/Context-Engineering-Thesis1/eval_harness/data/logs/phase2-cp9-full.log
OUT=/home/g7/Documents/HODA/Context-Engineering-Thesis1/eval_harness/data/reports/phase2_repoprobe_py/full
n=$(grep -c ' scored ' "$LOG" 2>/dev/null || true)
n=${n:-0}
pass=$(grep -c '\[PASS\]' "$LOG" 2>/dev/null || true)
pass=${pass:-0}
echo "scored: ${n} / 756  arms_done: ${pass} / 7"
grep -E '^--- |\[PASS\]|=== done|FAIL|Traceback' "$LOG" 2>/dev/null | tail -20
if [[ -f "$OUT/summary.json" ]]; then
  echo "SUMMARY READY"
  python3 -c "import json;d=json.load(open('$OUT/summary.json'));print([(a['arm'], round(a['overall_pct_mean'],1) if a.get('overall_pct_mean') is not None else None) for a in d.get('arms',[])])"
fi
pgrep -af phase2_repoprobe_score_pipeline | grep -v grep || echo 'scorer: not running'
