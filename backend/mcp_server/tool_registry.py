tools = {}

def register_tool(name, function):
    tools[name] = function

def call_tool(name, *args, **kwargs):
    if name not in tools:
        raise Exception(f"Tool {name} not registered")
    return tools[name](*args, **kwargs)


"""""
Expected tools' interface

def retrieve_context(query: str):
    pass

def prune_context(context: str):
    pass

def inject_context(prompt: str, context: str):
    pass

def search_codebase(query: str):
    pass
"""

