"""Person 5 — LLM agent (Ollama)."""

from .ollama_agent import OllamaAgent, generate_response, get_shared_agent

__all__ = ["OllamaAgent", "generate_response", "get_shared_agent"]
