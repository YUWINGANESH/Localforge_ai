"""Health and diagnostics endpoints."""
import sys
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.config.settings import Settings, get_settings
from app.config.telemetry import get_memory_stats
from app.llm.ollama_client import OllamaClient, get_ollama_client
from app.schemas.health import HealthResponse, OllamaStatus

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def get_health(
    settings: Settings = Depends(get_settings),
    ollama_client: OllamaClient = Depends(get_ollama_client),
) -> HealthResponse:
    """
    Check backend health, runtime environment, and Ollama connection status.
    """
    ollama_ok, version, error = await ollama_client.check_health()
    active_model_installed = False

    if ollama_ok:
        active_model_installed = await ollama_client.is_model_installed(settings.active_model)

    status_str = "healthy" if ollama_ok else "degraded"

    return HealthResponse(
        status=status_str,
        app_name=settings.app_name,
        version=settings.app_version,
        timestamp=datetime.now(UTC),
        python_version=sys.version.split()[0],
        workspace_root=str(settings.get_effective_workspace()),
        ollama=OllamaStatus(
            connected=ollama_ok,
            base_url=settings.ollama_base_url,
            version=version,
            active_model=settings.active_model,
            active_model_installed=active_model_installed,
            error=error,
        ),
        memory=get_memory_stats(),
    )


@router.get("/")
async def root(settings: Settings = Depends(get_settings)):
    """Root status summary."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "online",
        "docs_url": "/docs",
    }
