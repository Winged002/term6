from __future__ import annotations

import json
import subprocess
from pathlib import Path

from term5.apps.manager import AppRuntime
from term5.apps.registry import AppManifest
from term5.config import load_config
from term5.ops.github import GitHubManager
from term5.ops.git import GitManager
from term5.ops.manager import OperationsRuntime
from term5.projects.manager import ProjectManager
from term5.projects.registry import ProjectRegistry
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.state.transactions import TransactionManager
from term5.ui.web_assets import HTML, JS


def _projects(tmp_path: Path) -> tuple[ProjectManager, object]:
    cfg = load_config(tmp_path)
    guard = PathGuard(tmp_path)
    manager = ProjectManager(tmp_path, tmp_path / ".term5", guard, config=cfg.projects)
    return manager, cfg


def test_project_registry_roundtrip_active_and_brain(tmp_path: Path):
    projects, _ = _projects(tmp_path)
    (tmp_path / "existing").mkdir()
    item = projects.import_existing(name="alpha", path="existing", make_active=True)
    goal = projects.registry.add_goal(item.name, "Ship the first production release")
    decision = projects.registry.add_decision(item.name, "Keep Flask/Jinja for this application")
    task = projects.registry.add_backlog(item.name, "Add password recovery", priority="p1", acceptance="Reset link expires")

    again = ProjectRegistry(tmp_path / ".term5" / "projects.json")
    assert again.active_name == "alpha"
    loaded = again.get("alpha")
    assert loaded is not None
    assert loaded.goals[-1]["id"] == goal["id"]
    assert loaded.decisions[-1]["id"] == decision["id"]
    assert loaded.backlog[-1]["id"] == task["id"]
    assert loaded.backlog[-1]["priority"] == "p1"


def test_projects_are_independent_git_repositories(tmp_path: Path):
    projects, _ = _projects(tmp_path)
    a = projects.create(name="alpha")
    b = projects.create(name="beta")
    assert (tmp_path / a.path / ".git").is_dir()
    assert (tmp_path / b.path / ".git").is_dir()
    assert not (tmp_path / ".git").exists()
    assert projects.git("alpha").available()["repository"] is True
    assert projects.git("beta").available()["repository"] is True


def test_active_project_context_contains_git_and_constraints(tmp_path: Path):
    projects, _ = _projects(tmp_path)
    projects.create(name="koalacare")
    projects.registry.add_goal("koalacare", "Prepare for real users")
    projects.registry.add_decision("koalacare", "Retain Flask/Jinja")
    projects.registry.add_backlog("koalacare", "Improve documents", priority="p1")
    text = projects.context_text()
    assert "Project: koalacare (koalacare)" in text
    assert "Retain Flask/Jinja" in text
    assert "P1 Improve documents" in text
    assert "prefer this project's path/repository" in text


def test_git_clone_uses_typed_argv_and_no_shell(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/bin/{name}")
    target = tmp_path / "projects" / "alpha"
    target.parent.mkdir(parents=True)
    result = GitManager.clone("git@github.com:Winged002/alpha.git", target, branch="main", runner=fake_run)
    assert result.ok is True
    argv, kwargs = calls[-1]
    assert argv == ["git", "clone", "--origin", "origin", "--branch", "main", "git@github.com:Winged002/alpha.git", str(target)]
    assert kwargs["shell"] is False


def test_git_tracking_parses_upstream_ahead_behind(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        args = argv[1:]
        if args == ["branch", "--show-current"]:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return subprocess.CompletedProcess(argv, 0, "origin/main\n", "")
        if args == ["rev-list", "--left-right", "--count", "origin/main...HEAD"]:
            return subprocess.CompletedProcess(argv, 0, "2\t3\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/bin/{name}")
    gm = GitManager(tmp_path, runner=fake_run)
    assert gm.tracking() == {"branch": "main", "upstream": "origin/main", "ahead": 3, "behind": 2}


def test_github_manager_uses_typed_gh_argv(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        args = argv[1:]
        if args == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "gh version 2.80.0\n", "")
        if args[:3] == ["auth", "status", "--hostname"]:
            return subprocess.CompletedProcess(argv, 0, "logged in\n", "")
        if args == ["api", "user", "--jq", ".login"]:
            return subprocess.CompletedProcess(argv, 0, "Winged002\n", "")
        if args[:2] == ["repo", "list"]:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        if args[:2] == ["repo", "create"]:
            return subprocess.CompletedProcess(argv, 0, "created", "")
        if args[:2] == ["repo", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"nameWithOwner":"Winged002/demo","sshUrl":"git@github.com:Winged002/demo.git","isPrivate":True}), "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")

    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/bin/{name}")
    gh = GitHubManager(runner=fake_run)
    st = gh.status()
    assert st["authenticated"] is True and st["login"] == "Winged002"
    made = gh.create_repo("Winged002/demo", private=True, description="Demo", source=tmp_path, remote="origin", push=True)
    assert made["ok"] is True
    create_argv, create_kwargs = next((argv, kw) for argv, kw in calls if argv[1:3] == ["repo", "create"])
    assert create_argv[:4] == ["gh", "repo", "create", "Winged002/demo"]
    assert "--private" in create_argv and "--source" in create_argv and "--push" in create_argv
    assert create_kwargs["shell"] is False


def test_github_slug_inference_handles_ssh_and_https():
    assert GitHubManager.slug_from_remote("git@github.com:Winged002/koalacare.git") == "Winged002/koalacare"
    assert GitHubManager.slug_from_remote("https://github.com/Winged002/koalacare.git") == "Winged002/koalacare"


def test_existing_app_is_adopted_in_place_and_deployment_uses_project_repo(tmp_path: Path):
    cfg = load_config(tmp_path)
    guard = PathGuard(tmp_path)
    tx = TransactionManager(tmp_path / ".term5", guard)
    apps = AppRuntime(tmp_path, tmp_path / ".term5", guard, tx)
    app_dir = tmp_path / "koalacare"
    app_dir.mkdir()
    (app_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    assert GitManager(app_dir).init("main").ok
    apps.registry.add(AppManifest(name="koalacare", path="koalacare", url="http://127.0.0.1:8792"))

    projects = ProjectManager(tmp_path, tmp_path / ".term5", guard, config=cfg.projects)
    assert projects.adopt_apps(apps) == 1
    assert projects.registry.get("koalacare").path == "koalacare"

    ops = OperationsRuntime(tmp_path, tmp_path / ".term5", apps, config=cfg.operations, security_config=cfg.security, projects=projects)
    dep = ops.register(name="koalacare-prod", app_name="koalacare", domain="my.koalacare.app", upstream_port=8792)
    assert dep.project_name == "koalacare"
    assert ops._git_for_deployment(dep).root == app_dir


def test_runtime_registers_project_and_provider_tools_with_separate_gate(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    names = set(runtime.tools.names())
    required = {
        "project_list", "project_status", "project_switch", "project_import", "project_create", "project_clone",
        "project_goal_add", "project_decision_add", "project_backlog_add", "project_backlog_update",
        "github_status", "github_repo_list", "github_repo_view", "github_repo_create",
        "git_remote_add", "git_remote_set_url",
    }
    assert required.issubset(names)
    assert runtime.security.capabilities.has("project.read")
    assert runtime.security.capabilities.has("project.write")
    assert not runtime.security.capabilities.has("git.provider.write")

    cfg2 = load_config(tmp_path / "other")
    cfg2.security.allow_git_provider_write = True
    runtime2 = AgentRuntime(cfg2)
    assert runtime2.security.capabilities.has("git.provider.write")


def test_project_repository_skill_resolves(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    resolved = runtime.skills.resolve("clone this github repository and switch the project then push it")
    assert "project-repository" in resolved.selected


def test_project_workbench_is_exposed_without_extra_poller():
    for text in ["Project", "Project Brain", "Goals", "Decisions", "Backlog", "GitHub"]:
        assert text in HTML
    assert "/api/projects" in JS
    assert "/api/project-switch" in JS
    assert "activeProject" in JS
    assert JS.count("setTimeout(poll") == 1
    assert "setInterval(" not in JS


def test_git_first_push_can_set_upstream(monkeypatch, tmp_path: Path):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")
    monkeypatch.setattr("term5.ops.command.shutil.which", lambda name: f"/usr/bin/{name}")
    gm = GitManager(tmp_path, runner=fake_run)
    result = gm.push("origin", "main", set_upstream=True)
    assert result.ok
    assert calls[-1][0] == ["git", "push", "--set-upstream", "origin", "main"]
    assert calls[-1][1]["shell"] is False


def test_github_create_requires_all_three_git_write_gates(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    tool = runtime.tools.get("github_repo_create")
    assert tool is not None
    assert tool.capabilities == {"git.write", "git.remote", "git.provider.write"}


def test_project_api_returns_active_project(tmp_path: Path):
    import asyncio
    import urllib.request
    from urllib.parse import urlparse, parse_qs
    from term5.ui.web import LocalWebApp

    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    runtime.projects.create(name="alpha")
    loop = asyncio.new_event_loop()
    web = LocalWebApp(runtime, loop, port=0)
    base = web.start()
    try:
        parsed = urlparse(base)
        token = parse_qs(parsed.query)["token"][0]
        req = urllib.request.Request(f"http://127.0.0.1:{parsed.port}/api/projects?token={token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["active"] == "alpha"
        assert data["projects"][0]["name"] == "alpha"
    finally:
        web.close()
        loop.close()
