from __future__ import annotations

import json
from typing import Any

from ..models import RiskLevel, ToolDefinition, ToolResult


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def s() -> dict[str, Any]: return {"type": "string"}
def b() -> dict[str, Any]: return {"type": "boolean"}
def arr() -> dict[str, Any]: return {"type": "array", "items": {"type": "string"}, "maxItems": 200}


def register_configuration_tools(registry, manager, events=None, human_tasks=None, projects=None) -> None:
    async def configuration_status(project: str = "", environment: str = "") -> ToolResult:
        data = manager.status(project, environment, discover=True)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "configuration_status",
        "Inspect required/missing environment variables for the active/specified project and environment. Secret values are never returned; only configured/missing metadata is visible.",
        obj({"project": s(), "environment": s()}), configuration_status, {"config.read"}, RiskLevel.LOW, True,
    ))

    async def configuration_discover(project: str = "", environment: str = "") -> ToolResult:
        data = manager.discover(project, environment, persist=True)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "configuration_discover",
        "Discover environment variable requirements from .env.example/schema files, Docker Compose substitutions, Python os.environ/os.getenv and JS process.env usage. Does not expose current secret values.",
        obj({"project": s(), "environment": s()}), configuration_discover, {"config.read"}, RiskLevel.LOW, True,
    ))

    async def configuration_require(keys: list[str], secret_keys: list[str] | None = None, reason: str = "",
                                    service: str = "application", project: str = "", environment: str = "") -> ToolResult:
        data = manager.require(keys, project=project, environment=environment, secret_keys=secret_keys or [], reason=reason, service=service)
        missing = data.get("missing") or []
        human_task = None
        if human_tasks is not None and missing:
            project_name = str(data.get("project") or project or (projects.registry.active_name if projects is not None else ""))
            secret_set = {str(x).upper() for x in (secret_keys or [])}
            existing = [x for x in human_tasks.list(state="open", project=project_name, limit=500) if x.get("kind") == "configure" and str((x.get("metadata") or {}).get("environment") or "production") == str(data.get("environment") or environment or "production") and set(missing).issubset({str(f.get("name") or "") for f in x.get("fields") or []})]
            if existing:
                human_task = existing[0]
            else:
                human_task = human_tasks.create(
                    kind="configure", title=f"Configure {service or 'application'}",
                    description=str(reason or "Configuration is required to continue this work."),
                    project=project_name,
                    fields=[{"name": k, "label": k, "secret": bool(k.upper() in secret_set), "required": True, "service": service or "application"} for k in missing],
                    timeout_policy="DEFER", timeout_seconds=30, blocking=True,
                    resume_instruction="Required configuration is now available. Test the relevant connection when supported, then resume the blocked engineering work.",
                    metadata={"environment": str(data.get("environment") or environment or "production"), "service": service or "application"},
                )
        if events is not None:
            await events.emit("configuration.required", project=data.get("project"), environment=data.get("environment"),
                              keys=list(keys), secret_keys=list(secret_keys or []), reason=reason, service=service, human_task_id=(human_task or {}).get("id", ""))
        body = (
            "Human configuration input is required. Do NOT ask the user to paste secret values into chat. "
            "The Configuration UI will request these values locally. End this turn after explaining what is blocked.\n\n"
            f"Project: {data.get('project')}\nEnvironment: {data.get('environment')}\nMissing: {', '.join(missing) or '(none)'}\n"
            f"Reason: {reason or 'configuration required'}"
        )
        return ToolResult(True, body, data={**data, "waiting_for_human": bool(missing), "human_task": human_task})
    registry.register(ToolDefinition(
        "configuration_require",
        "Declare human-supplied project configuration/credentials that are required to continue. Use instead of asking the user to paste passwords/API keys in chat. This creates a Configuration UI prompt and the turn should stop cleanly until the user saves and resumes.",
        obj({"keys": arr(), "secret_keys": arr(), "reason": s(), "service": s(), "project": s(), "environment": s()}, ["keys"]),
        configuration_require, {"config.write"}, RiskLevel.MEDIUM, False, True,
    ))

    async def configuration_set(values: list[dict[str, Any]], project: str = "", environment: str = "", materialize: bool = True) -> ToolResult:
        # Model-facing setter intentionally rejects secret fields. Human-entered secrets use the UI API.
        clean: list[dict[str, Any]] = []
        for item in values:
            if bool(item.get("secret")):
                return ToolResult(False, "Secret values cannot be supplied through model tool arguments. Use configuration_require so the user can enter them in the Configuration UI.")
            clean.append({
                "name": str(item.get("name") or ""), "value": str(item.get("value") or ""),
                "secret": False, "required": bool(item.get("required", True)),
                "service": str(item.get("service") or "application"), "description": str(item.get("description") or ""),
            })
        data = manager.set_values(clean, project=project, environment=environment, materialize=materialize)
        if events is not None:
            await events.emit("configuration.updated", project=data.get("project"), environment=data.get("environment"), keys=[x.get("name") for x in clean])
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "configuration_set",
        "Set non-secret environment configuration for the active/specified project. Secret values are explicitly forbidden in tool arguments and must be supplied by the user through the Configuration UI.",
        obj({
            "values": {"type": "array", "minItems": 1, "maxItems": 200, "items": {"type": "object", "properties": {
                "name": s(), "value": s(), "required": b(), "service": s(), "description": s(), "secret": b(),
            }, "required": ["name", "value"], "additionalProperties": False}},
            "project": s(), "environment": s(), "materialize": b(),
        }, ["values"]), configuration_set, {"config.write"}, RiskLevel.HIGH, False, True,
    ))

    async def configuration_test(service: str, project: str = "", environment: str = "") -> ToolResult:
        data = manager.test_connection(service, project=project, environment=environment)
        if events is not None:
            await events.emit("configuration.test", service=service, project=data.get("project", project), environment=environment, ok=bool(data.get("ok")), error=data.get("error", ""))
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "configuration_test",
        "Run a typed local connection test for configured SMTP, DeepSeek, MongoDB, Redis or PostgreSQL/database settings. Secrets are resolved locally and never included in the tool result.",
        obj({"service": {"type": "string", "enum": ["smtp", "deepseek", "mongodb", "redis", "postgres", "database"]}, "project": s(), "environment": s()}, ["service"]),
        configuration_test, {"config.read"}, RiskLevel.MEDIUM, True,
    ))
