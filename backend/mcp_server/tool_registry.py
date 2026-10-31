from indexing.code_indexer_endpoint import index_codebase
from retrieval.retrieval_tool_endpoint import retrieve_context
from retrieval.retrieval_tool import list_files, find_symbol, find_references
from pruning.pruning_tool_endpoint import prune_context
from injection.prompt_builder_endpoint import inject_context
from agent.agent_endpoint import ollama_generate
from diamond.sufficiency_assessor import (
    assess_sufficiency,
    assess_rag_gate,
    assess_structure_vs_context,
)

tools = {}  # Populated dynamically


def register_tool(name, function):
    tools[name] = function


def call_tool(name, *args, **kwargs):
    if name not in tools:
        raise ValueError(f"Tool {name!r} not registered")
    return tools[name](*args, **kwargs)


# Indexing and Embeddings----------------------------------------------------
from pathlib import Path  # For absolute path that is not machine specific

BASE_DIR = Path(__file__).resolve().parent.parent  # goes up to backend/

register_tool("index_codebase", index_codebase)
# --------------------------------------------------------------------------


# Retrieval ----------------------------------------------------------------
register_tool("retrieve_context", retrieve_context)
register_tool("list_files", list_files)
register_tool("find_symbol", find_symbol)
register_tool("find_references", find_references)


def _load_graph_expand():
    from retrieval.graph_expand import graph_expand_tool

    return graph_expand_tool


def graph_expand_wrapper(*args, **kwargs):
    return _load_graph_expand()(*args, **kwargs)


register_tool("graph_expand", graph_expand_wrapper)
# --------------------------------------------------------------------------

# Sufficiency Assessment ---------------------------------------------------
register_tool("assess_sufficiency", assess_sufficiency)
register_tool("assess_rag_gate", assess_rag_gate)
register_tool("assess_structure_vs_context", assess_structure_vs_context)
# --------------------------------------------------------------------------

# Pruning ----------------------------------------------------------------
register_tool("prune_context", prune_context)
# --------------------------------------------------------------------------


# Graphify visualization ---------------------------------------------------
def _load_visualize_graphify():
    from retrieval.graphify_viz import visualize_graphify

    return visualize_graphify


def visualize_graphify_wrapper(*args, **kwargs):
    return _load_visualize_graphify()(*args, **kwargs)


register_tool("visualize_graphify", visualize_graphify_wrapper)
# --------------------------------------------------------------------------


# Injection ----------------------------------------------------------------
register_tool("inject_context", inject_context)
# --------------------------------------------------------------------------


# Agent ----------------------------------------------------------------
register_tool("ollama_generate", ollama_generate)
# --------------------------------------------------------------------------
