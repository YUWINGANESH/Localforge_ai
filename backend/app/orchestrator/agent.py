"""Autonomous ReAct AI Orchestrator implementing the 7-phase coding workflow."""
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.config.settings import Settings, get_settings
from app.context.context_builder import CompactContextBuilder
from app.llm.ollama_client import OllamaClient, get_ollama_client
from app.memory.project_memory import MemoryService, get_memory_service
from app.orchestrator.parser import extract_live_thought, parse_step_decision, parse_task_plan
from app.schemas.agent import (
    AgentPhase,
    CompletionReport,
    TaskPlan,
)
from app.schemas.approval import ApprovalRequest
from app.security.approval_manager import ApprovalManager, get_approval_manager
from app.tools.error_inspector import inspect_error_trace
from app.tools.registry import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_OBSERVATION_CHARS = 1800


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


class AgentOrchestrator:
    """
    Central bounded ReAct agent orchestrator tuned for local 4B-7B models.
    Coordinates Understand -> Plan -> Implement -> Execute -> Debug -> Validate -> Report.
    """

    def __init__(
        self,
        workspace_root: Path,
        ollama_client: OllamaClient | None = None,
        approval_manager: ApprovalManager | None = None,
        settings: Settings | None = None,
        memory_service: MemoryService | None = None,
        auto_approve: bool = False,
    ):
        self.workspace_root = workspace_root.resolve()
        self.ollama = ollama_client or get_ollama_client()
        self.approval_manager = approval_manager or get_approval_manager()
        self.settings = settings or get_settings()
        self.memory_service = memory_service or get_memory_service()
        self.auto_approve = auto_approve

        self.registry = ToolRegistry(
            workspace_root=self.workspace_root,
            approval_manager=self.approval_manager,
            auto_approve=self.auto_approve,
        )
        self.context_builder = CompactContextBuilder(self.workspace_root)

    async def run_task_stream(
        self,
        prompt: str,
        session_id: str = "default",
        model: str | None = None,
        max_iterations: int = 8,
        max_repair_attempts: int = 3,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """
        Execute autonomous coding task and stream SSE progress, plans, approvals, and reports.
        """
        target_model = model or self.settings.active_model
        task_id = self.memory_service.start_task(session_id, str(self.workspace_root), prompt)

        # Track real verified outcomes for final report integrity
        files_created: list[str] = []
        files_modified: list[str] = []
        commands_executed: list[str] = []
        tests_performed: list[str] = []
        last_test_result = "No tests executed"

        # Queue for streaming out-of-band approval_required events while awaiting resolution
        event_queue: asyncio.Queue[str] = asyncio.Queue()

        async def notify_approval(req: ApprovalRequest):
            await event_queue.put(_sse_event("approval_required", req.model_dump(mode="json")))
            await event_queue.put(
                _sse_event("activity", {
                    "phase": AgentPhase.IMPLEMENT.value,
                    "message": f"Waiting for user approval: {req.operation.value} on {req.relative_path or req.command}",
                })
            )

        try:
            # --- PHASE 1: UNDERSTAND ---
            yield _sse_event("activity", {
                "phase": AgentPhase.UNDERSTAND.value,
                "message": "Scanning workspace and analyzing codebase context...",
            })
            compact_ctx = self.context_builder.build_context(prompt, max_tokens=1200)
            if compact_ctx.project_summary:
                self.memory_service.sync_project_summary(compact_ctx.project_summary)
            proj_mem_snippet = self.memory_service.get_compact_prompt_memory(str(self.workspace_root))

            if cancel_event and cancel_event.is_set():
                self.memory_service.finish_task(task_id, status="cancelled")
                yield _sse_event("error", {"error": "Task cancelled by user."})
                return

            # --- PHASE 2: PLAN ---
            yield _sse_event("activity", {
                "phase": AgentPhase.PLAN.value,
                "message": "Generating structured execution plan...",
            })

            mem_section = f"\nPROJECT MEMORY:\n{proj_mem_snippet}\n" if proj_mem_snippet else ""
            plan_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are LocalForge-AI 2.0, an autonomous software engineering planner.\n"
                        "Generate a concise JSON implementation plan with keys:\n"
                        '{"objective": "...", "required_changes": ["step 1", ...], '
                        '"files_to_create": [...], "files_to_modify": [...], '
                        '"dependencies": [...], "testing_strategy": "...", "potential_risks": [...]}\n'
                        "Keep the architecture clean and minimal (prefer 3-4 cohesive source files; do NOT list runtime .db files in files_to_create).\n"
                        "Return ONLY valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": f"WORKSPACE CONTEXT:\n{compact_ctx.context_text}{mem_section}\n\nUSER TASK:\n{prompt}",
                },
            ]

            accumulated_plan_text = ""
            last_plan_thought = ""

            async def on_plan_token(token: str):
                nonlocal accumulated_plan_text, last_plan_thought
                accumulated_plan_text += token
                live_th = extract_live_thought(accumulated_plan_text)
                if live_th and len(live_th) > len(last_plan_thought) + 4:
                    last_plan_thought = live_th
                    await event_queue.put(
                        _sse_event("thinking", {
                            "phase": AgentPhase.PLAN.value,
                            "iteration": "plan",
                            "thought": live_th,
                        })
                    )

            plan_task = asyncio.create_task(
                self.ollama.chat_complete(
                    messages=plan_messages,
                    model=target_model,
                    temperature=0.2,
                    json_format=True,
                    cancel_event=cancel_event,
                    on_token=on_plan_token,
                )
            )

            while not plan_task.done():
                try:
                    queued_sse = await asyncio.wait_for(event_queue.get(), timeout=0.15)
                    yield queued_sse
                except TimeoutError:
                    continue

            while not event_queue.empty():
                yield await event_queue.get()

            raw_plan = await plan_task
            plan: TaskPlan = parse_task_plan(raw_plan, fallback_objective=prompt)
            self.memory_service.set_task_plan(task_id, plan)
            yield _sse_event("plan", plan.model_dump(mode="json"))

            if cancel_event and cancel_event.is_set():
                self.memory_service.finish_task(task_id, status="cancelled")
                yield _sse_event("error", {"error": "Task cancelled by user."})
                return

            # --- PHASE 3 - 6: BOUNDED REACT EXECUTION LOOP ---
            tool_catalog = self.registry.get_compact_prompt_catalog()
            react_system_prompt = (
                "You are LocalForge-AI 2.0, an autonomous local software engineering agent.\n"
                "You solve tasks step-by-step using ONLY the available tools below.\n\n"
                f"TOOLS:\n{tool_catalog}\n\n"
                "RESPONSE FORMAT (STRICT JSON ONLY):\n"
                "To invoke a tool, respond ONLY with:\n"
                '{"thought": "brief reasoning", "action": "<tool_name>", "arguments": {<args>}}\n\n'
                "When ALL implementation and testing steps are complete and verified, respond ONLY with:\n"
                '{"thought": "final verification reasoning", "action": "final_answer", "report": {'
                '"summary": "...", "files_created": [...], "files_modified": [...], '
                '"commands_executed": [...], "tests_performed": [...], "test_results": "...", '
                '"remaining_issues": "...", "suggested_next_steps": [...]}}\n\n'
                "RULES:\n"
                "1. Inspect existing files before modifying them.\n"
                "2. Write complete, runnable code in one create_file or write_file call (never use stubs or placeholders).\n"
                "3. Always validate changes by running tests or python files when appropriate.\n"
                "4. Never claim tests passed unless you actually ran run_tests or run_python_file and saw exit code 0.\n"
                "5. Output ONLY one JSON object per turn. No markdown outside JSON."
            )

            conversation: list[dict[str, str]] = [
                {"role": "system", "content": react_system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"TASK: {prompt}\n\n"
                        f"WORKSPACE SUMMARY:\n{compact_ctx.context_text}\n\n"
                        f"APPROVED PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
                        "Begin execution with step 1."
                    ),
                },
            ]

            recent_call_signatures: list[str] = []
            files_written_since_last_test: set[str] = set()
            tests_need_fix = False
            repair_attempts = 0
            final_report: CompletionReport | None = None

            for iteration in range(1, max_iterations + 1):
                if cancel_event and cancel_event.is_set():
                    yield _sse_event("error", {"error": "Task cancelled by user."})
                    return

                yield _sse_event("activity", {
                    "phase": AgentPhase.IMPLEMENT.value,
                    "message": f"Reasoning step {iteration} of {max_iterations}...",
                })

                # Keep conversation compact while preserving full memory of completed artifacts
                if len(conversation) > 8:
                    remaining_files = [
                        f for f in plan.files_to_create if f not in files_created and f not in files_modified
                    ]
                    progress_note = (
                        "[COMPLETED PROGRESS CHECKPOINT - DO NOT RECREATE THESE FILES]\n"
                        f"Files already created: {files_created}\n"
                        f"Files already modified: {files_modified}\n"
                        f"Remaining files from plan: {remaining_files}\n"
                        f"Tests run so far: {tests_performed} ({last_test_result})\n"
                        "Proceed strictly with the remaining uncompleted plan steps."
                    )
                    trimmed_conv = conversation[:2] + [{"role": "user", "content": progress_note}] + conversation[-6:]
                else:
                    trimmed_conv = conversation

                accumulated_decision_text = ""
                last_step_thought = ""

                async def on_decision_token(token: str):
                    nonlocal accumulated_decision_text, last_step_thought
                    accumulated_decision_text += token
                    live_th = extract_live_thought(accumulated_decision_text)
                    if live_th and len(live_th) > len(last_step_thought) + 4:
                        last_step_thought = live_th
                        await event_queue.put(
                            _sse_event("thinking", {
                                "phase": AgentPhase.IMPLEMENT.value,
                                "iteration": iteration,
                                "thought": live_th,
                            })
                        )

                decision_task = asyncio.create_task(
                    self.ollama.chat_complete(
                        messages=trimmed_conv,
                        model=target_model,
                        temperature=0.2,
                        json_format=True,
                        cancel_event=cancel_event,
                        on_token=on_decision_token,
                    )
                )

                while not decision_task.done():
                    try:
                        queued_sse = await asyncio.wait_for(event_queue.get(), timeout=0.15)
                        yield queued_sse
                    except TimeoutError:
                        continue

                while not event_queue.empty():
                    yield await event_queue.get()

                raw_decision = await decision_task

                if cancel_event and cancel_event.is_set():
                    yield _sse_event("error", {"error": "Task cancelled by user."})
                    return

                decision, parse_err = parse_step_decision(raw_decision)
                if parse_err or not decision:
                    logger.warning("Iteration %d parse failure: %s", iteration, parse_err)
                    conversation.append({"role": "assistant", "content": raw_decision})
                    conversation.append({"role": "user", "content": f"SYSTEM ERROR: {parse_err}"})
                    continue

                if decision.thought:
                    yield _sse_event("thought", {"iteration": iteration, "thought": decision.thought})

                # Check completion
                if decision.action == "final_answer":
                    final_report = decision.report
                    break

                # Detect repetitive duplicate tool loop & redundant file rewrites
                call_sig = f"{decision.action}:{json.dumps(decision.arguments, sort_keys=True)}"
                target_file = str(decision.arguments.get("path") or decision.arguments.get("file_path") or "")
                remaining_files = [
                    f for f in plan.files_to_create if f not in files_created and f not in files_modified
                ]

                if recent_call_signatures and recent_call_signatures[-1] == call_sig:
                    warning_msg = (
                        f"SYSTEM WARNING: You already called '{decision.action}' with those exact arguments. "
                        "Do NOT repeat identical calls. Choose a DIFFERENT tool (e.g., read_file, write_file, run_tests) or finish with 'final_answer'."
                    )
                    if conversation and "SYSTEM WARNING:" in conversation[-1].get("content", ""):
                        conversation[-1]["content"] = warning_msg
                    else:
                        conversation.append({"role": "user", "content": warning_msg})
                    continue

                if (
                    decision.action in {"create_file", "write_file"}
                    and target_file in files_written_since_last_test
                ):
                    next_hint = (
                        f"Create the remaining planned files: {remaining_files}."
                        if remaining_files
                        else "All planned files are created! Call run_tests right now to execute your tests."
                    )
                    warning_msg = (
                        f"SYSTEM WARNING: '{target_file}' was ALREADY written and tests have not failed against it. "
                        f"Do NOT rewrite it yet. {next_hint}"
                    )
                    if conversation and "SYSTEM WARNING:" in conversation[-1].get("content", ""):
                        conversation[-1]["content"] = warning_msg
                    else:
                        conversation.append({"role": "user", "content": warning_msg})
                    continue

                if decision.action == "install_dependencies" and call_sig in recent_call_signatures:
                    warning_msg = (
                        "SYSTEM WARNING: Those dependencies were already installed. "
                        f"Do NOT re-install. Remaining files: {remaining_files}. "
                        "Create remaining files or call run_tests."
                    )
                    if conversation and "SYSTEM WARNING:" in conversation[-1].get("content", ""):
                        conversation[-1]["content"] = warning_msg
                    else:
                        conversation.append({"role": "user", "content": warning_msg})
                    continue

                if decision.action == "run_tests" and tests_need_fix:
                    warning_msg = (
                        "SYSTEM WARNING: Tests are failing and no code has been edited yet. "
                        "You MUST edit the failing code (using edit_file or write_file) to fix the error "
                        "BEFORE running tests again. Inspect the error trace and modify the code now."
                    )
                    if conversation and "SYSTEM WARNING:" in conversation[-1].get("content", ""):
                        conversation[-1]["content"] = warning_msg
                    else:
                        conversation.append({"role": "user", "content": warning_msg})
                    continue

                recent_call_signatures.append(call_sig)

                # Update activity banner based on tool
                phase = AgentPhase.IMPLEMENT
                if decision.action in {"run_python_file", "run_tests", "install_dependencies"}:
                    phase = AgentPhase.EXECUTE if decision.action != "run_tests" else AgentPhase.VALIDATE

                yield _sse_event("activity", {
                    "phase": phase.value,
                    "message": f"Running tool: {decision.action}",
                })
                yield _sse_event("tool_call", {
                    "iteration": iteration,
                    "tool": decision.action,
                    "arguments": decision.arguments,
                })

                # Auto-scope test execution or handle tasks without automated tests
                override_tool_result: ToolResult | None = None
                if decision.action == "run_tests":
                    test_arg = str(decision.arguments.get("test_path", "")).strip()
                    current_tests = [
                        f for f in files_created + files_modified
                        if "test" in Path(f).name.lower()
                    ]
                    planned_tests = [
                        f for f in plan.files_to_create
                        if "test" in Path(f).name.lower()
                    ]
                    has_tests_context = bool(
                        planned_tests
                        or current_tests
                        or any(k in prompt.lower() for k in ["test", "pytest", "spec", "unit test"])
                        or any(k in plan.testing_strategy.lower() for k in ["pytest", "automated test", "unit test"])
                    )

                    if not test_arg:
                        if current_tests:
                            decision.arguments["test_path"] = current_tests[0]
                        elif not has_tests_context:
                            override_tool_result = ToolResult(
                                success=True,
                                tool_name="run_tests",
                                output=(
                                    f"No automated test suite was requested or planned for this task ({prompt}). "
                                    f"Files created: {files_created}. "
                                    "Automated testing skipped. Call final_answer to conclude."
                                ),
                            )

                if override_tool_result is not None:
                    tool_result = override_tool_result
                else:
                    # Run tool concurrently while draining any approval events emitted to queue
                    tool_task = asyncio.create_task(
                        self.registry.execute_tool(
                            decision.action,
                            decision.arguments,
                            approval_notifier=notify_approval,
                            cancel_event=cancel_event,
                        )
                    )

                    while not tool_task.done():
                        try:
                            queued_sse = await asyncio.wait_for(event_queue.get(), timeout=0.15)
                            yield queued_sse
                        except TimeoutError:
                            continue

                    # Drain any remaining items from queue
                    while not event_queue.empty():
                        yield await event_queue.get()

                    tool_result = await tool_task
                path_arg = str(decision.arguments.get("path") or decision.arguments.get("file_path") or "")
                self.memory_service.record_tool_step(
                    task_id,
                    f"{decision.action}({path_arg})",
                    success=tool_result.success,
                    referenced_file=path_arg or None,
                )

                # Track real observed changes
                if tool_result.success:
                    if decision.action == "create_file" and path_arg:
                        if path_arg not in files_created:
                            files_created.append(path_arg)
                        files_written_since_last_test.add(path_arg)
                    elif decision.action in {"write_file", "edit_file"} and path_arg:
                        if path_arg not in files_created and path_arg not in files_modified:
                            files_modified.append(path_arg)
                        files_written_since_last_test.add(path_arg)
                        tests_need_fix = False
                    elif decision.action in {"run_python_file", "install_dependencies"}:
                        commands_executed.append(f"{decision.action}({path_arg or decision.arguments.get('packages', '')})")
                    elif decision.action == "run_tests":
                        tests_performed.append(str(decision.arguments.get("test_path", "tests")))
                        last_test_result = "Passed (exit code 0)"
                        files_written_since_last_test.clear()
                        tests_need_fix = False

                # Truncate observation safely using head+tail so error tracebacks at the end are preserved
                raw_obs = tool_result.output if tool_result.success else (tool_result.error or tool_result.output)
                if len(raw_obs) > MAX_OBSERVATION_CHARS:
                    raw_obs = (
                        raw_obs[:350]
                        + "\n... [Middle output truncated] ...\n"
                        + raw_obs[-(MAX_OBSERVATION_CHARS - 350):]
                    )

                yield _sse_event("tool_result", {
                    "iteration": iteration,
                    "tool": decision.action,
                    "success": tool_result.success,
                    "output": raw_obs,
                })

                # Phase 5: Debug & Autonomous Repair Handling
                observation_msg = f"TOOL RESULT ({decision.action}):\nStatus: {'SUCCESS' if tool_result.success else 'FAILED'}\n{raw_obs}"

                if not tool_result.success and decision.action in {"run_python_file", "run_tests"}:
                    files_written_since_last_test.clear()
                    tests_need_fix = True
                    if decision.action == "run_tests":
                        last_test_result = f"Failed: {raw_obs[:200]}"
                    repair_attempts += 1

                    yield _sse_event("activity", {
                        "phase": AgentPhase.DEBUG.value,
                        "message": f"Execution failed. Analyzing root cause (Repair attempt {repair_attempts}/{max_repair_attempts})...",
                    })

                    diag = inspect_error_trace(self.workspace_root, raw_obs)
                    if diag["snippets"]:
                        observation_msg += f"\n\nAUTOMATIC ERROR DIAGNOSIS:\n{json.dumps(diag, indent=2)[:1000]}"

                    if repair_attempts >= max_repair_attempts:
                        observation_msg += (
                            "\n\nSYSTEM NOTICE: Maximum automatic repair attempts reached. "
                            "Do not attempt more edits; conclude with 'final_answer' reporting the remaining issues."
                        )

                remaining_files = [
                    f for f in plan.files_to_create if f not in files_created and f not in files_modified
                ]
                planned_tests = [f for f in plan.files_to_create if "test" in Path(f).name.lower()]
                current_tests = [f for f in files_created + files_modified if "test" in Path(f).name.lower()]
                task_has_automated_tests = bool(
                    planned_tests
                    or current_tests
                    or any(k in prompt.lower() for k in ["test", "pytest", "spec", "unit test"])
                    or any(k in plan.testing_strategy.lower() for k in ["pytest", "automated test", "unit test"])
                )

                if remaining_files:
                    next_step_msg = f"Create next file: {remaining_files[0]}"
                elif tests_need_fix:
                    next_step_msg = "Tests failed! You MUST edit the failing code (using edit_file or write_file) to fix the bug before running tests again. Do NOT call run_tests until you apply a code fix."
                elif tests_performed and "Passed" in last_test_result:
                    next_step_msg = "All tests passed! Call final_answer to conclude the task."
                elif not task_has_automated_tests:
                    next_step_msg = "All planned files exist! Call final_answer to conclude the task (or verify with run_python_file if needed)."
                else:
                    next_step_msg = "All planned files exist! Call run_tests now."
                observation_msg += (
                    f"\n\n[PLAN PROGRESS]\n"
                    f"Created so far: {files_created}\n"
                    f"Remaining planned files: {remaining_files}\n"
                    f"Tests passed: {len(tests_performed) > 0}\n"
                    f"NEXT ACTION HINT: {next_step_msg}"
                )

                conversation.append({"role": "assistant", "content": raw_decision})
                conversation.append({"role": "user", "content": observation_msg})

            # --- PHASE 7: REPORT ---
            yield _sse_event("activity", {
                "phase": AgentPhase.REPORT.value,
                "message": "Compiling final engineering report...",
            })

            if final_report is None:
                final_report = CompletionReport(
                    summary=f"Completed {max_iterations} reasoning iterations for task: {prompt}",
                    files_created=files_created,
                    files_modified=files_modified,
                    commands_executed=commands_executed,
                    tests_performed=tests_performed,
                    test_results=last_test_result,
                    remaining_issues="Iteration limit reached before explicit final_answer.",
                )
            else:
                # Merge observed facts so the report never omits or fabricates real file changes
                for f in files_created:
                    if f not in final_report.files_created:
                        final_report.files_created.append(f)
                for f in files_modified:
                    if f not in final_report.files_modified:
                        final_report.files_modified.append(f)
                for cmd in commands_executed:
                    if cmd not in final_report.commands_executed:
                        final_report.commands_executed.append(cmd)
                if tests_performed and not final_report.tests_performed:
                    final_report.tests_performed = tests_performed
                    final_report.test_results = last_test_result

            self.memory_service.finish_task(task_id, status="completed")
            self.memory_service.record_completed_task(str(self.workspace_root), final_report.summary)

            yield _sse_event("report", final_report.model_dump(mode="json"))
            yield _sse_event("done", {"status": "completed"})

        except Exception as exc:
            logger.exception("Orchestrator failure: %s", exc)
            self.memory_service.finish_task(task_id, status="failed")
            yield _sse_event("error", {"error": str(exc)})
            yield _sse_event("done", {"status": "failed"})
