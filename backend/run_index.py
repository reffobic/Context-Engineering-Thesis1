from indexing.code_indexer import CodeIndexer
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent / "superset"
indexer = CodeIndexer(db_path=str(Path(__file__).resolve().parent / "chroma_db"))
stats = indexer.index_repository(
    repo_path=str(repo_root),
    languages=["python"],
    max_files=3000,
    incremental=True
)
print(stats)