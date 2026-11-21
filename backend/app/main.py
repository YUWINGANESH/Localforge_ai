"""Entry point and application factory for LocalForge-AI 2.0 FastAPI backend."""
import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agent import router as agent_router
from app.api.approvals import router as approvals_router
from app.api.chat import router as chat_router
from app.api.context import router as context_router
from app.api.health import router as health_router
from app.api.memory import router as memory_router
from app.api.models import router as models_router
from app.api.settings import router as settings_router
from app.config.settings import get_settings

# Configure structured logging (console + rotating local log file)
settings = get_settings()
log_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    log_dir = Path(".localforge")
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "localforge.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    log_handlers.append(file_handler)
except OSError:
    pass

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("localforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events management for startup and shutdown."""
    logger.info("Starting LocalForge-AI 2.0 backend on %s:%s", settings.host, settings.port)
    logger.info("Configured Ollama URL: %s | Default model: %s", settings.ollama_base_url, settings.active_model)
    logger.info("Workspace directory: %s", settings.get_effective_workspace())
    yield
    logger.info("LocalForge-AI backend shutting down gracefully.")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Local-First Autonomous AI Coding Agent backend powered by Ollama.",
    lifespan=lifespan,
)

# Enable CORS for VS Code Webviews and local browser tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(health_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(context_router)
app.include_router(approvals_router)
app.include_router(agent_router)
app.include_router(memory_router)
app.include_router(settings_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Safe global exception handler preventing raw stack leakage."""
    logger.exception("Unhandled server exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred inside LocalForge-AI backend.",
            "path": request.url.path,
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)
