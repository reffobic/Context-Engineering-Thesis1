# Endpoint for tool_registry.py 
from pathlib import Path
from retrieval_tool import retrieve_context as _retrieve

def retrieve_context(query: str, top_k: int = 5) -> list[dict]:
    return _retrieve(query, top_k=top_k)