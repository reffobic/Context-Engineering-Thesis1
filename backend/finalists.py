import json, os, re, subprocess
Q = "context_metrics/eval_queries_scrapy_v2.json"
d = {t["trial"]: t for t in json.load(open("context_metrics/pareto_v2_results.json"))}
pat = re.compile(r"final order \(pipeline\):\s+any gold in top5 (\d+/\d+)\s+top20 (\d+/\d+)\s+top100 (\d+/\d+)\s+recall@5 ([\d.]+)")
FREEZE = {"FINAL_W_HYBRID": "1", "FINAL_W_PROXIMITY": "0", "FINAL_W_FUNCTION": "0", "FINAL_W_SYMBOL": "0"}
subprocess.run(["ln", "-sfn", "chroma_db_scrapy", "chroma_db"])
for tid in [25, 17, 13, 20]:
    for variant in ("as-is", "no-rerank"):
        env = dict(os.environ)
        env.update({k: str(v) for k, v in d[tid]["params"].items()})
        if variant == "no-rerank":
            env.update(FREEZE)
        out = subprocess.run(["python", "-m", "context_metrics.probe_retrieval", "--queries", Q],
                             env=env, capture_output=True, text=True).stdout
        m = pat.search(out)
        print("trial", tid, variant, m.groups() if m else "ERR", flush=True)
