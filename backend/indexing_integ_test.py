from mcp_server.tool_registry import call_tool
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

result = call_tool(
    "index_codebase",
    repo_path=str(BASE_DIR.parent / "flask_test"),
    languages=["python"]
)

print(result)