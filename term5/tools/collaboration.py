from __future__ import annotations

import json
from typing import Any

from ..models import RiskLevel, ToolDefinition, ToolResult


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def register_collaboration_tools(registry, human_tasks, durable_runs, *, projects=None, events=None, config=None) -> None:
    s = lambda: {"type": "string"}
    b = lambda: {"type": "boolean"}
    i = lambda lo=0, hi=86400: {"type": "integer", "minimum": lo, "maximum": hi}

    option_schema = {
        "type": "object",
        "properties": {
            "id": s(), "label": s(), "description": s(), "artifact_id": s(), "recommended": b(),
        },
        "required": ["id", "label"], "additionalProperties": False,
    }

    async def human_task_create(kind: str, title: str, description: str = "", options: list[dict[str, Any]] | None = None,
                                timeout_policy: str = "BLOCK", timeout_seconds: int = 30, default_option: str = "",
                                blocking: bool = True, project: str = "", resume_instruction: str = "", priority: str = "normal") -> ToolResult:
        project_name = project
        if not project_name and projects is not None:
            project_name = projects.registry.active_name
        rec = human_tasks.create(
            kind=kind, title=title, description=description, options=options or [], timeout_policy=timeout_policy,
            timeout_seconds=timeout_seconds, default_option=default_option, blocking=blocking,
            project=project_name, resume_instruction=resume_instruction, priority=priority,
        )
        if events is not None:
            await events.emit("human_task.created", task_id=rec["id"], kind=rec["kind"], title=rec["title"], project=rec.get("project", ""), blocking=rec.get("blocking"), timeout_policy=rec.get("timeout_policy"), deadline_at=rec.get("deadline_at"))
        body = (
            f"Human task created: {rec['id']} — {rec['title']}\n"
            f"Kind: {rec['kind']}\nPolicy: {rec['timeout_policy']}\nBlocking: {rec['blocking']}\n"
            "Do not ask the user to repeat the choice in chat. The My Tasks board owns this interaction. "
            "If the task is blocking, end the current turn cleanly after stating what can continue and what is waiting."
        )
        return ToolResult(True, body, data=rec)

    registry.register(ToolDefinition(
        "human_task_create",
        "Create a durable task for the human when their preference, information, review, authorization or approval materially improves the result. Use AUTO_DECIDE only for safe subjective choices, DEFER for input that can wait, and BLOCK for risky approvals. Do not create tasks for trivial decisions the AI can safely make itself.",
        _obj({
            "kind": {"type": "string", "enum": ["choose", "approve", "answer", "authorize", "review", "resolve"]},
            "title": s(), "description": s(),
            "options": {"type": "array", "maxItems": 12, "items": option_schema},
            "timeout_policy": {"type": "string", "enum": ["AUTO_DECIDE", "DEFER", "BLOCK"]},
            "timeout_seconds": i(5, 86400), "default_option": s(), "blocking": b(), "project": s(),
            "resume_instruction": s(), "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
        }, ["kind", "title"]),
        human_task_create, {"human.task"}, RiskLevel.MEDIUM, False, True,
    ))

    async def human_task_list(state: str = "open", project: str = "", limit: int = 100) -> ToolResult:
        data = human_tasks.list(state=state, project=project, limit=limit)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition(
        "human_task_list", "Inspect the durable My Tasks queue. No secret values are ever returned.",
        _obj({"state": {"type": "string", "enum": ["open", "completed", "resolved", "deferred"]}, "project": s(), "limit": i(1, 500)}),
        human_task_list, {"human.task"}, RiskLevel.LOW, True,
    ))

    async def durable_run_list(status: str = "", limit: int = 100) -> ToolResult:
        data = durable_runs.list(limit=limit, status=status)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition(
        "durable_run_list", "Inspect durable term_6 objective/run state, including running, waiting-human and completed work.",
        _obj({"status": s(), "limit": i(1, 500)}), durable_run_list, {"human.task"}, RiskLevel.LOW, True,
    ))

    async def durable_run_status(run_id: str) -> ToolResult:
        data = durable_runs.get(run_id)
        return ToolResult(bool(data), json.dumps(data or {"error": "run not found"}, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition(
        "durable_run_status", "Inspect one durable term_6 run by id.",
        _obj({"run_id": s()}, ["run_id"]), durable_run_status, {"human.task"}, RiskLevel.LOW, True,
    ))
