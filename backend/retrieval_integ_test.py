from mcp_server.tool_registry import call_tool
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Step 1 — index (should skip everything if already indexed)
print("=== Indexing ===")
index_result = call_tool(
    "index_codebase",
    repo_path=str(BASE_DIR.parent / "flask_test"),
    languages=["python"]
)
print(index_result)

# Step 2 — retrieve
print("\n=== Retrieval ===")
results = call_tool("retrieve_context", "function that handles routing")

for i, r in enumerate(results, 1):
    print(f"\n[{i}] {r['metadata']['file']} :: {r['metadata']['function']}")
    print(f"    final_score={r['final_score']}  similarity={r['similarity']}")
    print(f"    code preview: {r['code'][:100]}")
    print(f"    parent_context available: {r.get('parent_context') is not None}")