from mcp_server.tool_registry import call_tool

# Step 1 — retrieve
print("=== Retrieval ===")
chunks = call_tool("retrieve_context", "function that handles routing")
print(f"Got {len(chunks)} chunks")

# Step 2 — prune (stub for now)
print("\n=== Pruning (stub) ===")
pruned = call_tool("prune_context", chunks)
print("Passthrough — no pruning applied")

# Step 3 — inject
print("\n=== Injection ===")
prompt = call_tool("inject_context", "How does Flask handle routing?", pruned)
if isinstance(prompt, dict):
    print(f"Prompt: system={len(prompt['system'])} chars, user={len(prompt['user'])} chars")
else:
    print(f"Prompt length: {len(prompt)} chars")

# Step 4 — generate
print("\n=== Generation ===")
result = call_tool("ollama_generate", prompt)
print(result)