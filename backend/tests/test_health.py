"""Tests for health and diagnostics endpoints."""
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.llm.ollama_client import get_ollama_client
from app.main import app


@pytest.mark.asyncio
async def test_root_endpoint():
    """Verify root endpoint returns app metadata."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "LocalForge-AI"
        assert data["status"] == "online"
        assert data["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_health_endpoint_ollama_online():
    """Verify /health returns healthy status when Ollama responds."""
    mock_ollama = AsyncMock()
    mock_ollama.check_health.return_value = (True, "0.4.16", None)
    mock_ollama.is_model_installed.return_value = True

    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["app_name"] == "LocalForge-AI"
            assert data["ollama"]["connected"] is True
            assert data["ollama"]["version"] == "0.4.16"
            assert data["ollama"]["active_model_installed"] is True
            assert data["ollama"]["error"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_endpoint_ollama_offline():
    """Verify /health returns degraded status cleanly without throwing 500 when Ollama is down."""
    mock_ollama = AsyncMock()
    mock_ollama.check_health.return_value = (False, None, "Cannot connect to Ollama")
    mock_ollama.is_model_installed.return_value = False

    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "degraded"
            assert data["ollama"]["connected"] is False
            assert data["ollama"]["error"] == "Cannot connect to Ollama"
    finally:
        app.dependency_overrides.clear()
