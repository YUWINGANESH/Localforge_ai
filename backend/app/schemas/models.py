"""Model catalog and configuration schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    """Details about an installed Ollama model."""
    name: str = Field(..., description="Model name/tag (e.g., 'qwen2.5-coder:7b')")
    size_bytes: int | None = Field(None, description="Model file size in bytes")
    modified_at: datetime | None = Field(None, description="When the model was pulled or updated")
    format: str | None = Field(None, description="Model format (e.g., 'gguf')")
    family: str | None = Field(None, description="Model architecture family (e.g., 'qwen2')")
    parameter_size: str | None = Field(None, description="Parameter scale (e.g., '7.6B')")
    quantization_level: str | None = Field(None, description="Quantization (e.g., 'Q4_K_M')")
    is_active: bool = Field(False, description="Whether this is the currently selected active model")


class ModelListResponse(BaseModel):
    """Response containing available local models."""
    connected: bool = Field(..., description="Whether Ollama daemon is reachable")
    active_model: str = Field(..., description="Currently configured active model name")
    models: list[ModelInfo] = Field(default_factory=list, description="List of local models discovered")
    total_count: int = Field(0, description="Total count of discovered models")
    error: str | None = Field(None, description="Error message if Ollama is unreachable")


class SetActiveModelRequest(BaseModel):
    """Request to change active model."""
    model_name: str = Field(..., min_length=1, description="Name/tag of model to activate")
