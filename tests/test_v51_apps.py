from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

from term5.apps.docker import DockerManager
from term5.apps.manager import AppRuntime, AppRuntimeError
from term5.apps.registry import AppManifest, AppRegistry
from term5.config import load_config
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.state.transactions import TransactionManager


def make_apps(tmp_path: Path) -> AppRuntime:
    guard = PathGuard(tmp_path)
    tx = TransactionManager(tmp_path / ".term5", guard)
    return AppRuntime(tmp_path, tmp_path / ".term5", guard, tx)


def test_app_registry_roundtrip(tmp_path: Path):
    path = tmp_path / ".term5" / "apps.json"
    reg = AppRegistry(path)
    reg.add(AppManifest(name="demo", path="apps/demo", url="http://127.0.0.1:4990"))
    again = AppRegistry(path)
    assert again.get("demo") is not None
    assert again.get("demo").compose_project == "demo"
    assert json.loads(path.read_text())["format"] == 1


def test_flask_scaffold_is_transactional_and_registered(tmp_path: Path):
    apps = make_apps(tmp_path)
    app = apps.scaffold_flask(name="demo", path="apps/demo", port=4990, celery=True, mongodb=True)
    root = tmp_path / "apps" / "demo"
    assert app.url == "http://127.0.0.1:4990"
    assert {"web", "worker", "redis", "mongo"}.issubset(set(app.services))
    assert (root / "app.py").is_file()
    assert (root / "tasks.py").is_file()
    compose = (root / "compose.yaml").read_text()
    assert '127.0.0.1:4990:5000' in compose
    assert "mongo_data" in compose
    assert apps.registry.get("demo") is not None
    with pytest.raises(AppRuntimeError, match="refusing to overwrite"):
        apps.scaffold_flask(name="demo2", path="apps/demo", port=4991)


def test_port_probe_detects_bound_port(tmp_path: Path):
    apps = make_apps(tmp_path)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    try:
        port = sock.getsockname()[1]
        assert apps.port_available(port) is False
    finally:
        sock.close()
    assert apps.port_available(port) is True


def test_docker_manager_uses_typed_argv_no_shell(monkeypatch, tmp_path: Path):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(argv, 0, '{"Version":"28.0","ApiVersion":"1.48"}')
        if argv[:3] == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(argv, 0, "2.40.0")
        return subprocess.CompletedProcess(argv, 0, "")
    monkeypatch.setattr("term5.apps.docker.shutil.which", lambda _: "/usr/bin/docker")
    dm = DockerManager(runner=fake_run)
    st = dm.engine_status()
    assert st["available"] is True and st["compose_available"] is True
    dm.compose(tmp_path, "compose.yaml", "demo", ["config", "-q"])
    argv, kwargs = calls[-1]
    assert argv == ["docker", "compose", "-f", "compose.yaml", "-p", "demo", "config", "-q"]
    assert kwargs["shell"] is False


def test_compose_up_falls_back_when_wait_flag_unsupported(monkeypatch, tmp_path: Path):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "--wait" in argv:
            return subprocess.CompletedProcess(argv, 1, "unknown flag: --wait")
        return subprocess.CompletedProcess(argv, 0, "started")
    monkeypatch.setattr("term5.apps.docker.shutil.which", lambda _: "/usr/bin/docker")
    dm = DockerManager(runner=fake_run)
    result = dm.up(tmp_path, "compose.yaml", "demo", build=True, wait=True)
    assert result.ok is True
    assert len(calls) == 2
    assert "--wait" in calls[0]
    assert "--wait" not in calls[1]
    assert "--build" in calls[1]


def test_app_status_combines_compose_and_http(monkeypatch, tmp_path: Path):
    apps = make_apps(tmp_path)
    project = tmp_path / "demo"; project.mkdir(); (project / "compose.yaml").write_text("services: {}\n")
    apps.registry.add(AppManifest(name="demo", path="demo", url="http://127.0.0.1:4990", health_url="http://127.0.0.1:4990/health"))
    class R: ok=True; stdout=""; argv=[]
    monkeypatch.setattr(apps.docker, "ps", lambda *a, **k: (R(), [{"Service":"web","State":"running","Health":"healthy"}]))
    monkeypatch.setattr(apps, "http_probe", lambda *a, **k: {"ok": True, "status": 200})
    status = apps.status("demo")
    assert status["ok"] is True
    assert status["services"][0]["running"] is True
    assert status["http"]["status"] == 200


def test_app_open_refuses_unhealthy(monkeypatch, tmp_path: Path):
    apps = make_apps(tmp_path)
    project = tmp_path / "demo"; project.mkdir(); (project / "compose.yaml").write_text("services: {}\n")
    apps.registry.add(AppManifest(name="demo", path="demo", url="http://127.0.0.1:4990"))
    monkeypatch.setattr(apps, "status", lambda name: {"ok": False})
    assert apps.open("demo")["opened"] is False


def test_runtime_registers_local_app_tools(tmp_path: Path):
    cfg = load_config(tmp_path)
    rt = AgentRuntime(cfg)
    names = set(rt.tools.names())
    assert {"docker_status", "app_create_flask", "app_start", "app_stop", "app_restart", "app_status", "app_logs", "app_open"}.issubset(names)
    st = rt.status()
    assert st["app_runtime_enabled"] is True
    assert st["registered_apps"] == 0

def test_end_to_end_flask_create_orders_health_before_open(monkeypatch, tmp_path: Path):
    apps = make_apps(tmp_path)
    monkeypatch.setattr(apps, "docker_status", lambda refresh=False: {"available": True, "compose_available": True})
    monkeypatch.setattr(apps, "port_available", lambda port, host="127.0.0.1": True)
    calls = []
    def fake_start(name, **kwargs):
        calls.append(("start", name, kwargs))
        return {"ok": True, "status": {"ok": True}}
    def fake_open(name):
        calls.append(("open", name, {}))
        return {"ok": True, "opened": True, "url": "http://127.0.0.1:4990"}
    monkeypatch.setattr(apps, "start", fake_start)
    monkeypatch.setattr(apps, "open", fake_open)
    result = apps.create_flask(name="demo", path="demo", port=4990, start=True, open_browser=True)
    assert result["ok"] is True and result["started"] is True and result["opened"] is True
    assert [x[0] for x in calls] == ["start", "open"]
    assert (tmp_path / "demo" / "compose.yaml").exists()
