"""Tests for execution sandbox, timeouts, error inspection, git tools, and repair bounds."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.execution.command_runner import CommandRunner
from app.orchestrator.agent import AgentOrchestrator
from app.security.approval_manager import ApprovalManager
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_run_python_file_success_and_exception(tmp_path: Path):
    """Verify executing Python scripts captures stdout on success and tracebacks on failure."""
    good_py = tmp_path / "good.py"
    good_py.write_text("print('RESULT_OK')\n", encoding="utf-8")

    bad_py = tmp_path / "bad.py"
    bad_py.write_text("def boom():\n    raise ValueError('bad_input_val')\n\nboom()\n", encoding="utf-8")

    registry = ToolRegistry(workspace_root=tmp_path, auto_approve=True)

    res_ok = await registry.execute_tool("run_python_file", {"path": "good.py"})
    assert res_ok.success is True
    assert "RESULT_OK" in res_ok.output

    res_bad = await registry.execute_tool("run_python_file", {"path": "bad.py"})
    assert res_bad.success is False
    assert "ValueError: bad_input_val" in (res_bad.error or res_bad.output)

    # Analyze captured error using inspect_error tool
    diag = await registry.execute_tool("inspect_error", {"error_output": res_bad.error or res_bad.output})
    assert diag.success is True
    parsed = json.loads(diag.output)
    assert "ValueError: bad_input_val" in parsed["probable_exception"]
    assert parsed["workspace_locations_found"] >= 1
    assert parsed["snippets"][0]["file"] == "bad.py"


@pytest.mark.asyncio
async def test_command_timeout_kills_process(tmp_path: Path):
    """Verify long-running commands are killed cleanly upon timeout."""
    runner = CommandRunner(workspace_root=tmp_path, timeout=0.8)
    py_exe = runner.get_python_executable()

    res = await runner.execute_direct(f'"{py_exe}" -c "import time; time.sleep(5)"', timeout=0.6)
    assert res.timed_out is True
    assert res.success is False


@pytest.mark.asyncio
async def test_forbidden_destructive_command_blocked(tmp_path: Path):
    """Verify destructive commands are blocked prior to subprocess spawn."""
    runner = CommandRunner(workspace_root=tmp_path)
    with pytest.raises(ValueError, match="Blocked forbidden destructive command"):
        await runner.execute_direct("rm -rf /")


@pytest.mark.asyncio
async def test_get_terminal_output_retrieves_latest(tmp_path: Path):
    """Verify terminal output history buffer tracks recent command executions."""
    registry = ToolRegistry(workspace_root=tmp_path, auto_approve=True)
    script = tmp_path / "echo_test.py"
    script.write_text("print('HELLO_TERMINAL')\n", encoding="utf-8")

    await registry.execute_tool("run_python_file", {"path": "echo_test.py"})
    out_res = await registry.execute_tool("get_terminal_output", {})
    assert out_res.success is True
    assert "HELLO_TERMINAL" in out_res.output


@pytest.mark.asyncio
async def test_git_tools_in_workspace(tmp_path: Path):
    """Verify read-only git tools run cleanly inside a git repository."""
    runner = CommandRunner(workspace_root=tmp_path)
    await runner.execute_direct("git init")
    await runner.execute_direct('git config user.email "test@localforge.ai"')
    await runner.execute_direct('git config user.name "Test"')
    (tmp_path / "hello.txt").write_text("v1\n", encoding="utf-8")
    await runner.execute_direct("git add hello.txt")
    await runner.execute_direct('git commit -m "initial commit"')

    (tmp_path / "hello.txt").write_text("v2\n", encoding="utf-8")

    registry = ToolRegistry(workspace_root=tmp_path)

    status_res = await registry.execute_tool("git_status", {})
    assert status_res.success is True
    assert "hello.txt" in status_res.output

    diff_res = await registry.execute_tool("git_diff", {})
    assert diff_res.success is True
    assert "+v2" in diff_res.output

    log_res = await registry.execute_tool("git_log", {"limit": 5})
    assert log_res.success is True
    assert "initial commit" in log_res.output


@pytest.mark.asyncio
async def test_agent_max_repair_attempts_enforced(tmp_path: Path):
    """Verify orchestrator enforces max_repair_attempts without looping indefinitely."""
    (tmp_path / "test_fail.py").write_text("def test_always_fails():\n    assert 1 == 2\n", encoding="utf-8")

    mock_ollama = AsyncMock()
    # Model tries tests -> edits -> tries tests again -> hits max_repair_attempts=2 -> concludes
    turns = [
        json.dumps({"objective": "Fix tests", "required_changes": ["Run tests"]}),
        json.dumps({"thought": "Run tests", "action": "run_tests", "arguments": {"test_path": "test_fail.py"}}),
        json.dumps({"thought": "Attempt repair 1", "action": "edit_file", "arguments": {"path": "test_fail.py", "target": "1 == 2", "replacement": "1 == 3"}}),
        json.dumps({"thought": "Run tests again", "action": "run_tests", "arguments": {"test_path": "test_fail.py"}}),
        json.dumps({"thought": "Conclude", "action": "final_answer", "report": {"summary": "Max repairs hit", "remaining_issues": "Assertion 1 == 3 fails"}}),
    ]
    turn_iter = iter(turns)

    async def fake_complete(**kwargs):
        return next(turn_iter, json.dumps({"thought": "Stop", "action": "final_answer", "report": {"summary": "Stopped"}}))

    mock_ollama.chat_complete = fake_complete

    orchestrator = AgentOrchestrator(
        workspace_root=tmp_path,
        ollama_client=mock_ollama,
        approval_manager=ApprovalManager(),
        auto_approve=True,
    )

    events = []
    async for sse in orchestrator.run_task_stream(
        prompt="Fix failing test",
        max_iterations=6,
        max_repair_attempts=2,
    ):
        lines = sse.strip().split("\n")
        ev_type = lines[0].replace("event: ", "")
        ev_data = json.loads(lines[1].replace("data: ", ""))
        events.append((ev_type, ev_data))

    debug_activities = [d for t, d in events if t == "activity" and d.get("phase") == "DEBUG"]
    assert len(debug_activities) == 2  # Exactly 2 repair cycles triggered

    report = next(d for t, d in events if t == "report")
    assert "Max repairs hit" in report["summary"] or "1 == 2" in report["remaining_issues"]
