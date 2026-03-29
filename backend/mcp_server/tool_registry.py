from indexing.code_indexer_endpoint import index_codebase
from retrieval.retrieval_tool_endpoint import retrieve_context
from injection.prompt_builder_endpoint import inject_context
from agent.agent_endpoint import ollama_generate 

tools = {} # Populated dynamically  

def register_tool(name, function):
    tools[name] = function

def call_tool(name, *args, **kwargs):
    if name not in tools:
        raise Exception(f"Tool {name} not registered")
    return tools[name](*args, **kwargs)


# Indexing and Embeddings----------------------------------------------------
from pathlib import Path # For absolute path that is not machine specific
BASE_DIR = Path(__file__).resolve().parent.parent  # goes up to backend/

register_tool("index_codebase", index_codebase)
#--------------------------------------------------------------------------


# Retrieval ----------------------------------------------------------------
register_tool("retrieve_context", retrieve_context)
#--------------------------------------------------------------------------


# Pruning (passthrough for now) ------------------------------------------------
def prune_context(context):
    return context  # passthrough for now

register_tool("prune_context", prune_context)
#--------------------------------------------------------------------------


# Injection ----------------------------------------------------------------
register_tool("inject_context", inject_context)
#--------------------------------------------------------------------------


# Agent ----------------------------------------------------------------
register_tool("ollama_generate", ollama_generate)
#--------------------------------------------------------------------------

