"""Application settings and configuration management."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """LocalForge-AI Backend Configuration Settings."""

    # Application details
    app_name: str = "LocalForge-AI"
    app_version: str = "2.0.0"
    debug: bool = False

    # Server network settings
    host: str = "127.0.0.1"
    port: int = 8765

    # Ollama Local Inference Settings
    ollama_base_url: str = "http://127.0.0.1:11434"
    default_model: str = "qwen2.5-coder:7b"
    fallback_model: str = "qwen3:4b"
    active_model: str = "qwen2.5-coder:7b"
    context_window: int = 4096
    request_timeout_seconds: float = 300.0

    # Agent & Orchestrator Limits
    max_reasoning_turns: int = 16
    max_repair_attempts: int = 4
    max_concurrent_tasks: int = 1

    # Active Workspace Sandbox
    workspace_root: str | None = None

    # Persistence
    database_path: str = "localforge.db"

    # Logging
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow hot-patching fields at runtime (e.g. from /api/settings PATCH)
        frozen=False,
    )

    def get_effective_workspace(self) -> Path:
        """Return the resolved workspace directory, defaulting to current working directory."""
        if self.workspace_root:
            return Path(self.workspace_root).resolve()
        return Path.cwd().resolve()


@lru_cache
def get_settings() -> Settings:
    """Return cached instance of application settings."""
    return Settings()
