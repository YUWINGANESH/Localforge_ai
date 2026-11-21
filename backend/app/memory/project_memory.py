"""Level 2 (Task Memory) and Level 3 (Project Memory) persistence managers."""
import logging
import uuid
from typing import Any

from app.database.sqlite_store import SQLiteStore, get_sqlite_store
from app.schemas.agent import TaskPlan
from app.schemas.context import ProjectSummary

logger = logging.getLogger(__name__)


class MemoryService:
    """Manages persistent Task Memory (L2) and Project Memory (L3)."""

    def __init__(self, store: SQLiteStore | None = None):
        self.store = store or SQLiteStore(":memory:")
        self._active_tasks: dict[str, dict[str, Any]] = {}

    # ---------------- Level 2: Task Memory ----------------

    def start_task(self, session_id: str, workspace_root: str, objective: str) -> str:
        """Create and persist a new task execution state."""
        task_id = str(uuid.uuid4())
        state = {
            "task_id": task_id,
            "session_id": session_id,
            "workspace_root": workspace_root,
            "objective": objective,
            "plan": None,
            "completed_steps": [],
            "failed_steps": [],
            "pending_tasks": [],
            "referenced_files": [],
            "status": "running",
        }
        self._active_tasks[task_id] = state
        self.store.upsert_task_memory(**state)
        return task_id

    def set_task_plan(self, task_id: str, plan: TaskPlan):
        state = self._active_tasks.get(task_id)
        if not state:
            return
        state["plan"] = plan.model_dump(mode="json")
        state["pending_tasks"] = list(plan.required_changes)
        self.store.upsert_task_memory(**state)

    def record_tool_step(
        self,
        task_id: str,
        step_desc: str,
        success: bool,
        referenced_file: str | None = None,
    ):
        state = self._active_tasks.get(task_id)
        if not state:
            return
        if success:
            state["completed_steps"].append(step_desc)
            if state["pending_tasks"]:
                state["pending_tasks"].pop(0)
        else:
            state["failed_steps"].append(step_desc)

        if referenced_file and referenced_file not in state["referenced_files"]:
            state["referenced_files"].append(referenced_file)

        self.store.upsert_task_memory(**state)

    def finish_task(self, task_id: str, status: str = "completed"):
        state = self._active_tasks.get(task_id)
        if not state:
            return
        state["status"] = status
        if status == "completed":
            state["pending_tasks"] = []
        self.store.upsert_task_memory(**state)

    def get_session_tasks(self, session_id: str) -> list[dict[str, Any]]:
        return self.store.get_tasks_for_session(session_id)

    # ---------------- Level 3: Project Memory ----------------

    def sync_project_summary(self, summary: ProjectSummary):
        """Update persisted project structure, frameworks, and dependencies without storing file bodies."""
        existing = self.store.get_project_memory(summary.workspace_root) or {}
        dep_names = [d.name for d in summary.dependencies[:30]]

        self.store.upsert_project_memory(
            workspace_root=summary.workspace_root,
            structure_summary=summary.structure_tree[:2000],
            frameworks=summary.frameworks,
            dependencies=dep_names,
            conventions=existing.get("conventions", []),
            architectural_decisions=existing.get("architectural_decisions", []),
            completed_tasks=existing.get("completed_tasks", []),
        )

    def add_project_convention(self, workspace_root: str, convention: str):
        existing = self.store.get_project_memory(workspace_root) or {}
        conventions = existing.get("conventions", [])
        if convention not in conventions:
            conventions.append(convention)
        self.store.upsert_project_memory(
            workspace_root=workspace_root,
            structure_summary=existing.get("structure_summary", ""),
            frameworks=existing.get("frameworks", []),
            dependencies=existing.get("dependencies", []),
            conventions=conventions[-20:],
            architectural_decisions=existing.get("architectural_decisions", []),
            completed_tasks=existing.get("completed_tasks", []),
        )

    def add_architectural_decision(self, workspace_root: str, decision: str):
        existing = self.store.get_project_memory(workspace_root) or {}
        decisions = existing.get("architectural_decisions", [])
        if decision not in decisions:
            decisions.append(decision)
        self.store.upsert_project_memory(
            workspace_root=workspace_root,
            structure_summary=existing.get("structure_summary", ""),
            frameworks=existing.get("frameworks", []),
            dependencies=existing.get("dependencies", []),
            conventions=existing.get("conventions", []),
            architectural_decisions=decisions[-20:],
            completed_tasks=existing.get("completed_tasks", []),
        )

    def record_completed_task(self, workspace_root: str, task_summary: str):
        existing = self.store.get_project_memory(workspace_root) or {}
        tasks = existing.get("completed_tasks", [])
        tasks.append(task_summary[:300])
        self.store.upsert_project_memory(
            workspace_root=workspace_root,
            structure_summary=existing.get("structure_summary", ""),
            frameworks=existing.get("frameworks", []),
            dependencies=existing.get("dependencies", []),
            conventions=existing.get("conventions", []),
            architectural_decisions=existing.get("architectural_decisions", []),
            completed_tasks=tasks[-15:],
        )

    def get_project_memory(self, workspace_root: str) -> dict[str, Any] | None:
        return self.store.get_project_memory(workspace_root)

    def get_compact_prompt_memory(self, workspace_root: str) -> str:
        """Return concise project memory snippet for LLM context injection."""
        mem = self.get_project_memory(workspace_root)
        if not mem:
            return ""
        parts = []
        if mem.get("conventions"):
            parts.append("Conventions: " + "; ".join(mem["conventions"][:5]))
        if mem.get("architectural_decisions"):
            parts.append("Decisions: " + "; ".join(mem["architectural_decisions"][:5]))
        if mem.get("completed_tasks"):
            parts.append("Recent Completed Tasks: " + " | ".join(mem["completed_tasks"][-3:]))
        return "\n".join(parts)

    def clear_project_memory(self, workspace_root: str | None = None):
        self.store.clear_project_memory(workspace_root)


_global_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    """Singleton getter for MemoryService backed by SQLiteStore."""
    global _global_memory_service
    if _global_memory_service is None:
        _global_memory_service = MemoryService(store=get_sqlite_store())
    return _global_memory_service
