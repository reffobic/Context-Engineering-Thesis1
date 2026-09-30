"""
token_usage_report.py

Reads the latest live_modes_compare_suite.py results JSON and reports
token usage (prompt + completion tokens for the assess/gate LLM call only,
not the final generation call) per mode.

Run from the repo root:
    python3 token_usage_report.py
"""

import json
import glob

path = sorted(glob.glob('results/live_modes_compare/modes_compare_run_*.json'))[-1]
data = json.load(open(path))
print(f"Source: {path}\n")


def get_tokens(row):
    # PREROUTE stores it at entry_gate_token_usage (top-level in the summary).
    # GASR doesn't hoist it to the top level, but it's nested in trace[0]
    # (the same decision dict assess_sufficiency returns, carried through
    # by run_gasr_ladder's bookkeeping) -- check both.
    asum = row['summary'].get('assess_summary') or {}
    tu = asum.get('entry_gate_token_usage') or asum.get('token_usage')
    if tu:
        return tu
    trace = asum.get('trace') or []
    if trace and isinstance(trace, list):
        return trace[0].get('token_usage')
    return None


by_mode = {}
missing = {}
for row in data['results']:
    if not row.get('ok'):
        continue
    mode = row['mode']
    tu = get_tokens(row)
    if tu and tu.get('prompt_tokens') is not None:
        by_mode.setdefault(mode, []).append(tu)
    else:
        missing[mode] = missing.get(mode, 0) + 1

print("--- Token usage by mode (assess/gate call only) ---")
for mode in ('GASR', 'SELF_ROUTE', 'PREROUTE'):
    rows = by_mode.get(mode, [])
    if not rows:
        print(f"{mode:<12} NO TOKEN DATA CAPTURED ({missing.get(mode, 0)} rows) "
              f"-- sufficiency_assessor.py patch likely not applied or server not restarted after it")
        continue
    n = len(rows)
    avg_prompt = sum(r['prompt_tokens'] for r in rows) / n
    avg_completion = sum(r['completion_tokens'] for r in rows) / n
    print(f"{mode:<12} n={n:<3} avg_prompt={avg_prompt:.0f}  "
          f"avg_completion={avg_completion:.0f}  avg_total={avg_prompt + avg_completion:.0f}")