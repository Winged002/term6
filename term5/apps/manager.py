from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from ..security.paths import PathGuard
from ..state.transactions import TransactionManager
from .docker import DockerManager, DockerUnavailable
from .registry import AppManifest, AppRegistry
from .templates import flask_celery_mongo


class AppRuntimeError(RuntimeError):
    pass


class AppRuntime:
    def __init__(self, root: Path, state_dir: Path, guard: PathGuard, tx: TransactionManager, *, docker_timeout_s: int = 300) -> None:
        self.root = root
        self.state_dir = state_dir
        self.guard = guard
        self.tx = tx
        self.registry = AppRegistry(state_dir / "apps.json")
        self.docker = DockerManager(timeout_s=docker_timeout_s)
        self._docker_status_cache: tuple[float, dict[str, Any]] | None = None

    def docker_status(self, *, refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not refresh and self._docker_status_cache and now - self._docker_status_cache[0] < 10:
            return dict(self._docker_status_cache[1])
        value = self.docker.engine_status()
        self._docker_status_cache = (now, dict(value))
        return value

    def _app(self, name: str) -> AppManifest:
        app = self.registry.get(name)
        if app is None:
            raise AppRuntimeError(f"unknown app: {name}")
        return app

    def _project_dir(self, app: AppManifest) -> Path:
        p = self.guard.resolve(app.path)
        if not p.is_dir():
            raise AppRuntimeError(f"registered app directory does not exist: {app.path}")
        compose = p / app.compose_file
        if not compose.is_file():
            raise AppRuntimeError(f"compose file does not exist: {compose.relative_to(self.root)}")
        return p

    @staticmethod
    def port_available(port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, int(port)))
            except OSError:
                return False
            return True

    @staticmethod
    def http_probe(url: str, timeout_s: float = 5.0) -> dict[str, Any]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "term5-local-runtime/5.5"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return {"ok": 200 <= int(resp.status) < 400, "status": int(resp.status), "url": url}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": int(exc.code), "url": url, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": None, "url": url, "error": f"{type(exc).__name__}: {exc}"}

    def list(self, *, include_status: bool = False) -> list[dict[str, Any]]:
        rows = []
        for app in self.registry.list():
            item = {
                "name": app.name, "path": app.path, "runtime": app.runtime,
                "url": app.url, "framework": app.framework, "services": app.services,
            }
            if include_status:
                try:
                    item["status"] = self.status(app.name)
                except Exception as exc:
                    item["status"] = {"ok": False, "error": str(exc)}
            rows.append(item)
        return rows

    def register(self, *, name: str, path: str, compose_file: str = "compose.yaml", entry_service: str = "web",
                 url: str = "", health_url: str = "", framework: str = "custom", replace: bool = False) -> AppManifest:
        p = self.guard.resolve(path)
        if not p.is_dir():
            raise AppRuntimeError(f"app path must be an existing workspace directory: {path}")
        compose = p / compose_file
        if not compose.is_file():
            raise AppRuntimeError(f"compose file not found: {(p / compose_file).relative_to(self.root)}")
        rel = p.relative_to(self.root).as_posix() or "."
        app = AppManifest(name=name, path=rel, compose_file=compose_file, entry_service=entry_service,
                          url=url, health_url=health_url or (url.rstrip("/") + "/health" if url else ""), framework=framework)
        self.registry.add(app, replace=replace)
        return app

    def scaffold_flask(self, *, name: str, path: str, port: int, celery: bool = True, mongodb: bool = True,
                       replace_registry: bool = False) -> AppManifest:
        port = int(port)
        if not 1 <= port <= 65535:
            raise AppRuntimeError("port must be between 1 and 65535")
        target = self.guard.resolve(path)
        target.mkdir(parents=True, exist_ok=True)
        rel_target = target.relative_to(self.root).as_posix()
        files = flask_celery_mongo(name, port, with_celery=celery, with_mongo=mongodb)
        for rel, content in files.items():
            if rel.endswith(".py"):
                compile(content, rel, "exec")
        changes = []
        for rel, content in files.items():
            full_rel = (Path(rel_target) / rel).as_posix()
            existing = self.tx.fingerprint(full_rel) if (self.root / full_rel).exists() else None
            if existing is not None:
                raise AppRuntimeError(f"refusing to overwrite existing scaffold file: {full_rel}")
            changes.append((full_rel, content))
        self.tx.write_group(changes)
        services = ["web"] + (["worker", "redis"] if celery else []) + (["mongo"] if mongodb else [])
        app = AppManifest(
            name=name, path=rel_target, compose_file="compose.yaml", entry_service="web",
            url=f"http://127.0.0.1:{port}", health_url=f"http://127.0.0.1:{port}/health",
            services=services, framework="flask",
        )
        self.registry.add(app, replace=replace_registry)
        return app

    def create_flask(self, *, name: str, path: str, port: int, celery: bool = True, mongodb: bool = True,
                     start: bool = True, open_browser: bool = True, wait_timeout_s: int = 120) -> dict[str, Any]:
        if not self.port_available(port):
            raise AppRuntimeError(f"port {port} is already in use on 127.0.0.1")
        if start:
            docker = self.docker_status(refresh=True)
            if not docker.get("available"):
                raise AppRuntimeError(docker.get("reason") or "Docker Engine is unavailable")
            if not docker.get("compose_available"):
                raise AppRuntimeError("Docker Compose is unavailable")
        app = self.scaffold_flask(name=name, path=path, port=port, celery=celery, mongodb=mongodb)
        result: dict[str, Any] = {
            "ok": True, "phase": "scaffold", "name": app.name, "path": app.path,
            "url": app.url, "services": app.services, "started": False, "opened": False,
        }
        if not start:
            return result
        started = self.start(name, build=True, wait_timeout_s=wait_timeout_s)
        result["start"] = started
        result["started"] = bool(started.get("ok"))
        result["phase"] = "healthy" if result["started"] else "start_failed"
        result["ok"] = result["started"]
        if not result["started"]:
            try:
                result["logs"] = self.logs(name, tail=160).get("output", "")[-20000:]
            except Exception:
                pass
            return result
        if open_browser:
            opened = self.open(name)
            result["open"] = opened
            result["opened"] = bool(opened.get("ok"))
        return result

    def validate(self, name: str) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        result = self.docker.validate(p, app.compose_file, app.compose_project)
        return {"ok": result.ok, "output": result.stdout, "argv": result.argv}

    def build(self, name: str, services: list[str] | None = None) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        result = self.docker.build(p, app.compose_file, app.compose_project, services)
        return {"ok": result.ok, "output": result.stdout, "argv": result.argv}

    def start(self, name: str, *, build: bool = False, wait_timeout_s: int = 120, services: list[str] | None = None) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        valid = self.docker.validate(p, app.compose_file, app.compose_project)
        if not valid.ok:
            return {"ok": False, "phase": "validate", "output": valid.stdout}
        result = self.docker.up(p, app.compose_file, app.compose_project, build=build, wait=True,
                                wait_timeout_s=wait_timeout_s, services=services)
        status = self.status(name)
        if result.ok and not status.get("ok", False):
            deadline = time.monotonic() + max(1, int(wait_timeout_s))
            while time.monotonic() < deadline and not status.get("ok", False):
                time.sleep(1.0)
                status = self.status(name)
        ok = result.ok and status.get("ok", False)
        return {"ok": ok, "phase": "start", "output": result.stdout, "status": status}

    def stop(self, name: str, services: list[str] | None = None) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        result = self.docker.stop(p, app.compose_file, app.compose_project, services)
        return {"ok": result.ok, "output": result.stdout}

    def restart(self, name: str, services: list[str] | None = None) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        result = self.docker.restart(p, app.compose_file, app.compose_project, services)
        return {"ok": result.ok, "output": result.stdout, "status": self.status(name)}

    def down(self, name: str) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        result = self.docker.down(p, app.compose_file, app.compose_project)
        return {"ok": result.ok, "output": result.stdout, "volumes_removed": False}

    def logs(self, name: str, *, service: str = "", tail: int = 200) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        result = self.docker.logs(p, app.compose_file, app.compose_project, service=service, tail=tail)
        return {"ok": result.ok, "output": result.stdout}

    def status(self, name: str) -> dict[str, Any]:
        app = self._app(name); p = self._project_dir(app)
        result, rows = self.docker.ps(p, app.compose_file, app.compose_project)
        services = []
        all_running = bool(rows)
        for row in rows:
            state = str(row.get("State") or row.get("state") or "").lower()
            health = str(row.get("Health") or row.get("health") or "").lower()
            running = state in {"running", "up"} or state.startswith("running")
            healthy = health in {"", "healthy"}
            all_running = all_running and running and healthy
            services.append({
                "service": row.get("Service") or row.get("service") or row.get("Name") or row.get("name"),
                "state": state, "health": health, "running": running,
                "publishers": row.get("Publishers") or row.get("publishers") or [],
            })
        probe = self.http_probe(app.health_url) if app.health_url and all_running else {"ok": False, "skipped": True, "url": app.health_url}
        return {
            "ok": bool(result.ok and all_running and (probe.get("ok") if app.health_url else True)),
            "compose_ok": result.ok, "services": services, "http": probe,
            "url": app.url, "health_url": app.health_url,
        }

    def open(self, name: str) -> dict[str, Any]:
        app = self._app(name)
        if not app.url:
            raise AppRuntimeError("app has no URL")
        status = self.status(name)
        if not status.get("ok"):
            return {"ok": False, "opened": False, "reason": "app is not healthy", "status": status}
        opened = bool(webbrowser.open(app.url))
        return {"ok": opened, "opened": opened, "url": app.url, "status": status}
