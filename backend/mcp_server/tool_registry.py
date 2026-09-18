from indexing.code_indexer_endpoint import index_codebase
from retrieval.retrieval_tool_endpoint import retrieve_context
from pruning.pruning_tool_endpoint import prune_context
from injection.prompt_builder_endpoint import inject_context
from agent.agent_endpoint import ollama_generate 
from diamond.sufficiency_assessor import assess_sufficiency

tools = {} # Populated dynamically  

def register_tool(name, function):
    tools[name] = function

def call_tool(name, *args, **kwargs):
    if name not in tools:
        raise ValueError(f"Tool {name!r} not registered")
    return tools[name](*args, **kwargs)


# Indexing and Embeddings----------------------------------------------------
from pathlib import Path # For absolute path that is not machine specific
BASE_DIR = Path(__file__).resolve().parent.parent  # goes up to backend/

register_tool("index_codebase", index_codebase)
#--------------------------------------------------------------------------


# Retrieval ----------------------------------------------------------------
register_tool("retrieve_context", retrieve_context)
#--------------------------------------------------------------------------

# Sufficiency Assessment ---------------------------------------------------
register_tool("assess_sufficiency", assess_sufficiency)
#--------------------------------------------------------------------------

# Pruning ----------------------------------------------------------------
register_tool("prune_context", prune_context)
#--------------------------------------------------------------------------


# Graph visualization (from graph-embeddings) -----------------------------
def _load_visualize_graph():
    from retrieval.graph_visualization_tool import visualize_graph
    return visualize_graph


def visualize_graph_wrapper(*args, **kwargs):
    return _load_visualize_graph()(*args, **kwargs)


register_tool("visualize_graph", visualize_graph_wrapper)
#--------------------------------------------------------------------------


# Injection ----------------------------------------------------------------
register_tool("inject_context", inject_context)
#--------------------------------------------------------------------------


# Agent ----------------------------------------------------------------
register_tool("ollama_generate", ollama_generate)
#--------------------------------------------------------------------------

