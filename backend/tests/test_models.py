"""Tests for model catalog and selection endpoints."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.llm.ollama_client import OllamaConnectionError, get_ollama_client
from app.main import app
from app.schemas.models import ModelInfo


@pytest.mark.asyncio
async def test_list_models_success():
    """Verify listing models when Ollama is available."""
    mock_ollama = AsyncMock()
    sample_model = ModelInfo(
        name="qwen2.5-coder:7b",
        size_bytes=4700000000,
        modified_at=datetime.now(UTC),
        format="gguf",
        family="qwen2",
        parameter_size="7.6B",
        quantization_level="Q4_K_M",
        is_active=True,
    )
    mock_ollama.list_models.return_value = [sample_model]

    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/models")
            assert response.status_code == 200
            data = response.json()
            assert data["connected"] is True
            assert data["total_count"] == 1
            assert len(data["models"]) == 1
            assert data["models"][0]["name"] == "qwen2.5-coder:7b"
            assert data["models"][0]["is_active"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_models_ollama_offline():
    """Verify listing models gracefully handles offline Ollama."""
    mock_ollama = AsyncMock()
    mock_ollama.list_models.side_effect = OllamaConnectionError("Connection refused")

    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/models")
            assert response.status_code == 200
            data = response.json()
            assert data["connected"] is False
            assert data["total_count"] == 0
            assert "Connection refused" in data["error"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_set_active_model():
    """Verify switching the active model."""
    mock_ollama = AsyncMock()
    mock_ollama.is_model_installed.return_value = True

    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/models/active", json={"model_name": "qwen3:4b"})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["active_model"] == "qwen3:4b"
            assert data["is_installed"] is True
            assert data["warning"] is None
    finally:
        app.dependency_overrides.clear()
