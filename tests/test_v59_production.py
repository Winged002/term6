from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from term5.apps.registry import AppManifest
from term5.config import load_config
from term5.ops.nginx import NginxManager
from term5.ops.registry import DeploymentManifest, DeploymentRegistry
from term5.production.registry import EnvironmentRecord, ProductionRegistry
from term5.runtime import AgentRuntime
from term5.ui.web import LocalWebApp
from term5.ui.web_assets import HTML, JS


def _runtime_with_deployment(tmp_path: Path, *, environment: str = "production"):
    cfg = load_config(tmp_path)
    cfg.operations.require_git = False
    cfg.operations.require_clean_git = False
    cfg.operations.require_dns = False
    cfg.production.nginx_log_dir = str(tmp_path / "nginx-logs")
    runtime = AgentRuntime(cfg)
    project = runtime.projects.create(name="demo")
    project_root = runtime.projects.path(project.name)
    (project_root / "compose.yaml").write_text("services:\n  web:\n    image: demo\n", encoding="utf-8")
    runtime.apps.registry.add(AppManifest(
        name="demo", path=str(project_root.relative_to(tmp_path)), compose_file="compose.yaml",
        compose_project="demo", entry_service="web", url="http://127.0.0.1:8899",
        health_url="http://127.0.0.1:8899/health", services=["web"],
    ))
    runtime.projects.update("demo", app_name="demo")
    dep = runtime.operations.register(
        name=f"demo-{environment}", app_name="demo", upstream_port=8899,
        domain="demo.example.com", environment=environment,
    )
    dep.current_commit = "abc123"
    runtime.operations.registry.upsert(dep)
    runtime.production.sync_deployments()
    return runtime, dep


def _healthy_runtime(monkeypatch, runtime: AgentRuntime, tmp_path: Path, dep_name: str, *, five_xx: int = 0, total: int = 30):
    monkeypatch.setattr(runtime.apps, "status", lambda name: {
        "ok": True, "name": name, "url": "http://127.0.0.1:8899", "health": {"ok": True}, "services": {"web": "running"}
    })
    monkeypatch.setattr(runtime.apps.docker, "stats", lambda *args, **kwargs: {
        "ok": True, "containers": [{"name": "demo-web-1", "cpu_percent": 2.5, "memory_percent": 10.0}]
    })
    monkeypatch.setattr(runtime.operations, "external_probe", lambda url, timeout_s=10: {
        "ok": True, "url": url, "status": 200, "elapsed_ms": 42.0
    })
    monkeypatch.setattr(runtime.operations.server, "status", lambda: {
        "ok": True, "load": {"1m": 0.2}, "memory": {"percent": 40.0}, "disks": [{"mount": "/", "percent": 55.0}]
    })
    logs = tmp_path / "nginx-logs"
    logs.mkdir(exist_ok=True)
    good = max(0, total - five_xx)
    rows = []
    rows += [f'127.0.0.1 - - [28/Sep/2026:20:00:00 +0000] "GET /ok HTTP/1.1" 200 10 rt=0.050' for _ in range(good)]
    rows += [f'127.0.0.1 - - [28/Sep/2026:20:00:01 +0000] "POST /fail HTTP/1.1" 500 10 rt=0.400' for _ in range(five_xx)]
    (logs / "demo.example.com.access.log").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (logs / "demo.example.com.error.log").write_text("", encoding="utf-8")


def test_production_registry_persists_environments_incidents_and_snapshots(tmp_path: Path):
    registry = ProductionRegistry(tmp_path / ".term5", max_snapshots=100)
    registry.upsert_environment(EnvironmentRecord(project="demo", environment="staging", deployment="demo-staging"))
    registry.append_snapshot({"ts": "2026-09-28T20:00:00Z", "project": "demo", "environment": "staging", "deployment": "demo-staging", "healthy": True})
    incident, created = registry.open_incident(title="Application unhealthy", project="demo", environment="staging", deployment="demo-staging", severity="P1")
    assert created is True
    again = ProductionRegistry(tmp_path / ".term5", max_snapshots=100)
    assert again.get_environment("demo", "staging").deployment == "demo-staging"
    assert again.snapshots(project="demo", environment="staging")[-1]["healthy"] is True
    assert again.incidents[incident.id].severity == "P1"
    assert (again.metrics_path.stat().st_mode & 0o777) == 0o600
    assert (again.path.stat().st_mode & 0o777) == 0o600


def test_old_deployment_registry_defaults_to_production_environment(tmp_path: Path):
    path = tmp_path / "deployments.json"
    path.write_text(json.dumps({"format": 1, "deployments": {"legacy": {"name": "legacy", "app_name": "demo"}}}), encoding="utf-8")
    registry = DeploymentRegistry(path)
    dep = registry.get("legacy")
    assert dep.environment == "production"
    assert dep.release_status == "unknown"


def test_nginx_render_has_site_scoped_logs(tmp_path: Path):
    class DummyRunner:
        pass
    manager = NginxManager(sites_available=str(tmp_path / "available"), sites_enabled=str(tmp_path / "enabled"), runner=DummyRunner())
    text = manager.render(domain="app.example.com", upstream_port=8000)
    assert "access_log /var/log/nginx/app.example.com.access.log;" in text
    assert "error_log /var/log/nginx/app.example.com.error.log;" in text
    assert "# managed-by: term_5 v5.2" in text


def test_snapshot_collects_evidence_and_opens_scoped_5xx_incident(monkeypatch, tmp_path: Path):
    runtime, dep = _runtime_with_deployment(tmp_path)
    _healthy_runtime(monkeypatch, runtime, tmp_path, dep.name, five_xx=4, total=30)
    snap = runtime.production.snapshot(deployment=dep.name, persist=True, detect=True)
    assert snap["healthy"] is True
    assert snap["nginx"]["scoped"] is True
    assert snap["nginx"]["requests"] == 30
    assert snap["nginx"]["status_classes"]["5xx"] == 4
    assert snap["public"]["elapsed_ms"] == 42.0
    assert any(x["title"] == "Elevated HTTP 5xx rate" for x in snap["incidents"])
    persisted = runtime.production.metrics(deployment=dep.name, limit=5)
    assert persisted["count"] >= 1


def test_unscoped_global_nginx_log_does_not_create_project_5xx_incident(monkeypatch, tmp_path: Path):
    runtime, dep = _runtime_with_deployment(tmp_path)
    _healthy_runtime(monkeypatch, runtime, tmp_path, dep.name, five_xx=0, total=0)
    logs = tmp_path / "nginx-logs"
    (logs / "demo.example.com.access.log").unlink(missing_ok=True)
    (logs / "demo.example.com.error.log").unlink(missing_ok=True)
    rows = ['127.0.0.1 - - [28/Sep/2026:20:00:01 +0000] "GET /x HTTP/1.1" 500 10' for _ in range(50)]
    (logs / "access.log").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (logs / "error.log").write_text("", encoding="utf-8")
    snap = runtime.production.snapshot(deployment=dep.name, persist=False, detect=True)
    assert snap["nginx"]["scoped"] is False
    assert snap["nginx"]["error_rate_percent"] == 100.0
    assert not any(x["title"] == "Elevated HTTP 5xx rate" for x in snap["incidents"])


def test_release_verify_marks_release_verified(monkeypatch, tmp_path: Path):
    runtime, dep = _runtime_with_deployment(tmp_path)
    _healthy_runtime(monkeypatch, runtime, tmp_path, dep.name, five_xx=0, total=30)
    dep.release_status = "observing"
    runtime.operations.registry.upsert(dep)
    result = runtime.production.verify_release(dep.name, samples=2, interval_s=0)
    assert result["ok"] is True
    assert result["samples"] == 2
    stored = runtime.operations.registry.get(dep.name)
    assert stored.release_status == "verified"
    assert stored.last_verified_at



def test_release_verify_ignores_unscoped_global_5xx_for_release_attribution(monkeypatch, tmp_path: Path):
    runtime, dep = _runtime_with_deployment(tmp_path)
    _healthy_runtime(monkeypatch, runtime, tmp_path, dep.name, five_xx=0, total=0)
    logs = tmp_path / "nginx-logs"
    (logs / "demo.example.com.access.log").unlink(missing_ok=True)
    (logs / "demo.example.com.error.log").unlink(missing_ok=True)
    rows = ['127.0.0.1 - - [28/Sep/2026:20:00:01 +0000] "GET /other-app HTTP/1.1" 500 10' for _ in range(50)]
    (logs / "access.log").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (logs / "error.log").write_text("", encoding="utf-8")
    result = runtime.production.verify_release(dep.name, samples=1, interval_s=0)
    assert result["ok"] is True
    assert result["nginx_scoped_samples"] == 0
    assert result["max_5xx_rate_percent"] == 0.0
    env = runtime.production.registry.get_environment("demo", "production")
    assert env.last_observed_at
    assert env.last_verified_at

def test_failed_dns_preflight_records_failed_release_not_deploying(monkeypatch, tmp_path: Path):
    runtime, dep = _runtime_with_deployment(tmp_path)
    runtime.config.operations.require_dns = True
    monkeypatch.setattr(runtime.operations, "dns_status", lambda domain: {"ok": False, "domain": domain, "addresses": []})
    result = runtime.operations.deploy(dep.name, build=False, configure_nginx=True, issue_tls=False)
    assert result["ok"] is False
    assert "does not resolve" in result["error"]
    stored = runtime.operations.registry.get(dep.name)
    assert stored.release_status == "failed"
    releases = runtime.production.registry.releases(dep.name, limit=10)
    assert any(x.get("type") == "deploy.finished" and x.get("ok") is False for x in releases)


def test_open_p1_incident_is_in_production_readiness(monkeypatch, tmp_path: Path):
    runtime, dep = _runtime_with_deployment(tmp_path)
    runtime.production.registry.open_incident(title="Public endpoint unhealthy", project="demo", environment="production", deployment=dep.name, severity="P1")
    # Other host dependencies may be unavailable in the test environment; the incident warning itself must be present.
    result = runtime.operations.production_readiness()
    assert any("open P1 production incident: Public endpoint unhealthy" in w for w in result["warnings"])
    assert result["production"] is not None


def test_runtime_registers_production_tools_and_skill(tmp_path: Path):
    runtime = AgentRuntime(load_config(tmp_path))
    names = set(runtime.tools.names())
    required = {
        "production_overview", "environment_list", "environment_register", "production_snapshot", "production_metrics",
        "production_logs", "incident_list", "incident_detect", "incident_update", "release_verify", "release_compare",
    }
    assert required.issubset(names)
    assert runtime.security.capabilities.has("production.read")
    assert runtime.security.capabilities.has("production.manage")
    resolved = runtime.skills.resolve("inspect production metrics logs and incident health after a deployment")
    assert "production-intelligence" in resolved.selected



def test_production_log_api_redacts_known_secrets(tmp_path: Path, monkeypatch):
    runtime = AgentRuntime(load_config(tmp_path))
    runtime.redactor.add("super-secret-value")
    monkeypatch.setattr(runtime.production, "logs", lambda **kwargs: {
        "deployment": "demo-prod", "source": "app", "count": 1,
        "lines": ["SMTP_PASSWORD=super-secret-value"],
    })
    loop = asyncio.new_event_loop()
    web = LocalWebApp(runtime, loop, port=0)
    base = web.start()
    try:
        parsed = urlparse(base); token = parse_qs(parsed.query)["token"][0]
        url = f"http://127.0.0.1:{parsed.port}/api/production-logs?token={token}&deployment=demo-prod&source=app"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["lines"] == ["SMTP_PASSWORD=[REDACTED]"]
        assert "super-secret-value" not in json.dumps(data)
    finally:
        web.close(); loop.close()

def test_production_operations_ui_and_api(tmp_path: Path):
    for text in ["Operations", "Environments", "Incidents", "Collect now", "OpsLogs"]:
        assert text in HTML or text in JS
    for endpoint in ["/api/production-overview", "/api/production-metrics", "/api/incidents", "/api/production-logs", "/api/production-snapshot", "/api/incident-update"]:
        assert endpoint in JS or endpoint in (Path(__file__).resolve().parents[1] / 'term5/ui/web.py').read_text(encoding='utf-8')
    assert JS.count("setTimeout(poll") == 1
    assert "setInterval(" not in JS

    runtime = AgentRuntime(load_config(tmp_path))
    loop = asyncio.new_event_loop()
    web = LocalWebApp(runtime, loop, port=0)
    base = web.start()
    try:
        parsed = urlparse(base); token = parse_qs(parsed.query)["token"][0]
        with urllib.request.urlopen(f"http://127.0.0.1:{parsed.port}/api/production-overview?token={token}", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert "environments" in data and "incidents" in data and "monitoring" in data
        with urllib.request.urlopen(f"http://127.0.0.1:{parsed.port}/api/incidents?token={token}", timeout=5) as resp:
            incidents = json.loads(resp.read().decode("utf-8"))
        assert isinstance(incidents, list)
    finally:
        web.close(); loop.close()
