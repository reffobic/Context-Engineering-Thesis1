tools = {} # Populated dynamically  

def register_tool(name, function):
    tools[name] = function

def call_tool(name, *args, **kwargs):
    if name not in tools:
        raise ValueError(f"Tool {name!r} not registered")
    return tools[name](*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────
# Lazy imports: Defer heavy dependencies (chromadb, sentence-transformers)
# ─────────────────────────────────────────────────────────────────────────

from pathlib import Path # For absolute path that is not machine specific
BASE_DIR = Path(__file__).resolve().parent.parent  # goes up to backend/


# Indexing and Embeddings (P2)-------------------------------------------
def _load_index_codebase():
    from indexing.code_indexer_endpoint import index_codebase
    return index_codebase

def index_codebase_wrapper(*args, **kwargs):
    return _load_index_codebase()(*args, **kwargs)

register_tool("index_codebase", index_codebase_wrapper)
#--------------------------------------------------------------------------


# Retrieval (P3) with Graph-based Structure-------------------------------
def _load_retrieve_context():
    from retrieval.retrieval_tool_endpoint import retrieve_context
    return retrieve_context

def retrieve_context_wrapper(*args, **kwargs):
    return _load_retrieve_context()(*args, **kwargs)

register_tool("retrieve_context", retrieve_context_wrapper)
#--------------------------------------------------------------------------


# Graph Visualization (for understanding code structure)-------------------
def _load_visualize_graph():
    from retrieval.graph_visualization_tool import visualize_graph
    return visualize_graph

def visualize_graph_wrapper(*args, **kwargs):
    return _load_visualize_graph()(*args, **kwargs)

register_tool("visualize_graph", visualize_graph_wrapper)
# Usage: call_tool("visualize_graph", output_format="html|json|dot|ascii")
#--------------------------------------------------------------------------


# Pruning (P4)-----------------------------------------------------------
def _load_prune_context():
    from pruning.pruning_tool_endpoint import prune_context
    return prune_context

def prune_context_wrapper(*args, **kwargs):
    return _load_prune_context()(*args, **kwargs)

register_tool("prune_context", prune_context_wrapper)
#--------------------------------------------------------------------------


# Injection (P5)-------------------------------------------------------------
def _load_inject_context():
    from injection.prompt_builder_endpoint import inject_context
    return inject_context

def inject_context_wrapper(*args, **kwargs):
    return _load_inject_context()(*args, **kwargs)

register_tool("inject_context", inject_context_wrapper)
#--------------------------------------------------------------------------


# Agent (LLM)-------------------------------------------------------------
def _load_ollama_generate():
    from agent.agent_endpoint import ollama_generate
    return ollama_generate

def ollama_generate_wrapper(*args, **kwargs):
    return _load_ollama_generate()(*args, **kwargs)

register_tool("ollama_generate", ollama_generate_wrapper)
#--------------------------------------------------------------------------

