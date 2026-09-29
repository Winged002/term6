from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..apps import AppRuntime
from .git import GitManager
from .nginx import NginxManager
from .registry import DeploymentManifest, DeploymentRegistry, ReleaseRecord
from .tls import TLSManager
from .server import ServerManager


class OperationsError(RuntimeError):
    pass


class OperationsRuntime:
    def __init__(self, root: Path, state_dir: Path, apps: AppRuntime, *, config, security_config, projects=None, configuration=None, runner=None) -> None:
        self.root = root
        self.state_dir = state_dir
        self.apps = apps
        self.config = config
        self.security = security_config
        self.projects = projects
        self.configuration = configuration
        self.runner = runner
        self.git = GitManager(root, timeout_s=config.command_timeout_s, runner=runner)
        self.nginx = NginxManager(sites_available=config.nginx_sites_available, sites_enabled=config.nginx_sites_enabled,
                                  timeout_s=config.command_timeout_s, runner=runner)
        self.tls = TLSManager(timeout_s=max(config.command_timeout_s, 300), runner=runner)
        self.server = ServerManager(root=root, timeout_s=config.command_timeout_s, runner=runner, allowed_services=config.managed_services, disk_warn_percent=config.server_disk_warn_percent)
        self.registry = DeploymentRegistry(state_dir / "deployments.json")
        self.production = None
        self._status_cache: tuple[float, dict[str, Any]] | None = None

    def attach_production(self, production) -> None:
        self.production = production

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not refresh and self._status_cache and now - self._status_cache[0] < 30:
            return dict(self._status_cache[1])
        value = {"enabled": bool(self.config.enabled), "git": self.git.available(), "nginx": self.nginx.status(),
                 "tls": self.tls.status(), "server": self.server.status(), "deployments": len(self.registry.list()),
                 "projects": len(self.projects.registry.list()) if self.projects is not None else 0,
                 "production_intelligence": bool(self.production is not None),
                 "write_gates": {"git": self.security.allow_git_write, "git_remote": self.security.allow_git_remote,
                                 "git_provider": self.security.allow_git_provider_write,
                                 "nginx": self.security.allow_nginx_write, "tls": self.security.allow_tls_issue,
                                 "server": self.security.allow_server_write, "auto_rollback": self.config.auto_rollback}}
        self._status_cache = (now, dict(value))
        return value

    def git_manager(self, project: str = "") -> GitManager:
        """Return the selected/active project's Git manager, with workspace fallback."""
        if self.projects is not None:
            target = project or self.projects.registry.active_name
            if target:
                return self.projects.git(target)
        return self.git

    def production_readiness(self) -> dict[str, Any]:
        """Aggregate read-only host, app, Git, Nginx, TLS and deployment readiness."""
        warnings: list[str] = []
        server = self.server.audit()
        if not server.get("ok"):
            warnings.extend(server.get("warnings") or [])
        docker = self.apps.docker_status(refresh=True)
        if not docker.get("ok"):
            warnings.append("Docker engine is unavailable or unhealthy")
        apps = self.apps.list(include_status=True)
        for app in apps:
            st = app.get("status") or {}
            if not st.get("ok"):
                warnings.append(f"application unhealthy: {app.get('name')}")
        project_git: list[dict[str, Any]] = []
        if self.projects is not None and self.projects.registry.list():
            for project in self.projects.registry.list():
                try:
                    gs = self.projects.git_status(project.name)
                    project_git.append({"project": project.name, **gs})
                    repo = bool((gs.get("available") or {}).get("repository"))
                    clean = bool((gs.get("status") or {}).get("clean")) if repo else True
                    if self.config.require_git and not repo:
                        warnings.append(f"project has no Git repository: {project.name}")
                    elif self.config.require_clean_git and not clean:
                        warnings.append(f"project Git working tree is dirty: {project.name}")
                except Exception as exc:
                    warnings.append(f"project Git status failed: {project.name}: {exc}")
            git = {"mode": "projects", "projects": project_git}
            git_status = {"ok": not any("Git" in w or "repository" in w for w in warnings)}
        else:
            git = self.git.available()
            git_status = self.git.status() if git.get("repository") else {"ok": True, "clean": True, "repository": False}
            if self.config.require_git and not git.get("repository"):
                warnings.append("workspace is not a Git repository and no projects are registered")
            elif self.config.require_clean_git and not git_status.get("clean"):
                warnings.append("Git working tree is dirty")
        nginx = self.nginx.status()
        if not nginx.get("available"):
            warnings.append("Nginx is unavailable")
        elif not nginx.get("config_ok"):
            warnings.append("Nginx configuration validation failed")
        tls = self.tls.status()
        if not tls.get("available"):
            warnings.append("Certbot is unavailable")
        configuration = {"enabled": bool(self.configuration is not None), "projects": []}
        if self.configuration is not None and self.projects is not None:
            for project in self.projects.registry.list():
                try:
                    cs = self.configuration.status(project.name, "production", discover=True)
                    configuration["projects"].append(cs)
                    if not cs.get("ready"):
                        warnings.append(f"project production configuration missing required variables: {project.name}: {', '.join(cs.get('missing') or [])}")
                except Exception as exc:
                    warnings.append(f"project configuration status failed: {project.name}: {exc}")
        deployments = []
        for dep in self.registry.list():
            try:
                deployments.append(self.deployment_status(dep.name))
            except Exception as exc:
                deployments.append({"name": dep.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                warnings.append(f"deployment status failed: {dep.name}")
        production = None
        if self.production is not None:
            try:
                production = self.production.overview()
                for incident in production.get("incidents") or []:
                    if incident.get("severity") in {"P0", "P1"} and incident.get("status") != "resolved":
                        warnings.append(f"open {incident.get('severity')} production incident: {incident.get('title')}")
            except Exception as exc:
                warnings.append(f"production intelligence status failed: {exc}")
        return {
            "ok": not warnings,
            "warnings": list(dict.fromkeys(warnings)),
            "server": server,
            "docker": docker,
            "apps": apps,
            "git": {"availability": git, "status": git_status},
            "nginx": nginx,
            "tls": tls,
            "configuration": configuration,
            "production": production,
            "deployments": deployments,
        }

    @staticmethod
    def _port_from_url(url: str) -> int:
        parsed = urlparse(url)
        if parsed.port:
            return int(parsed.port)
        if parsed.scheme == "https":
            return 443
        if parsed.scheme == "http":
            return 80
        return 0

    def register(self, *, name: str, app_name: str, domain: str = "", upstream_port: int = 0,
                 tls: bool = False, migration_kind: str = "none", backup_kind: str = "none", backup_service: str = "",
                 environment: str = "production") -> DeploymentManifest:
        environment = str(environment or "production").strip().lower()
        if environment not in {"development", "staging", "production"}:
            raise OperationsError("environment must be development, staging, or production")
        app = self.apps.registry.get(app_name)
        if app is None:
            raise OperationsError(f"unknown app: {app_name}")
        domain = self.nginx.validate_domain(domain) if domain else ""
        port = int(upstream_port or self._port_from_url(app.url))
        if domain and not 1 <= port <= 65535:
            raise OperationsError("a valid loopback upstream port is required for public deployment")
        if migration_kind not in {"none", "flask-db", "django"}:
            raise OperationsError("migration_kind must be none, flask-db, or django")
        if backup_kind not in {"none", "mongodb", "postgres"}:
            raise OperationsError("backup_kind must be none, mongodb, or postgres")
        project_name = ""
        if self.projects is not None:
            project = self.projects.project_for_app(app_name) or self.projects.project_for_path(app.path)
            if project is not None:
                project_name = project.name
        item = DeploymentManifest(name=name, app_name=app_name, project_name=project_name, domain=domain, upstream_port=port,
                                  nginx_site=name, tls=bool(tls), migration_kind=migration_kind,
                                  backup_kind=backup_kind, backup_service=backup_service, environment=environment)
        old = self.registry.get(name)
        if old:
            item.current_commit, item.previous_commit, item.history = old.current_commit, old.previous_commit, list(old.history)
            item.release_status, item.last_deployed_at, item.last_verified_at = old.release_status, old.last_deployed_at, old.last_verified_at
            if not item.project_name:
                item.project_name = old.project_name
        self.registry.upsert(item)
        if self.projects is not None and item.project_name:
            try:
                self.projects.update(item.project_name, app_name=app_name, deployment_name=name, domain=domain)
            except Exception:
                pass
        if self.production is not None:
            try:
                self.production.sync_deployments()
            except Exception:
                pass
        return item

    def list(self) -> list[dict[str, Any]]:
        return [json.loads(json.dumps(x, default=lambda o: o.__dict__)) if hasattr(x, "__dict__") else {
            "name": x.name, "app_name": x.app_name, "project_name": x.project_name, "domain": x.domain, "upstream_port": x.upstream_port,
            "tls": x.tls, "current_commit": x.current_commit, "previous_commit": x.previous_commit,
            "migration_kind": x.migration_kind, "backup_kind": x.backup_kind, "environment": x.environment,
            "release_status": x.release_status, "last_deployed_at": x.last_deployed_at, "last_verified_at": x.last_verified_at
        } for x in self.registry.list()]

    def deployment_status(self, name: str) -> dict[str, Any]:
        dep = self.registry.get(name)
        if dep is None:
            raise OperationsError(f"unknown deployment: {name}")
        out: dict[str, Any] = {
            "name": dep.name, "app_name": dep.app_name, "project_name": dep.project_name, "domain": dep.domain,
            "current_commit": dep.current_commit, "previous_commit": dep.previous_commit,
            "tls": dep.tls, "environment": dep.environment, "release_status": dep.release_status,
            "last_deployed_at": dep.last_deployed_at, "last_verified_at": dep.last_verified_at, "history": dep.history[-10:],
        }
        try:
            out["app"] = self.apps.status(dep.app_name)
        except Exception as exc:
            out["app"] = {"ok": False, "error": str(exc)}
        if dep.domain:
            out["dns"] = self.dns_status(dep.domain)
            out["tls_status"] = self.tls.status(dep.domain)
        if self.production is not None:
            latest = self.production.registry.snapshots(deployment=dep.name, limit=1)
            out["production"] = latest[-1] if latest else None
        return out

    def backup(self, name: str) -> dict[str, Any]:
        dep = self.registry.get(name)
        if dep is None:
            raise OperationsError(f"unknown deployment: {name}")
        return self._backup_database(dep)

    def dns_status(self, domain: str) -> dict[str, Any]:
        d = self.nginx.validate_domain(domain)
        try:
            rows = socket.getaddrinfo(d, None)
            addrs = sorted({r[4][0] for r in rows})
            return {"ok": bool(addrs), "domain": d, "addresses": addrs}
        except Exception as exc:
            return {"ok": False, "domain": d, "addresses": [], "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def external_probe(url: str, timeout_s: int = 10) -> dict[str, Any]:
        started = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "term5-ops/5.9"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return {"ok": 200 <= int(resp.status) < 400, "status": int(resp.status), "url": url, "elapsed_ms": round((time.monotonic()-started)*1000, 2)}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": int(exc.code), "url": url, "error": str(exc), "elapsed_ms": round((time.monotonic()-started)*1000, 2)}
        except Exception as exc:
            return {"ok": False, "status": None, "url": url, "error": f"{type(exc).__name__}: {exc}", "elapsed_ms": round((time.monotonic()-started)*1000, 2)}

    def _backup_database(self, dep: DeploymentManifest) -> dict[str, Any]:
        if dep.backup_kind == "none":
            return {"ok": True, "skipped": True}
        app = self.apps.registry.get(dep.app_name)
        if app is None:
            raise OperationsError("deployment app missing")
        service = dep.backup_service or ("mongo" if dep.backup_kind == "mongodb" else "db")
        p = self.apps._project_dir(app)
        if dep.backup_kind == "mongodb":
            args = ["exec", "-T", service, "mongodump", "--archive", "--gzip"]
            ext = "archive.gz"
        else:
            args = ["exec", "-T", service, "pg_dumpall", "-U", "postgres"]
            ext = "sql"
        ok, code, payload, argv = self.apps.docker.compose_capture_bytes(p, app.compose_file, app.compose_project, args, timeout_s=900)
        if not ok:
            return {"ok": False, "output": payload.decode("utf-8", errors="replace")[-12000:], "argv": argv}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = self.state_dir / "backups" / dep.name
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{stamp}-{dep.backup_kind}.{ext}"
        target.write_bytes(payload)
        return {"ok": True, "path": str(target), "bytes": len(payload), "kind": dep.backup_kind, "service": service}

    def _run_migration(self, dep: DeploymentManifest) -> dict[str, Any]:
        if dep.migration_kind == "none":
            return {"ok": True, "skipped": True}
        app = self.apps.registry.get(dep.app_name)
        if app is None:
            raise OperationsError("deployment app missing")
        p = self.apps._project_dir(app)
        if dep.migration_kind == "flask-db":
            argv = ["run", "--rm", app.entry_service, "flask", "db", "upgrade"]
        else:
            argv = ["run", "--rm", app.entry_service, "python", "manage.py", "migrate", "--noinput"]
        r = self.apps.docker.compose(p, app.compose_file, app.compose_project, argv, timeout_s=600)
        return {"ok": r.ok, "output": r.stdout, "argv": r.argv}

    def _git_for_deployment(self, dep: DeploymentManifest) -> GitManager:
        if self.projects is not None:
            project = self.projects.registry.get(dep.project_name) if dep.project_name else self.projects.project_for_app(dep.app_name)
            if project is not None:
                return self.projects.git(project.name)
            app = self.apps.registry.get(dep.app_name)
            if app is not None:
                by_path = self.projects.project_for_path(app.path)
                if by_path is not None:
                    dep.project_name = by_path.name
                    self.registry.upsert(dep)
                    return self.projects.git(by_path.name)
                try:
                    app_root = self.apps._project_dir(app)
                    candidate = GitManager(app_root, timeout_s=self.config.command_timeout_s, runner=self.runner)
                    if candidate.available().get("repository"):
                        return candidate
                except Exception:
                    pass
        return self.git

    def deploy(self, name: str, *, build: bool = True, configure_nginx: bool = True, issue_tls: bool = False,
               email: str = "", staging_tls: bool = False, auto_rollback: bool | None = None) -> dict[str, Any]:
        dep = self.registry.get(name)
        if dep is None:
            raise OperationsError(f"unknown deployment: {name}")
        git_manager = self._git_for_deployment(dep)
        git_info = git_manager.available()
        if self.config.require_git and not git_info.get("repository"):
            raise OperationsError("deployment requires a Git repository for this project; initialize/import the project and commit it first")
        gs = git_manager.status() if git_info.get("repository") else {"ok": True, "clean": True}
        if self.config.require_clean_git and git_info.get("repository") and not gs.get("clean"):
            raise OperationsError("deployment requires a clean Git working tree; commit or stash changes first")
        commit = git_manager.head() or ""
        previous = dep.current_commit
        result: dict[str, Any] = {"ok": False, "deployment": name, "commit": commit, "previous_commit": previous, "environment": dep.environment, "steps": []}
        dep.release_status = "deploying"
        self.registry.upsert(dep)
        if self.production is not None:
            self.production.release_started(dep, commit=commit, previous_commit=previous)
        rollback_enabled = self.config.auto_rollback if auto_rollback is None else bool(auto_rollback)
        try:
            if dep.domain and configure_nginx and self.config.require_dns:
                dns = self.dns_status(dep.domain)
                result["steps"].append({"dns": dns})
                if not dns.get("ok"):
                    raise OperationsError("deployment hostname does not resolve; refusing Nginx/TLS changes")
            started = self.apps.start(dep.app_name, build=build, wait_timeout_s=self.config.health_timeout_s)
            result["steps"].append({"app_start": started})
            if not started.get("ok"):
                raise OperationsError("application failed health-gated startup")
            backup = self._backup_database(dep)
            result["steps"].append({"backup": backup})
            if not backup.get("ok"):
                raise OperationsError("pre-migration database backup failed")
            mig = self._run_migration(dep)
            result["steps"].append({"migration": mig})
            if not mig.get("ok"):
                raise OperationsError("database migration failed")
            if configure_nginx and dep.domain:
                if not self.security.allow_nginx_write:
                    raise OperationsError("Nginx writes are disabled; enable security.allow_nginx_write")
                ng = self.nginx.install(name=dep.nginx_site or dep.name, domain=dep.domain, upstream_port=dep.upstream_port,
                                        max_body_mb=self.config.nginx_max_body_mb, websocket=True, reload=True)
                result["steps"].append({"nginx": {"ok": ng.ok, "output": ng.output, "rolled_back": ng.rolled_back}})
                if not ng.ok:
                    raise OperationsError("Nginx configuration failed validation/reload")
                http = self.external_probe(f"http://{dep.domain}/", timeout_s=10)
                result["steps"].append({"http_probe": http})
                if not http.get("ok") and not issue_tls:
                    raise OperationsError("public HTTP probe failed")
            if (issue_tls or dep.tls) and dep.domain:
                if not self.security.allow_tls_issue:
                    raise OperationsError("TLS issuance is disabled; enable security.allow_tls_issue")
                tls = self.tls.issue(domain=dep.domain, email=email, redirect=True, staging=staging_tls)
                result["steps"].append({"tls": tls})
                if not tls.get("ok"):
                    raise OperationsError("certificate issuance failed")
                https = self.external_probe(f"https://{dep.domain}/", timeout_s=15)
                result["steps"].append({"https_probe": https})
                if not staging_tls and not https.get("ok"):
                    raise OperationsError("public HTTPS probe failed")
            dep.previous_commit = previous if previous and previous != commit else dep.previous_commit
            dep.current_commit = commit or dep.current_commit
            dep.last_deployed_at = datetime.now(timezone.utc).isoformat()
            dep.release_status = "observing" if self.production is not None else "healthy"
            dep.history.append({"commit": commit, "status": "healthy", "environment": dep.environment, "deployed_at": ReleaseRecord(commit, "healthy").deployed_at})
            dep.history = dep.history[-50:]
            self.registry.upsert(dep)
            result["ok"] = True
            result["status"] = self.apps.status(dep.app_name)
            if self.production is not None:
                result["production_snapshot"] = self.production.release_finished(dep, ok=True, commit=commit)
            return result
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            dep.release_status = "failed"
            dep.history.append({"commit": commit, "status": "failed", "environment": dep.environment, "deployed_at": ReleaseRecord(commit, "failed").deployed_at, "note": str(exc)})
            dep.history = dep.history[-50:]
            self.registry.upsert(dep)
            if self.production is not None:
                self.production.release_finished(dep, ok=False, commit=commit, error=str(exc))
            if rollback_enabled and previous and previous != commit:
                try:
                    result["rollback"] = self.rollback(name, target=previous, automatic=True)
                except Exception as rb_exc:
                    result["rollback"] = {"ok": False, "error": f"{type(rb_exc).__name__}: {rb_exc}"}
            return result

    def rollback(self, name: str, *, target: str = "", automatic: bool = False) -> dict[str, Any]:
        dep = self.registry.get(name)
        if dep is None:
            raise OperationsError(f"unknown deployment: {name}")
        if not self.security.allow_git_write:
            raise OperationsError("Git writes are disabled; enable security.allow_git_write")
        target = target or dep.previous_commit
        if not target:
            raise OperationsError("no previous release is recorded")
        git_manager = self._git_for_deployment(dep)
        status = git_manager.status()
        if not status.get("clean"):
            raise OperationsError("rollback refused because the project Git working tree is not clean")
        before = git_manager.head() or ""
        reset = git_manager.reset_hard(target)
        if not reset.ok:
            return {"ok": False, "phase": "git-reset", "output": reset.stdout}
        started = self.apps.start(dep.app_name, build=True, wait_timeout_s=self.config.health_timeout_s)
        if not started.get("ok"):
            # Best effort return to the release we started from.
            if before:
                git_manager.reset_hard(before)
                self.apps.start(dep.app_name, build=True, wait_timeout_s=self.config.health_timeout_s)
            return {"ok": False, "phase": "rollback-start", "status": started, "restored_original": bool(before)}
        old_current = dep.current_commit
        dep.current_commit = target
        dep.previous_commit = before or old_current
        dep.last_deployed_at = datetime.now(timezone.utc).isoformat()
        dep.release_status = "observing" if self.production is not None else "healthy"
        dep.history.append({"commit": target, "status": "rollback", "environment": dep.environment, "deployed_at": ReleaseRecord(target, "rollback").deployed_at,
                            "note": "automatic" if automatic else "manual"})
        dep.history = dep.history[-50:]
        self.registry.upsert(dep)
        out = {"ok": True, "from": before, "to": target, "status": self.apps.status(dep.app_name)}
        if self.production is not None:
            out["production_snapshot"] = self.production.release_finished(dep, ok=True, commit=target)
        return out
