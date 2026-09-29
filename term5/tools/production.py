from __future__ import annotations

import json
from typing import Any

from ..models import RiskLevel, ToolDefinition, ToolResult
from .registry import ToolRegistry


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def s() -> dict[str, Any]: return {"type": "string"}
def b() -> dict[str, Any]: return {"type": "boolean"}
def i(a: int = 0, z: int = 10000) -> dict[str, Any]: return {"type": "integer", "minimum": a, "maximum": z}


ENV = {"type": "string", "enum": ["development", "staging", "production"]}


def register_production_tools(registry: ToolRegistry, production) -> None:
    async def production_overview(project: str = "") -> ToolResult:
        data = production.overview(project)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "production_overview", "Show active-project environments, releases, latest production evidence and open incidents without changing production.",
        obj({"project": s()}), production_overview, {"production.read"}, RiskLevel.LOW, True,
    ))

    async def environment_list(project: str = "") -> ToolResult:
        data = production.environments(project)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "environment_list", "List development/staging/production environment mappings and configuration readiness for one project.",
        obj({"project": s()}), environment_list, {"production.read"}, RiskLevel.LOW, True,
    ))

    async def environment_register(project: str, environment: str, deployment: str = "", app_name: str = "", url: str = "") -> ToolResult:
        try:
            data = production.register_environment(project=project, environment=environment, deployment=deployment, app_name=app_name, url=url)
            return ToolResult(True, json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")
    registry.register(ToolDefinition(
        "environment_register", "Register or update an environment mapping for a project. This changes only term_5 environment metadata; it does not deploy code.",
        obj({"project": s(), "environment": ENV, "deployment": s(), "app_name": s(), "url": s()}, ["project", "environment"]),
        environment_register, {"production.manage"}, RiskLevel.MEDIUM, False, True,
    ))

    async def production_snapshot(deployment: str = "", project: str = "", environment: str = "production", persist: bool = True) -> ToolResult:
        try:
            data = production.snapshot(deployment=deployment, project=project, environment=environment, persist=persist, detect=True)
            return ToolResult(True, json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")
    registry.register(ToolDefinition(
        "production_snapshot", "Collect one evidence snapshot: app/container health, Docker resource use, public probe latency, scoped Nginx request/error sample, configuration readiness and host state. Persists bounded history by default.",
        obj({"deployment": s(), "project": s(), "environment": ENV, "persist": b()}), production_snapshot,
        {"production.read"}, RiskLevel.LOW, False,
    ))

    async def production_metrics(deployment: str = "", project: str = "", environment: str = "", limit: int = 100) -> ToolResult:
        data = production.metrics(deployment=deployment, project=project, environment=environment, limit=limit)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "production_metrics", "Read persisted production evidence snapshots for trend/correlation analysis.",
        obj({"deployment": s(), "project": s(), "environment": s(), "limit": i(1,1000)}), production_metrics,
        {"production.read"}, RiskLevel.LOW, True,
    ))

    async def production_logs(deployment: str, source: str = "app", service: str = "", search: str = "", level: str = "", lines: int = 300) -> ToolResult:
        try:
            data = production.logs(deployment=deployment, source=source, service=service, search=search, level=level, lines=lines)
            text = "\n".join(data.get("lines") or [])
            return ToolResult(True, text or "(no matching log lines)", data={k:v for k,v in data.items() if k != "lines"})
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")
    registry.register(ToolDefinition(
        "production_logs", "Search bounded application or Nginx logs for one deployment. source: app, nginx_access, nginx_error.",
        obj({"deployment": s(), "source": {"type":"string","enum":["app","nginx_access","nginx_error"]}, "service": s(), "search": s(), "level": s(), "lines": i(1,5000)}, ["deployment"]),
        production_logs, {"production.read"}, RiskLevel.LOW, False,
    ))

    async def incident_list(project: str = "", environment: str = "", status: str = "", limit: int = 200) -> ToolResult:
        data = production.incidents(project=project, environment=environment, status=status, limit=limit)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "incident_list", "List durable production incidents with deployment/commit correlation and evidence.",
        obj({"project": s(), "environment": s(), "status": s(), "limit": i(1,1000)}), incident_list,
        {"production.read"}, RiskLevel.LOW, True,
    ))

    async def incident_detect(deployment: str) -> ToolResult:
        try:
            snap = production.snapshot(deployment=deployment, persist=True, detect=False)
            data = production.detect_incidents(snapshot=snap)
            return ToolResult(True, json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")
    registry.register(ToolDefinition(
        "incident_detect", "Collect current evidence for one deployment and create/update incident records for concrete health/5xx/configuration failures.",
        obj({"deployment": s()}, ["deployment"]), incident_detect, {"production.manage"}, RiskLevel.MEDIUM, False,
    ))

    async def incident_update(incident_id: str, status: str = "", severity: str = "", note: str = "") -> ToolResult:
        try:
            data = production.update_incident(incident_id, status=status, severity=severity, note=note)
            return ToolResult(True, json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")
    registry.register(ToolDefinition(
        "incident_update", "Update incident status/severity or append an operator note. Does not change code or production resources.",
        obj({"incident_id": s(), "status": {"type":"string","enum":["","open","investigating","mitigated","resolved"]}, "severity": {"type":"string","enum":["","P0","P1","P2","P3"]}, "note": s()}, ["incident_id"]),
        incident_update, {"production.manage"}, RiskLevel.MEDIUM, False, True,
    ))

    async def release_verify(deployment: str, samples: int = 3, interval_s: int = 0) -> ToolResult:
        try:
            data = production.verify_release(deployment, samples=samples, interval_s=interval_s)
            return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")
    registry.register(ToolDefinition(
        "release_verify", "Run bounded post-deployment verification samples and mark the release verified/degraded using app/public health and observed 5xx evidence.",
        obj({"deployment": s(), "samples": i(1,20), "interval_s": i(0,600)}, ["deployment"]), release_verify,
        {"production.manage"}, RiskLevel.MEDIUM, False,
    ))

    async def release_compare(source: str, target: str) -> ToolResult:
        try:
            data = production.release_compare(source=source, target=target)
            return ToolResult(True, json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")
    registry.register(ToolDefinition(
        "release_compare", "Compare source/target environment deployment commits and verification status before promotion.",
        obj({"source": s(), "target": s()}, ["source", "target"]), release_compare,
        {"production.read"}, RiskLevel.LOW, True,
    ))
