"""API endpoints for managing human approvals and file operation proposals."""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config.settings import Settings, get_settings
from app.schemas.approval import ApprovalRequest, FileOperationResult, ResolveApprovalRequest
from app.security.approval_manager import ApprovalManager, get_approval_manager
from app.security.path_guard import PathSecurityError
from app.tools.file_ops import FileTools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/approvals", tags=["Approvals"])


class ProposeFileRequest(BaseModel):
    """Payload to propose a file creation, modification, or deletion."""
    operation: str = Field(..., description="'create', 'write', 'edit', or 'delete'")
    path: str = Field(..., description="Workspace-relative or absolute path")
    content: str | None = Field(None, description="File content for create/write")
    target: str | None = Field(None, description="Target snippet for edit")
    replacement: str | None = Field(None, description="Replacement snippet for edit")
    description: str | None = Field(None, description="Reason for change")


@router.get("", response_model=list[ApprovalRequest])
async def list_pending_approvals(
    approval_manager: ApprovalManager = Depends(get_approval_manager),
) -> list[ApprovalRequest]:
    """List all currently pending operations awaiting human review."""
    return approval_manager.list_pending()


@router.post("/propose-file", response_model=FileOperationResult)
async def propose_file_operation(
    payload: ProposeFileRequest,
    settings: Settings = Depends(get_settings),
    approval_manager: ApprovalManager = Depends(get_approval_manager),
) -> FileOperationResult:
    """
    Create a pending file operation with diff preview requiring human authorization.
    """
    workspace_root: Path = settings.get_effective_workspace()
    tools = FileTools(workspace_root=workspace_root, approval_manager=approval_manager)

    try:
        op = payload.operation.lower()
        if op == "create":
            return await tools.create_file(
                path=payload.path,
                content=payload.content or "",
                description=payload.description,
                require_approval=True,
            )
        elif op == "write":
            return await tools.write_file(
                path=payload.path,
                content=payload.content or "",
                description=payload.description,
                require_approval=True,
            )
        elif op == "edit":
            if payload.target is None or payload.replacement is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="'target' and 'replacement' fields are required for 'edit' operation.",
                )
            return await tools.edit_file(
                path=payload.path,
                target=payload.target,
                replacement=payload.replacement,
                description=payload.description,
                require_approval=True,
            )
        elif op == "delete":
            return await tools.delete_file(
                path=payload.path,
                description=payload.description,
                require_approval=True,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported operation '{payload.operation}'. Expected create, write, edit, or delete.",
            )
    except PathSecurityError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.get("/{request_id}", response_model=ApprovalRequest)
async def get_approval(
    request_id: str,
    approval_manager: ApprovalManager = Depends(get_approval_manager),
) -> ApprovalRequest:
    """Get details of a specific approval request."""
    req = approval_manager.get_request(request_id)
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request '{request_id}' not found.",
        )
    return req


@router.post("/{request_id}/resolve", response_model=FileOperationResult)
async def resolve_approval(
    request_id: str,
    payload: ResolveApprovalRequest,
    approval_manager: ApprovalManager = Depends(get_approval_manager),
) -> FileOperationResult:
    """
    Approve or reject a pending operation.
    If approved, executes the authorized file or terminal operation immediately.
    """
    try:
        return await approval_manager.resolve(
            request_id=request_id,
            approved=payload.approved,
            rejection_reason=payload.rejection_reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
