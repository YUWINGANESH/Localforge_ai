"""Workspace path validation and secret redaction utilities."""
import re
from pathlib import Path

SENSITIVE_FILENAME_PATTERNS: set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "secrets.json",
    "credentials.json",
}

SENSITIVE_DIR_PREFIXES: set[str] = {
    ".git",
}

SECRET_REGEX_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|password|passwd|token|auth[_-]?token)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----[\s\S]*?-----END .*?PRIVATE KEY-----"),
]


class PathSecurityError(Exception):
    """Raised when an access or write attempt violates workspace sandbox rules."""
    pass


def resolve_safe_workspace_path(workspace_root: Path, target_path: str, allow_sensitive: bool = False) -> Path:
    """
    Resolve a target file path and guarantee it remains inside workspace_root.
    
    Raises:
        PathSecurityError: If path escapes workspace or targets restricted system paths.
    """
    resolved_root = workspace_root.resolve()
    raw = Path(target_path)

    if raw.is_absolute():
        resolved_target = raw.resolve()
    else:
        resolved_target = (resolved_root / raw).resolve()

    try:
        rel_path = resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise PathSecurityError(
            f"Security Violation: Path '{target_path}' escapes active workspace '{resolved_root}'"
        ) from exc

    # Block internal .git manipulation
    parts = rel_path.parts
    if parts and parts[0] in SENSITIVE_DIR_PREFIXES:
        raise PathSecurityError(f"Security Violation: Direct access to '{parts[0]}' directory is restricted.")

    # Block credential files unless explicitly allowed
    if not allow_sensitive and resolved_target.name.lower() in SENSITIVE_FILENAME_PATTERNS:
        raise PathSecurityError(f"Security Violation: Direct modification or exposure of '{resolved_target.name}' is restricted.")

    return resolved_target


def redact_secrets(text: str) -> str:
    """Redact potential API keys, tokens, and private keys from logs or model context."""
    redacted = text
    for pattern in SECRET_REGEX_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted
