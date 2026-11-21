"""Pydantic schemas for codebase scanning, search, and context management."""

from pydantic import BaseModel, Field


class DependencyInfo(BaseModel):
    """Identified project dependency."""
    name: str
    version: str | None = None
    category: str = "runtime"  # 'runtime' or 'dev'


class ProjectSummary(BaseModel):
    """High-level summary of an inspected repository."""
    workspace_root: str
    languages: dict[str, int] = Field(default_factory=dict, description="Language name to file count mapping")
    primary_language: str | None = None
    frameworks: list[str] = Field(default_factory=list, description="Detected frameworks and libraries")
    entry_points: list[str] = Field(default_factory=list, description="Detected main/entry point files")
    config_files: list[str] = Field(default_factory=list, description="Detected build and configuration files")
    test_directories: list[str] = Field(default_factory=list, description="Detected test directories")
    dependencies: list[DependencyInfo] = Field(default_factory=list, description="Detected dependencies")
    total_files_scanned: int = 0
    structure_tree: str = Field("", description="ASCII directory structure representation")


class SearchMatch(BaseModel):
    """Single match result within a file."""
    line_number: int
    line_content: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)


class FileSearchResult(BaseModel):
    """Search matches grouped by file."""
    file_path: str
    relative_path: str
    matches: list[SearchMatch] = Field(default_factory=list)
    match_count: int = 0


class SearchRequest(BaseModel):
    """Request to perform code or filename search."""
    query: str = Field(..., min_length=1, description="Search term, regex pattern, or glob")
    search_type: str = Field("text", description="'text', 'filename', or 'symbol'")
    file_extensions: list[str] | None = Field(None, description="Optional extensions to restrict search, e.g. ['.py']")
    is_regex: bool = Field(False, description="Whether query should be evaluated as regular expression")
    max_results: int = Field(30, ge=1, le=200, description="Maximum matches to return")


class SearchResponse(BaseModel):
    """Response containing code search results."""
    query: str
    search_type: str
    total_matches: int
    results: list[FileSearchResult] = Field(default_factory=list)


class ReadFileRequest(BaseModel):
    """Request to read a file or slice of a file safely."""
    path: str = Field(..., description="Workspace-relative or absolute file path")
    start_line: int | None = Field(None, ge=1, description="1-based start line")
    end_line: int | None = Field(None, ge=1, description="1-based end line (inclusive)")


class ReadFileResponse(BaseModel):
    """Response containing safely read file content."""
    path: str
    relative_path: str
    content: str
    total_lines: int
    start_line: int
    end_line: int
    truncated: bool = False
    size_bytes: int = 0


class BuildContextRequest(BaseModel):
    """Request to construct compact context for a user objective."""
    user_query: str = Field(..., description="User prompt or task description")
    max_tokens: int = Field(2000, description="Max token/character budget for context")


class CompactContextResponse(BaseModel):
    """Compact context ready for injection into LLM prompt."""
    context_text: str
    estimated_tokens: int
    referenced_files: list[str] = Field(default_factory=list)
    project_summary: ProjectSummary | None = None
