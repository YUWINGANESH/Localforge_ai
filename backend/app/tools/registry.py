"""Extensible agent tool registry implementing all 18 core file, dev, and git tools."""
import asyncio
import importlib.util
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.context.file_reader import FileAccessError, SafeFileReader, SecurityPathViolationError
from app.context.scanner import DEFAULT_IGNORE_DIRS, DEFAULT_IGNORE_EXTENSIONS, WorkspaceScanner
from app.context.search import CodeSearchEngine
from app.execution.command_runner import CommandRunner
from app.schemas.approval import ApprovalRequest, OperationType
from app.security.approval_manager import ApprovalManager, get_approval_manager
from app.security.path_guard import PathSecurityError, resolve_safe_workspace_path
from app.tools.error_inspector import inspect_error_trace
from app.tools.file_ops import FileTools

logger = logging.getLogger(__name__)


class ToolResult(BaseModel):
    """Standardized output returned by every registered agent tool."""
    success: bool
    tool_name: str
    output: str
    approval_required: bool = False
    approval_request: ApprovalRequest | None = None
    error: str | None = None


@dataclass
class ToolDefinition:
    """Metadata and execution handler for a single agent tool."""
    name: str
    description: str
    parameters: dict[str, str]
    required: list[str]
    requires_approval: bool
    handler: Callable[[dict[str, Any]], Awaitable[ToolResult]]


class ToolRegistry:
    """
    Central registry managing all 18 local agent tools with validation,
    sandboxing, and permission policy enforcement.
    """

    def __init__(
        self,
        workspace_root: Path,
        approval_manager: ApprovalManager | None = None,
        auto_approve: bool = False,
    ):
        self.workspace_root = workspace_root.resolve()
        self.approval_manager = approval_manager or get_approval_manager()
        self.auto_approve = auto_approve

        self.scanner = WorkspaceScanner(self.workspace_root)
        self.search_engine = CodeSearchEngine(self.workspace_root)
        self.file_reader = SafeFileReader(self.workspace_root)
        self.file_tools = FileTools(self.workspace_root, self.approval_manager)
        self.command_runner = CommandRunner(self.workspace_root, self.approval_manager)

        self._tools: dict[str, ToolDefinition] = {}
        self._register_all_tools()

    def _register_all_tools(self):
        # --- FILE OPERATIONS (1 - 8) ---
        self.register(ToolDefinition(
            name="list_files",
            description="List files in workspace or subdirectory.",
            parameters={"directory": "Relative directory path (default '.')", "max_files": "Max entries (default 60)"},
            required=[],
            requires_approval=False,
            handler=self._tool_list_files,
        ))
        self.register(ToolDefinition(
            name="search_files",
            description="Search for files matching a filename pattern or substring.",
            parameters={"pattern": "Filename or glob pattern (e.g. '*.py' or 'main')"},
            required=["pattern"],
            requires_approval=False,
            handler=self._tool_search_files,
        ))
        self.register(ToolDefinition(
            name="search_code",
            description="Search code contents for text, regex, or symbol definitions.",
            parameters={"query": "Search term or symbol name", "search_type": "'text' or 'symbol'"},
            required=["query"],
            requires_approval=False,
            handler=self._tool_search_code,
        ))
        self.register(ToolDefinition(
            name="read_file",
            description="Read contents or specific line ranges of a workspace file.",
            parameters={"path": "Relative file path", "start_line": "1-based start line", "end_line": "1-based end line"},
            required=["path"],
            requires_approval=False,
            handler=self._tool_read_file,
        ))
        self.register(ToolDefinition(
            name="create_file",
            description="Create a new file (fails if file already exists; requires approval).",
            parameters={"path": "Relative file path", "content": "Full text content", "description": "Reason"},
            required=["path", "content"],
            requires_approval=True,
            handler=self._tool_create_file,
        ))
        self.register(ToolDefinition(
            name="write_file",
            description="Write or overwrite an entire file (requires approval).",
            parameters={"path": "Relative file path", "content": "Full text content", "description": "Reason"},
            required=["path", "content"],
            requires_approval=True,
            handler=self._tool_write_file,
        ))
        self.register(ToolDefinition(
            name="edit_file",
            description="Replace an exact target code snippet in an existing file (requires approval).",
            parameters={"path": "Relative file path", "target": "Exact text to replace", "replacement": "New text"},
            required=["path", "target", "replacement"],
            requires_approval=True,
            handler=self._tool_edit_file,
        ))
        self.register(ToolDefinition(
            name="delete_file",
            description="Delete a file in the workspace (requires approval).",
            parameters={"path": "Relative file path", "description": "Reason for deletion"},
            required=["path"],
            requires_approval=True,
            handler=self._tool_delete_file,
        ))

        # --- DEVELOPMENT OPERATIONS (9 - 15) ---
        self.register(ToolDefinition(
            name="run_python_file",
            description="Execute a Python script inside the workspace (requires approval).",
            parameters={"path": "Relative path to .py file", "args": "Optional CLI arguments"},
            required=["path"],
            requires_approval=True,
            handler=self._tool_run_python_file,
        ))
        self.register(ToolDefinition(
            name="run_tests",
            description="Run automated test suite using pytest or npm test (requires approval).",
            parameters={"test_path": "Test folder or file (default 'tests')", "framework": "'pytest' or 'npm'"},
            required=[],
            requires_approval=True,
            handler=self._tool_run_tests,
        ))
        self.register(ToolDefinition(
            name="inspect_project",
            description="Scan repository to inspect structure, languages, frameworks, and entry points.",
            parameters={},
            required=[],
            requires_approval=False,
            handler=self._tool_inspect_project,
        ))
        self.register(ToolDefinition(
            name="detect_dependencies",
            description="Inspect project manifests (requirements.txt, package.json) for dependencies.",
            parameters={},
            required=[],
            requires_approval=False,
            handler=self._tool_detect_dependencies,
        ))
        self.register(ToolDefinition(
            name="install_dependencies",
            description="Install packages via pip or npm (requires explicit approval).",
            parameters={"packages": "List of package names or space-separated string", "manager": "'pip' or 'npm'"},
            required=["packages"],
            requires_approval=True,
            handler=self._tool_install_dependencies,
        ))
        self.register(ToolDefinition(
            name="inspect_error",
            description="Analyze traceback/error output and extract surrounding failing source code.",
            parameters={"error_output": "Full error traceback or test failure text"},
            required=["error_output"],
            requires_approval=False,
            handler=self._tool_inspect_error,
        ))
        self.register(ToolDefinition(
            name="get_terminal_output",
            description="Retrieve stdout/stderr from the latest terminal command execution.",
            parameters={"execution_id": "Optional execution ID"},
            required=[],
            requires_approval=False,
            handler=self._tool_get_terminal_output,
        ))

        # --- GIT OPERATIONS (16 - 18) ---
        self.register(ToolDefinition(
            name="git_status",
            description="Check current git repository working tree status.",
            parameters={},
            required=[],
            requires_approval=False,
            handler=self._tool_git_status,
        ))
        self.register(ToolDefinition(
            name="git_diff",
            description="Inspect git diff of modified workspace files.",
            parameters={"path": "Optional specific file path"},
            required=[],
            requires_approval=False,
            handler=self._tool_git_diff,
        ))
        self.register(ToolDefinition(
            name="git_log",
            description="View recent git commit history.",
            parameters={"limit": "Number of recent commits (default 10)"},
            required=[],
            requires_approval=False,
            handler=self._tool_git_log,
        ))

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_compact_prompt_catalog(self) -> str:
        """Render a compact, token-efficient tool guide optimized for 4B-7B models."""
        lines = []
        for t in self._tools.values():
            params = ", ".join(f"{k}{' (req)' if k in t.required else ''}: {v}" for k, v in t.parameters.items())
            lines.append(f"- {t.name}({params}): {t.description}")
        return "\n".join(lines)

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        approval_notifier: Callable[[ApprovalRequest], Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ToolResult:
        """
        Validate arguments, enforce sandbox and approval gates, and execute the tool.
        """
        tool = self._tools.get(tool_name)
        if not tool:
            available = ", ".join(self._tools.keys())
            return ToolResult(
                success=False,
                tool_name=tool_name,
                output="",
                error=f"Unknown tool '{tool_name}'. Available tools: {available}",
            )

        # Normalize common argument aliases emitted by smaller LLMs
        args = self._normalize_arguments(tool_name, arguments or {})

        # Recovery: If LLM calls edit_file with full 'content' instead of target/replacement, route to write_file
        if tool_name == "edit_file" and "content" in args and ("target" not in args or "replacement" not in args):
            tool_name = "write_file"
            tool = self._tools["write_file"]

        # Validate required arguments
        missing = [req for req in tool.required if req not in args or args[req] is None]
        if missing:
            return ToolResult(
                success=False,
                tool_name=tool_name,
                output="",
                error=f"Missing required argument(s) for '{tool_name}': {', '.join(missing)}. Expected parameters: {tool.parameters}",
            )

        try:
            result = await tool.handler(args)

            # If approval is required and we are not auto-approving, wait for human resolution!
            if result.approval_required and result.approval_request is not None:
                if approval_notifier:
                    await approval_notifier(result.approval_request)

                decision = await self.approval_manager.wait_for_decision(
                    result.approval_request.id,
                    timeout=300.0,
                    cancel_event=cancel_event,
                )
                return ToolResult(
                    success=decision.success,
                    tool_name=tool_name,
                    output=decision.message,
                    approval_required=False,
                    approval_request=decision.approval_request,
                    error=None if decision.success else decision.message,
                )

            return result
        except (PathSecurityError, SecurityPathViolationError) as sec_err:
            return ToolResult(
                success=False,
                tool_name=tool_name,
                output="",
                error=str(sec_err),
            )
        except Exception as exc:
            logger.exception("Tool '%s' raised exception: %s", tool_name, exc)
            return ToolResult(
                success=False,
                tool_name=tool_name,
                output="",
                error=f"Tool execution error: {exc}",
            )

    def _normalize_arguments(self, tool_name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
        """Handle harmless LLM parameter naming variations (e.g. file_path -> path)."""
        args = dict(raw_args)
        if "path" not in args:
            for alias in ("file_path", "filepath", "file", "filename"):
                if alias in args and args[alias]:
                    args["path"] = args[alias]
                    break
        if tool_name == "search_files" and "pattern" not in args:
            for alias in ("query", "filename", "name", "path"):
                if alias in args:
                    args["pattern"] = args[alias]
                    break
        if tool_name == "search_code" and "query" not in args:
            for alias in ("pattern", "text", "symbol"):
                if alias in args:
                    args["query"] = args[alias]
                    break
        return args

    # ---------------- Tool Handler Implementations ----------------

    async def _tool_list_files(self, args: dict[str, Any]) -> ToolResult:
        rel_dir = str(args.get("directory", ".")).strip() or "."
        max_files = int(args.get("max_files", 60))

        target_dir = resolve_safe_workspace_path(self.workspace_root, rel_dir)
        if not target_dir.exists() or not target_dir.is_dir():
            return ToolResult(success=False, tool_name="list_files", output="", error=f"Directory not found: {rel_dir}")

        collected = []
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".git")]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in DEFAULT_IGNORE_EXTENSIONS:
                    continue
                p = Path(root) / file
                collected.append(p.relative_to(self.workspace_root).as_posix())
                if len(collected) >= max_files:
                    break
            if len(collected) >= max_files:
                break

        output = "\n".join(sorted(collected)) if collected else "(No files found)"
        return ToolResult(success=True, tool_name="list_files", output=output)

    async def _tool_search_files(self, args: dict[str, Any]) -> ToolResult:
        res = self.search_engine.search(query=str(args["pattern"]), search_type="filename", max_results=30)
        paths = [r.relative_path for r in res.results]
        output = "\n".join(paths) if paths else f"No files matching '{args['pattern']}'."
        return ToolResult(success=True, tool_name="search_files", output=output)

    async def _tool_search_code(self, args: dict[str, Any]) -> ToolResult:
        search_type = str(args.get("search_type", "text"))
        res = self.search_engine.search(query=str(args["query"]), search_type=search_type, max_results=20)
        if res.total_matches == 0:
            return ToolResult(success=True, tool_name="search_code", output=f"No matches for '{args['query']}'.")

        lines = []
        for file_res in res.results:
            for m in file_res.matches:
                lines.append(f"{file_res.relative_path}:{m.line_number}: {m.line_content}")
        return ToolResult(success=True, tool_name="search_code", output="\n".join(lines))

    async def _tool_read_file(self, args: dict[str, Any]) -> ToolResult:
        try:
            start_line = int(args["start_line"]) if args.get("start_line") else None
            end_line = int(args["end_line"]) if args.get("end_line") else None
            res = self.file_reader.read_file(str(args["path"]), start_line=start_line, end_line=end_line)
            header = f"File: {res.relative_path} (Lines {res.start_line}-{res.end_line} of {res.total_lines})\n"
            return ToolResult(success=True, tool_name="read_file", output=header + res.content)
        except (FileNotFoundError, FileAccessError) as exc:
            return ToolResult(success=False, tool_name="read_file", output="", error=str(exc))

    async def _tool_create_file(self, args: dict[str, Any]) -> ToolResult:
        op_res = await self.file_tools.create_file(
            path=str(args["path"]),
            content=str(args["content"]),
            description=args.get("description"),
            require_approval=not self.auto_approve,
        )
        return ToolResult(
            success=op_res.success,
            tool_name="create_file",
            output=op_res.message,
            approval_required=op_res.approval_required,
            approval_request=op_res.approval_request,
            error=None if op_res.success else op_res.message,
        )

    async def _tool_write_file(self, args: dict[str, Any]) -> ToolResult:
        op_res = await self.file_tools.write_file(
            path=str(args["path"]),
            content=str(args["content"]),
            description=args.get("description"),
            require_approval=not self.auto_approve,
        )
        return ToolResult(
            success=op_res.success,
            tool_name="write_file",
            output=op_res.message,
            approval_required=op_res.approval_required,
            approval_request=op_res.approval_request,
            error=None if op_res.success else op_res.message,
        )

    async def _tool_edit_file(self, args: dict[str, Any]) -> ToolResult:
        op_res = await self.file_tools.edit_file(
            path=str(args["path"]),
            target=str(args["target"]),
            replacement=str(args["replacement"]),
            description=args.get("description"),
            require_approval=not self.auto_approve,
        )
        return ToolResult(
            success=op_res.success,
            tool_name="edit_file",
            output=op_res.message,
            approval_required=op_res.approval_required,
            approval_request=op_res.approval_request,
            error=None if op_res.success else op_res.message,
        )

    async def _tool_delete_file(self, args: dict[str, Any]) -> ToolResult:
        op_res = await self.file_tools.delete_file(
            path=str(args["path"]),
            description=args.get("description"),
            require_approval=not self.auto_approve,
        )
        return ToolResult(
            success=op_res.success,
            tool_name="delete_file",
            output=op_res.message,
            approval_required=op_res.approval_required,
            approval_request=op_res.approval_request,
            error=None if op_res.success else op_res.message,
        )

    async def _tool_run_python_file(self, args: dict[str, Any]) -> ToolResult:
        resolved = resolve_safe_workspace_path(self.workspace_root, str(args["path"]))
        if not resolved.exists():
            return ToolResult(success=False, tool_name="run_python_file", output="", error=f"File not found: {args['path']}")

        rel = resolved.relative_to(self.workspace_root).as_posix()
        py_exe = self.command_runner.get_python_executable()
        extra_args = str(args.get("args", "")).strip()
        cmd = f'"{py_exe}" "{rel}" {extra_args}'.strip()

        op_res, _ = await self.command_runner.propose_command(
            command=cmd,
            description=f"Run Python script '{rel}'",
            operation_type=OperationType.EXECUTE_COMMAND,
            require_approval=not self.auto_approve,
        )
        return ToolResult(
            success=op_res.success,
            tool_name="run_python_file",
            output=op_res.message,
            approval_required=op_res.approval_required,
            approval_request=op_res.approval_request,
            error=None if op_res.success else op_res.message,
        )

    async def _tool_run_tests(self, args: dict[str, Any]) -> ToolResult:
        framework = str(args.get("framework", "pytest")).lower()
        test_path = str(args.get("test_path", "")).strip()

        if framework == "npm":
            cmd = "npm test"
        else:
            py_exe = self.command_runner.get_python_executable()
            # If no test_path is specified, check if any test files exist in the workspace
            if not test_path:
                test_candidates = [
                    p for p in self.workspace_root.glob("**/test_*.py")
                    if not any(part.startswith(".") or part in {"node_modules", "venv", "__pycache__"} for part in p.parts)
                ] + [
                    p for p in self.workspace_root.glob("**/*_test.py")
                    if not any(part.startswith(".") or part in {"node_modules", "venv", "__pycache__"} for part in p.parts)
                ]
                if not test_candidates:
                    return ToolResult(
                        success=True,
                        tool_name="run_tests",
                        output="No automated test files found in workspace (e.g., test_*.py). Test verification skipped. Proceed with final_answer.",
                        error=None,
                    )
            cmd = f'"{py_exe}" -m pytest {test_path}'.strip()

        op_res, cmd_res = await self.command_runner.propose_command(
            command=cmd,
            description=f"Run automated tests ({cmd})",
            operation_type=OperationType.EXECUTE_COMMAND,
            require_approval=not self.auto_approve,
        )

        # Pytest exit code 5 means NO_TESTS_COLLECTED (valid syntax, 0 tests collected)
        is_success = op_res.success
        msg = op_res.message
        if cmd_res and cmd_res.return_code == 5:
            is_success = True
            msg = "Pytest executed: No automated tests collected (exit code 5). Source files parsed without collection errors. Conclude with final_answer."

        return ToolResult(
            success=is_success,
            tool_name="run_tests",
            output=msg,
            approval_required=op_res.approval_required,
            approval_request=op_res.approval_request,
            error=None if is_success else msg,
        )

    async def _tool_inspect_project(self, _args: dict[str, Any]) -> ToolResult:
        summary = self.scanner.scan()
        return ToolResult(
            success=True,
            tool_name="inspect_project",
            output=summary.model_dump_json(indent=2),
        )

    async def _tool_detect_dependencies(self, _args: dict[str, Any]) -> ToolResult:
        summary = self.scanner.scan()
        deps = [{"name": d.name, "version": d.version, "category": d.category} for d in summary.dependencies]
        return ToolResult(
            success=True,
            tool_name="detect_dependencies",
            output=json.dumps({"frameworks": summary.frameworks, "dependencies": deps}, indent=2),
        )

    async def _tool_install_dependencies(self, args: dict[str, Any]) -> ToolResult:
        raw_pkgs = args["packages"]
        if isinstance(raw_pkgs, list):
            pkg_str = " ".join(str(p).strip() for p in raw_pkgs if str(p).strip())
        else:
            pkg_str = str(raw_pkgs).strip()

        manager = str(args.get("manager", "pip")).lower()

        # Fast path: check if package(s) are already importable in Python environment
        if manager != "npm" and pkg_str:
            pkgs = [p.strip() for p in pkg_str.split() if p.strip()]
            if pkgs:
                all_installed = True
                for p in pkgs:
                    clean_name = re.split(r"[><=~]", p)[0].strip().replace("-", "_")
                    try:
                        if not importlib.util.find_spec(clean_name):
                            all_installed = False
                            break
                    except Exception:
                        all_installed = False
                        break
                if all_installed:
                    return ToolResult(
                        success=True,
                        tool_name="install_dependencies",
                        output=f"Package(s) '{pkg_str}' are already installed and available in the environment.",
                    )

        if manager == "npm":
            cmd = f"npm install {pkg_str}"
        else:
            py_exe = self.command_runner.get_python_executable()
            cmd = f'"{py_exe}" -m pip install {pkg_str}'

        op_res, _ = await self.command_runner.propose_command(
            command=cmd,
            description=f"Install dependencies via {manager}: {pkg_str}",
            operation_type=OperationType.INSTALL_DEPENDENCY,
            require_approval=not self.auto_approve,
        )
        return ToolResult(
            success=op_res.success,
            tool_name="install_dependencies",
            output=op_res.message,
            approval_required=op_res.approval_required,
            approval_request=op_res.approval_request,
            error=None if op_res.success else op_res.message,
        )

    async def _tool_inspect_error(self, args: dict[str, Any]) -> ToolResult:
        err_text = str(args.get("error_output", "")).strip()
        # If model passed a summary/placeholder string, fall back to actual terminal output
        if len(err_text) < 50 or ("line " not in err_text and ".py" not in err_text):
            latest = self.command_runner.get_latest_output()
            if latest and (latest.stderr or latest.stdout):
                err_text = f"{latest.stdout}\n{latest.stderr}"
        info = inspect_error_trace(self.workspace_root, err_text)
        return ToolResult(
            success=True,
            tool_name="inspect_error",
            output=json.dumps(info, indent=2),
        )

    async def _tool_get_terminal_output(self, args: dict[str, Any]) -> ToolResult:
        exec_id = args.get("execution_id")
        res = self.command_runner.get_latest_output(execution_id=str(exec_id) if exec_id else None)
        if not res:
            return ToolResult(success=True, tool_name="get_terminal_output", output="No terminal commands executed yet.")
        return ToolResult(
            success=True,
            tool_name="get_terminal_output",
            output=f"[{res.execution_id}] {res.command} (exit {res.return_code})\n{res.combined_output()}",
        )

    async def _tool_git_status(self, _args: dict[str, Any]) -> ToolResult:
        res = await self.command_runner.execute_direct("git status --short")
        return ToolResult(
            success=res.success,
            tool_name="git_status",
            output=res.combined_output(),
            error=None if res.success else res.stderr,
        )

    async def _tool_git_diff(self, args: dict[str, Any]) -> ToolResult:
        path = args.get("path")
        if path:
            resolved = resolve_safe_workspace_path(self.workspace_root, str(path))
            rel = resolved.relative_to(self.workspace_root).as_posix()
            cmd = f'git diff -- "{rel}"'
        else:
            cmd = "git diff"
        res = await self.command_runner.execute_direct(cmd)
        return ToolResult(
            success=res.success,
            tool_name="git_diff",
            output=res.combined_output(),
            error=None if res.success else res.stderr,
        )

    async def _tool_git_log(self, args: dict[str, Any]) -> ToolResult:
        limit = int(args.get("limit", 10))
        res = await self.command_runner.execute_direct(f"git log -n {limit} --oneline")
        return ToolResult(
            success=res.success,
            tool_name="git_log",
            output=res.combined_output(),
            error=None if res.success else res.stderr,
        )
