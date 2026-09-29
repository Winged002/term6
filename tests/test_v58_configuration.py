from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from term5.config import load_config
from term5.configuration.manager import ConfigurationManager, MASK
from term5.projects.manager import ProjectManager
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.security.redaction import SecretRedactor
from term5.ui.web import LocalWebApp
from term5.ui.web_assets import HTML, JS


def _manager(tmp_path: Path):
    cfg = load_config(tmp_path)
    guard = PathGuard(tmp_path)
    projects = ProjectManager(tmp_path, tmp_path / ".term5", guard, config=cfg.projects)
    projects.create(name="demo")
    redactor = SecretRedactor()
    manager = ConfigurationManager(tmp_path, tmp_path / ".term5", projects, config=cfg.configuration, redactor=redactor)
    return cfg, projects, manager, redactor


def test_configuration_discovery_from_example_code_and_compose(tmp_path: Path):
    _cfg, projects, manager, _redactor = _manager(tmp_path)
    root = projects.path("demo")
    (root / ".env.example").write_text("SMTP_HOST=\nSMTP_PORT=587\nSMTP_PASSWORD=<required>\n", encoding="utf-8")
    (root / "app.py").write_text("import os\nKEY=os.getenv('DEEPSEEK_API_KEY')\nDEBUG=os.getenv('DEBUG', '0')\n", encoding="utf-8")
    (root / "compose.yaml").write_text("services:\n  web:\n    environment:\n      REDIS_URL: ${REDIS_URL}\n", encoding="utf-8")
    st = manager.discover("demo", "production")
    names = {v["name"] for v in st["variables"]}
    assert {"SMTP_HOST", "SMTP_PORT", "SMTP_PASSWORD", "DEEPSEEK_API_KEY", "DEBUG", "REDIS_URL"}.issubset(names)
    smtp_pass = next(v for v in st["variables"] if v["name"] == "SMTP_PASSWORD")
    assert smtp_pass["secret"] is True
    assert smtp_pass["configured"] is False
    assert "SMTP_PASSWORD" in st["missing"]


def test_secret_values_never_return_from_status_and_are_redacted(tmp_path: Path):
    _cfg, _projects, manager, redactor = _manager(tmp_path)
    manager.require(["DEEPSEEK_API_KEY"], project="demo", secret_keys=["DEEPSEEK_API_KEY"], reason="AI provider")
    secret = "sk-test-super-secret-0123456789"
    st = manager.set_values([{"name":"DEEPSEEK_API_KEY","value":secret,"secret":True,"service":"deepseek"}], project="demo")
    encoded = json.dumps(st)
    assert secret not in encoded
    row = next(v for v in st["variables"] if v["name"] == "DEEPSEEK_API_KEY")
    assert row["configured"] is True and row["secret"] is True and row["value"] == ""
    assert redactor.redact("token=" + secret) == "token=[REDACTED]"
    vault_mode = (tmp_path / ".term5" / "secrets.json").stat().st_mode & 0o777
    assert vault_mode == 0o600


def test_materialized_env_is_protected_gitignored_and_masked_in_ui(tmp_path: Path):
    _cfg, projects, manager, _redactor = _manager(tmp_path)
    manager.set_values([
        {"name":"APP_URL","value":"https://example.test","secret":False,"service":"application"},
        {"name":"SMTP_PASSWORD","value":"p@ssw0rd-secret","secret":True,"service":"smtp"},
    ], project="demo")
    root = projects.path("demo")
    env = root / ".env"
    assert env.exists()
    text = env.read_text(encoding="utf-8")
    assert "APP_URL=https://example.test" in text
    assert "SMTP_PASSWORD=p@ssw0rd-secret" in text
    assert (env.stat().st_mode & 0o777) == 0o600
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore and ".env.*" in gitignore and "!.env.example" in gitignore
    ui = manager.read_env_file(".env", project="demo")
    assert "p@ssw0rd-secret" not in ui["content"]
    assert f"SMTP_PASSWORD={MASK}" in ui["content"]


def test_masked_env_file_roundtrip_preserves_existing_secret(tmp_path: Path):
    _cfg, projects, manager, _redactor = _manager(tmp_path)
    manager.set_values([{"name":"SMTP_PASSWORD","value":"original-secret","secret":True}], project="demo")
    masked = manager.read_env_file(".env", project="demo")["content"]
    edited = masked + "APP_NAME=Demo\n"
    manager.write_env_file(".env", edited, project="demo")
    actual = (projects.path("demo") / ".env").read_text(encoding="utf-8")
    assert "SMTP_PASSWORD=original-secret" in actual
    assert "APP_NAME=Demo" in actual


def test_configuration_require_creates_waiting_human_state_then_resume(tmp_path: Path):
    _cfg, _projects, manager, _redactor = _manager(tmp_path)
    st = manager.require(["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"], project="demo", secret_keys=["SMTP_PASSWORD"], reason="Send invitations", service="smtp")
    assert st["pending"]["status"] == "waiting"
    assert "SMTP_PASSWORD" in st["pending"]["missing"]
    manager.set_values([
        {"name":"SMTP_HOST","value":"smtp.example.com","secret":False,"service":"smtp"},
        {"name":"SMTP_USERNAME","value":"mailer@example.com","secret":False,"service":"smtp"},
        {"name":"SMTP_PASSWORD","value":"secret-value","secret":True,"service":"smtp"},
    ], project="demo")
    st2 = manager.status("demo", "production", discover=False)
    assert st2["pending"]["status"] == "ready"
    resumed = manager.resume_pending("demo", "production")
    assert resumed["reason"] == "Send invitations"
    assert manager.pending("demo", "production") is None


def test_smtp_connection_test_resolves_secret_without_returning_it(monkeypatch, tmp_path: Path):
    _cfg, _projects, manager, _redactor = _manager(tmp_path)
    manager.set_values([
        {"name":"SMTP_HOST","value":"smtp.example.com","secret":False,"service":"smtp"},
        {"name":"SMTP_PORT","value":"587","secret":False,"service":"smtp"},
        {"name":"SMTP_USERNAME","value":"mailer@example.com","secret":False,"service":"smtp"},
        {"name":"SMTP_PASSWORD","value":"top-secret","secret":True,"service":"smtp"},
    ], project="demo")
    calls = []
    class FakeSMTP:
        def __init__(self, host, port, timeout=None, **kwargs): calls.append(("connect",host,port,timeout))
        def ehlo(self): calls.append(("ehlo",))
        def starttls(self, context=None): calls.append(("starttls",))
        def login(self, username, password): calls.append(("login",username,password))
        def quit(self): calls.append(("quit",))
    monkeypatch.setattr("term5.configuration.manager.smtplib.SMTP", FakeSMTP)
    result = manager.test_connection("smtp", project="demo")
    assert result["ok"] is True and result["authenticated"] is True
    assert ("login", "mailer@example.com", "top-secret") in calls
    assert "top-secret" not in json.dumps(result)


def test_runtime_registers_configuration_tools_and_skill(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    runtime.projects.create(name="demo")
    names = set(runtime.tools.names())
    assert {"configuration_status", "configuration_discover", "configuration_require", "configuration_set", "configuration_test"}.issubset(names)
    assert runtime.security.capabilities.has("config.read")
    assert runtime.security.capabilities.has("config.write")
    resolved = runtime.skills.resolve("set up smtp credentials and environment variables for production")
    assert "configuration-secrets" in resolved.selected


def test_configuration_context_exposes_names_not_values(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    runtime.projects.create(name="demo")
    runtime.configuration.require(["SMTP_PASSWORD"], project="demo", secret_keys=["SMTP_PASSWORD"], reason="mailer")
    runtime.configuration.set_values([{"name":"SMTP_PASSWORD","value":"never-show-me","secret":True,"service":"smtp"}], project="demo")
    context = runtime.configuration.context_text()
    assert "SMTP_PASSWORD" in context
    assert "never-show-me" not in context
    assert "values inaccessible" in context


def test_production_readiness_blocks_missing_project_configuration(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    runtime.projects.create(name="demo")
    runtime.configuration.require(["SMTP_PASSWORD"], project="demo", secret_keys=["SMTP_PASSWORD"], reason="mailer")
    data = runtime.operations.production_readiness()
    assert data["configuration"]["enabled"] is True
    assert any("production configuration missing" in w for w in data["warnings"])


def test_configuration_ui_and_api_are_exposed(tmp_path: Path):
    for text in ["Configuration", "configVariables", "configServices", "Environment files", "Save configuration", "Save & resume"]:
        assert text in HTML or text in JS
    assert "/api/configuration" in JS
    assert "/api/configuration-save" in JS
    assert "/api/configuration-test" in JS
    assert "/api/environment-file" in JS
    assert "/api/configuration-resume" in JS
    assert JS.count("setTimeout(poll") == 1
    assert "setInterval(" not in JS

    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    runtime.projects.create(name="demo")
    runtime.configuration.require(["SMTP_HOST"], project="demo", reason="mailer", service="smtp")
    loop = asyncio.new_event_loop()
    web = LocalWebApp(runtime, loop, port=0)
    base = web.start()
    try:
        parsed = urlparse(base); token = parse_qs(parsed.query)["token"][0]
        with urllib.request.urlopen(f"http://127.0.0.1:{parsed.port}/api/configuration?token={token}", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["project"] == "demo"
        assert "SMTP_HOST" in data["missing"]
        body = json.dumps({"entries":[{"name":"SMTP_HOST","value":"smtp.example.com","secret":False,"service":"smtp"}]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{parsed.port}/api/configuration-save?token={token}", data=body, headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            saved = json.loads(resp.read().decode("utf-8"))
        assert "SMTP_HOST" not in saved["missing"]
    finally:
        web.close(); loop.close()
