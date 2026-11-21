"""Persistent session and conversation history manager backed by SQLite."""
import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.database.sqlite_store import SQLiteStore, get_sqlite_store
from app.schemas.chat import ChatMessage, ChatSession

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages active chat sessions, turn history, SQLite persistence, and cancellation tokens."""

    def __init__(self, max_history_turns: int = 20, store: SQLiteStore | None = None):
        self._sessions: dict[str, ChatSession] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self.max_history_turns = max_history_turns
        self.store = store or SQLiteStore(":memory:")

    def get_or_create_session(self, session_id: str) -> ChatSession:
        """Retrieve existing session (hydrating from SQLite if needed) or initialize a fresh one."""
        if session_id not in self._sessions:
            # Hydrate from SQLite store if available
            stored_rows = self.store.get_session_messages(session_id, limit=self.max_history_turns)
            hydrated_msgs = []
            for row in stored_rows:
                try:
                    ts = datetime.fromisoformat(row["timestamp"])
                except ValueError:
                    ts = datetime.now(UTC)
                hydrated_msgs.append(
                    ChatMessage(
                        id=row["id"],
                        role=row["role"],
                        content=row["content"],
                        timestamp=ts,
                    )
                )
            self._sessions[session_id] = ChatSession(
                session_id=session_id,
                messages=hydrated_msgs,
            )
        return self._sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str) -> ChatMessage:
        """Add a message to session history and persist in SQLite."""
        session = self.get_or_create_session(session_id)
        now = datetime.now(UTC)
        msg = ChatMessage(
            id=str(uuid.uuid4()),
            role=role,
            content=content,
            timestamp=now,
        )
        session.messages.append(msg)
        session.updated_at = now

        # Persist to SQLite
        self.store.save_message(msg.id, session_id, role, content, now)

        # Enforce history bound in active context window
        if len(session.messages) > self.max_history_turns:
            system_msgs = [m for m in session.messages if m.role == "system"]
            other_msgs = [m for m in session.messages if m.role != "system"]
            pruned = other_msgs[-(self.max_history_turns - len(system_msgs)):]
            session.messages = system_msgs + pruned

        return msg

    def get_messages(self, session_id: str) -> list[ChatMessage]:
        """Return all messages for the specified session."""
        session = self.get_or_create_session(session_id)
        return list(session.messages)

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all persisted sessions ordered by recent activity."""
        return self.store.list_sessions()

    def clear_session(self, session_id: str) -> bool:
        """Clear all messages from a conversation session (memory + SQLite)."""
        cleared_db = self.store.clear_session_messages(session_id)
        cleared_mem = False
        if session_id in self._sessions:
            self._sessions[session_id].messages.clear()
            self._sessions[session_id].updated_at = datetime.now(UTC)
            cleared_mem = True
        return cleared_db or cleared_mem

    def clear_all_conversations(self):
        """Wipe all stored conversation history."""
        self._sessions.clear()
        self.store.delete_all_conversations()

    def register_cancel_event(self, session_id: str) -> asyncio.Event:
        """Register a cancellation flag for an active streaming session."""
        event = asyncio.Event()
        self._cancel_events[session_id] = event
        return event

    def trigger_cancel(self, session_id: str) -> bool:
        """Signal cancellation to an ongoing streaming generation."""
        if session_id in self._cancel_events:
            self._cancel_events[session_id].set()
            logger.info("Cancellation signaled for session %s", session_id)
            return True
        return False

    def unregister_cancel_event(self, session_id: str):
        """Clean up cancellation flag when streaming completes."""
        self._cancel_events.pop(session_id, None)


_global_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Singleton getter for SessionManager backed by persistent SQLiteStore."""
    global _global_session_manager
    if _global_session_manager is None:
        _global_session_manager = SessionManager(store=get_sqlite_store())
    return _global_session_manager
