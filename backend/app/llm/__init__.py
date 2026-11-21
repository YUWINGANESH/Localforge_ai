"""LLM integration package for LocalForge-AI 2.0."""
from app.llm.ollama_client import OllamaClient, get_ollama_client

__all__ = ["OllamaClient", "get_ollama_client"]
