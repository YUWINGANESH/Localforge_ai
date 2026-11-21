"""Streaming chat API endpoints using Server-Sent Events (SSE)."""
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.config.settings import Settings, get_settings
from app.context.context_builder import CompactContextBuilder
from app.llm.ollama_client import OllamaClient, get_ollama_client
from app.memory.session_manager import SessionManager, get_session_manager
from app.schemas.chat import (
    ChatHistoryResponse,
    ChatRequest,
    StopChatRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["Chat"])

DEFAULT_SYSTEM_PROMPT = (
    "You are LocalForge-AI 2.0, an expert local software engineering assistant running directly "
    "on the user's machine. You assist with coding, debugging, architecture, and tests. "
    "Always provide precise, runnable code, clear explanations, and maintain software quality."
)


@router.post("")
async def chat_stream(
    payload: ChatRequest,
    settings: Settings = Depends(get_settings),
    ollama_client: OllamaClient = Depends(get_ollama_client),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """
    Stream chat responses token-by-token via Server-Sent Events (SSE).
    """
    session_id = payload.session_id or "default"
    user_prompt = payload.message.strip()

    # Save user message into session history
    session_manager.add_message(session_id, "user", user_prompt)

    # Prepare message payload
    system_prompt = payload.system_prompt or DEFAULT_SYSTEM_PROMPT

    # Automatically extract compact codebase context for the user prompt
    try:
        builder = CompactContextBuilder(settings.get_effective_workspace())
        compact_ctx = builder.build_context(user_prompt, max_tokens=1500)
        if compact_ctx.context_text:
            system_prompt = f"{system_prompt}\n\n[LOCAL WORKSPACE CONTEXT]\n{compact_ctx.context_text}"
    except Exception as exc:
        logger.warning("Could not build workspace context: %s", exc)

    history = session_manager.get_messages(session_id)

    formatted_messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        formatted_messages.append({"role": msg.role, "content": msg.content})

    target_model = payload.model or settings.active_model
    cancel_event = session_manager.register_cancel_event(session_id)

    async def sse_event_generator() -> AsyncIterator[str]:
        full_response = []
        try:
            yield f"event: start\ndata: {json.dumps({'session_id': session_id, 'model': target_model})}\n\n"

            async for token in ollama_client.chat_stream(
                messages=formatted_messages,
                model=target_model,
                cancel_event=cancel_event,
            ):
                full_response.append(token)
                yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"

            complete_text = "".join(full_response)
            session_manager.add_message(session_id, "assistant", complete_text)
            yield f"event: done\ndata: {json.dumps({'session_id': session_id, 'full_text': complete_text})}\n\n"

        except Exception as exc:
            logger.exception("Error during chat stream for session %s: %s", session_id, exc)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            session_manager.unregister_cancel_event(session_id)

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stop")
async def stop_chat(
    payload: StopChatRequest,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """
    Signal immediate stop/cancellation for an active generation.
    """
    stopped = session_manager.trigger_cancel(payload.session_id)
    return {"status": "stopped" if stopped else "not_active", "session_id": payload.session_id}


@router.get("/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    session_id: str = Query("default", description="Conversation session ID"),
    session_manager: SessionManager = Depends(get_session_manager),
) -> ChatHistoryResponse:
    """
    Retrieve message history for the given session.
    """
    messages = session_manager.get_messages(session_id)
    return ChatHistoryResponse(
        session_id=session_id,
        messages=messages,
        total_count=len(messages),
    )


@router.delete("/history")
async def clear_chat_history(
    session_id: str = Query("default", description="Conversation session ID"),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """
    Clear message history for the given session.
    """
    cleared = session_manager.clear_session(session_id)
    return {"status": "cleared", "session_id": session_id, "cleared": cleared}
