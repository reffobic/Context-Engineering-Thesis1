# Endpoint for tool_registry.py
from pathlib import Path # For absolute path that is not machine specific

from .code_indexer import CodeIndexer, LANGUAGE_MAP

BASE_DIR = Path(__file__).resolve().parent.parent  # goes up to backend/

indexer = CodeIndexer(db_path=str(BASE_DIR / "chroma_db"))

def index_codebase(
    repo_path: str,
    languages: list[str] = None,
    incremental: bool = True,
    max_files: int | None = None,
    rebuild_bm25: bool = False,
    include_dirs: list[str] | None = None,
) -> dict:
    return indexer.index_repository(
        repo_path,
        languages=languages,
        incremental=incremental,
        max_files=max_files,
        rebuild_bm25=rebuild_bm25,
        include_dirs=include_dirs,
    )


def _language_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    for language, extensions in LANGUAGE_MAP.items():
        if suffix in extensions:
            return language
    return "unknown"


def get_index_summary(file_limit: int = 500) -> dict:
    """
    Presentation/debug summary of the persistent code index.

    Uses Chroma metadata for chunk coverage and the incremental hash cache for
    the file list, so the UI can show what was indexed without reading SQLite.
    """
    stats = indexer.index_stats()
    cache = indexer._load_hash_cache()
    indexer._hash_cache = cache

    files: dict[str, dict] = {}
    by_extension: dict[str, int] = {}

    for path in sorted(cache):
        suffix = Path(path).suffix.lower() or "(none)"
        by_extension[suffix] = by_extension.get(suffix, 0) + 1
        files[path] = {
            "path": path,
            "language": _language_from_path(path),
            "extension": suffix,
            "chunks": 0,
            "chunk_types": {},
            "symbols": [],
            "start_line": None,
            "end_line": None,
        }

    total_chunks = stats.get("total_chunks", 0)
    batch_size = 2000
    all_metadatas = []
    for offset in range(0, total_chunks, batch_size):
        try:
            batch = indexer.collection.get(include=["metadatas"], limit=batch_size, offset=offset)
            all_metadatas.extend(batch.get("metadatas", []) or [])
        except Exception as exc:
            break

    for meta in all_metadatas:
        path = meta.get("file_path", "") or "(unknown path)"
        entry = files.setdefault(
            path,
            {
                "path": path,
                "language": meta.get("language") or _language_from_path(path),
                "extension": Path(path).suffix.lower() or "(none)",
                "chunks": 0,
                "chunk_types": {},
                "symbols": [],
                "start_line": None,
                "end_line": None,
            },
        )
        entry["chunks"] += 1
        chunk_type = meta.get("chunk_type") or "unknown"
        entry["chunk_types"][chunk_type] = entry["chunk_types"].get(chunk_type, 0) + 1

        name = meta.get("name")
        if name and name not in entry["symbols"] and len(entry["symbols"]) < 8:
            entry["symbols"].append(name)

        start = meta.get("start_line")
        end = meta.get("end_line")
        if isinstance(start, int):
            entry["start_line"] = start if entry["start_line"] is None else min(entry["start_line"], start)
        if isinstance(end, int):
            entry["end_line"] = end if entry["end_line"] is None else max(entry["end_line"], end)

    file_list = sorted(files.values(), key=lambda item: item["path"])
    total_files = len(file_list)

    return {
        "db_path": str(indexer.db_path),
        "cache_path": str(indexer._cache_path()),
        "bm25_path": stats.get("bm25_path"),
        "bm25_exists": stats.get("bm25_exists"),
        "collection": "code_embeddings",
        "model": stats.get("model"),
        "total_chunks": stats.get("total_chunks", 0),
        "by_language": stats.get("by_language", {}),
        "by_chunk_type": stats.get("by_chunk_type", {}),
        "indexed_files_total": total_files,
        "indexed_files_returned": min(total_files, file_limit),
        "by_extension": by_extension,
        "files": file_list[:file_limit],
    }