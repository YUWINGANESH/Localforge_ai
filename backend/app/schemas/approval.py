"""Pydantic schemas for file operations, diffs, and approval workflows."""
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class OperationType(StrEnum):
    """Types of agent operations requiring approval policies."""
    READ = "READ"
    SEARCH = "SEARCH"
    CREATE_FILE = "CREATE_FILE"
    EDIT_FILE = "EDIT_FILE"
    DELETE_FILE = "DELETE_FILE"
    EXECUTE_COMMAND = "EXECUTE_COMMAND"
    INSTALL_DEPENDENCY = "INSTALL_DEPENDENCY"


class ApprovalStatus(StrEnum):
    """Status of an approval request."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalRequest(BaseModel):
    """Pending operation awaiting human authorization."""
    id: str = Field(..., description="Unique approval request ID")
    operation: OperationType
    file_path: str | None = None
    relative_path: str | None = None
    command: str | None = None
    diff: str | None = None
    original_content: str | None = None
    proposed_content: str | None = None
    additions: int = 0
    deletions: int = 0
    status: ApprovalStatus = ApprovalStatus.PENDING
    description: str = Field(..., description="Human-readable explanation of why this change is proposed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None


class ResolveApprovalRequest(BaseModel):
    """Decision payload to approve or reject a pending request."""
    approved: bool = Field(..., description="True to authorize and execute, False to reject")
    rejection_reason: str | None = Field(None, description="Optional feedback if rejected")


class FileOperationResult(BaseModel):
    """Outcome of a file operation."""
    success: bool
    operation: OperationType
    file_path: str
    relative_path: str
    message: str
    approval_required: bool = False
    approval_request: ApprovalRequest | None = None
    diff: str | None = None
