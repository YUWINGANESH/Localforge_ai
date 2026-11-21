"""Sandboxed asynchronous command execution with timeout, output capping, and approval gating."""
import asyncio
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.approval import FileOperationResult, OperationType
from app.security.approval_manager import ApprovalManager, get_approval_manager
from app.security.path_guard import redact_secrets

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 8000
DEFAULT_COMMAND_TIMEOUT = 60.0

FORBIDDEN_COMMAND_FRAGMENTS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "format c:",
    "del /s /q c:\\",
    "rd /s /q c:\\",
    ":(){:|:&};:",
]


@dataclass
class CommandResult:
    """Captured execution outcome of a subprocess command."""
    execution_id: str
    command: str
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float = 0.0
    executed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def success(self) -> bool:
        return self.return_code == 0 and not self.timed_out

    def combined_output(self) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(f"[STDOUT]\n{self.stdout.strip()}")
        if self.stderr.strip():
            parts.append(f"[STDERR]\n{self.stderr.strip()}")
        if not parts:
            parts.append("(No output)")
        return "\n\n".join(parts)


class CommandRunner:
    """Safely executes workspace-bound terminal commands with human approval."""

    def __init__(
        self,
        workspace_root: Path,
        approval_manager: ApprovalManager | None = None,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ):
        self.workspace_root = workspace_root.resolve()
        self.approval_manager = approval_manager or get_approval_manager()
        self.timeout = timeout
        self._history: list[CommandResult] = []

    def get_python_executable(self) -> str:
        """Select active virtual environment python if present in workspace, else current interpreter."""
        candidates = [
            self.workspace_root / ".venv" / "Scripts" / "python.exe",
            self.workspace_root / "venv" / "Scripts" / "python.exe",
            self.workspace_root / ".venv" / "bin" / "python",
            self.workspace_root / "venv" / "bin" / "python",
        ]
        for cand in candidates:
            if cand.exists():
                return str(cand)
        return sys.executable

    def _validate_command_safety(self, command: str):
        cmd_lower = command.lower()
        for frag in FORBIDDEN_COMMAND_FRAGMENTS:
            if frag in cmd_lower:
                raise ValueError(f"Blocked forbidden destructive command pattern: {frag}")

    def _build_safe_env(self) -> dict[str, str]:
        """Build clean environment without leaking arbitrary credential variables if avoidable."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # Strip known sensitive token env vars from child processes
        for key in list(env.keys()):
            upper = key.upper()
            if any(secret in upper for secret in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET", "GITHUB_TOKEN"]):
                env.pop(key, None)
        return env

    async def execute_direct(self, command: str, timeout: float | None = None) -> CommandResult:
        """
        Execute shell command inside workspace_root.
        Should ONLY be called after approval or for explicitly safe read-only commands (like git status).
        """
        self._validate_command_safety(command)
        exec_id = str(uuid.uuid4())[:8]
        effective_timeout = timeout or self.timeout
        start_time = asyncio.get_event_loop().time()

        logger.info("Executing command [%s] in %s: %s", exec_id, self.workspace_root, command)

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.workspace_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._build_safe_env(),
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        except TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stdout_bytes, stderr_bytes = await proc.communicate()

        duration = round(asyncio.get_event_loop().time() - start_time, 2)
        raw_out = stdout_bytes.decode("utf-8", errors="replace")
        raw_err = stderr_bytes.decode("utf-8", errors="replace")

        # Scrub secrets & cap output size
        clean_out = redact_secrets(raw_out)
        clean_err = redact_secrets(raw_err)

        if len(clean_out) > MAX_OUTPUT_CHARS:
            clean_out = clean_out[:MAX_OUTPUT_CHARS] + "\n... [Output truncated]"
        if len(clean_err) > MAX_OUTPUT_CHARS:
            clean_err = clean_err[:MAX_OUTPUT_CHARS] + "\n... [Stderr truncated]"

        res = CommandResult(
            execution_id=exec_id,
            command=command,
            return_code=-1 if timed_out else (proc.returncode if proc.returncode is not None else -1),
            stdout=clean_out,
            stderr=clean_err,
            timed_out=timed_out,
            duration_seconds=duration,
        )
        self._history.append(res)
        if len(self._history) > 20:
            self._history.pop(0)
        return res

    async def propose_command(
        self,
        command: str,
        description: str | None = None,
        operation_type: OperationType = OperationType.EXECUTE_COMMAND,
        require_approval: bool = True,
    ) -> tuple[FileOperationResult, CommandResult | None]:
        """
        Propose a command execution gated behind human approval.
        Returns (FileOperationResult, Optional[CommandResult]).
        """
        self._validate_command_safety(command)

        last_cmd_result: dict[str, CommandResult] = {}

        async def _execute() -> FileOperationResult:
            cmd_res = await self.execute_direct(command)
            last_cmd_result["result"] = cmd_res
            return FileOperationResult(
                success=cmd_res.success,
                operation=operation_type,
                file_path="",
                relative_path=command,
                message=(
                    f"Command completed (exit code {cmd_res.return_code}, {cmd_res.duration_seconds}s):\n"
                    f"{cmd_res.combined_output()}"
                ),
            )

        if not require_approval:
            op_res = await _execute()
            return op_res, last_cmd_result.get("result")

        req = self.approval_manager.create_request(
            operation=operation_type,
            description=description or f"Execute terminal command: {command}",
            command=command,
            relative_path=command,
            diff=f"$ {command}",
            execution_callback=_execute,
        )

        return (
            FileOperationResult(
                success=True,
                operation=operation_type,
                file_path="",
                relative_path=command,
                message="Command execution pending user approval.",
                approval_required=True,
                approval_request=req,
                diff=f"$ {command}",
            ),
            None,
        )

    def get_latest_output(self, execution_id: str | None = None) -> CommandResult | None:
        """Return latest captured terminal output or match by execution_id."""
        if not self._history:
            return None
        if execution_id:
            for item in reversed(self._history):
                if item.execution_id == execution_id:
                    return item
            return None
        return self._history[-1]
