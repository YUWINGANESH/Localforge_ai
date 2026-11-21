"""API routers package for LocalForge-AI 2.0."""
from app.api.agent import router as agent_router
from app.api.approvals import router as approvals_router
from app.api.chat import router as chat_router
from app.api.context import router as context_router
from app.api.health import router as health_router
from app.api.memory import router as memory_router
from app.api.models import router as models_router

__all__ = [
    "agent_router",
    "health_router",
    "memory_router",
    "models_router",
    "chat_router",
    "context_router",
    "approvals_router",
]
