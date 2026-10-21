import json, os, re, subprocess
front = [0] + [int(x) for x in open("pareto_front.txt").read().split(",")]
d = {t["trial"]: t for t in json.load(open("context_metrics/pareto_v2_results.json"))}
REPOS = {"scrapy": "context_metrics/eval_queries_scrapy_v2.json",
         "superset": "context_metrics/eval_queries_superset.json"}
pat = re.compile(r"final order \(pipeline\):\s+any gold in top5 (\d+/\d+)\s+top20 (\d+/\d+)\s+top100 (\d+/\d+)\s+recall@5 ([\d.]+)")
for repo, q in REPOS.items():
    subprocess.run(["ln", "-sfn", f"chroma_db_{repo}", "chroma_db"])
    for tid in front:
        env = dict(os.environ); env.update({k: str(v) for k, v in d[tid]["params"].items()})
        out = subprocess.run(["python", "-m", "context_metrics.probe_retrieval", "--queries", q],
                             env=env, capture_output=True, text=True).stdout
        m = pat.search(out)
        print(repo, "trial", tid, m.groups() if m else "ERR", flush=True)
