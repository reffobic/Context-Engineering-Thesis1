from indexing.code_indexer import CodeIndexer
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
indexer = CodeIndexer(db_path='./superset_chroma_db')
stats = indexer.index_repository(
    repo_path=r'C:\Users\hodam\Downloads\Context-Engineering-Thesis1\superset',
    incremental=True
)
print(stats)