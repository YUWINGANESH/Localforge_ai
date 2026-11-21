"""Safe file modification tools with diff generation and approval gating."""
import logging
import os
from pathlib import Path

from app.schemas.approval import FileOperationResult, OperationType
from app.security.approval_manager import ApprovalManager, get_approval_manager
from app.security.path_guard import resolve_safe_workspace_path
from app.tools.diff_generator import generate_unified_diff

logger = logging.getLogger(__name__)


class FileTools:
    """Workspace-sandboxed file modification tools requiring approval."""

    def __init__(
        self,
        workspace_root: Path,
        approval_manager: ApprovalManager | None = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.approval_manager = approval_manager or get_approval_manager()

    async def create_file(
        self,
        path: str,
        content: str,
        description: str | None = None,
        require_approval: bool = True,
    ) -> FileOperationResult:
        """Create a new file with diff preview and approval gating."""
        resolved = resolve_safe_workspace_path(self.workspace_root, path)
        rel_path = resolved.relative_to(self.workspace_root).as_posix()

        if resolved.exists():
            return FileOperationResult(
                success=False,
                operation=OperationType.CREATE_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"File '{rel_path}' already exists. Use write_file or edit_file instead.",
            )

        diff_text, additions, deletions = generate_unified_diff("", content, rel_path)

        async def _execute_create() -> FileOperationResult:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            logger.info("Created file: %s", rel_path)
            return FileOperationResult(
                success=True,
                operation=OperationType.CREATE_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"Successfully created '{rel_path}'.",
                diff=diff_text,
            )

        if not require_approval:
            return await _execute_create()

        req = self.approval_manager.create_request(
            operation=OperationType.CREATE_FILE,
            description=description or f"Create new file '{rel_path}'",
            file_path=str(resolved),
            relative_path=rel_path,
            diff=diff_text,
            original_content="",
            proposed_content=content,
            additions=additions,
            deletions=deletions,
            execution_callback=_execute_create,
        )

        return FileOperationResult(
            success=True,
            operation=OperationType.CREATE_FILE,
            file_path=str(resolved),
            relative_path=rel_path,
            message="File creation pending user approval.",
            approval_required=True,
            approval_request=req,
            diff=diff_text,
        )

    async def write_file(
        self,
        path: str,
        content: str,
        description: str | None = None,
        require_approval: bool = True,
    ) -> FileOperationResult:
        """Overwrite or create a file with full diff comparison and approval gating."""
        resolved = resolve_safe_workspace_path(self.workspace_root, path)
        rel_path = resolved.relative_to(self.workspace_root).as_posix()

        original_content = ""
        if resolved.exists():
            if not resolved.is_file():
                return FileOperationResult(
                    success=False,
                    operation=OperationType.EDIT_FILE,
                    file_path=str(resolved),
                    relative_path=rel_path,
                    message=f"Target '{rel_path}' exists and is not a regular file.",
                )
            original_content = resolved.read_text(encoding="utf-8", errors="replace")

        op_type = OperationType.EDIT_FILE if resolved.exists() else OperationType.CREATE_FILE
        diff_text, additions, deletions = generate_unified_diff(original_content, content, rel_path)

        if original_content == content and resolved.exists():
            return FileOperationResult(
                success=True,
                operation=op_type,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"No changes detected in '{rel_path}'.",
                diff="",
            )

        async def _execute_write() -> FileOperationResult:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            logger.info("Wrote file: %s", rel_path)
            return FileOperationResult(
                success=True,
                operation=op_type,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"Successfully wrote '{rel_path}'.",
                diff=diff_text,
            )

        if not require_approval:
            return await _execute_write()

        req = self.approval_manager.create_request(
            operation=op_type,
            description=description or f"{'Modify' if resolved.exists() else 'Create'} '{rel_path}'",
            file_path=str(resolved),
            relative_path=rel_path,
            diff=diff_text,
            original_content=original_content,
            proposed_content=content,
            additions=additions,
            deletions=deletions,
            execution_callback=_execute_write,
        )

        return FileOperationResult(
            success=True,
            operation=op_type,
            file_path=str(resolved),
            relative_path=rel_path,
            message="File modification pending user approval.",
            approval_required=True,
            approval_request=req,
            diff=diff_text,
        )

    async def edit_file(
        self,
        path: str,
        target: str,
        replacement: str,
        description: str | None = None,
        require_approval: bool = True,
    ) -> FileOperationResult:
        """Perform targeted search-and-replace edit on an existing file."""
        resolved = resolve_safe_workspace_path(self.workspace_root, path)
        rel_path = resolved.relative_to(self.workspace_root).as_posix()

        if not resolved.exists() or not resolved.is_file():
            return FileOperationResult(
                success=False,
                operation=OperationType.EDIT_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"File '{rel_path}' does not exist.",
            )

        original_content = resolved.read_text(encoding="utf-8", errors="replace")

        if target == replacement:
            preview = original_content if len(original_content) <= 1500 else original_content[:1500] + "\n... [truncated]"
            return FileOperationResult(
                success=False,
                operation=OperationType.EDIT_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=(
                    f"Target and replacement snippets are identical. Actual content of '{rel_path}':\n{preview}\n"
                    "Provide an actual modification or use write_file to rewrite the file cleanly."
                ),
            )

        if target not in original_content:
            preview = original_content if len(original_content) <= 1500 else original_content[:1500] + "\n... [truncated]"
            return FileOperationResult(
                success=False,
                operation=OperationType.EDIT_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=(
                    f"Target snippet not found in '{rel_path}'. Actual content:\n{preview}\n"
                    "Use an exact target snippet from above or use write_file."
                ),
            )

        # Replace first exact occurrence
        proposed_content = original_content.replace(target, replacement, 1)
        diff_text, additions, deletions = generate_unified_diff(original_content, proposed_content, rel_path)

        async def _execute_edit() -> FileOperationResult:
            resolved.write_text(proposed_content, encoding="utf-8")
            logger.info("Edited file: %s", rel_path)
            return FileOperationResult(
                success=True,
                operation=OperationType.EDIT_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"Successfully applied edit to '{rel_path}'.",
                diff=diff_text,
            )

        if not require_approval:
            return await _execute_edit()

        req = self.approval_manager.create_request(
            operation=OperationType.EDIT_FILE,
            description=description or f"Edit '{rel_path}'",
            file_path=str(resolved),
            relative_path=rel_path,
            diff=diff_text,
            original_content=original_content,
            proposed_content=proposed_content,
            additions=additions,
            deletions=deletions,
            execution_callback=_execute_edit,
        )

        return FileOperationResult(
            success=True,
            operation=OperationType.EDIT_FILE,
            file_path=str(resolved),
            relative_path=rel_path,
            message="File edit pending user approval.",
            approval_required=True,
            approval_request=req,
            diff=diff_text,
        )

    async def delete_file(
        self,
        path: str,
        description: str | None = None,
        require_approval: bool = True,
    ) -> FileOperationResult:
        """Delete an existing file with explicit approval gating."""
        resolved = resolve_safe_workspace_path(self.workspace_root, path)
        rel_path = resolved.relative_to(self.workspace_root).as_posix()

        if not resolved.exists():
            return FileOperationResult(
                success=False,
                operation=OperationType.DELETE_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"File '{rel_path}' not found.",
            )

        if not resolved.is_file():
            return FileOperationResult(
                success=False,
                operation=OperationType.DELETE_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"Path '{rel_path}' is a directory, not a regular file.",
            )

        original_content = resolved.read_text(encoding="utf-8", errors="replace")
        diff_text, additions, deletions = generate_unified_diff(original_content, "", rel_path)

        async def _execute_delete() -> FileOperationResult:
            os.remove(resolved)
            logger.info("Deleted file: %s", rel_path)
            return FileOperationResult(
                success=True,
                operation=OperationType.DELETE_FILE,
                file_path=str(resolved),
                relative_path=rel_path,
                message=f"Successfully deleted '{rel_path}'.",
                diff=diff_text,
            )

        if not require_approval:
            return await _execute_delete()

        req = self.approval_manager.create_request(
            operation=OperationType.DELETE_FILE,
            description=description or f"Delete file '{rel_path}'",
            file_path=str(resolved),
            relative_path=rel_path,
            diff=diff_text,
            original_content=original_content,
            proposed_content="",
            additions=additions,
            deletions=deletions,
            execution_callback=_execute_delete,
        )

        return FileOperationResult(
            success=True,
            operation=OperationType.DELETE_FILE,
            file_path=str(resolved),
            relative_path=rel_path,
            message="File deletion pending explicit user approval.",
            approval_required=True,
            approval_request=req,
            diff=diff_text,
        )
