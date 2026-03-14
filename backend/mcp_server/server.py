from fastapi import FastAPI
from tool_registry import register_tool
import requests
import logging
import signal # in the case that a tool hangs 

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

""""
Once tools are created in each folder, they can be called here using tool_registry like this:

from retrieval.retrieval_tool import retrieve_context
from pruning.pruning_tool import prune_context
from injection.prompt_builder import inject_context

register_tool("retrieve_context", retrieve_context)
register_tool("prune_context", prune_context)
register_tool("inject_context", inject_context)


then...

call_tool("retrieve_context", query)
"""

@app.get("/")
async def root():
    return {"Context Engine Running"}

# Example/tentative main pipeline 
# !!!!!!!!!!! MISSING: add logging e.g. logging.info("retrieval started")
def run_pipeline(user_prompt):

    context = call_tool("retrieve_context", user_prompt)

    pruned_context = call_tool("prune_context", context)

    final_prompt = call_tool("inject_context", user_prompt, pruned_context)

    result = ollama_generate(final_prompt)

    return result


# Add API endpoint
@app.post("/generate")
def generate(prompt: str):

    result = run_pipeline(prompt)

    return {"output": result}
