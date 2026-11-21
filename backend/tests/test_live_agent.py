"""Live autonomous integration test using local Ollama qwen2.5-coder:7b (when available)."""
import asyncio
import json
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.llm.ollama_client import OllamaClient
from app.orchestrator.agent import AgentOrchestrator
from app.security.approval_manager import ApprovalManager


@pytest.mark.asyncio
async def test_live_agent_build_test_repair(tmp_path: Path):
    """
    Verify live qwen2.5-coder:7b autonomous agent inspects buggy code,
    fixes it via approval flow, writes unit tests, runs pytest, and reports completion.
    """
    settings = Settings(request_timeout_seconds=120.0)
    ollama = OllamaClient(settings=settings)
    online, _, _ = await ollama.check_health()
    if not online:
        pytest.skip("Ollama is offline. Skipping live agent integration test.")

    has_model = await ollama.is_model_installed(settings.active_model)
    if not has_model:
        pytest.skip(f"Model {settings.active_model} not installed.")

    # Seed buggy file in isolated workspace
    math_file = tmp_path / "math_ops.py"
    math_file.write_text("def multiply(a, b):\n    return a + b\n", encoding="utf-8")

    mgr = ApprovalManager()
    orchestrator = AgentOrchestrator(
        workspace_root=tmp_path,
        ollama_client=ollama,
        approval_manager=mgr,
        settings=settings,
        auto_approve=False,  # Verify full approval flow with live model
    )

    prompt = (
        "Inspect math_ops.py. Fix the bug in multiply(a, b) so it returns a * b. "
        "Then create test_math_ops.py with a pytest test asserting multiply(3, 4) == 12, "
        "run run_tests on test_math_ops.py to confirm it passes, and conclude with final_answer."
    )

    events = []
    report_data = None

    async for sse in orchestrator.run_task_stream(prompt=prompt, max_iterations=8):
        lines = sse.strip().split("\n")
        ev_type = lines[0].replace("event: ", "")
        ev_data = json.loads(lines[1].replace("data: ", ""))
        events.append((ev_type, ev_data))

        if ev_type == "approval_required":
            asyncio.create_task(mgr.resolve(ev_data["id"], approved=True))
        elif ev_type == "report":
            report_data = ev_data

    assert any(t == "plan" for t, _ in events), "Expected structured execution plan"
    assert report_data is not None, "Expected completion report"

    # Verify the bug in math_ops.py was actually fixed on disk!
    fixed_content = math_file.read_text(encoding="utf-8")
    assert "a * b" in fixed_content, f"Expected multiply bug fixed, got: {fixed_content}"

    # Verify test file was created and tests ran
    test_file = tmp_path / "test_math_ops.py"
    assert test_file.exists(), "Expected test_math_ops.py to be created"
    assert len(report_data.get("tests_performed", [])) >= 1, "Expected run_tests execution recorded"
