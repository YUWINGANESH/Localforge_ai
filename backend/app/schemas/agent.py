"""Schemas for autonomous agent planning, ReAct turns, and completion reports."""
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentPhase(StrEnum):
    """Seven phases of the LocalForge-AI autonomous software engineering workflow."""
    UNDERSTAND = "UNDERSTAND"
    PLAN = "PLAN"
    IMPLEMENT = "IMPLEMENT"
    EXECUTE = "EXECUTE"
    DEBUG = "DEBUG"
    VALIDATE = "VALIDATE"
    REPORT = "REPORT"


class TaskPlan(BaseModel):
    """Structured implementation plan generated before coding."""
    objective: str = Field(..., description="Clear statement of the task goal")
    required_changes: list[str] = Field(default_factory=list, description="Ordered steps to execute")
    files_to_create: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    testing_strategy: str = Field("", description="How the implementation will be verified")
    potential_risks: list[str] = Field(default_factory=list)


class CompletionReport(BaseModel):
    """Structured summary produced at the conclusion of an autonomous task."""
    summary: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    commands_executed: list[str] = Field(default_factory=list)
    tests_performed: list[str] = Field(default_factory=list)
    test_results: str = "No tests executed"
    remaining_issues: str = "None"
    suggested_next_steps: list[str] = Field(default_factory=list)


class AgentStepDecision(BaseModel):
    """Parsed ReAct turn decision from the LLM."""
    thought: str = Field("", description="Reasoning for the next step")
    action: str = Field(..., description="Tool name to invoke or 'final_answer'")
    arguments: dict[str, Any] = Field(default_factory=dict)
    report: CompletionReport | None = None


class AgentRunRequest(BaseModel):
    """API request to run an autonomous agent workflow."""
    session_id: str = Field("default", description="Session identifier")
    prompt: str = Field(..., min_length=1, description="Task instruction from the user")
    model: str | None = Field(None, description="Optional model override")
    max_iterations: int = Field(16, ge=1, le=25, description="Maximum bounded ReAct loop steps")
    max_repair_attempts: int = Field(4, ge=0, le=6, description="Maximum autonomous debug/repair loops")
    auto_approve: bool = Field(False, description="Skip interactive approval (testing only)")
