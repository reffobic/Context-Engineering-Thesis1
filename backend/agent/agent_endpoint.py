from .ollama_agent import OllamaAgent

agent = OllamaAgent()  # initialize once

def ollama_generate(prompt: str) -> str:
    return agent.generate_response(prompt)