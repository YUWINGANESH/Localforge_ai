"""Code and file search engine supporting text, regex, filename, and symbol queries."""
import fnmatch
import logging
import os
import re
from pathlib import Path

from app.context.scanner import DEFAULT_IGNORE_DIRS, DEFAULT_IGNORE_EXTENSIONS
from app.schemas.context import FileSearchResult, SearchMatch, SearchResponse

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 1_000_000  # 1 MB threshold for searchable text files


class CodeSearchEngine:
    """Fast, safe search engine for source code files within a workspace."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.ignore_dirs = set(DEFAULT_IGNORE_DIRS)
        self.ignore_extensions = set(DEFAULT_IGNORE_EXTENSIONS)

    def search(
        self,
        query: str,
        search_type: str = "text",
        file_extensions: list[str] | None = None,
        is_regex: bool = False,
        max_results: int = 30,
    ) -> SearchResponse:
        """
        Execute search across the workspace.
        
        Args:
            query: String query, regex, or filename pattern.
            search_type: 'text', 'filename', or 'symbol'.
            file_extensions: Filter by extensions (e.g. ['.py', '.ts']).
            is_regex: Whether to treat query as regex.
            max_results: Upper bound on returned matches.
        """
        if search_type == "filename":
            return self._search_filenames(query, max_results)
        elif search_type == "symbol":
            return self._search_symbols(query, file_extensions, max_results)
        else:
            return self._search_text(query, file_extensions, is_regex, max_results)

    def _search_filenames(self, pattern: str, max_results: int) -> SearchResponse:
        """Search workspace for files matching filename glob or substring."""
        results: list[FileSearchResult] = []
        total_matches = 0
        norm_pattern = pattern.lower()

        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith(".git")]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in self.ignore_extensions:
                    continue

                full_path = Path(root) / file
                rel_path = full_path.relative_to(self.workspace_root).as_posix()

                # Match against glob or substring
                if fnmatch.fnmatch(file.lower(), norm_pattern) or norm_pattern in rel_path.lower():
                    results.append(
                        FileSearchResult(
                            file_path=str(full_path),
                            relative_path=rel_path,
                            matches=[],
                            match_count=1,
                        )
                    )
                    total_matches += 1
                    if total_matches >= max_results:
                        return SearchResponse(
                            query=pattern,
                            search_type="filename",
                            total_matches=total_matches,
                            results=results,
                        )

        return SearchResponse(
            query=pattern,
            search_type="filename",
            total_matches=total_matches,
            results=results,
        )

    def _search_text(
        self,
        query: str,
        file_extensions: list[str] | None,
        is_regex: bool,
        max_results: int,
    ) -> SearchResponse:
        """Search text contents of workspace files."""
        results: list[FileSearchResult] = []
        total_matches = 0

        # Compile pattern
        try:
            if is_regex:
                pattern = re.compile(query, re.IGNORECASE)
            else:
                pattern = re.compile(re.escape(query), re.IGNORECASE)
        except re.error as exc:
            logger.warning("Invalid search regex: %s", exc)
            return SearchResponse(query=query, search_type="text", total_matches=0, results=[])

        filter_exts = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in file_extensions} if file_extensions else None

        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith(".git")]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in self.ignore_extensions:
                    continue
                if filter_exts and ext not in filter_exts:
                    continue

                full_path = Path(root) / file
                # Skip oversized files
                try:
                    if full_path.stat().st_size > MAX_FILE_SIZE_BYTES:
                        continue
                except OSError:
                    continue

                file_matches = self._find_matches_in_file(full_path, pattern, max_results - total_matches)
                if file_matches:
                    rel_path = full_path.relative_to(self.workspace_root).as_posix()
                    results.append(
                        FileSearchResult(
                            file_path=str(full_path),
                            relative_path=rel_path,
                            matches=file_matches,
                            match_count=len(file_matches),
                        )
                    )
                    total_matches += len(file_matches)
                    if total_matches >= max_results:
                        break

            if total_matches >= max_results:
                break

        return SearchResponse(
            query=query,
            search_type="text",
            total_matches=total_matches,
            results=results,
        )

    def _search_symbols(
        self,
        symbol_name: str,
        file_extensions: list[str] | None,
        max_results: int,
    ) -> SearchResponse:
        """Search for class and function definitions matching symbol name."""
        escaped = re.escape(symbol_name)
        # Match python/js/ts function or class definitions
        symbol_regex = rf"(?:def|class|function|const|let|var)\s+{escaped}\b"
        return self._search_text(
            query=symbol_regex,
            file_extensions=file_extensions,
            is_regex=True,
            max_results=max_results,
        )

    def _find_matches_in_file(
        self,
        file_path: Path,
        pattern: re.Pattern,
        remaining_budget: int,
    ) -> list[SearchMatch]:
        """Inspect file content line-by-line and capture matches with surrounding context."""
        matches: list[SearchMatch] = []
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return []

        for idx, line in enumerate(lines):
            if pattern.search(line):
                # Capture 2 lines before and 2 lines after
                start_ctx = max(0, idx - 2)
                end_ctx = min(len(lines), idx + 3)

                ctx_before = [line_item.rstrip("\r\n") for line_item in lines[start_ctx:idx]]
                ctx_after = [line_item.rstrip("\r\n") for line_item in lines[idx + 1:end_ctx]]

                matches.append(
                    SearchMatch(
                        line_number=idx + 1,
                        line_content=line.rstrip("\r\n"),
                        context_before=ctx_before,
                        context_after=ctx_after,
                    )
                )

                if len(matches) >= remaining_budget:
                    break

        return matches
