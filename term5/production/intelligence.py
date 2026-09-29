from __future__ import annotations

import asyncio
import json
import math
import re
import statistics
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry import EnvironmentRecord, ProductionRegistry, clean_environment, now_iso

_ACCESS_RX = re.compile(r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/[^\"]+"\s+(?P<status>\d{3})\s+(?P<bytes>\d+|-)')
_RT_RX = re.compile(r'(?:request_time[=:]\s*|rt=)(?P<value>\d+(?:\.\d+)?)')
_LEVEL_RX = re.compile(r'\[(?P<level>emerg|alert|crit|error|warn|notice|info|debug)\]', re.I)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    idx = (len(vals) - 1) * percentile
    lo = math.floor(idx); hi = math.ceil(idx)
    if lo == hi:
        return round(vals[lo], 3)
    return round(vals[lo] * (hi - idx) + vals[hi] * (idx - lo), 3)


def _parse_percent(value: str) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except Exception:
        return None


class ProductionIntelligence:
    """Production evidence layer built from existing typed host/app/deployment adapters."""

    def __init__(self, state_dir: Path, ops, *, config, events=None) -> None:
        self.state_dir = Path(state_dir)
        self.ops = ops
        self.config = config
        self.events = events
        self.registry = ProductionRegistry(self.state_dir, max_snapshots=int(config.max_snapshots))
        self._monitor_thread = None
        self.sync_deployments()
        if bool(config.background_monitor):
            self._start_background_monitor()

    def _start_background_monitor(self) -> None:
        if self._monitor_thread is not None:
            return
        def worker() -> None:
            interval = max(10, int(self.config.monitor_interval_s))
            while True:
                time.sleep(interval)
                try:
                    deployments = list(self.ops.registry.list())
                except Exception:
                    continue
                for dep in deployments:
                    try:
                        self.snapshot(deployment=dep.name, persist=True, detect=True)
                    except Exception as exc:
                        self._emit("production.snapshot_failed", deployment=getattr(dep, "name", ""), error=f"{type(exc).__name__}: {exc}")
        self._monitor_thread = threading.Thread(target=worker, name="term5-production-monitor", daemon=True)
        self._monitor_thread.start()

    def _emit(self, typ: str, **data: Any) -> None:
        if self.events is None:
            return
        try:
            coro = self.events.emit(typ, **data)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                asyncio.run(coro)
        except Exception:
            pass

    def sync_deployments(self) -> None:
        for dep in self.ops.registry.list():
            env = clean_environment(getattr(dep, "environment", "production"))
            app = self.ops.apps.registry.get(dep.app_name)
            url = (f"https://{dep.domain}" if dep.domain and dep.tls else f"http://{dep.domain}" if dep.domain else (app.url if app else ""))
            old = self.registry.get_environment(dep.project_name, env) if dep.project_name else None
            rec = EnvironmentRecord(
                project=dep.project_name or (old.project if old else ""), environment=env,
                deployment=dep.name, app_name=dep.app_name, url=url,
                current_commit=dep.current_commit, status=(old.status if old else "unknown"),
                last_deployed_at=(getattr(dep, "last_deployed_at", "") or (old.last_deployed_at if old else "")),
                last_observed_at=(old.last_observed_at if old else ""),
                last_verified_at=(getattr(dep, "last_verified_at", "") or (old.last_verified_at if old else "")),
            )
            if rec.project:
                self.registry.upsert_environment(rec)

    def register_environment(self, *, project: str, environment: str, deployment: str = "", app_name: str = "", url: str = "") -> dict[str, Any]:
        env = clean_environment(environment)
        if self.ops.projects is not None:
            self.ops.projects._item(project)
        dep = self.ops.registry.get(deployment) if deployment else None
        if dep:
            app_name = app_name or dep.app_name
            url = url or (f"https://{dep.domain}" if dep.domain and dep.tls else f"http://{dep.domain}" if dep.domain else "")
        item = self.registry.upsert_environment(EnvironmentRecord(project=project, environment=env, deployment=deployment, app_name=app_name, url=url))
        return asdict(item)

    def environments(self, project: str = "") -> list[dict[str, Any]]:
        self.sync_deployments()
        out: list[dict[str, Any]] = []
        for item in self.registry.list_environments(project):
            row = asdict(item)
            if self.ops.configuration is not None and item.project:
                try:
                    c = self.ops.configuration.status(item.project, item.environment, discover=True)
                    row["configuration"] = {"ready": c.get("ready"), "missing": c.get("missing") or [], "configured": c.get("configured", 0), "required": c.get("required", 0)}
                except Exception as exc:
                    row["configuration"] = {"ready": False, "error": str(exc)}
            if item.deployment:
                dep = self.ops.registry.get(item.deployment)
                if dep:
                    row["current_commit"] = dep.current_commit
                    row["last_deployed_at"] = getattr(dep, "last_deployed_at", "") or item.last_deployed_at
            out.append(row)
        return out

    def _deployment(self, deployment: str = "", *, project: str = "", environment: str = "production"):
        if deployment:
            dep = self.ops.registry.get(deployment)
            if dep is None:
                raise ValueError(f"unknown deployment: {deployment}")
            return dep
        env = clean_environment(environment)
        if project:
            item = self.registry.get_environment(project, env)
            if item and item.deployment:
                dep = self.ops.registry.get(item.deployment)
                if dep:
                    return dep
        if self.ops.projects is not None:
            target = project or self.ops.projects.registry.active_name
            if target:
                for dep in self.ops.registry.list():
                    if dep.project_name == target and clean_environment(getattr(dep, "environment", "production")) == env:
                        return dep
        raise ValueError("no deployment matches the selected project/environment")

    def _docker_stats(self, dep) -> dict[str, Any]:
        app = self.ops.apps.registry.get(dep.app_name)
        if app is None:
            return {"ok": False, "error": "app missing"}
        try:
            project_dir = self.ops.apps._project_dir(app)
            result = self.ops.apps.docker.stats(project_dir, app.compose_file, app.compose_project)
            return result
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _site_log_paths(self, dep) -> tuple[Path, Path]:
        base = Path(str(self.config.nginx_log_dir))
        site = dep.nginx_site or dep.name
        candidates = [
            (base / f"{site}.access.log", base / f"{site}.error.log"),
            (base / f"{dep.domain}.access.log", base / f"{dep.domain}.error.log") if dep.domain else (Path(""), Path("")),
        ]
        for access, error in candidates:
            if str(access) and (access.is_file() or error.is_file()):
                return access, error
        return base / "access.log", base / "error.log"

    @staticmethod
    def _tail(path: Path, lines: int) -> list[str]:
        if not path.is_file():
            return []
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(int(lines), 10000)):]
        except OSError:
            return []

    def nginx_metrics(self, dep, *, lines: int | None = None) -> dict[str, Any]:
        access_path, error_path = self._site_log_paths(dep)
        rows = self._tail(access_path, lines or self.config.log_tail_lines)
        status_counts: dict[str, int] = {}
        classes = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
        paths: dict[str, int] = {}
        latencies: list[float] = []
        parsed = 0
        for line in rows:
            m = _ACCESS_RX.search(line)
            if not m:
                continue
            parsed += 1
            status = m.group("status")
            status_counts[status] = status_counts.get(status, 0) + 1
            klass = f"{status[0]}xx" if status[0] in "2345" else "other"
            classes[klass] = classes.get(klass, 0) + 1
            path = m.group("path").split("?", 1)[0]
            paths[path] = paths.get(path, 0) + 1
            rt = _RT_RX.search(line)
            if rt:
                try:
                    latencies.append(float(rt.group("value")) * 1000.0)
                except Exception:
                    pass
        errors = self._tail(error_path, min(int(lines or self.config.log_tail_lines), 2000))
        levels: dict[str, int] = {}
        for line in errors:
            m = _LEVEL_RX.search(line)
            if m:
                level = m.group("level").lower()
                levels[level] = levels.get(level, 0) + 1
        total = max(parsed, 0)
        five = classes.get("5xx", 0)
        scoped = access_path.name not in {"access.log", "error.log"}
        return {
            "access_log": str(access_path), "error_log": str(error_path), "scoped": scoped, "sample_lines": len(rows), "requests": total,
            "status_classes": classes, "status_counts": status_counts,
            "error_rate_percent": round((five / total * 100.0), 3) if total else 0.0,
            "top_paths": sorted(({"path": k, "requests": v} for k, v in paths.items()), key=lambda x: x["requests"], reverse=True)[:20],
            "latency_ms": {"count": len(latencies), "p50": _percentile(latencies, .50), "p95": _percentile(latencies, .95), "p99": _percentile(latencies, .99)},
            "error_levels": levels, "error_lines": len(errors),
        }

    def snapshot(self, *, deployment: str = "", project: str = "", environment: str = "production", persist: bool = True, detect: bool = True) -> dict[str, Any]:
        dep = self._deployment(deployment, project=project, environment=environment)
        env = clean_environment(getattr(dep, "environment", environment or "production"))
        started = time.monotonic()
        app_status = self.ops.apps.status(dep.app_name)
        public_url = ""
        if dep.domain:
            public_url = f"https://{dep.domain}/" if dep.tls else f"http://{dep.domain}/"
        elif app_status.get("url"):
            public_url = str(app_status.get("url"))
        public = self.ops.external_probe(public_url, timeout_s=min(15, int(self.config.probe_timeout_s))) if public_url else {"ok": False, "skipped": True}
        docker = self._docker_stats(dep)
        nginx = self.nginx_metrics(dep)
        configuration = None
        if self.ops.configuration is not None and dep.project_name:
            try:
                configuration = self.ops.configuration.status(dep.project_name, env, discover=True)
                configuration = {"ready": configuration.get("ready"), "missing": configuration.get("missing") or [], "required": configuration.get("required", 0), "configured": configuration.get("configured", 0)}
            except Exception as exc:
                configuration = {"ready": False, "error": str(exc)}
        host = self.ops.server.status()
        snapshot = {
            "ts": now_iso(), "deployment": dep.name, "project": dep.project_name, "environment": env,
            "commit": dep.current_commit, "release_status": getattr(dep, "release_status", "unknown"),
            "app": app_status, "public": public, "docker": docker, "nginx": nginx, "configuration": configuration,
            "host": {"load": host.get("load"), "memory": host.get("memory"), "disks": host.get("disks")},
            "collection_ms": round((time.monotonic() - started) * 1000.0, 2),
        }
        healthy = bool(app_status.get("ok") and (public.get("ok") if public_url else True))
        snapshot["healthy"] = healthy
        if persist:
            self.registry.append_snapshot(snapshot)
        item = self.registry.get_environment(dep.project_name, env) if dep.project_name else None
        if item:
            item.status = "healthy" if healthy else "degraded"
            item.current_commit = dep.current_commit
            item.last_observed_at = snapshot["ts"]
            self.registry.upsert_environment(item)
        self._emit("production.snapshot", deployment=dep.name, project=dep.project_name, environment=env, healthy=healthy, commit=dep.current_commit)
        if detect:
            snapshot["incidents"] = self.detect_incidents(snapshot=snapshot)
        return snapshot

    def detect_incidents(self, *, deployment: str = "", snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        snap = snapshot or self.snapshot(deployment=deployment, persist=True, detect=False)
        project = str(snap.get("project") or "")
        environment = str(snap.get("environment") or "production")
        dep_name = str(snap.get("deployment") or deployment)
        commit = str(snap.get("commit") or "")
        findings: list[tuple[str, str, str, dict[str, Any]]] = []
        if not (snap.get("app") or {}).get("ok"):
            findings.append(("P1", "Application unhealthy", "Docker/container or application health check is failing.", {"app": snap.get("app")}))
        public = snap.get("public") or {}
        if not public.get("skipped") and not public.get("ok"):
            findings.append(("P1", "Public endpoint unhealthy", f"Public probe failed for {public.get('url') or dep_name}.", {"public": public}))
        ng = snap.get("nginx") or {}
        requests = int(ng.get("requests") or 0)
        rate = float(ng.get("error_rate_percent") or 0.0)
        if ng.get("scoped") and requests >= int(self.config.incident_5xx_min_requests) and rate >= float(self.config.incident_5xx_rate_percent):
            findings.append(("P1", "Elevated HTTP 5xx rate", f"5xx rate is {rate:.2f}% across the current bounded Nginx sample.", {"requests": requests, "5xx_rate_percent": rate, "status_classes": ng.get("status_classes")}))
        cfg = snap.get("configuration")
        if isinstance(cfg, dict) and cfg.get("ready") is False and cfg.get("missing"):
            findings.append(("P2", "Production configuration incomplete", "Required environment configuration is missing.", {"missing": cfg.get("missing")}))
        opened: list[dict[str, Any]] = []
        for severity, title, summary, evidence in findings:
            inc, created = self.registry.open_incident(title=title, project=project, environment=environment, deployment=dep_name,
                                                       severity=severity, summary=summary, commit=commit, evidence=evidence)
            row = asdict(inc); row["created"] = created
            opened.append(row)
            self._emit("incident.opened" if created else "incident.updated", incident_id=inc.id, title=title, severity=severity, deployment=dep_name)
        return opened

    def metrics(self, *, deployment: str = "", project: str = "", environment: str = "", limit: int = 100) -> dict[str, Any]:
        rows = self.registry.snapshots(deployment=deployment, project=project, environment=environment, limit=limit)
        return {"snapshots": rows, "count": len(rows)}

    def logs(self, *, deployment: str, source: str = "app", service: str = "", search: str = "", level: str = "", lines: int = 300) -> dict[str, Any]:
        dep = self._deployment(deployment)
        limit = max(1, min(int(lines), 5000))
        if source == "app":
            result = self.ops.apps.logs(dep.app_name, service=service, tail=limit)
            raw = str(result.get("output") or "").splitlines()
        elif source in {"nginx_access", "nginx_error"}:
            access, error = self._site_log_paths(dep)
            raw = self._tail(access if source == "nginx_access" else error, limit)
        else:
            raise ValueError("source must be app, nginx_access, or nginx_error")
        if search:
            needle = search.lower()
            raw = [x for x in raw if needle in x.lower()]
        if level:
            needle = level.lower()
            raw = [x for x in raw if needle in x.lower()]
        return {"deployment": dep.name, "source": source, "service": service, "count": len(raw), "lines": raw[-limit:]}

    def incidents(self, *, project: str = "", environment: str = "", status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        return [asdict(x) for x in self.registry.list_incidents(project=project, environment=environment, status=status, limit=limit)]

    def update_incident(self, incident_id: str, *, status: str = "", severity: str = "", note: str = "") -> dict[str, Any]:
        item = self.registry.update_incident(incident_id, status=status, severity=severity, note=note)
        self._emit("incident.updated", incident_id=item.id, status=item.status, severity=item.severity)
        return asdict(item)

    def release_started(self, dep, *, commit: str, previous_commit: str) -> None:
        self.registry.release_marker({"type": "deploy.started", "deployment": dep.name, "project": dep.project_name,
                                      "environment": getattr(dep, "environment", "production"), "commit": commit, "previous_commit": previous_commit})
        self._emit("release.started", deployment=dep.name, project=dep.project_name, environment=getattr(dep, "environment", "production"), commit=commit)

    def release_finished(self, dep, *, ok: bool, commit: str, error: str = "") -> dict[str, Any] | None:
        env = clean_environment(getattr(dep, "environment", "production"))
        marker = {"type": "deploy.finished", "deployment": dep.name, "project": dep.project_name, "environment": env,
                  "commit": commit, "ok": bool(ok), "error": str(error or "")[:4000]}
        self.registry.release_marker(marker)
        self._emit("release.finished", **marker)
        if not ok:
            return None
        try:
            snap = self.snapshot(deployment=dep.name, persist=True, detect=True)
            return snap
        except Exception as exc:
            self._emit("production.snapshot_failed", deployment=dep.name, error=f"{type(exc).__name__}: {exc}")
            return None

    def verify_release(self, deployment: str, *, samples: int = 3, interval_s: int = 0) -> dict[str, Any]:
        dep = self._deployment(deployment)
        count = max(1, min(int(samples), int(self.config.verify_max_samples)))
        wait = max(0, min(int(interval_s), int(self.config.verify_max_interval_s)))
        snapshots: list[dict[str, Any]] = []
        for idx in range(count):
            snapshots.append(self.snapshot(deployment=deployment, persist=True, detect=True))
            if idx + 1 < count and wait:
                time.sleep(wait)
        healthy = all(bool(x.get("healthy")) for x in snapshots)
        scoped_rates = [float((x.get("nginx") or {}).get("error_rate_percent") or 0.0) for x in snapshots if (x.get("nginx") or {}).get("scoped")]
        max_scoped_rate = max(scoped_rates) if scoped_rates else 0.0
        result = {
            "ok": healthy and max_scoped_rate < float(self.config.incident_5xx_rate_percent),
            "deployment": deployment, "commit": dep.current_commit, "samples": len(snapshots),
            "healthy_samples": sum(1 for x in snapshots if x.get("healthy")),
            "nginx_scoped_samples": len(scoped_rates),
            "max_5xx_rate_percent": max_scoped_rate,
            "snapshots": snapshots,
        }
        verified_at = now_iso()
        dep.last_verified_at = verified_at
        dep.release_status = "verified" if result["ok"] else "degraded"
        self.ops.registry.upsert(dep)
        if dep.project_name:
            item = self.registry.get_environment(dep.project_name, clean_environment(getattr(dep, "environment", "production")))
            if item is not None:
                item.last_verified_at = verified_at
                item.status = "healthy" if result["ok"] else "degraded"
                self.registry.upsert_environment(item)
        self.registry.release_marker({"type": "release.verified", "deployment": dep.name, "project": dep.project_name,
                                      "environment": getattr(dep, "environment", "production"), "commit": dep.current_commit, "ok": result["ok"]})
        self._emit("release.verified", deployment=dep.name, ok=result["ok"], commit=dep.current_commit)
        return result

    def release_compare(self, *, source: str, target: str) -> dict[str, Any]:
        src = self._deployment(source)
        dst = self._deployment(target)
        return {
            "source": {"deployment": src.name, "project": src.project_name, "environment": getattr(src, "environment", "production"), "commit": src.current_commit, "status": getattr(src, "release_status", "unknown")},
            "target": {"deployment": dst.name, "project": dst.project_name, "environment": getattr(dst, "environment", "production"), "commit": dst.current_commit, "status": getattr(dst, "release_status", "unknown")},
            "same_project": bool(src.project_name and src.project_name == dst.project_name),
            "same_commit": bool(src.current_commit and src.current_commit == dst.current_commit),
            "promotion_ready": bool(src.project_name and src.project_name == dst.project_name and src.current_commit and getattr(src, "release_status", "") == "verified"),
        }

    def overview(self, project: str = "") -> dict[str, Any]:
        if not project and self.ops.projects is not None:
            project = self.ops.projects.registry.active_name
        envs = self.environments(project)
        incidents = self.incidents(project=project, status="open", limit=50) + self.incidents(project=project, status="investigating", limit=50)
        deployments = []
        for dep in self.ops.registry.list():
            if project and dep.project_name != project:
                continue
            latest = self.registry.snapshots(deployment=dep.name, limit=1)
            deployments.append({
                "name": dep.name, "project": dep.project_name, "environment": getattr(dep, "environment", "production"), "domain": dep.domain,
                "commit": dep.current_commit, "release_status": getattr(dep, "release_status", "unknown"),
                "last_deployed_at": getattr(dep, "last_deployed_at", ""), "last_verified_at": getattr(dep, "last_verified_at", ""),
                "latest": latest[-1] if latest else None,
            })
        return {"project": project, "environments": envs, "deployments": deployments, "incidents": incidents, "open_incidents": len(incidents), "monitoring": {"background_enabled": bool(self.config.background_monitor), "interval_s": int(self.config.monitor_interval_s)}}
