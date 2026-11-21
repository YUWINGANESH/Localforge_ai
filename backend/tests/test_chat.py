"""Automated tests for streaming chat, session memory, and cancellation."""
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.llm.ollama_client import get_ollama_client
from app.main import app
from app.memory.session_manager import SessionManager, get_session_manager


def test_session_manager_message_lifecycle():
    """Verify session manager adds, retrieves, and prunes messages."""
    mgr = SessionManager(max_history_turns=4)
    session_id = "test-session-1"

    # Add messages
    mgr.add_message(session_id, "user", "Hello")
    mgr.add_message(session_id, "assistant", "Hi there!")
    mgr.add_message(session_id, "user", "How are you?")
    mgr.add_message(session_id, "assistant", "I am ready.")

    msgs = mgr.get_messages(session_id)
    assert len(msgs) == 4
    assert msgs[0].content == "Hello"

    # Add 5th message - should prune oldest to maintain 4
    mgr.add_message(session_id, "user", "What is 2+2?")
    msgs_pruned = mgr.get_messages(session_id)
    assert len(msgs_pruned) == 4
    assert msgs_pruned[0].content == "Hi there!"
    assert msgs_pruned[-1].content == "What is 2+2?"

    # Clear session
    assert mgr.clear_session(session_id) is True
    assert len(mgr.get_messages(session_id)) == 0


def test_session_manager_cancellation():
    """Verify registration and triggering of cancel tokens."""
    mgr = SessionManager()
    session_id = "cancel-session"

    event = mgr.register_cancel_event(session_id)
    assert not event.is_set()

    assert mgr.trigger_cancel(session_id) is True
    assert event.is_set()

    mgr.unregister_cancel_event(session_id)
    assert mgr.trigger_cancel(session_id) is False


@pytest.mark.asyncio
async def test_chat_stream_sse_flow():
    """Verify POST /api/chat streams SSE tokens and records turn in history."""
    mock_ollama = AsyncMock()

    async def mock_chat_stream(messages, model=None, temperature=0.1, cancel_event=None):
        yield "Hello"
        yield " "
        yield "world!"

    mock_ollama.chat_stream = mock_chat_stream

    test_mgr = SessionManager()

    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama
    app.dependency_overrides[get_session_manager] = lambda: test_mgr

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "session_id": "stream-test",
                    "message": "Say hello world",
                },
            )
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            content = response.text
            assert "event: start" in content
            assert "event: token" in content
            assert 'data: {"token": "Hello"}' in content
            assert 'data: {"token": "world!"}' in content
            assert "event: done" in content

            # Verify history was recorded
            msgs = test_mgr.get_messages("stream-test")
            assert len(msgs) == 2
            assert msgs[0].role == "user"
            assert msgs[0].content == "Say hello world"
            assert msgs[1].role == "assistant"
            assert msgs[1].content == "Hello world!"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_stop_endpoint():
    """Verify POST /api/chat/stop endpoint."""
    test_mgr = SessionManager()
    test_mgr.register_cancel_event("active-task")

    app.dependency_overrides[get_session_manager] = lambda: test_mgr

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/chat/stop", json={"session_id": "active-task"})
            assert res.status_code == 200
            assert res.json()["status"] == "stopped"

            res2 = await client.post("/api/chat/stop", json={"session_id": "nonexistent"})
            assert res2.status_code == 200
            assert res2.json()["status"] == "not_active"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_history_endpoints():
    """Verify GET and DELETE /api/chat/history endpoints."""
    test_mgr = SessionManager()
    test_mgr.add_message("hist-session", "user", "Question 1")
    test_mgr.add_message("hist-session", "assistant", "Answer 1")

    app.dependency_overrides[get_session_manager] = lambda: test_mgr

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Query history
            res = await client.get("/api/chat/history?session_id=hist-session")
            assert res.status_code == 200
            data = res.json()
            assert data["session_id"] == "hist-session"
            assert data["total_count"] == 2
            assert data["messages"][0]["content"] == "Question 1"

            # Clear history
            del_res = await client.delete("/api/chat/history?session_id=hist-session")
            assert del_res.status_code == 200
            assert del_res.json()["status"] == "cleared"

            # Query again - should be 0
            res_after = await client.get("/api/chat/history?session_id=hist-session")
            assert res_after.json()["total_count"] == 0
    finally:
        app.dependency_overrides.clear()
