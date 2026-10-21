"""
gasr_vs_preroute_slide_numbers.py

Fills in ONLY the two rows tonight's data actually supports: Tokens
(tokens_injected_est) and Wall time (duration_ms). Faithfulness,
Precision/Recall, and Answer relevancy are deliberately NOT computed here --
none of them are available from what live_modes_compare_suite.py saved
tonight (see the message this script came with for why).

Run from the repo root:
    python3 scripts/gasr_vs_preroute_slide_numbers.py
"""

import json
import glob

path = sorted(glob.glob('results/live_modes_compare/modes_compare_run_*.json'))[-1]
data = json.load(open(path))
print(f"Source: {path}\n")

by_mode = {"GASR": [], "PREROUTE": []}
for row in data['results']:
    if row['mode'] not in by_mode or not row.get('ok'):
        continue
    by_mode[row['mode']].append(row)

n_gasr, n_pre = len(by_mode["GASR"]), len(by_mode["PREROUTE"])
print(f"GASR: n={n_gasr}   PREROUTE: n={n_pre}")
if n_gasr != n_pre:
    print("NOTE: unequal n -- some case(s) failed in one mode but not the "
          "other. Check data['results'] for 'ok': false rows before citing "
          "these as directly comparable means.\n")

def avg_summary(rows, key):
    vals = [r['summary'].get(key) for r in rows if r['summary'].get(key) is not None]
    return sum(vals) / len(vals) if vals else None

def avg_top_level(rows, key):
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None

def pct_delta(self_val, other_val):
    if self_val in (None, 0):
        return None
    return (other_val - self_val) / self_val * 100

tok_gasr = avg_summary(by_mode["GASR"], "tokens_injected_est")
tok_pre = avg_summary(by_mode["PREROUTE"], "tokens_injected_est")
wall_gasr = avg_top_level(by_mode["GASR"], "duration_ms")
wall_pre = avg_top_level(by_mode["PREROUTE"], "duration_ms")

def fmt(v, spec="{:.0f}"):
    return spec.format(v) if v is not None else "N/A"

def fmt_delta(v):
    return f"{v:+.1f}%" if v is not None else "N/A"

print("--- Metric | GASR | PREROUTE | % Delta (PREROUTE vs GASR) ---")
print(f"Tokens     | {fmt(tok_gasr)} | {fmt(tok_pre)} | {fmt_delta(pct_delta(tok_gasr, tok_pre))}")
print(f"Wall time  | {fmt(wall_gasr)} | {fmt(wall_pre)} | {fmt_delta(pct_delta(wall_gasr, wall_pre))}")

print("\nFaithfulness, Precision, Recall, Answer relevancy: NOT computed "
      "from this data. See accompanying message for why each is missing.")