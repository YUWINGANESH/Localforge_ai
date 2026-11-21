"""Integration test with live running Ollama (if available)."""
import pytest

from app.llm.ollama_client import OllamaClient


@pytest.mark.asyncio
async def test_live_ollama_stream_if_available():
    """Verify live token generation against local Ollama if it is online."""
    client = OllamaClient()
    online, version, _ = await client.check_health()
    if not online:
        pytest.skip("Ollama is offline. Skipping live model generation test.")

    # Check if active model exists
    has_model = await client.is_model_installed(client.settings.active_model)
    if not has_model:
        pytest.skip(f"Model {client.settings.active_model} is not pulled.")

    tokens = []
    messages = [
        {"role": "system", "content": "You are a concise coding assistant."},
        {"role": "user", "content": "Return only: Hello from LocalForge"},
    ]

    async for token in client.chat_stream(messages=messages, temperature=0.1):
        tokens.append(token)
        if len(tokens) >= 5:  # Got at least 5 tokens from real model
            break

    assert len(tokens) > 0, "Expected at least one token from live Ollama model"
