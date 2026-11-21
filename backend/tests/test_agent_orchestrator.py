"""Automated tests for tool registry, ReAct orchestrator, error inspection, and repair loop."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import Settings, get_settings
from app.llm.ollama_client import get_ollama_client
from app.main import app
from app.orchestrator.agent import AgentOrchestrator
from app.orchestrator.parser import parse_step_decision, parse_task_plan
from app.security.approval_manager import ApprovalManager, get_approval_manager
from app.tools.error_inspector import inspect_error_trace
from app.tools.registry import ToolRegistry


def test_tool_registry_all_18_tools_registered(tmp_path: Path):
    """Verify all 18 required file, dev, and git tools are registered."""
    registry = ToolRegistry(workspace_root=tmp_path)
    names = {t.name for t in registry.list_tools()}

    expected = {
        "list_files",
        "search_files",
        "search_code",
        "read_file",
        "create_file",
        "write_file",
        "edit_file",
        "delete_file",
        "run_python_file",
        "run_tests",
        "inspect_project",
        "detect_dependencies",
        "install_dependencies",
        "inspect_error",
        "get_terminal_output",
        "git_status",
        "git_diff",
        "git_log",
    }
    assert expected.issubset(names)
    assert len(names) >= 18


@pytest.mark.asyncio
async def test_tool_registry_validation_and_normalization(tmp_path: Path):
    """Verify parameter validation and argument alias normalization."""
    (tmp_path / "sample.py").write_text("x = 42\n", encoding="utf-8")
    registry = ToolRegistry(workspace_root=tmp_path)

    # Alias 'file_path' -> 'path'
    res = await registry.execute_tool("read_file", {"file_path": "sample.py"})
    assert res.success is True
    assert "x = 42" in res.output

    # Missing required parameter returns structured error
    err_res = await registry.execute_tool("read_file", {})
    assert err_res.success is False
    assert "Missing required argument" in err_res.error

    # Unknown tool returns structured error
    unk_res = await registry.execute_tool("nonexistent_tool", {})
    assert unk_res.success is False
    assert "Unknown tool" in unk_res.error


def test_error_inspector_extracts_tracebacks(tmp_path: Path):
    """Verify error inspector parses traceback and extracts surrounding failing lines."""
    buggy = tmp_path / "broken.py"
    buggy.write_text(
        "def divide(a, b):\n"
        "    result = a / b\n"
        "    return result\n",
        encoding="utf-8",
    )

    tb = (
        f'Traceback (most recent call last):\n'
        f'  File "{buggy}", line 2, in divide\n'
        f'    result = a / b\n'
        f'ZeroDivisionError: division by zero\n'
    )
    info = inspect_error_trace(tmp_path, tb)
    assert info["probable_exception"] == "ZeroDivisionError: division by zero"
    assert info["workspace_locations_found"] == 1
    assert info["snippets"][0]["file"] == "broken.py"
    assert info["snippets"][0]["error_line"] == 2
    assert "result = a / b" in info["snippets"][0]["code"]


def test_parser_resilience_for_small_models():
    """Verify JSON parser strips <think> tags, fences, and extracts steps reliably."""
    qwen3_output = (
        "<think>I should read main.py first to inspect routes.</think>\n"
        "```json\n"
        '{"thought": "Inspect main.py", "action": "read_file", "arguments": {"path": "main.py"}}\n'
        "```"
    )
    decision, err = parse_step_decision(qwen3_output)
    assert err is None
    assert decision is not None
    assert decision.action == "read_file"
    assert decision.arguments["path"] == "main.py"

    # Malformed response triggers recovery prompt
    bad_decision, bad_err = parse_step_decision("Sure! Here is the Python code: print('hi')")
    assert bad_decision is None
    assert "valid JSON" in bad_err

    # Task plan parsing
    plan = parse_task_plan(
        '{"objective": "Build math module", "required_changes": ["Create math.py"], "files_to_create": ["math.py"]}',
        fallback_objective="fallback",
    )
    assert plan.objective == "Build math module"
    assert plan.files_to_create == ["math.py"]


@pytest.mark.asyncio
async def test_autonomous_workflow_with_repair_and_approval(tmp_path: Path):
    """
    End-to-end autonomous agent test:
    Plan -> Create buggy code -> Create test -> Run tests (FAIL) -> Debug & Edit fix -> Run tests (PASS) -> Report.
    Includes real interactive human approval waiting!
    """
    mock_ollama = AsyncMock()
    mgr = ApprovalManager()

    # Simulated LLM responses across Planning + 6 ReAct turns
    llm_turns = [
        # 0. Plan turn
        json.dumps({
            "objective": "Create a calculator module and test it",
            "required_changes": ["Create calc.py", "Create test_calc.py", "Run pytest"],
            "files_to_create": ["calc.py", "test_calc.py"],
            "testing_strategy": "pytest",
        }),
        # 1. Create buggy calc.py
        json.dumps({
            "thought": "Creating calc.py",
            "action": "create_file",
            "arguments": {
                "path": "calc.py",
                "content": "def add(a, b):\n    return a - b\n",
            },
        }),
        # 2. Create test_calc.py
        json.dumps({
            "thought": "Creating unit test test_calc.py",
            "action": "create_file",
            "arguments": {
                "path": "test_calc.py",
                "content": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            },
        }),
        # 3. Run tests (will FAIL!)
        json.dumps({
            "thought": "Running pytest to validate implementation",
            "action": "run_tests",
            "arguments": {"test_path": "test_calc.py"},
        }),
        # 4. Repair bug after seeing failure
        json.dumps({
            "thought": "add() returned a - b instead of a + b. Fixing it.",
            "action": "edit_file",
            "arguments": {
                "path": "calc.py",
                "target": "return a - b",
                "replacement": "return a + b",
            },
        }),
        # 5. Re-run tests (will PASS!)
        json.dumps({
            "thought": "Re-running pytest after fix",
            "action": "run_tests",
            "arguments": {"test_path": "test_calc.py"},
        }),
        # 6. Final Report
        json.dumps({
            "thought": "Tests pass. Returning final report.",
            "action": "final_answer",
            "report": {
                "summary": "Created calc.py, detected subtraction bug via pytest, fixed it, and verified tests pass.",
                "files_created": ["calc.py", "test_calc.py"],
                "files_modified": ["calc.py"],
                "test_results": "1 passed",
            },
        }),
    ]

    turn_iter = iter(llm_turns)

    async def fake_chat_complete(**kwargs):
        return next(turn_iter)

    mock_ollama.chat_complete = fake_chat_complete

    orchestrator = AgentOrchestrator(
        workspace_root=tmp_path,
        ollama_client=mock_ollama,
        approval_manager=mgr,
        auto_approve=False,  # Test REAL interactive approval flow!
    )

    events_seen = []
    report_data = None

    async def run_and_auto_approve_interactively():
        nonlocal report_data
        async for sse in orchestrator.run_task_stream("Create calc.py and verify with tests"):
            lines = sse.strip().split("\n")
            ev_type = lines[0].replace("event: ", "")
            ev_data = json.loads(lines[1].replace("data: ", ""))
            events_seen.append((ev_type, ev_data))

            if ev_type == "approval_required":
                # Simulate user clicking Approve in VS Code UI!
                req_id = ev_data["id"]
                asyncio.create_task(mgr.resolve(req_id, approved=True))
            elif ev_type == "report":
                report_data = ev_data

    await run_and_auto_approve_interactively()

    # Verify full lifecycle events occurred
    event_names = [e[0] for e in events_seen]
    assert "plan" in event_names
    assert "approval_required" in event_names
    assert "tool_result" in event_names
    assert "report" in event_names

    # Verify real files were created and fixed on disk
    assert (tmp_path / "calc.py").exists()
    assert "return a + b" in (tmp_path / "calc.py").read_text(encoding="utf-8")
    assert (tmp_path / "test_calc.py").exists()

    # Verify final report accuracy
    assert report_data is not None
    assert "calc.py" in report_data["files_created"]
    assert "test_calc.py" in report_data["files_created"]
    assert "test_calc.py" in report_data["tests_performed"]


@pytest.mark.asyncio
async def test_agent_api_endpoint_stream(tmp_path: Path):
    """Verify POST /api/agent/run SSE endpoint."""
    mock_ollama = AsyncMock()
    turns = iter([
        json.dumps({"objective": "Inspect workspace", "required_changes": ["List files"]}),
        json.dumps({"thought": "List files", "action": "list_files", "arguments": {}}),
        json.dumps({"thought": "Done", "action": "final_answer", "report": {"summary": "Listed files."}}),
    ])

    async def fake_complete(**kwargs):
        return next(turns)

    mock_ollama.chat_complete = fake_complete
    (tmp_path / "readme.md").write_text("hello", encoding="utf-8")

    test_settings = Settings(workspace_root=str(tmp_path))
    test_mgr = ApprovalManager()

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_ollama_client] = lambda: mock_ollama
    app.dependency_overrides[get_approval_manager] = lambda: test_mgr

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/agent/run",
                json={"session_id": "agent-api-test", "prompt": "List files in workspace"},
            )
            assert res.status_code == 200
            assert "event: plan" in res.text
            assert "event: tool_call" in res.text
            assert "event: report" in res.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_agent_handles_non_test_tasks_gracefully(tmp_path: Path):
    """
    Verify that tasks without automated tests (e.g., snake game, utility scripts without pytest)
    do not get trapped in test failure loops if run_tests is invoked.
    """
    mock_ollama = AsyncMock()
    turns = iter([
        json.dumps({
            "objective": "Create snake game",
            "files_to_create": ["snake_game.py"],
            "testing_strategy": "Run manually",
        }),
        json.dumps({
            "thought": "Create snake_game.py",
            "action": "create_file",
            "arguments": {"path": "snake_game.py", "content": "print('snake')\n"},
        }),
        json.dumps({
            "thought": "Try running tests anyway",
            "action": "run_tests",
            "arguments": {},
        }),
        json.dumps({
            "thought": "Complete task",
            "action": "final_answer",
            "report": {"summary": "Game created successfully."},
        }),
    ])

    async def fake_complete(**kwargs):
        return next(turns)

    mock_ollama.chat_complete = fake_complete

    orchestrator = AgentOrchestrator(
        workspace_root=tmp_path,
        ollama_client=mock_ollama,
        auto_approve=True,
    )

    events = []
    async for sse in orchestrator.run_task_stream("create a snake pygame in this folde"):
        lines = sse.strip().split("\n")
        ev_type = lines[0].replace("event: ", "")
        ev_data = json.loads(lines[1].replace("data: ", ""))
        events.append((ev_type, ev_data))

    ev_names = [e[0] for e in events]
    assert "report" in ev_names
    # Verify tool_result for run_tests reported success (skipped) without triggering repair loop
    tool_results = [e[1] for e in events if e[0] == "tool_result"]
    assert any(tr["tool"] == "run_tests" and tr["success"] is True for tr in tool_results)
    assert (tmp_path / "snake_game.py").exists()
