from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from term5.apps.manager import AppRuntime
from term5.apps.registry import AppManifest
from term5.config import load_config
from term5.ops.command import CommandResult
from term5.ops.git import GitManager
from term5.ops.manager import OperationsError, OperationsRuntime
from term5.ops.nginx import MANAGED_MARKER, NginxManager
from term5.ops.registry import DeploymentRegistry
from term5.ops.tls import TLSManager
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.state.transactions import TransactionManager


def make_apps(tmp_path: Path) -> AppRuntime:
    guard = PathGuard(tmp_path)
    tx = TransactionManager(tmp_path / ".term5", guard)
    apps = AppRuntime(tmp_path, tmp_path / ".term5", guard, tx)
    project = tmp_path / "app"
    project.mkdir()
    (project / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    apps.registry.add(AppManifest(name="app", path="app", url="http://127.0.0.1:4990", health_url="http://127.0.0.1:4990/health"))
    return apps


def ops_config(tmp_path: Path):
    cfg = load_config(tmp_path)
    cfg.operations.nginx_sites_available = str(tmp_path / "nginx" / "available")
    cfg.operations.nginx_sites_enabled = str(tmp_path / "nginx" / "enabled")
    return cfg


def test_git_manager_uses_typed_argv(monkeypatch, tmp_path: Path):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok")
    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/bin/{name}")
    git = GitManager(tmp_path, runner=fake_run)
    r = git.add(["app.py", "templates/index.html"])
    assert r.ok
    argv, kwargs = calls[-1]
    assert argv == ["git", "add", "--", "app.py", "templates/index.html"]
    assert kwargs["shell"] is False


def test_nginx_plan_is_loopback_and_managed(tmp_path: Path):
    ng = NginxManager(sites_available=str(tmp_path / "a"), sites_enabled=str(tmp_path / "e"))
    plan = ng.plan(name="social", domain="social.example.com", upstream_port=4990)
    assert MANAGED_MARKER in plan["config"]
    assert "proxy_pass http://127.0.0.1:4990" in plan["config"]
    assert "proxy_set_header Upgrade $http_upgrade" in plan["config"]
    with pytest.raises(Exception, match="loopback"):
        ng.render(domain="social.example.com", upstream_port=4990, upstream_host="10.0.0.5")


def test_nginx_install_rolls_back_invalid_candidate(monkeypatch, tmp_path: Path):
    available = tmp_path / "available"; enabled = tmp_path / "enabled"
    available.mkdir(); enabled.mkdir()
    old = available / "social.conf"
    old.write_text(MANAGED_MARKER + "\nold\n", encoding="utf-8")
    (enabled / "social.conf").symlink_to(old)
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv == ["nginx", "-t"] and len([c for c in calls if c == ["nginx", "-t"]]) == 1:
            return subprocess.CompletedProcess(argv, 1, "invalid config")
        return subprocess.CompletedProcess(argv, 0, "ok")
    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/sbin/{name}")
    ng = NginxManager(sites_available=str(available), sites_enabled=str(enabled), runner=fake_run)
    result = ng.install(name="social", domain="social.example.com", upstream_port=4990)
    assert result.ok is False and result.rolled_back is True
    assert old.read_text(encoding="utf-8") == MANAGED_MARKER + "\nold\n"
    assert (enabled / "social.conf").is_symlink()


def test_tls_issue_is_typed_certbot_command(monkeypatch):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "certificate installed")
    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/bin/{name}")
    tls = TLSManager(runner=fake_run)
    result = tls.issue(domain="social.example.com", email="admin@example.com", staging=True)
    assert result["ok"] is True
    argv, kwargs = calls[-1]
    assert argv[:5] == ["certbot", "--nginx", "-d", "social.example.com", "--non-interactive"]
    assert "--staging" in argv and "--redirect" in argv
    assert kwargs["shell"] is False


def test_deployment_registry_roundtrip(tmp_path: Path):
    apps = make_apps(tmp_path)
    cfg = ops_config(tmp_path)
    ops = OperationsRuntime(tmp_path, tmp_path / ".term5", apps, config=cfg.operations, security_config=cfg.security)
    dep = ops.register(name="prod", app_name="app", domain="social.example.com", upstream_port=4990, tls=True)
    assert dep.domain == "social.example.com"
    again = DeploymentRegistry(tmp_path / ".term5" / "deployments.json")
    assert again.get("prod") is not None and again.get("prod").app_name == "app"


def test_deploy_refuses_dirty_git(tmp_path: Path, monkeypatch):
    apps = make_apps(tmp_path)
    cfg = ops_config(tmp_path)
    ops = OperationsRuntime(tmp_path, tmp_path / ".term5", apps, config=cfg.operations, security_config=cfg.security)
    ops.register(name="prod", app_name="app")
    monkeypatch.setattr(ops.git, "available", lambda: {"available": True, "repository": True})
    monkeypatch.setattr(ops.git, "status", lambda: {"ok": True, "clean": False})
    with pytest.raises(OperationsError, match="clean Git"):
        ops.deploy("prod", configure_nginx=False)


def test_healthy_deploy_records_release(tmp_path: Path, monkeypatch):
    apps = make_apps(tmp_path)
    cfg = ops_config(tmp_path)
    ops = OperationsRuntime(tmp_path, tmp_path / ".term5", apps, config=cfg.operations, security_config=cfg.security)
    ops.register(name="prod", app_name="app")
    monkeypatch.setattr(ops.git, "available", lambda: {"available": True, "repository": True})
    monkeypatch.setattr(ops.git, "status", lambda: {"ok": True, "clean": True})
    monkeypatch.setattr(ops.git, "head", lambda: "abc123")
    monkeypatch.setattr(apps, "start", lambda *a, **k: {"ok": True, "status": {"ok": True}})
    monkeypatch.setattr(apps, "status", lambda *a, **k: {"ok": True})
    result = ops.deploy("prod", configure_nginx=False)
    assert result["ok"] is True
    dep = ops.registry.get("prod")
    assert dep.current_commit == "abc123"
    assert dep.history[-1]["status"] == "healthy"


def test_deployment_rollback_is_clean_tree_guarded(tmp_path: Path, monkeypatch):
    apps = make_apps(tmp_path)
    cfg = ops_config(tmp_path)
    cfg.security.allow_git_write = True
    ops = OperationsRuntime(tmp_path, tmp_path / ".term5", apps, config=cfg.operations, security_config=cfg.security)
    dep = ops.register(name="prod", app_name="app")
    dep.current_commit = "new222"; dep.previous_commit = "old111"; ops.registry.upsert(dep)
    monkeypatch.setattr(ops.git, "status", lambda: {"clean": True})
    monkeypatch.setattr(ops.git, "head", lambda: "new222")
    monkeypatch.setattr(ops.git, "reset_hard", lambda ref: CommandResult(True, 0, "reset", ["git", "reset", "--hard", ref]))
    monkeypatch.setattr(apps, "start", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(apps, "status", lambda *a, **k: {"ok": True})
    result = ops.rollback("prod")
    assert result["ok"] is True and result["to"] == "old111"
    assert ops.registry.get("prod").current_commit == "old111"


def test_runtime_registers_operations_tools_but_write_caps_are_gated(tmp_path: Path):
    cfg = load_config(tmp_path)
    rt = AgentRuntime(cfg)
    names = set(rt.tools.names())
    assert {"ops_status", "nginx_site_plan", "nginx_site_install", "tls_issue", "deployment_deploy", "deployment_rollback", "git_commit"}.issubset(names)
    assert rt.security.capabilities.has("ops.read")
    assert not rt.security.capabilities.has("git.write")
    assert not rt.security.capabilities.has("ops.nginx.write")
    assert not rt.security.capabilities.has("ops.tls.write")


def test_write_capabilities_enable_explicitly(tmp_path: Path):
    cfg = load_config(tmp_path)
    cfg.security.allow_git_write = True
    cfg.security.allow_nginx_write = True
    cfg.security.allow_tls_issue = True
    rt = AgentRuntime(cfg)
    assert rt.security.capabilities.has("git.write")
    assert rt.security.capabilities.has("ops.nginx.write")
    assert rt.security.capabilities.has("ops.tls.write")


def test_git_remote_capability_is_separate(tmp_path: Path):
    cfg = load_config(tmp_path)
    cfg.security.allow_git_write = True
    rt = AgentRuntime(cfg)
    assert rt.security.capabilities.has("git.write")
    assert not rt.security.capabilities.has("git.remote")
    cfg2 = load_config(tmp_path)
    cfg2.security.allow_git_remote = True
    rt2 = AgentRuntime(cfg2)
    assert rt2.security.capabilities.has("git.remote")


def test_deployment_skill_resolution_loads_ops_guidance(tmp_path: Path):
    cfg = load_config(tmp_path)
    rt = AgentRuntime(cfg)
    resolved = rt.skills.resolve("deploy this flask app publicly with nginx and letsencrypt")
    assert "deployment-operations" in resolved.selected
    assert "nginx-production" in resolved.selected
    assert "letsencrypt" in resolved.selected
    assert "git-release" in resolved.selected


def test_deploy_requires_git_repository_by_default(tmp_path: Path, monkeypatch):
    apps = make_apps(tmp_path)
    cfg = ops_config(tmp_path)
    ops = OperationsRuntime(tmp_path, tmp_path / ".term5", apps, config=cfg.operations, security_config=cfg.security)
    ops.register(name="prod", app_name="app")
    monkeypatch.setattr(ops.git, "available", lambda: {"available": True, "repository": False})
    with pytest.raises(OperationsError, match="requires a Git repository"):
        ops.deploy("prod", configure_nginx=False)


def test_nginx_service_control_uses_typed_systemctl(monkeypatch, tmp_path: Path):
    calls=[]
    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "")
    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/bin/{name}")
    ng=NginxManager(sites_available=str(tmp_path/"a"), sites_enabled=str(tmp_path/"e"), runner=fake_run)
    result=ng.service_control("restart")
    assert result["ok"] is True
    assert calls[-1][0] == ["systemctl", "restart", "nginx"]
    assert calls[-1][1]["shell"] is False


def test_production_operations_route_to_max_reasoning():
    from term5.brain.router import AttentionRouter
    from term5.models import ReasoningMode
    d = AttentionRouter().decide("deploy this app to production with nginx and letsencrypt")
    assert d.reasoning == ReasoningMode.MAX
    assert d.parallel_hint >= 4
