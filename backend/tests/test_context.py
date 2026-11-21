"""Automated tests for codebase scanning, search, safe reading, and context construction."""
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.context.context_builder import CompactContextBuilder
from app.context.file_reader import SafeFileReader, SecurityPathViolationError
from app.context.scanner import WorkspaceScanner
from app.context.search import CodeSearchEngine
from app.main import app

WORKSPACE_PATH = Path(__file__).resolve().parent.parent.parent


def test_workspace_scanner_real_repo():
    """Verify scanner analyzes actual LocalForge repository structure."""
    scanner = WorkspaceScanner(WORKSPACE_PATH)
    summary = scanner.scan()

    assert summary.total_files_scanned > 0
    assert "Python" in summary.languages or "TypeScript" in summary.languages
    assert any("FastAPI" in fw for fw in summary.frameworks) or any("VS Code" in fw for fw in summary.frameworks)
    assert any("main.py" in ep for ep in summary.entry_points)
    assert summary.structure_tree != ""


def test_code_search_filenames():
    """Verify filename search locates known project files."""
    engine = CodeSearchEngine(WORKSPACE_PATH)
    res = engine.search("main.py", search_type="filename")
    assert res.total_matches >= 1
    assert any("main.py" in r.relative_path for r in res.results)


def test_code_search_text():
    """Verify text search locates exact terms and captures surrounding context."""
    engine = CodeSearchEngine(WORKSPACE_PATH)
    res = engine.search("DEFAULT_SYSTEM_PROMPT", search_type="text")
    assert res.total_matches >= 1

    first_file = res.results[0]
    assert len(first_file.matches) >= 1
    match = first_file.matches[0]
    assert "DEFAULT_SYSTEM_PROMPT" in match.line_content
    assert match.line_number > 0


def test_code_search_symbols():
    """Verify symbol search identifies class/function definitions."""
    engine = CodeSearchEngine(WORKSPACE_PATH)
    res = engine.search("WorkspaceScanner", search_type="symbol")
    assert res.total_matches >= 1
    assert any("class WorkspaceScanner" in m.line_content for r in res.results for m in r.matches)


def test_safe_file_reader_success():
    """Verify safe file reading and line slicing."""
    reader = SafeFileReader(WORKSPACE_PATH)
    res = reader.read_file("backend/requirements.txt", start_line=1, end_line=3)

    assert res.relative_path == "backend/requirements.txt"
    assert res.start_line == 1
    assert res.end_line == 3
    assert "fastapi" in res.content.lower()


def test_safe_file_reader_security_violation():
    """Verify sandbox containment prevents directory traversal outside workspace."""
    reader = SafeFileReader(WORKSPACE_PATH)

    with pytest.raises(SecurityPathViolationError):
        reader.read_file("../../Windows/System32/drivers/etc/hosts")

    with pytest.raises(SecurityPathViolationError):
        reader.read_file("C:/some_outside_dir/secret.txt")


def test_compact_context_builder():
    """Verify compact context builder constructs bounded context with file snippets."""
    builder = CompactContextBuilder(WORKSPACE_PATH)
    res = builder.build_context("Modify backend/app/main.py to add health check", max_tokens=1500)

    assert "WORKSPACE OVERVIEW" in res.context_text
    assert "backend/app/main.py" in res.referenced_files
    assert res.estimated_tokens <= 1500


@pytest.mark.asyncio
async def test_context_api_endpoints():
    """Verify FastAPI context API routes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /api/context/summary
        sum_res = await client.get("/api/context/summary")
        assert sum_res.status_code == 200
        data = sum_res.json()
        assert data["total_files_scanned"] > 0

        # POST /api/context/search
        search_res = await client.post(
            "/api/context/search",
            json={"query": "OllamaClient", "search_type": "symbol"},
        )
        assert search_res.status_code == 200
        assert search_res.json()["total_matches"] >= 1

        # GET /api/context/file
        path_to_test = "backend/requirements.txt" if (Path.cwd() / "backend/requirements.txt").exists() else "requirements.txt"
        file_res = await client.get(f"/api/context/file?path={path_to_test}&start_line=1&end_line=2")
        assert file_res.status_code == 200
        assert file_res.json()["relative_path"] == path_to_test

        # Traversal test on API
        trav_res = await client.get("/api/context/file?path=../../etc/passwd")
        assert trav_res.status_code == 403

        # POST /api/context/build
        build_res = await client.post(
            "/api/context/build",
            json={"user_query": "Explain how backend/app/main.py works", "max_tokens": 1000},
        )
        assert build_res.status_code == 200
        assert "main.py" in build_res.json()["context_text"]
