"""Pydantic schemas for chat sessions, streaming tokens, and history."""
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Single chat message in a conversation."""
    id: str = Field(..., description="Unique message ID")
    role: str = Field(..., description="Message role: 'user', 'assistant', or 'system'")
    content: str = Field(..., description="Text content of the message")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatRequest(BaseModel):
    """Request payload to initiate a chat generation."""
    session_id: str = Field("default", description="Conversation session ID")
    message: str = Field(..., min_length=1, description="User prompt or instruction")
    model: str | None = Field(None, description="Optional model override")
    system_prompt: str | None = Field(None, description="Optional custom system prompt")


class StopChatRequest(BaseModel):
    """Payload to stop an ongoing generation."""
    session_id: str = Field(..., description="Conversation session ID to cancel")


class ChatSession(BaseModel):
    """Conversation session containing message history."""
    session_id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatHistoryResponse(BaseModel):
    """Response containing message history for a session."""
    session_id: str
    messages: list[ChatMessage]
    total_count: int
