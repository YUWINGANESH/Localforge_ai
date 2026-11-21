"""Tests for the runtime settings inspection and update endpoints."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import Settings, get_settings
from app.main import app


@pytest.mark.asyncio
async def test_get_settings():
    """Verify GET /api/settings returns all key configuration fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        assert "app_name" in data
        assert "active_model" in data
        assert "max_reasoning_turns" in data
        assert "max_repair_attempts" in data
        assert "context_window" in data


@pytest.mark.asyncio
async def test_patch_settings_valid():
    """Verify PATCH /api/settings hot-updates mutable fields."""
    test_settings = Settings()
    app.dependency_overrides[get_settings] = lambda: test_settings

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "max_reasoning_turns": 24,
                "max_repair_attempts": 6,
                "context_window": 8192,
                "request_timeout_seconds": 90.0,
            }
            response = await client.patch("/api/settings", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "updated"
            assert "max_reasoning_turns" in data["changed_fields"]
            assert data["current"]["max_reasoning_turns"] == 24
            assert data["current"]["max_repair_attempts"] == 6
            assert data["current"]["context_window"] == 8192
            assert data["current"]["request_timeout_seconds"] == 90.0

            # Verify in the settings instance
            assert test_settings.max_reasoning_turns == 24
            assert test_settings.max_repair_attempts == 6
            assert test_settings.context_window == 8192
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_settings_validation_bounds():
    """Verify PATCH /api/settings rejects out-of-range bounds."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # max_reasoning_turns must be ge=4, le=64
        response = await client.patch("/api/settings", json={"max_reasoning_turns": 1})
        assert response.status_code == 422

        # max_repair_attempts must be ge=1, le=10
        response = await client.patch("/api/settings", json={"max_repair_attempts": 99})
        assert response.status_code == 422
