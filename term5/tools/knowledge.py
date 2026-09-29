from __future__ import annotations

import json

from ..models import RiskLevel, ToolDefinition, ToolResult
from ..skills import SkillEngine


def register_knowledge_tools(registry, skills: SkillEngine, product_planner) -> None:
    def obj(props=None, required=None):
        return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}
    def s(): return {"type": "string"}

    async def skill_list() -> ToolResult:
        rows = []
        for name in skills.names():
            skill = skills.get(name)
            rows.append({"name": name, "category": skill.category if skill else "", "description": skill.description if skill else ""})
        return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), data=rows)

    registry.register(ToolDefinition(
        "skill_list", "List local/bundled procedural knowledge skills available to term_5.",
        obj(), skill_list, set(), RiskLevel.LOW, True,
    ))

    async def skill_reload() -> ToolResult:
        skills.reload()
        data = {"skills": len(skills.names()), "names": skills.names()}
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition(
        "skill_reload", "Reload bundled plus .term5/skills declarative procedural knowledge from disk.",
        obj(), skill_reload, set(), RiskLevel.LOW, True, True,
    ))

    async def skill_show(name: str) -> ToolResult:
        data = skills.describe(name)
        if data is None:
            return ToolResult(False, f"Unknown skill: {name}")
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition(
        "skill_show", "Inspect one declarative procedural skill including features, journeys, quality rules and definition of done.",
        obj({"name": s()}, ["name"]), skill_show, set(), RiskLevel.LOW, True,
    ))

    async def skill_resolve(text: str) -> ToolResult:
        res = skills.resolve(text)
        data = {"direct": res.direct, "selected": res.selected, "scores": res.scores}
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition(
        "skill_resolve", "Resolve the procedural/product/framework/quality skills relevant to a request without calling the model.",
        obj({"text": s()}, ["text"]), skill_resolve, set(), RiskLevel.LOW, True,
    ))

    async def product_plan_current() -> ToolResult:
        data = product_planner.current()
        if data is None:
            return ToolResult(False, "No product blueprint has been prepared in this runtime yet.")
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition(
        "product_plan_current", "Return the current automatically prepared product blueprint/feature matrix for this session.",
        obj(), product_plan_current, set(), RiskLevel.LOW, True,
    ))
