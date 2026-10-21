"""Run baselines and finalists on one repo with the fast retrieval probe.

Usage: python finalists_repo.py <repo> <queries.json>
Example: python finalists_repo.py superset context_metrics/eval_queries_superset_v2.json
"""
import json, os, re, subprocess, sys

repo, Q = sys.argv[1], sys.argv[2]
d = {t["trial"]: t for t in json.load(open("context_metrics/pareto_v2_results.json"))}
pat = re.compile(r"final order \(pipeline\):\s+any gold in top5 (\d+/\d+)\s+top20 (\d+/\d+)\s+top100 (\d+/\d+)\s+recall@5 ([\d.]+)")
FREEZE = {"FINAL_W_HYBRID": "1", "FINAL_W_PROXIMITY": "0", "FINAL_W_FUNCTION": "0", "FINAL_W_SYMBOL": "0"}

subprocess.run(["ln", "-sfn", f"chroma_db_{repo}", "chroma_db"])

def run(label, params, freeze):
    env = dict(os.environ)
    env.update({k: str(v) for k, v in params.items()})
    if freeze:
        env.update(FREEZE)
    out = subprocess.run(["python", "-m", "context_metrics.probe_retrieval", "--queries", Q],
                         env=env, capture_output=True, text=True).stdout
    m = pat.search(out)
    print(repo, label, m.groups() if m else "ERR", flush=True)

run("defaults", d[0]["params"], False)
run("C (defaults, no rerank)", d[0]["params"], True)
for tid in [13, 25, 17, 20]:
    run(f"trial {tid} as-is", d[tid]["params"], False)
    run(f"trial {tid} no-rerank", d[tid]["params"], True)

subprocess.run(["ln", "-sfn", "chroma_db_scrapy", "chroma_db"])
