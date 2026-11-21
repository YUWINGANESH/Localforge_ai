"""Runtime settings inspection and hot-update endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config.settings import Settings, get_settings

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class SettingsPatchRequest(BaseModel):
    """Fields that the VS Code extension may update at runtime without a restart."""
    default_model: str | None = Field(None, description="Ollama model name")
    active_model: str | None = Field(None, description="Alias for default_model")
    context_window: int | None = Field(None, ge=512, le=32768)
    max_reasoning_turns: int | None = Field(None, ge=4, le=64)
    max_repair_attempts: int | None = Field(None, ge=1, le=10)
    request_timeout_seconds: float | None = Field(None, ge=10, le=600)
    max_concurrent_tasks: int | None = Field(None, ge=1, le=4)


@router.get("")
async def get_current_settings(settings: Settings = Depends(get_settings)):
    """Return the current effective backend settings."""
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "ollama_base_url": settings.ollama_base_url,
        "default_model": settings.default_model,
        "active_model": settings.active_model,
        "fallback_model": settings.fallback_model,
        "context_window": settings.context_window,
        "max_reasoning_turns": settings.max_reasoning_turns,
        "max_repair_attempts": settings.max_repair_attempts,
        "max_concurrent_tasks": settings.max_concurrent_tasks,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "workspace_root": settings.workspace_root,
        "database_path": settings.database_path,
        "log_level": settings.log_level,
    }


@router.patch("")
async def patch_settings(
    payload: SettingsPatchRequest,
    settings: Settings = Depends(get_settings),
):
    """
    Hot-update mutable runtime settings without restarting the backend.
    Changes persist only for the lifetime of this process — they are not
    written back to the .env file.
    """
    changed: dict[str, object] = {}

    model_name = payload.active_model or payload.default_model
    if model_name is not None:
        settings.default_model = model_name
        settings.active_model = model_name
        changed["active_model"] = model_name

    if payload.context_window is not None:
        settings.context_window = payload.context_window
        changed["context_window"] = payload.context_window

    if payload.max_reasoning_turns is not None:
        settings.max_reasoning_turns = payload.max_reasoning_turns
        changed["max_reasoning_turns"] = payload.max_reasoning_turns

    if payload.max_repair_attempts is not None:
        settings.max_repair_attempts = payload.max_repair_attempts
        changed["max_repair_attempts"] = payload.max_repair_attempts

    if payload.request_timeout_seconds is not None:
        settings.request_timeout_seconds = payload.request_timeout_seconds
        changed["request_timeout_seconds"] = payload.request_timeout_seconds

    if payload.max_concurrent_tasks is not None:
        settings.max_concurrent_tasks = payload.max_concurrent_tasks
        changed["max_concurrent_tasks"] = payload.max_concurrent_tasks

    return {
        "status": "updated",
        "changed_fields": list(changed.keys()),
        "current": {
            "active_model": settings.active_model,
            "context_window": settings.context_window,
            "max_reasoning_turns": settings.max_reasoning_turns,
            "max_repair_attempts": settings.max_repair_attempts,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "max_concurrent_tasks": settings.max_concurrent_tasks,
        },
    }
