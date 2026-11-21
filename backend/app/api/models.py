"""Endpoints for inspecting and selecting local AI models."""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.config.settings import Settings, get_settings
from app.llm.ollama_client import (
    OllamaClient,
    OllamaClientError,
    OllamaConnectionError,
    get_ollama_client,
)
from app.schemas.models import ModelListResponse, SetActiveModelRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/models", tags=["Models"])


@router.get("", response_model=ModelListResponse)
async def list_models(
    settings: Settings = Depends(get_settings),
    ollama_client: OllamaClient = Depends(get_ollama_client),
) -> ModelListResponse:
    """
    List all local models installed in the Ollama instance.
    """
    try:
        models = await ollama_client.list_models()
        return ModelListResponse(
            connected=True,
            active_model=settings.active_model,
            models=models,
            total_count=len(models),
            error=None,
        )
    except OllamaConnectionError as exc:
        return ModelListResponse(
            connected=False,
            active_model=settings.active_model,
            models=[],
            total_count=0,
            error=str(exc),
        )
    except OllamaClientError as exc:
        return ModelListResponse(
            connected=False,
            active_model=settings.active_model,
            models=[],
            total_count=0,
            error=f"Ollama error: {exc}",
        )


@router.post("/active")
async def set_active_model(
    payload: SetActiveModelRequest,
    settings: Settings = Depends(get_settings),
    ollama_client: OllamaClient = Depends(get_ollama_client),
):
    """
    Switch the active model for subsequent tasks.
    """
    model_name = payload.model_name.strip()
    if not model_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Model name cannot be empty."
        )

    # Check if Ollama is available and model is installed
    is_installed = await ollama_client.is_model_installed(model_name)

    # Update active model in settings
    settings.active_model = model_name

    return {
        "status": "success",
        "active_model": settings.active_model,
        "is_installed": is_installed,
        "warning": None if is_installed else f"Model '{model_name}' was not detected in local Ollama repository. Run 'ollama pull {model_name}' to download it."
    }
