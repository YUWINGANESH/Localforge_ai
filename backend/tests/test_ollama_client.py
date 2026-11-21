"""Unit tests for the Ollama async client."""
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.config.settings import Settings
from app.llm.ollama_client import OllamaClient


@pytest.mark.asyncio
async def test_ollama_check_health_success():
    """Test successful health check."""
    settings = Settings(ollama_base_url="http://mock-ollama:11434")
    client = OllamaClient(settings=settings)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"version": "0.4.16"}

    with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
        ok, version, error = await client.check_health()
        assert ok is True
        assert version == "0.4.16"
        assert error is None


@pytest.mark.asyncio
async def test_ollama_check_health_connection_error():
    """Test health check when daemon is offline."""
    settings = Settings(ollama_base_url="http://invalid-host:11434")
    client = OllamaClient(settings=settings)

    with patch.object(httpx.AsyncClient, "get", side_effect=httpx.ConnectError("Failed to connect")):
        ok, version, error = await client.check_health()
        assert ok is False
        assert version is None
        assert "Cannot connect" in error
