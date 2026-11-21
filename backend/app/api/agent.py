"""API endpoints for running and cancelling autonomous coding tasks."""
import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.config.settings import Settings, get_settings
from app.llm.ollama_client import OllamaClient, get_ollama_client
from app.memory.project_memory import MemoryService, get_memory_service
from app.memory.session_manager import SessionManager, get_session_manager
from app.orchestrator.agent import AgentOrchestrator
from app.schemas.agent import AgentRunRequest
from app.schemas.chat import StopChatRequest
from app.security.approval_manager import ApprovalManager, get_approval_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent", tags=["Agent"])

# Enforce strict concurrency limit (1 autonomous task at a time for 4GB VRAM / 16GB RAM)
_active_task_lock = asyncio.Lock()


@router.post("/run")
async def run_agent_task(
    payload: AgentRunRequest,
    settings: Settings = Depends(get_settings),
    ollama_client: OllamaClient = Depends(get_ollama_client),
    approval_manager: ApprovalManager = Depends(get_approval_manager),
    session_manager: SessionManager = Depends(get_session_manager),
    memory_service: MemoryService = Depends(get_memory_service),
):
    """
    Launch an autonomous software engineering task using the bounded ReAct loop.
    Streams structured SSE events: activity, plan, thought, tool_call, approval_required, tool_result, report.
    """
    if _active_task_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="An autonomous agent task is already running. Please wait or stop the current task first.",
        )

    session_id = payload.session_id or "default"
    cancel_event = session_manager.register_cancel_event(session_id)

    orchestrator = AgentOrchestrator(
        workspace_root=settings.get_effective_workspace(),
        ollama_client=ollama_client,
        approval_manager=approval_manager,
        settings=settings,
        memory_service=memory_service,
        auto_approve=payload.auto_approve,
    )

    async def event_stream() -> AsyncIterator[str]:
        async with _active_task_lock:
            try:
                async for sse_chunk in orchestrator.run_task_stream(
                    prompt=payload.prompt,
                    session_id=session_id,
                    model=payload.model,
                    max_iterations=payload.max_iterations,
                    max_repair_attempts=payload.max_repair_attempts,
                    cancel_event=cancel_event,
                ):
                    yield sse_chunk
            except Exception as exc:
                logger.exception("Agent stream failed: %s", exc)
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            finally:
                session_manager.unregister_cancel_event(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stop")
async def stop_agent_task(
    payload: StopChatRequest,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Stop an ongoing autonomous agent task immediately."""
    stopped = session_manager.trigger_cancel(payload.session_id)
    return {"status": "stopped" if stopped else "not_active", "session_id": payload.session_id}
