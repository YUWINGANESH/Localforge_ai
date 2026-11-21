"""Health and system status schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OllamaStatus(BaseModel):
    """Detailed status of Ollama connection."""
    connected: bool = Field(..., description="Whether Ollama HTTP API responded successfully")
    base_url: str = Field(..., description="The Ollama endpoint checked")
    version: str | None = Field(None, description="Ollama daemon version if connected")
    active_model: str = Field(..., description="Currently selected active model")
    active_model_installed: bool = Field(False, description="Whether the active model is installed in Ollama")
    error: str | None = Field(None, description="Connection error message if offline")


class HealthResponse(BaseModel):
    """Overall backend health check response."""
    status: str = Field(..., description="Backend operational status (e.g., 'healthy', 'degraded')")
    app_name: str = Field("LocalForge-AI", description="Application name")
    version: str = Field("2.0.0", description="Application version")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="UTC timestamp of the check")
    python_version: str = Field(..., description="Running Python interpreter version")
    workspace_root: str | None = Field(None, description="Configured active workspace directory")
    ollama: OllamaStatus = Field(..., description="Ollama local inference status")
    memory: dict[str, Any] | None = Field(None, description="Process and system RAM telemetry")
