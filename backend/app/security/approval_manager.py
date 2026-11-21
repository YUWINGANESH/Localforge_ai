"""Approval manager enforcing human-in-the-loop permission policy."""
import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime

from app.schemas.approval import (
    ApprovalRequest,
    ApprovalStatus,
    FileOperationResult,
    OperationType,
)

logger = logging.getLogger(__name__)


class ApprovalManager:
    """Coordinates pending human approvals, execution callbacks, and async awaiting."""

    def __init__(self):
        self._pending_requests: dict[str, ApprovalRequest] = {}
        self._execution_callbacks: dict[str, Callable[[], Coroutine[None, None, FileOperationResult]]] = {}
        self._decision_events: dict[str, asyncio.Event] = {}
        self._resolved_results: dict[str, FileOperationResult] = {}

    def create_request(
        self,
        operation: OperationType,
        description: str,
        file_path: str | None = None,
        relative_path: str | None = None,
        command: str | None = None,
        diff: str | None = None,
        original_content: str | None = None,
        proposed_content: str | None = None,
        additions: int = 0,
        deletions: int = 0,
        execution_callback: Callable[[], Coroutine[None, None, FileOperationResult]] | None = None,
    ) -> ApprovalRequest:
        """Create and register a pending approval request."""
        req_id = str(uuid.uuid4())
        req = ApprovalRequest(
            id=req_id,
            operation=operation,
            file_path=file_path,
            relative_path=relative_path,
            command=command,
            diff=diff,
            original_content=original_content,
            proposed_content=proposed_content,
            additions=additions,
            deletions=deletions,
            status=ApprovalStatus.PENDING,
            description=description,
            created_at=datetime.now(UTC),
        )
        self._pending_requests[req_id] = req
        self._decision_events[req_id] = asyncio.Event()
        if execution_callback:
            self._execution_callbacks[req_id] = execution_callback

        logger.info("Created pending approval %s for %s (%s)", req_id, operation.value, relative_path or command)
        return req

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Fetch request by ID."""
        return self._pending_requests.get(request_id)

    def list_pending(self) -> list[ApprovalRequest]:
        """Return all active pending requests."""
        return [r for r in self._pending_requests.values() if r.status == ApprovalStatus.PENDING]

    async def wait_for_decision(
        self,
        request_id: str,
        timeout: float = 300.0,
        cancel_event: asyncio.Event | None = None,
    ) -> FileOperationResult:
        """
        Pause coroutine until human approves/rejects the request or task is cancelled.
        """
        req = self._pending_requests.get(request_id)
        if not req:
            raise KeyError(f"Approval request '{request_id}' not found.")

        event = self._decision_events.get(request_id)
        if not event:
            return self._resolved_results.get(request_id) or FileOperationResult(
                success=False,
                operation=req.operation,
                file_path=req.file_path or "",
                relative_path=req.relative_path or "",
                message="Approval event missing.",
            )

        elapsed = 0.0
        poll_interval = 0.25
        while not event.is_set():
            if cancel_event and cancel_event.is_set():
                req.status = ApprovalStatus.REJECTED
                self._execution_callbacks.pop(request_id, None)
                return FileOperationResult(
                    success=False,
                    operation=req.operation,
                    file_path=req.file_path or "",
                    relative_path=req.relative_path or "",
                    message="Task cancelled by user while awaiting approval.",
                    approval_request=req,
                )

            if elapsed >= timeout:
                req.status = ApprovalStatus.EXPIRED
                self._execution_callbacks.pop(request_id, None)
                return FileOperationResult(
                    success=False,
                    operation=req.operation,
                    file_path=req.file_path or "",
                    relative_path=req.relative_path or "",
                    message="Approval request timed out waiting for user response.",
                    approval_request=req,
                )

            try:
                await asyncio.wait_for(event.wait(), timeout=poll_interval)
            except TimeoutError:
                elapsed += poll_interval

        return self._resolved_results.get(request_id) or FileOperationResult(
            success=False,
            operation=req.operation,
            file_path=req.file_path or "",
            relative_path=req.relative_path or "",
            message="Approval resolved without result.",
            approval_request=req,
        )

    async def resolve(
        self,
        request_id: str,
        approved: bool,
        rejection_reason: str | None = None,
    ) -> FileOperationResult:
        """
        Resolve pending approval. If approved, execute associated callback.
        """
        req = self._pending_requests.get(request_id)
        if not req:
            raise KeyError(f"Approval request '{request_id}' not found.")

        if req.status != ApprovalStatus.PENDING:
            return FileOperationResult(
                success=False,
                operation=req.operation,
                file_path=req.file_path or "",
                relative_path=req.relative_path or "",
                message=f"Request '{request_id}' was already resolved as {req.status.value}",
                approval_request=req,
            )

        req.resolved_at = datetime.now(UTC)

        if not approved:
            req.status = ApprovalStatus.REJECTED
            self._execution_callbacks.pop(request_id, None)
            reason = rejection_reason or "Rejected by user."
            logger.info("Approval %s rejected: %s", request_id, reason)
            res = FileOperationResult(
                success=False,
                operation=req.operation,
                file_path=req.file_path or "",
                relative_path=req.relative_path or "",
                message=f"Operation rejected by user: {reason}",
                approval_request=req,
            )
            self._resolved_results[request_id] = res
            if request_id in self._decision_events:
                self._decision_events[request_id].set()
            return res

        # Authorized
        req.status = ApprovalStatus.APPROVED
        callback = self._execution_callbacks.pop(request_id, None)

        if callback:
            try:
                result = await callback()
                result.approval_request = req
            except Exception as exc:
                logger.exception("Error executing approved operation %s: %s", request_id, exc)
                result = FileOperationResult(
                    success=False,
                    operation=req.operation,
                    file_path=req.file_path or "",
                    relative_path=req.relative_path or "",
                    message=f"Execution error: {exc}",
                    approval_request=req,
                )
        else:
            result = FileOperationResult(
                success=True,
                operation=req.operation,
                file_path=req.file_path or "",
                relative_path=req.relative_path or "",
                message="Operation approved successfully.",
                approval_request=req,
            )

        self._resolved_results[request_id] = result
        if request_id in self._decision_events:
            self._decision_events[request_id].set()
        return result


_global_approval_manager: ApprovalManager | None = None


def get_approval_manager() -> ApprovalManager:
    """Singleton getter for ApprovalManager."""
    global _global_approval_manager
    if _global_approval_manager is None:
        _global_approval_manager = ApprovalManager()
    return _global_approval_manager
