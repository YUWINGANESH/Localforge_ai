"""Endpoints for codebase inspection, code search, and context construction."""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config.settings import Settings, get_settings
from app.context.context_builder import CompactContextBuilder
from app.context.file_reader import FileAccessError, SafeFileReader, SecurityPathViolationError
from app.context.scanner import WorkspaceScanner
from app.context.search import CodeSearchEngine
from app.schemas.context import (
    BuildContextRequest,
    CompactContextResponse,
    ProjectSummary,
    ReadFileResponse,
    SearchRequest,
    SearchResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/context", tags=["Context"])


def get_effective_workspace_path(settings: Settings = Depends(get_settings)) -> Path:
    """Helper to return resolved workspace root path."""
    return settings.get_effective_workspace()


@router.get("/summary", response_model=ProjectSummary)
async def get_project_summary(
    refresh: bool = Query(False, description="Whether to bypass cache and re-scan"),
    workspace_root: Path = Depends(get_effective_workspace_path),
) -> ProjectSummary:
    """
    Scan and return structural and technological summary of the workspace.
    """
    scanner = WorkspaceScanner(workspace_root)
    return scanner.scan()


@router.post("/search", response_model=SearchResponse)
async def search_codebase(
    payload: SearchRequest,
    workspace_root: Path = Depends(get_effective_workspace_path),
) -> SearchResponse:
    """
    Perform fast search across the workspace by text, regex, filename, or symbol.
    """
    engine = CodeSearchEngine(workspace_root)
    return engine.search(
        query=payload.query,
        search_type=payload.search_type,
        file_extensions=payload.file_extensions,
        is_regex=payload.is_regex,
        max_results=payload.max_results,
    )


@router.get("/file", response_model=ReadFileResponse)
async def read_workspace_file(
    path: str = Query(..., description="File path relative to workspace or absolute"),
    start_line: int | None = Query(None, ge=1, description="1-based start line"),
    end_line: int | None = Query(None, ge=1, description="1-based end line"),
    workspace_root: Path = Depends(get_effective_workspace_path),
) -> ReadFileResponse:
    """
    Safely read content or line slices of a file within the workspace boundaries.
    """
    reader = SafeFileReader(workspace_root)
    try:
        return reader.read_file(target_path=path, start_line=start_line, end_line=end_line)
    except SecurityPathViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except FileAccessError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/build", response_model=CompactContextResponse)
async def build_compact_context(
    payload: BuildContextRequest,
    workspace_root: Path = Depends(get_effective_workspace_path),
) -> CompactContextResponse:
    """
    Build a compact context block for the given user objective, within token budgets.
    """
    builder = CompactContextBuilder(workspace_root)
    return builder.build_context(
        user_query=payload.user_query,
        max_tokens=payload.max_tokens,
    )


@router.post("/workspace")
async def set_workspace_root(
    path: str = Query(..., description="Absolute path to workspace directory"),
    settings: Settings = Depends(get_settings),
):
    """
    Update active workspace root dynamically from VS Code.
    """
    target = Path(path).resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Directory does not exist: {path}",
        )
    settings.workspace_root = str(target)
    return {"status": "success", "workspace_root": settings.workspace_root}
