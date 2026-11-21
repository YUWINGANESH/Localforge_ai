"""Safe file reader with workspace containment validation."""
import logging
from pathlib import Path

from app.schemas.context import ReadFileResponse

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 500_000  # 500 KB max limit for single file read into context


class FileAccessError(Exception):
    """Base exception for file access errors."""
    pass


class SecurityPathViolationError(FileAccessError):
    """Raised when an access attempt breaches the workspace sandbox."""
    pass


class SafeFileReader:
    """Safely reads files within the boundaries of the designated workspace."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def resolve_safe_path(self, target_path: str) -> Path:
        """
        Resolve a path and ensure it stays strictly within the workspace root.
        
        Raises:
            SecurityPathViolationError if path escapes workspace.
            FileNotFoundError if path does not exist.
        """
        raw_path = Path(target_path)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (self.workspace_root / raw_path).resolve()

        # Enforce strict containment
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise SecurityPathViolationError(
                f"Access denied: Path '{target_path}' is outside active workspace '{self.workspace_root}'"
            ) from exc

        return resolved

    def read_file(
        self,
        target_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> ReadFileResponse:
        """
        Safely read file or slice of file.
        
        Args:
            target_path: File path relative or absolute.
            start_line: 1-based start line (inclusive).
            end_line: 1-based end line (inclusive).
        """
        resolved = self.resolve_safe_path(target_path)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {target_path}")

        if not resolved.is_file():
            raise FileAccessError(f"Path is not a regular file: {target_path}")

        file_size = resolved.stat().st_size
        rel_path = resolved.relative_to(self.workspace_root).as_posix()

        try:
            with open(resolved, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as exc:
            raise FileAccessError(f"Failed reading file '{target_path}': {exc}") from exc

        total_lines = len(lines)
        if total_lines == 0:
            return ReadFileResponse(
                path=str(resolved),
                relative_path=rel_path,
                content="",
                total_lines=0,
                start_line=1,
                end_line=1,
                truncated=False,
                size_bytes=0,
            )

        actual_start = max(1, start_line or 1)
        actual_end = min(total_lines, end_line or total_lines)

        if actual_start > total_lines:
            slice_lines = []
        else:
            slice_lines = lines[actual_start - 1 : actual_end]

        content = "".join(slice_lines)
        truncated = False

        if len(content.encode("utf-8")) > MAX_READ_BYTES:
            content = content[:MAX_READ_BYTES] + "\n... [Truncated due to size limit]"
            truncated = True

        return ReadFileResponse(
            path=str(resolved),
            relative_path=rel_path,
            content=content,
            total_lines=total_lines,
            start_line=actual_start,
            end_line=actual_end,
            truncated=truncated,
            size_bytes=file_size,
        )
