"""Tests for safe file operations, unified diffs, and approval gating."""
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import Settings, get_settings
from app.main import app
from app.schemas.approval import ApprovalStatus
from app.security.approval_manager import ApprovalManager, get_approval_manager
from app.security.path_guard import (
    PathSecurityError,
    redact_secrets,
    resolve_safe_workspace_path,
)
from app.tools.diff_generator import generate_unified_diff
from app.tools.file_ops import FileTools


def test_path_security_guard(tmp_path: Path):
    """Verify workspace path validation prevents escapes and protects secrets."""
    safe = resolve_safe_workspace_path(tmp_path, "src/app.py")
    assert safe == (tmp_path / "src" / "app.py").resolve()

    with pytest.raises(PathSecurityError):
        resolve_safe_workspace_path(tmp_path, "../outside.txt")

    with pytest.raises(PathSecurityError):
        resolve_safe_workspace_path(tmp_path, ".git/HEAD")

    with pytest.raises(PathSecurityError):
        resolve_safe_workspace_path(tmp_path, ".env")


def test_secret_redactor():
    """Verify credentials and keys are redacted from logs and strings."""
    raw = 'Connecting with api_key="my_super_secret_token_123" and sk-abcdefghij1234567890'
    cleaned = redact_secrets(raw)
    assert "my_super_secret_token_123" not in cleaned
    assert "sk-abcdefghij1234567890" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned


def test_unified_diff_generation():
    """Verify unified diff output and line counts."""
    old_txt = "line 1\nline 2\nline 3\n"
    new_txt = "line 1\nline 2 modified\nline 3\nline 4\n"

    diff_text, adds, dels = generate_unified_diff(old_txt, new_txt, "demo.py")
    assert "--- a/demo.py" in diff_text
    assert "+++ b/demo.py" in diff_text
    assert "-line 2" in diff_text
    assert "+line 2 modified" in diff_text
    assert "+line 4" in diff_text
    assert adds == 2
    assert dels == 1


@pytest.mark.asyncio
async def test_create_file_with_approval_workflow(tmp_path: Path):
    """Verify file creation requires approval before touching disk."""
    mgr = ApprovalManager()
    tools = FileTools(workspace_root=tmp_path, approval_manager=mgr)

    res = await tools.create_file(
        path="hello/world.py",
        content="print('Hello LocalForge!')\n",
        require_approval=True,
    )

    assert res.success is True
    assert res.approval_required is True
    assert res.approval_request is not None
    req_id = res.approval_request.id

    # Verify file is NOT on disk before approval
    target_file = tmp_path / "hello" / "world.py"
    assert not target_file.exists()

    # Verify pending list
    assert len(mgr.list_pending()) == 1

    # Approve and execute
    exec_res = await mgr.resolve(req_id, approved=True)
    assert exec_res.success is True
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "print('Hello LocalForge!')\n"
    assert mgr.get_request(req_id).status == ApprovalStatus.APPROVED

    # Attempt creating existing file fails safely
    dup_res = await tools.create_file(path="hello/world.py", content="overwrite")
    assert dup_res.success is False
    assert "already exists" in dup_res.message


@pytest.mark.asyncio
async def test_edit_file_approval_and_rejection(tmp_path: Path):
    """Verify file edits preview diff and respect rejection vs approval."""
    target_file = tmp_path / "calc.py"
    target_file.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    mgr = ApprovalManager()
    tools = FileTools(workspace_root=tmp_path, approval_manager=mgr)

    # Propose fix
    res = await tools.edit_file(
        path="calc.py",
        target="return a - b",
        replacement="return a + b",
        require_approval=True,
    )
    assert res.approval_required is True
    assert "-    return a - b" in res.diff
    assert "+    return a + b" in res.diff

    # Reject first
    rej_res = await mgr.resolve(res.approval_request.id, approved=False, rejection_reason="Not ready")
    assert rej_res.success is False
    assert "return a - b" in target_file.read_text(encoding="utf-8")

    # Propose again and approve
    res2 = await tools.edit_file(
        path="calc.py",
        target="return a - b",
        replacement="return a + b",
        require_approval=True,
    )
    app_res = await mgr.resolve(res2.approval_request.id, approved=True)
    assert app_res.success is True
    assert "return a + b" in target_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_delete_file_with_approval(tmp_path: Path):
    """Verify deletion requires explicit approval."""
    target_file = tmp_path / "temp.txt"
    target_file.write_text("temporary data", encoding="utf-8")

    mgr = ApprovalManager()
    tools = FileTools(workspace_root=tmp_path, approval_manager=mgr)

    res = await tools.delete_file(path="temp.txt", require_approval=True)
    assert res.approval_required is True
    assert target_file.exists()

    exec_res = await mgr.resolve(res.approval_request.id, approved=True)
    assert exec_res.success is True
    assert not target_file.exists()


@pytest.mark.asyncio
async def test_approvals_api_endpoints(tmp_path: Path):
    """Verify HTTP approval workflow endpoints."""
    test_settings = Settings(workspace_root=str(tmp_path))
    test_mgr = ApprovalManager()

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_approval_manager] = lambda: test_mgr

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Propose creation
            prop_res = await client.post(
                "/api/approvals/propose-file",
                json={
                    "operation": "create",
                    "path": "app.py",
                    "content": "print('API Test')\n",
                    "description": "Create initial app file",
                },
            )
            assert prop_res.status_code == 200
            data = prop_res.json()
            assert data["approval_required"] is True
            req_id = data["approval_request"]["id"]

            # 2. Check pending endpoint
            list_res = await client.get("/api/approvals")
            assert list_res.status_code == 200
            assert len(list_res.json()) == 1

            # 3. Verify file not created yet
            assert not (tmp_path / "app.py").exists()

            # 4. Resolve with approval
            res_res = await client.post(
                f"/api/approvals/{req_id}/resolve",
                json={"approved": True},
            )
            assert res_res.status_code == 200
            assert res_res.json()["success"] is True
            assert (tmp_path / "app.py").exists()

            # 5. Traversal attempt returns 403
            sec_res = await client.post(
                "/api/approvals/propose-file",
                json={
                    "operation": "create",
                    "path": "../../evil.py",
                    "content": "bad",
                },
            )
            assert sec_res.status_code == 403
    finally:
        app.dependency_overrides.clear()
