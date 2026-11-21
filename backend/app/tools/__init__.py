"""Agent tools package."""
from app.tools.diff_generator import generate_unified_diff
from app.tools.error_inspector import inspect_error_trace
from app.tools.file_ops import FileTools
from app.tools.registry import ToolDefinition, ToolRegistry, ToolResult

__all__ = [
    "FileTools",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "generate_unified_diff",
    "inspect_error_trace",
]
