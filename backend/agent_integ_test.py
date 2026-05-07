from mcp_server.tool_registry import call_tool

query = "How does Flask handle routing?"

# Step 1 — retrieve
print("=== Retrieval ===")
chunks = call_tool("retrieve_context", query)
print(f"Got {len(chunks)} chunks")

# Step 2 — prune
print("\n=== Pruning ===")
pruned = call_tool("prune_context", query, chunks)
print(f"Pruned to {len(pruned)} chunks")

# Step 3 — inject
print("\n=== Injection ===")
prompt = call_tool("inject_context", query, pruned)
if isinstance(prompt, dict):
    print(f"Prompt: system={len(prompt['system'])} chars, user={len(prompt['user'])} chars")
else:
    print(f"Prompt length: {len(prompt)} chars")

# Step 4 — generate
print("\n=== Generation ===")
result = call_tool("ollama_generate", prompt)
print(result)