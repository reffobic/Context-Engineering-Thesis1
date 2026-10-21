"""Check that each query's gold files exist in an indexed repo.

Usage: python check_gold.py <queries.json> <chroma_dir>
Example: python check_gold.py context_metrics/eval_queries_superset_v2.json chroma_db_superset
"""
import json, os, sys
import chromadb

qfile, db = sys.argv[1], sys.argv[2]
col = chromadb.PersistentClient(path=db).get_collection("code_embeddings")

files, offset = set(), 0
while True:
    r = col.get(include=["metadatas"], limit=5000, offset=offset)
    if not r["ids"]:
        break
    files |= {m.get("file_path") for m in r["metadatas"] if m}
    offset += 5000
print(f"{len(files)} distinct files in index {db}\n")

bad = 0
for i, q in enumerate(json.load(open(qfile)), 1):
    found = [g for g in q["gold_files"] if g in files]
    missing = [g for g in q["gold_files"] if g not in files]
    status = "OK " if found else "BAD"
    if not found:
        bad += 1
    print(f"Q{i} {status} found={found}")
    if missing:
        for g in missing:
            base = os.path.basename(g)
            cands = sorted(f for f in files if f and os.path.basename(f) == base)[:5]
            print(f"      not indexed: {g}   same-name files: {cands}")
print(f"\n{bad} queries have NO valid gold file (fix or drop these)")
