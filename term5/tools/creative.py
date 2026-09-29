from __future__ import annotations

import json
from typing import Any

from ..models import RiskLevel, ToolDefinition, ToolResult


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


def register_creative_tools(registry, studio, human_tasks, *, projects=None, configuration=None, events=None) -> None:
    s = lambda: {"type": "string"}
    b = lambda: {"type": "boolean"}
    direction_schema = {
        "type": "object",
        "properties": {
            "label": s(), "prompt": s(), "description": s(),
            "palette": {"type": "array", "maxItems": 5, "items": s()},
            "recommended": b(),
        },
        "required": ["label"], "additionalProperties": False,
    }

    def active_project(project: str) -> str:
        if project:
            return project
        if projects is not None:
            return projects.registry.active_name
        return ""

    async def creative_status(project: str = "", environment: str = "production") -> ToolResult:
        data = studio.status(active_project(project), environment)
        return ToolResult(True, json.dumps(data, indent=2), data=data)

    registry.register(ToolDefinition(
        "creative_status", "Inspect Creative Studio readiness. Reports whether OpenAI image generation is configured without exposing the API key.",
        obj({"project": s(), "environment": s()}), creative_status, {"creative.generate"}, RiskLevel.LOW, True,
    ))

    async def creative_svg_concepts(brief: str, directions: list[dict[str, Any]], project: str = "", ask_human: bool = True,
                                    timeout_seconds: int = 30, default_option: str = "") -> ToolResult:
        project_name = active_project(project)
        data = studio.generate_svg(brief=brief, directions=directions, project=project_name)
        task = None
        if ask_human and data.get("options"):
            options = []
            for idx, item in enumerate(data["options"]):
                source = directions[idx] if idx < len(directions) else {}
                options.append({**item, "recommended": bool(source.get("recommended"))})
            task = human_tasks.create(
                kind="choose", title="Choose a design direction", description=brief, project=project_name,
                options=options, timeout_policy="AUTO_DECIDE", timeout_seconds=timeout_seconds,
                default_option=default_option, blocking=True,
                resume_instruction="Continue the design/implementation using the selected concept. Inspect the selected artifact with vision when useful, then implement it in the real application and verify the rendered result.",
                metadata={"creative": True, "provider": "svg"},
            )
            if events is not None:
                await events.emit("human_task.created", task_id=task["id"], kind="choose", title=task["title"], project=project_name, blocking=True, timeout_policy="AUTO_DECIDE", deadline_at=task.get("deadline_at"))
        result = {**data, "human_task": task}
        return ToolResult(True, json.dumps(result, ensure_ascii=False, indent=2), data=result)

    registry.register(ToolDefinition(
        "creative_svg_concepts", "Create 2-4 lightweight SVG design-direction cards locally. Use when a visual decision benefits from alternatives but full image generation is unnecessary. Can create a 30-second My Tasks choice automatically.",
        obj({"brief": s(), "directions": {"type": "array", "minItems": 2, "maxItems": 4, "items": direction_schema}, "project": s(), "ask_human": b(), "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 600}, "default_option": s()}, ["brief", "directions"]),
        creative_svg_concepts, {"creative.generate"}, RiskLevel.LOW, False, True,
    ))

    async def creative_image_concepts(brief: str, directions: list[dict[str, Any]], project: str = "", environment: str = "production",
                                      ask_human: bool = True, timeout_seconds: int = 30, default_option: str = "") -> ToolResult:
        project_name = active_project(project)
        stat = studio.status(project_name, environment)
        if not stat.get("openai_configured"):
            key = stat.get("api_key_name") or "OPENAI_API_KEY"
            if configuration is not None and project_name:
                configuration.require([key], project=project_name, environment=environment, secret_keys=[key], reason="OpenAI image generation is required for creative design mockups", service="openai")
            existing = [x for x in human_tasks.list(state="open", project=project_name, limit=500) if x.get("kind") == "configure" and any(f.get("name") == key for f in x.get("fields") or [])]
            if existing:
                task = existing[0]
            else:
                task = human_tasks.create(
                    kind="configure", title="Configure OpenAI image generation", description="Creative Studio needs an OpenAI API key to generate visual mockups. You can skip it and term_6 will use local SVG/HTML concepts instead.",
                    project=project_name, fields=[{"name": key, "label": key, "secret": True, "required": True, "service": "openai"}],
                    timeout_policy="DEFER", timeout_seconds=30, blocking=False,
                    resume_instruction="OpenAI image generation is now configured. Generate the pending visual concepts and continue the design decision.",
                    metadata={"environment": environment, "service": "openai", "creative": True},
                )
            return ToolResult(False, f"{key} is not configured. A non-blocking My Tasks configuration task was created; use creative_svg_concepts if visual alternatives are needed immediately.", data={"waiting_for_human": True, "human_task": task, "fallback": "creative_svg_concepts"})
        data = await studio.generate_images(brief=brief, directions=directions, project=project_name, environment=environment)
        task = None
        if ask_human and data.get("options"):
            options = []
            for idx, item in enumerate(data["options"]):
                source = directions[idx] if idx < len(directions) else {}
                options.append({**item, "recommended": bool(source.get("recommended"))})
            task = human_tasks.create(
                kind="choose", title="Choose a visual direction", description=brief, project=project_name,
                options=options, timeout_policy="AUTO_DECIDE", timeout_seconds=timeout_seconds,
                default_option=default_option, blocking=True,
                resume_instruction="Continue from the selected image concept. Use vision_inspect on the selected creative artifact, extract a concrete UI design specification, implement it using the project's real components/styles, then render and compare the real UI.",
                metadata={"creative": True, "provider": "openai", "model": data.get("model", "")},
            )
            if events is not None:
                await events.emit("human_task.created", task_id=task["id"], kind="choose", title=task["title"], project=project_name, blocking=True, timeout_policy="AUTO_DECIDE", deadline_at=task.get("deadline_at"))
        result = {**data, "human_task": task}
        return ToolResult(True, json.dumps(result, ensure_ascii=False, indent=2), data=result)

    registry.register(ToolDefinition(
        "creative_image_concepts", "Generate 2-4 polished design mockups with the configured OpenAI image model, store them as local image artifacts, and optionally create a 30-second My Tasks choice. DeepSeek remains the engineering coordinator; after selection, use vision to translate the chosen concept into implementation evidence.",
        obj({"brief": s(), "directions": {"type": "array", "minItems": 2, "maxItems": 4, "items": direction_schema}, "project": s(), "environment": s(), "ask_human": b(), "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 600}, "default_option": s()}, ["brief", "directions"]),
        creative_image_concepts, {"creative.generate"}, RiskLevel.MEDIUM, False, True,
    ))
