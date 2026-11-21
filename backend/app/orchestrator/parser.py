"""Resilient JSON and structured output parser tuned for 4B-7B local LLMs."""
import json
import re
from typing import Any

from app.schemas.agent import AgentStepDecision, CompletionReport, TaskPlan

THINK_TAG_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)
FENCED_JSON_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_json_object(raw_text: str) -> tuple[dict[str, Any] | None, str]:
    """
    Extract the first valid JSON object from potentially noisy LLM output
    (handles <think> tags, markdown fences, and leading/trailing prose).
    
    Returns:
        (parsed_dict_or_none, extracted_think_text)
    """
    think_text = ""
    think_match = THINK_TAG_RE.search(raw_text)
    if think_match:
        think_text = think_match.group(1).strip()
        cleaned = THINK_TAG_RE.sub("", raw_text).strip()
    else:
        cleaned = raw_text.strip()

    # 1. Try fenced code block first
    fence_match = FENCED_JSON_RE.search(cleaned)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj, think_text
        except json.JSONDecodeError:
            pass

    # 2. Try raw string directly
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj, think_text
    except json.JSONDecodeError:
        pass

    # 3. Balanced brace scanner for first embedded JSON object
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        snippet = cleaned[start : i + 1]
                        try:
                            obj = json.loads(snippet)
                            if isinstance(obj, dict):
                                return obj, think_text
                        except json.JSONDecodeError:
                            break

    return None, think_text


def extract_live_thought(accumulated: str) -> str:
    """
    Extract progressive thinking / reasoning text from a live LLM token stream.
    Supports <think>...</think> tags (qwen3, deepseek-r1) and JSON "thought": "..." fields.
    """
    # 1. Check <think> tag
    if "<think>" in accumulated:
        idx = accumulated.find("<think>") + 7
        end_idx = accumulated.find("</think>")
        if end_idx != -1:
            return accumulated[idx:end_idx].strip()
        return accumulated[idx:].strip()

    # 2. Check JSON "thought": "..."
    m = re.search(r'"thought"\s*:\s*"((?:[^"\\]|\\.)*)', accumulated)
    if m:
        val = m.group(1).replace('\"', '"').replace('\\n', '\n')
        return val.strip()

    # 3. Leading plain-text reasoning before JSON starts
    json_start = accumulated.find("{")
    if json_start > 0:
        return accumulated[:json_start].strip()

    return ""


def parse_task_plan(raw_text: str, fallback_objective: str) -> TaskPlan:
    """Parse structured TaskPlan from LLM response with safe normalization."""
    data, _ = extract_json_object(raw_text)
    if not data:
        return TaskPlan(
            objective=fallback_objective,
            required_changes=[raw_text.strip()[:500] or "Analyze and implement requested changes"],
            testing_strategy="Verify behavior with automated tests.",
        )

    def ensure_list(val: Any) -> list[str]:
        if isinstance(val, list):
            res = []
            for x in val:
                if isinstance(x, dict):
                    name = x.get("path") or x.get("name") or x.get("file") or x.get("filename")
                    if name:
                        res.append(str(name).strip())
                    else:
                        res.append(str(x))
                else:
                    res.append(str(x))
            return res
        if isinstance(val, dict):
            name = val.get("path") or val.get("name") or val.get("file") or val.get("filename")
            if name:
                return [str(name).strip()]
            return [str(val)]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        return []

    return TaskPlan(
        objective=str(data.get("objective") or fallback_objective),
        required_changes=ensure_list(data.get("required_changes") or data.get("steps")),
        files_to_create=ensure_list(data.get("files_to_create")),
        files_to_modify=ensure_list(data.get("files_to_modify")),
        dependencies=ensure_list(data.get("dependencies")),
        testing_strategy=str(data.get("testing_strategy") or "Run automated tests to validate changes."),
        potential_risks=ensure_list(data.get("potential_risks")),
    )


def parse_step_decision(raw_text: str) -> tuple[AgentStepDecision | None, str | None]:
    """
    Parse a ReAct step decision from LLM output.
    Returns (AgentStepDecision, error_message_if_unparseable).
    """
    data, think_tag = extract_json_object(raw_text)
    if not data:
        return None, (
            "Invalid response format. You MUST respond with a valid JSON object containing "
            "'thought', 'action', and 'arguments' (or 'report' if action is 'final_answer')."
        )

    thought = str(data.get("thought") or think_tag or "").strip()
    action = str(data.get("action") or data.get("tool") or data.get("name") or "").strip()

    if not action:
        return None, "Missing 'action' field in JSON decision."

    if action.lower() in {"final_answer", "complete", "done", "finish"}:
        raw_report = data.get("report")
        if isinstance(raw_report, dict):
            report = CompletionReport(
                summary=str(raw_report.get("summary") or data.get("summary") or "Task completed."),
                files_created=[str(x) for x in raw_report.get("files_created", [])],
                files_modified=[str(x) for x in raw_report.get("files_modified", [])],
                commands_executed=[str(x) for x in raw_report.get("commands_executed", [])],
                tests_performed=[str(x) for x in raw_report.get("tests_performed", [])],
                test_results=str(raw_report.get("test_results") or "Not reported"),
                remaining_issues=str(raw_report.get("remaining_issues") or "None"),
                suggested_next_steps=[str(x) for x in raw_report.get("suggested_next_steps", [])],
            )
        else:
            summary_str = str(raw_report or data.get("answer") or data.get("summary") or thought or "Task complete.")
            report = CompletionReport(summary=summary_str)

        return AgentStepDecision(
            thought=thought,
            action="final_answer",
            arguments={},
            report=report,
        ), None

    raw_args = data.get("arguments") or data.get("args") or data.get("parameters") or data.get("input") or {}
    if not isinstance(raw_args, dict):
        raw_args = {}

    return AgentStepDecision(
        thought=thought,
        action=action,
        arguments=raw_args,
    ), None
