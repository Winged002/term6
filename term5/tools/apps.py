from __future__ import annotations

import json
from typing import Any

from ..apps import AppRuntime
from ..models import RiskLevel, ToolDefinition, ToolResult
from .registry import ToolRegistry


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def s() -> dict[str, Any]: return {"type": "string"}
def b() -> dict[str, Any]: return {"type": "boolean"}
def i(a: int = 0, z: int = 65535) -> dict[str, Any]: return {"type": "integer", "minimum": a, "maximum": z}
def arr() -> dict[str, Any]: return {"type": "array", "maxItems": 32, "items": {"type": "string"}}


def register_app_tools(registry: ToolRegistry, apps: AppRuntime, product_planner=None, projects=None) -> None:
    def ensure_project(app) -> None:
        if projects is None:
            return
        try:
            existing = projects.project_for_app(app.name) or projects.project_for_path(app.path)
            if existing is None:
                projects.import_existing(name=app.name, path=app.path, display_name=app.name, app_name=app.name, make_active=not bool(projects.registry.active_name))
            elif not existing.app_name:
                projects.update(existing.name, app_name=app.name)
        except Exception:
            # App creation/registration remains authoritative; project adoption is best-effort.
            pass

    async def docker_status() -> ToolResult:
        data = apps.docker_status()
        return ToolResult(bool(data.get("available")), json.dumps(data, indent=2), data=data)

    registry.register(ToolDefinition(
        "docker_status", "Inspect the local host Docker CLI, Docker Engine and Compose availability without modifying anything.",
        obj({}), docker_status, {"docker.inspect"}, RiskLevel.LOW, True,
    ))

    async def app_list(include_status: bool = False) -> ToolResult:
        data = apps.list(include_status=include_status)
        return ToolResult(True, json.dumps(data, indent=2), data=data)

    registry.register(ToolDefinition(
        "app_list", "List term_5 registered local Docker Compose applications.",
        obj({"include_status": b()}), app_list, {"docker.inspect"}, RiskLevel.LOW, True,
    ))

    async def app_register(name: str, path: str, compose_file: str = "compose.yaml", entry_service: str = "web",
                           url: str = "", health_url: str = "", framework: str = "custom") -> ToolResult:
        try:
            app = apps.register(name=name, path=path, compose_file=compose_file, entry_service=entry_service,
                                url=url, health_url=health_url, framework=framework)
            ensure_project(app)
            return ToolResult(True, f"Registered local app {app.name} at {app.path} and linked it to the project registry when available.", data={"name": app.name, "path": app.path})
        except Exception as exc:
            return ToolResult(False, f"App registration failed: {exc}")

    registry.register(ToolDefinition(
        "app_register", "Register an existing workspace Docker Compose project so term_5 can manage it later.",
        obj({"name": s(), "path": s(), "compose_file": s(), "entry_service": s(), "url": s(), "health_url": s(), "framework": s()}, ["name", "path"]),
        app_register, {"workspace.read", "app.manage"}, RiskLevel.MEDIUM, False, True,
    ))

    async def app_scaffold_flask(name: str, path: str, port: int, celery: bool = True, mongodb: bool = True) -> ToolResult:
        try:
            if not apps.port_available(port):
                return ToolResult(False, f"Port {port} is already in use on 127.0.0.1; no files were created.")
            app = apps.scaffold_flask(name=name, path=path, port=port, celery=celery, mongodb=mongodb)
            ensure_project(app)
            files = [f"{app.path}/{x}" for x in ["app.py", "requirements.txt", "Dockerfile", "compose.yaml", "term5.app.toml", "README.md"]]
            if celery: files.append(f"{app.path}/tasks.py")
            return ToolResult(True, f"Created Flask Compose application {name} at {app.path}. It is registered but not started yet.",
                              data={"name": app.name, "url": app.url, "services": app.services}, changed_files=files)
        except Exception as exc:
            return ToolResult(False, f"Flask scaffold failed: {exc}")

    registry.register(ToolDefinition(
        "app_scaffold_flask", "Create infrastructure starter files for a local Flask Docker Compose app with optional Celery+Redis and MongoDB. For product requests, continue implementing the active product blueprint before starting/opening the app.",
        obj({"name": s(), "path": s(), "port": i(1, 65535), "celery": b(), "mongodb": b()}, ["name", "path", "port"]),
        app_scaffold_flask, {"workspace.write", "app.manage"}, RiskLevel.HIGH, False, True,
    ))

    async def app_create_flask(name: str, path: str, port: int, celery: bool = True, mongodb: bool = True,
                               start: bool = True, open_browser: bool = True, wait_timeout_s: int = 120) -> ToolResult:
        try:
            if start and product_planner is not None and getattr(product_planner, "active", False):
                return ToolResult(False, "An active product blueprint exists. app_create_flask is intentionally blocked from immediately launching a generic starter because that would bypass product planning. Use app_scaffold_flask, implement/verify the blueprint, then app_start and app_open.")
            data = apps.create_flask(name=name, path=path, port=port, celery=celery, mongodb=mongodb,
                                     start=start, open_browser=open_browser, wait_timeout_s=wait_timeout_s)
            try:
                app = apps.registry.get(name)
                if app is not None:
                    ensure_project(app)
            except Exception:
                pass
            changed = []
            if data.get("path"):
                base = data["path"]
                changed = [f"{base}/app.py", f"{base}/requirements.txt", f"{base}/Dockerfile", f"{base}/compose.yaml", f"{base}/term5.app.toml", f"{base}/README.md"]
                if celery: changed.append(f"{base}/tasks.py")
            return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data, changed_files=changed)
        except Exception as exc:
            return ToolResult(False, f"Flask app creation failed: {exc}")

    registry.register(ToolDefinition(
        "app_create_flask", "Convenience end-to-end creation for generic/simple Flask apps. When a product blueprint is active, immediate generic launch is blocked so the product features/UX are implemented first.",
        obj({"name": s(), "path": s(), "port": i(1, 65535), "celery": b(), "mongodb": b(), "start": b(), "open_browser": b(), "wait_timeout_s": i(1, 1800)}, ["name", "path", "port"]),
        app_create_flask, {"workspace.write", "app.manage", "docker.build", "docker.lifecycle", "docker.inspect", "host.browser"}, RiskLevel.HIGH, False, True,
    ))

    async def app_validate(name: str) -> ToolResult:
        try:
            data = apps.validate(name)
            return ToolResult(bool(data["ok"]), data.get("output") or ("Compose configuration valid." if data["ok"] else "Compose validation failed."), data=data)
        except Exception as exc:
            return ToolResult(False, f"Compose validation failed: {exc}")

    registry.register(ToolDefinition(
        "app_validate", "Validate a registered app's compose.yaml with the local Docker Compose implementation.",
        obj({"name": s()}, ["name"]), app_validate, {"docker.inspect"}, RiskLevel.LOW, True,
    ))

    async def app_build(name: str, services: list[str] | None = None) -> ToolResult:
        try:
            data = apps.build(name, services or [])
            return ToolResult(bool(data["ok"]), data.get("output") or ("Build succeeded." if data["ok"] else "Build failed."), data=data)
        except Exception as exc:
            return ToolResult(False, f"App build failed: {exc}")

    registry.register(ToolDefinition(
        "app_build", "Build images for a registered local Compose app or selected services.",
        obj({"name": s(), "services": arr()}, ["name"]), app_build, {"docker.build"}, RiskLevel.HIGH, False, True,
    ))

    async def app_start(name: str, build: bool = False, wait_timeout_s: int = 120, services: list[str] | None = None) -> ToolResult:
        try:
            data = apps.start(name, build=build, wait_timeout_s=wait_timeout_s, services=services or [])
            body = json.dumps(data, indent=2)
            return ToolResult(bool(data.get("ok")), body, data=data)
        except Exception as exc:
            return ToolResult(False, f"App start failed: {exc}")

    registry.register(ToolDefinition(
        "app_start", "Start a registered Compose application on the host Docker Engine, wait for service health, then perform the configured HTTP health probe.",
        obj({"name": s(), "build": b(), "wait_timeout_s": i(1, 1800), "services": arr()}, ["name"]),
        app_start, {"docker.lifecycle"}, RiskLevel.HIGH, False, True,
    ))

    async def app_stop(name: str, services: list[str] | None = None) -> ToolResult:
        try:
            data = apps.stop(name, services or [])
            return ToolResult(bool(data["ok"]), data.get("output") or "Stopped.", data=data)
        except Exception as exc:
            return ToolResult(False, f"App stop failed: {exc}")

    registry.register(ToolDefinition(
        "app_stop", "Stop a registered Compose app or selected services without deleting persistent volumes.",
        obj({"name": s(), "services": arr()}, ["name"]), app_stop, {"docker.lifecycle"}, RiskLevel.MEDIUM, False, True,
    ))

    async def app_restart(name: str, services: list[str] | None = None) -> ToolResult:
        try:
            data = apps.restart(name, services or [])
            return ToolResult(bool(data["ok"]), json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"App restart failed: {exc}")

    registry.register(ToolDefinition(
        "app_restart", "Restart a registered Compose app or selected services and return refreshed health state.",
        obj({"name": s(), "services": arr()}, ["name"]), app_restart, {"docker.lifecycle"}, RiskLevel.MEDIUM, False, True,
    ))

    async def app_down(name: str) -> ToolResult:
        try:
            data = apps.down(name)
            return ToolResult(bool(data["ok"]), (data.get("output") or "Stack removed.") + "\nPersistent volumes were preserved.", data=data)
        except Exception as exc:
            return ToolResult(False, f"App down failed: {exc}")

    registry.register(ToolDefinition(
        "app_down", "Run compose down for a registered app while deliberately preserving persistent volumes.",
        obj({"name": s()}, ["name"]), app_down, {"docker.lifecycle"}, RiskLevel.HIGH, False, True,
    ))

    async def app_status(name: str) -> ToolResult:
        try:
            data = apps.status(name)
            return ToolResult(bool(data.get("compose_ok")), json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"App status failed: {exc}")

    registry.register(ToolDefinition(
        "app_status", "Inspect service state/health and configured HTTP health endpoint for a registered app.",
        obj({"name": s()}, ["name"]), app_status, {"docker.inspect"}, RiskLevel.LOW, True,
    ))

    async def app_logs(name: str, service: str = "", tail: int = 200) -> ToolResult:
        try:
            data = apps.logs(name, service=service, tail=tail)
            return ToolResult(bool(data["ok"]), data.get("output") or "(no logs)", data=data)
        except Exception as exc:
            return ToolResult(False, f"App logs failed: {exc}")

    registry.register(ToolDefinition(
        "app_logs", "Read bounded recent logs from a registered Compose app or service.",
        obj({"name": s(), "service": s(), "tail": i(1, 5000)}, ["name"]), app_logs, {"docker.inspect"}, RiskLevel.LOW, True,
    ))

    async def app_open(name: str) -> ToolResult:
        try:
            data = apps.open(name)
            return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
        except Exception as exc:
            return ToolResult(False, f"Open app failed: {exc}")

    registry.register(ToolDefinition(
        "app_open", "Open a registered application's URL in the host browser only after term_5 confirms the app is healthy.",
        obj({"name": s()}, ["name"]), app_open, {"host.browser", "docker.inspect"}, RiskLevel.MEDIUM, False, True,
    ))
