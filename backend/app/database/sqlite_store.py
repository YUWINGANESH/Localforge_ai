"""SQLite persistence layer for Conversation (L1), Task (L2), and Project (L3) memory."""
import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class SQLiteStore:
    """Thread-safe SQLite database store for local agent persistence."""

    def __init__(self, db_path: str | Path = "localforge.db"):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL;")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);

                CREATE TABLE IF NOT EXISTS task_memory (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    plan_json TEXT,
                    completed_steps_json TEXT,
                    failed_steps_json TEXT,
                    pending_tasks_json TEXT,
                    referenced_files_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_task_session ON task_memory(session_id, updated_at);

                CREATE TABLE IF NOT EXISTS project_memory (
                    workspace_root TEXT PRIMARY KEY,
                    structure_summary TEXT,
                    frameworks_json TEXT,
                    dependencies_json TEXT,
                    conventions_json TEXT,
                    architectural_decisions_json TEXT,
                    completed_tasks_json TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------------- Level 1: Conversation Memory ----------------

    def ensure_session(self, session_id: str, title: str | None = None):
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (session_id, title or f"Session {session_id[:12]}", now, now),
            )
            self._conn.commit()

    def save_message(self, msg_id: str, session_id: str, role: str, content: str, timestamp: datetime):
        self.ensure_session(session_id)
        ts_str = timestamp.isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (msg_id, session_id, role, content, ts_str),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (ts_str, session_id),
            )
            self._conn.commit()

    def get_session_messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, session_id, role, content, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.session_id, s.title, s.created_at, s.updated_at, COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON s.session_id = m.session_id
                GROUP BY s.session_id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def clear_session_messages(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_all_conversations(self):
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM sessions")
            self._conn.commit()

    # ---------------- Level 2: Task Memory ----------------

    def upsert_task_memory(
        self,
        task_id: str,
        session_id: str,
        workspace_root: str,
        objective: str,
        plan: dict[str, Any] | None = None,
        completed_steps: list[str] | None = None,
        failed_steps: list[str] | None = None,
        pending_tasks: list[str] | None = None,
        referenced_files: list[str] | None = None,
        status: str = "running",
    ):
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO task_memory (
                    task_id, session_id, workspace_root, objective,
                    plan_json, completed_steps_json, failed_steps_json,
                    pending_tasks_json, referenced_files_json, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    plan_json = COALESCE(excluded.plan_json, task_memory.plan_json),
                    completed_steps_json = excluded.completed_steps_json,
                    failed_steps_json = excluded.failed_steps_json,
                    pending_tasks_json = excluded.pending_tasks_json,
                    referenced_files_json = excluded.referenced_files_json,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    session_id,
                    workspace_root,
                    objective,
                    json.dumps(plan) if plan is not None else None,
                    json.dumps(completed_steps or []),
                    json.dumps(failed_steps or []),
                    json.dumps(pending_tasks or []),
                    json.dumps(referenced_files or []),
                    status,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def get_tasks_for_session(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM task_memory
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                results.append({
                    "task_id": d["task_id"],
                    "session_id": d["session_id"],
                    "workspace_root": d["workspace_root"],
                    "objective": d["objective"],
                    "plan": json.loads(d["plan_json"]) if d["plan_json"] else None,
                    "completed_steps": json.loads(d["completed_steps_json"] or "[]"),
                    "failed_steps": json.loads(d["failed_steps_json"] or "[]"),
                    "pending_tasks": json.loads(d["pending_tasks_json"] or "[]"),
                    "referenced_files": json.loads(d["referenced_files_json"] or "[]"),
                    "status": d["status"],
                    "created_at": d["created_at"],
                    "updated_at": d["updated_at"],
                })
            return results

    # ---------------- Level 3: Project Memory ----------------

    def upsert_project_memory(
        self,
        workspace_root: str,
        structure_summary: str = "",
        frameworks: list[str] | None = None,
        dependencies: list[str] | None = None,
        conventions: list[str] | None = None,
        architectural_decisions: list[str] | None = None,
        completed_tasks: list[str] | None = None,
    ):
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO project_memory (
                    workspace_root, structure_summary, frameworks_json,
                    dependencies_json, conventions_json, architectural_decisions_json,
                    completed_tasks_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_root) DO UPDATE SET
                    structure_summary = excluded.structure_summary,
                    frameworks_json = excluded.frameworks_json,
                    dependencies_json = excluded.dependencies_json,
                    conventions_json = excluded.conventions_json,
                    architectural_decisions_json = excluded.architectural_decisions_json,
                    completed_tasks_json = excluded.completed_tasks_json,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_root,
                    structure_summary,
                    json.dumps(frameworks or []),
                    json.dumps(dependencies or []),
                    json.dumps(conventions or []),
                    json.dumps(architectural_decisions or []),
                    json.dumps(completed_tasks or []),
                    now,
                ),
            )
            self._conn.commit()

    def get_project_memory(self, workspace_root: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM project_memory WHERE workspace_root = ?",
                (workspace_root,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            return {
                "workspace_root": d["workspace_root"],
                "structure_summary": d["structure_summary"],
                "frameworks": json.loads(d["frameworks_json"] or "[]"),
                "dependencies": json.loads(d["dependencies_json"] or "[]"),
                "conventions": json.loads(d["conventions_json"] or "[]"),
                "architectural_decisions": json.loads(d["architectural_decisions_json"] or "[]"),
                "completed_tasks": json.loads(d["completed_tasks_json"] or "[]"),
                "updated_at": d["updated_at"],
            }

    def clear_project_memory(self, workspace_root: str | None = None):
        with self._lock:
            if workspace_root:
                self._conn.execute("DELETE FROM project_memory WHERE workspace_root = ?", (workspace_root,))
            else:
                self._conn.execute("DELETE FROM project_memory")
            self._conn.commit()


_global_store: SQLiteStore | None = None


def get_sqlite_store() -> SQLiteStore:
    """Singleton getter for SQLiteStore."""
    global _global_store
    if _global_store is None:
        settings = get_settings()
        _global_store = SQLiteStore(db_path=settings.database_path)
    return _global_store
