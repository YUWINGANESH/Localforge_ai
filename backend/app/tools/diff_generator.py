"""Unified diff generation utility."""
import difflib


def generate_unified_diff(
    original_content: str,
    new_content: str,
    file_path: str = "file",
) -> tuple[str, int, int]:
    """
    Generate unified diff between original and new content.
    
    Returns:
        Tuple of (diff_text: str, additions_count: int, deletions_count: int)
    """
    orig_lines = original_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        orig_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    )

    diff_lines = list(diff)
    additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    return "\n".join(diff_lines), additions, deletions
