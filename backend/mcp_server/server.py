from fastapi import FastAPI
from tool_registry import call_tool
import requests
import logging
import signal # in the case that a tool hangs 
from pathlib import Path # For absolute path that is not machine specific

app = FastAPI()

logging.basicConfig(level=logging.INFO)

def ollama_generate(prompt):

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "codellama",
            "prompt": prompt
        }
    )

    return response.json()

# Confirm server is running
@app.get("/")
async def root():
    logging.info("Context Engine is running")
    return {"Context Engine Running"}


# Current pipeline  
def run_pipeline(user_prompt):
    logging.info("Retrieval started")
    context = call_tool("retrieve_context", user_prompt)

    logging.info("Pruning started")
    pruned_context = call_tool("prune_context", context)

    logging.info("Injection started")
    final_prompt = call_tool("inject_context", user_prompt, pruned_context)

    logging.info("Generating response")
    result = ollama_generate(final_prompt)
    return result


# Add API endpoints
@app.post("/index")
def index(repo_path: str, languages: list[str] = None):
    """
    Indexing and Embeddings 
    Note: no indexing in pipeline because it's a one-time setup
    
    Initial test with Flask: (PASS/FAIL)
    Final test with subversion: (PASS/FAIL)
    """
    logging.info("Indexing started")
    BASE_DIR = Path(__file__).resolve().parent.parent  # goes up to backend/
    result = call_tool("index_codebase", repo_path=str(BASE_DIR / "flask_test"), languages=languages)
    logging.info("Indexing complete: %s", result)
    return result

# @app.post("/generate")
# def generate(prompt: str):

#     result = run_pipeline(prompt)

#     return {"output": result}