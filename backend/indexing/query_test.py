from indexing.code_indexer import CodeIndexer

indexer = CodeIndexer(db_path='./subversion_db')

indexer.index_repository('../data/subversion', languages=['python', 'c'])

results = indexer.query('log in', top_k=5)

for i, r in enumerate(results, 1):
    print(f"[{i}] {r['file_path']} :: {r['name']} (dist={r['distance']:.4f})")
    print(f"     {r['code'][:100].strip()}")
    print()