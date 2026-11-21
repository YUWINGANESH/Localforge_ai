"""Endpoints for inspecting and clearing Conversation, Task, and Project memory, plus system telemetry."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.config.settings import Settings, get_settings
from app.config.telemetry import get_memory_stats
from app.memory.project_memory import MemoryService, get_memory_service
from app.memory.session_manager import SessionManager, get_session_manager

router = APIRouter(prefix="/api/memory", tags=["Memory"])


class ProjectNoteRequest(BaseModel):
    """Payload to store a project convention or architectural decision."""
    category: str = Field(..., description="'convention' or 'decision'")
    text: str = Field(..., min_length=1)


@router.get("/sessions")
async def list_persisted_sessions(
    session_manager: SessionManager = Depends(get_session_manager),
):
    """List all persisted conversation sessions."""
    return {"sessions": session_manager.list_sessions()}


@router.get("/tasks")
async def list_session_tasks(
    session_id: str = Query("default", description="Session identifier"),
    memory_service: MemoryService = Depends(get_memory_service),
):
    """List persisted Level 2 Task Memory records for a session."""
    return {"session_id": session_id, "tasks": memory_service.get_session_tasks(session_id)}


@router.get("/project")
async def get_project_memory(
    settings: Settings = Depends(get_settings),
    memory_service: MemoryService = Depends(get_memory_service),
):
    """Retrieve persisted Level 3 Project Memory for the active workspace."""
    workspace = str(settings.get_effective_workspace())
    mem = memory_service.get_project_memory(workspace)
    return {"workspace_root": workspace, "project_memory": mem}


@router.post("/project/note")
async def add_project_note(
    payload: ProjectNoteRequest,
    settings: Settings = Depends(get_settings),
    memory_service: MemoryService = Depends(get_memory_service),
):
    """Record a project convention or architectural decision in Project Memory."""
    workspace = str(settings.get_effective_workspace())
    if payload.category == "convention":
        memory_service.add_project_convention(workspace, payload.text.strip())
    else:
        memory_service.add_architectural_decision(workspace, payload.text.strip())
    return {"status": "saved", "workspace_root": workspace}


@router.delete("/conversations")
async def clear_conversations(
    session_id: str | None = Query(None, description="Specific session to clear, or omit to clear all"),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Clear conversation memory (Level 1)."""
    if session_id:
        cleared = session_manager.clear_session(session_id)
        return {"status": "cleared", "scope": session_id, "cleared": cleared}
    session_manager.clear_all_conversations()
    return {"status": "cleared", "scope": "all"}


@router.delete("/project")
async def clear_project_memory(
    settings: Settings = Depends(get_settings),
    memory_service: MemoryService = Depends(get_memory_service),
):
    """Clear persisted Level 3 Project Memory for the active workspace."""
    workspace = str(settings.get_effective_workspace())
    memory_service.clear_project_memory(workspace)
    return {"status": "cleared", "workspace_root": workspace}


@router.get("/telemetry")
async def get_backend_telemetry():
    """Return process memory footprint and host RAM availability."""
    return get_memory_stats()
