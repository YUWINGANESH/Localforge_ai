"""Constructs compact, relevance-filtered context for local LLM inference."""
import logging
import re
from pathlib import Path

from app.context.file_reader import SafeFileReader
from app.context.scanner import WorkspaceScanner
from app.context.search import CodeSearchEngine
from app.schemas.context import CompactContextResponse, ProjectSummary

logger = logging.getLogger(__name__)


class CompactContextBuilder:
    """Builds compact context blocks within tight token budgets (e.g. 4096 tokens total)."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.scanner = WorkspaceScanner(self.workspace_root)
        self.search_engine = CodeSearchEngine(self.workspace_root)
        self.file_reader = SafeFileReader(self.workspace_root)
        self._cached_summary: ProjectSummary | None = None

    def get_summary(self, force_refresh: bool = False) -> ProjectSummary:
        """Get or refresh workspace project summary."""
        if self._cached_summary is None or force_refresh:
            self._cached_summary = self.scanner.scan()
        return self._cached_summary

    def build_context(
        self,
        user_query: str,
        max_tokens: int = 2000,
    ) -> CompactContextResponse:
        """
        Build compact context for the user query respecting token budget.
        (Approx 1 token ~= 4 characters).
        """
        max_chars = max_tokens * 4
        summary = self.get_summary()

        sections: list[str] = []
        referenced_files: set[str] = set()

        # 1. Project High-Level Overview
        lang_str = ", ".join(f"{k} ({v})" for k, v in list(summary.languages.items())[:5]) or "Unknown"
        fw_str = ", ".join(summary.frameworks) or "None detected"
        entry_str = ", ".join(summary.entry_points[:4]) or "None detected"

        overview = (
            "=== WORKSPACE OVERVIEW ===\n"
            f"Languages: {lang_str}\n"
            f"Frameworks/Libraries: {fw_str}\n"
            f"Entry Points: {entry_str}\n"
            f"Total Files: {summary.total_files_scanned}\n"
        )
        sections.append(overview)

        # 2. Extract potential file references explicitly mentioned in user query
        # Matches patterns like foo.py, app/main.ts, etc.
        file_candidates = set(re.findall(r"[\w\./\\-]+\.[a-zA-Z0-9]+", user_query))
        
        current_chars = sum(len(s) for s in sections)

        # 3. Read explicitly mentioned files first
        for candidate in file_candidates:
            clean_cand = candidate.strip(".,;:()[]\"'")
            try:
                resolved = self.file_reader.resolve_safe_path(clean_cand)
                if resolved.exists() and resolved.is_file():
                    res = self.file_reader.read_file(clean_cand, start_line=1, end_line=100)
                    snippet = f"=== FILE: {res.relative_path} (Lines {res.start_line}-{res.end_line}) ===\n{res.content}\n"
                    if current_chars + len(snippet) < max_chars:
                        sections.append(snippet)
                        current_chars += len(snippet)
                        referenced_files.add(res.relative_path)
            except Exception:
                continue

        # 4. If space permits, search for keywords from user query
        if current_chars < max_chars - 500:
            words = [w for w in re.findall(r"[a-zA-Z_]\w{3,}", user_query) if w.lower() not in {"what", "how", "make", "create", "build", "this", "that", "code", "file", "test"}]
            if words:
                keyword = words[0]
                search_res = self.search_engine.search(query=keyword, search_type="text", max_results=5)
                for file_res in search_res.results:
                    if file_res.relative_path in referenced_files:
                        continue
                    if not file_res.matches:
                        continue

                    match_lines = [f"  L{m.line_number}: {m.line_content}" for m in file_res.matches[:4]]
                    snippet = f"=== RELEVANT MATCHES IN {file_res.relative_path} ===\n" + "\n".join(match_lines) + "\n"
                    if current_chars + len(snippet) < max_chars:
                        sections.append(snippet)
                        current_chars += len(snippet)
                        referenced_files.add(file_res.relative_path)

        final_context = "\n".join(sections)
        estimated_tokens = len(final_context) // 4

        return CompactContextResponse(
            context_text=final_context,
            estimated_tokens=estimated_tokens,
            referenced_files=sorted(list(referenced_files)),
            project_summary=summary,
        )
