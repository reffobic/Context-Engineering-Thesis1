from mcp_server.tool_registry import call_tool
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Step 1 — retrieve
print("=== Retrieval ===")
chunks = call_tool("retrieve_context", "function that handles routing")
print(f"Got {len(chunks)} chunks")

# Step 2 — inject
print("\n=== Injection ===")
prompt = call_tool("inject_context", "How does Flask handle routing?", chunks)
if isinstance(prompt, dict):
    print({k: f"{len(v)} chars" for k, v in prompt.items()})
else:
    print(prompt)