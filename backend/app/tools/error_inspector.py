"""Diagnostic utility to inspect tracebacks and extract surrounding failing source code."""
import re
from pathlib import Path
from typing import Any

from app.context.file_reader import SafeFileReader

PYTHON_TRACEBACK_RE = re.compile(r'File\s+"([^"]+)",\s+line\s+(\d+)')
PYTEST_LOCATION_RE = re.compile(r'([^\s:]+\.py):(\d+):')
JS_TRACEBACK_RE = re.compile(r'at\s+(?:.*?\s+\()?([^():\s]+\.[jt]sx?):(\d+):\d+\)?')


def inspect_error_trace(workspace_root: Path, error_output: str) -> dict[str, Any]:
    """
    Parse error output/tracebacks, extract failing file paths and lines,
    and read surrounding code context safely within the workspace.
    """
    reader = SafeFileReader(workspace_root)
    locations: list[tuple[str, int]] = []

    for match in PYTHON_TRACEBACK_RE.finditer(error_output):
        locations.append((match.group(1), int(match.group(2))))

    for match in PYTEST_LOCATION_RE.finditer(error_output):
        locations.append((match.group(1), int(match.group(2))))

    for match in JS_TRACEBACK_RE.finditer(error_output):
        locations.append((match.group(1), int(match.group(2))))

    # Deduplicate while preserving order
    seen = set()
    unique_locs = []
    for file_str, line_no in locations:
        key = (file_str, line_no)
        if key not in seen:
            seen.add(key)
            unique_locs.append(key)

    snippets = []
    for file_str, line_no in unique_locs:
        try:
            start = max(1, line_no - 6)
            end = line_no + 6
            # Normalize path separators
            norm_str = file_str.replace("\\", "/")
            ws_name = workspace_root.name
            
            # Check direct resolution
            target_file = norm_str
            if not (workspace_root / target_file).exists():
                # If path has workspace folder prefix (e.g. acceptance_demo/app.py)
                if norm_str.startswith(f"{ws_name}/"):
                    target_file = norm_str[len(ws_name) + 1:]
                elif (workspace_root / Path(norm_str).name).exists():
                    target_file = Path(norm_str).name

            read_res = reader.read_file(target_file, start_line=start, end_line=end)
            snippets.append({
                "file": read_res.relative_path,
                "error_line": line_no,
                "start_line": read_res.start_line,
                "end_line": read_res.end_line,
                "code": read_res.content,
            })
        except Exception:
            # Skip system/stdlib/site-packages files outside workspace
            continue

    lines = [line.strip() for line in error_output.strip().splitlines() if line.strip()]
    probable_exception = lines[-1] if lines else "Unknown error"

    return {
        "probable_exception": probable_exception,
        "workspace_locations_found": len(snippets),
        "snippets": snippets,
    }
