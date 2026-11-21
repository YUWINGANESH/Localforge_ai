"""Tests for SQLite 3-tier memory persistence, telemetry, and multi-session recovery."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import Settings, get_settings
from app.config.telemetry import get_memory_stats
from app.database.sqlite_store import SQLiteStore
from app.llm.ollama_client import get_ollama_client
from app.main import app
from app.memory.project_memory import MemoryService, get_memory_service
from app.memory.session_manager import SessionManager, get_session_manager
from app.schemas.agent import TaskPlan
from app.schemas.context import DependencyInfo, ProjectSummary


def test_sqlite_conversation_persistence_across_restarts(tmp_path: Path):
    """Verify Level 1 conversation memory persists across SQLite store restarts."""
    db_file = tmp_path / "localforge_test.db"

    # Session 1 write
    store1 = SQLiteStore(db_file)
    mgr1 = SessionManager(store=store1)
    mgr1.add_message("sess-alpha", "user", "Build a REST API")
    mgr1.add_message("sess-alpha", "assistant", "Starting FastAPI setup.")
    mgr1.add_message("sess-beta", "user", "Explain scanner.py")
    store1.close()

    # Simulate restart with fresh store and manager
    store2 = SQLiteStore(db_file)
    mgr2 = SessionManager(store=store2)

    alpha_msgs = mgr2.get_messages("sess-alpha")
    assert len(alpha_msgs) == 2
    assert alpha_msgs[0].content == "Build a REST API"
    assert alpha_msgs[1].content == "Starting FastAPI setup."

    beta_msgs = mgr2.get_messages("sess-beta")
    assert len(beta_msgs) == 1
    assert beta_msgs[0].content == "Explain scanner.py"

    sessions = mgr2.list_sessions()
    assert len(sessions) == 2

    # Clear single session
    assert mgr2.clear_session("sess-alpha") is True
    assert len(mgr2.get_messages("sess-alpha")) == 0
    assert len(mgr2.get_messages("sess-beta")) == 1
    store2.close()


def test_task_memory_lifecycle_and_recovery(tmp_path: Path):
    """Verify Level 2 Task Memory persists objective, plan, steps, and files."""
    db_file = tmp_path / "tasks.db"
    store1 = SQLiteStore(db_file)
    mem1 = MemoryService(store=store1)

    task_id = mem1.start_task("sess-1", str(tmp_path), "Create auth module")
    plan = TaskPlan(
        objective="Create auth module",
        required_changes=["Create auth.py", "Run pytest"],
        files_to_create=["auth.py"],
        testing_strategy="pytest",
    )
    mem1.set_task_plan(task_id, plan)
    mem1.record_tool_step(task_id, "create_file(auth.py)", success=True, referenced_file="auth.py")
    mem1.record_tool_step(task_id, "run_tests(test_auth.py)", success=False, referenced_file="test_auth.py")
    mem1.finish_task(task_id, status="completed")
    store1.close()

    # Reload from disk
    store2 = SQLiteStore(db_file)
    mem2 = MemoryService(store=store2)
    tasks = mem2.get_session_tasks("sess-1")
    assert len(tasks) == 1
    t = tasks[0]
    assert t["objective"] == "Create auth module"
    assert t["status"] == "completed"
    assert "create_file(auth.py)" in t["completed_steps"]
    assert "run_tests(test_auth.py)" in t["failed_steps"]
    assert "auth.py" in t["referenced_files"]
    store2.close()


def test_project_memory_sync_and_clearing(tmp_path: Path):
    """Verify Level 3 Project Memory stores architectural metadata without raw files."""
    db_file = tmp_path / "project.db"
    store = SQLiteStore(db_file)
    mem = MemoryService(store=store)

    summary = ProjectSummary(
        workspace_root=str(tmp_path),
        frameworks=["FastAPI", "React"],
        dependencies=[DependencyInfo(name="fastapi"), DependencyInfo(name="pytest")],
        structure_tree="├── app.py\n├── tests/",
    )
    mem.sync_project_summary(summary)
    mem.add_project_convention(str(tmp_path), "Use type hints on all public methods")
    mem.add_architectural_decision(str(tmp_path), "Use SQLite WAL mode for local persistence")
    mem.record_completed_task(str(tmp_path), "Built initial task CRUD endpoints")

    prompt_snippet = mem.get_compact_prompt_memory(str(tmp_path))
    assert "Use type hints" in prompt_snippet
    assert "SQLite WAL mode" in prompt_snippet
    assert "Built initial task CRUD endpoints" in prompt_snippet

    mem.clear_project_memory(str(tmp_path))
    assert mem.get_project_memory(str(tmp_path)) is None
    store.close()


def test_memory_telemetry_helper():
    """Verify memory telemetry reports process PID and RAM footprint cleanly."""
    stats = get_memory_stats()
    assert stats["pid"] > 0
    # On Windows and Linux, process_rss_mb should be populated as float > 0
    if stats["process_rss_mb"] is not None:
        assert stats["process_rss_mb"] > 0


@pytest.mark.asyncio
async def test_memory_and_health_api_endpoints(tmp_path: Path):
    """Verify memory inspection, clearing, and health telemetry endpoints."""
    db_file = tmp_path / "api_mem.db"
    store = SQLiteStore(db_file)
    sess_mgr = SessionManager(store=store)
    mem_svc = MemoryService(store=store)
    test_settings = Settings(workspace_root=str(tmp_path))

    mock_ollama = AsyncMock()
    mock_ollama.check_health.return_value = (True, "0.40.16", None)
    mock_ollama.is_model_installed.return_value = True

    sess_mgr.add_message("demo-sess", "user", "Hello")
    task_id = mem_svc.start_task("demo-sess", str(tmp_path), "Demo task")
    mem_svc.finish_task(task_id, "completed")

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_session_manager] = lambda: sess_mgr
    app.dependency_overrides[get_memory_service] = lambda: mem_svc
    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Health includes memory telemetry
            h_res = await client.get("/health")
            assert h_res.status_code == 200
            assert h_res.json()["memory"]["pid"] > 0

            # 2. List sessions
            s_res = await client.get("/api/memory/sessions")
            assert s_res.status_code == 200
            assert len(s_res.json()["sessions"]) == 1

            # 3. List tasks
            t_res = await client.get("/api/memory/tasks?session_id=demo-sess")
            assert t_res.status_code == 200
            assert len(t_res.json()["tasks"]) == 1

            # 4. Add project note and inspect
            n_res = await client.post(
                "/api/memory/project/note",
                json={"category": "decision", "text": "Use Flask + SQLite"},
            )
            assert n_res.status_code == 200

            p_res = await client.get("/api/memory/project")
            assert p_res.status_code == 200
            assert "Use Flask + SQLite" in p_res.json()["project_memory"]["architectural_decisions"]

            # 5. Telemetry endpoint
            tel_res = await client.get("/api/memory/telemetry")
            assert tel_res.status_code == 200
            assert tel_res.json()["pid"] > 0

            # 6. Clear memory endpoints
            d1 = await client.delete("/api/memory/conversations")
            assert d1.status_code == 200
            d2 = await client.delete("/api/memory/project")
            assert d2.status_code == 200

            p_after = await client.get("/api/memory/project")
            assert p_after.json()["project_memory"] is None
    finally:
        app.dependency_overrides.clear()
        store.close()
