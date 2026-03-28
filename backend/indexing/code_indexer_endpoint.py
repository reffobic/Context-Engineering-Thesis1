# Endpoint for tool_registry.py 
from .code_indexer import CodeIndexer
from pathlib import Path # For absolute path that is not machine specific

BASE_DIR = Path(__file__).resolve().parent.parent  # goes up to backend/

indexer = CodeIndexer(db_path=str(BASE_DIR / "chroma_db"))

def index_codebase(repo_path: str, languages: list[str] = None, incremental: bool = True) -> dict:
    return indexer.index_repository(repo_path, languages=languages, incremental=incremental)