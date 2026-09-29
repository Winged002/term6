from __future__ import annotations

import json
from typing import Any

from ..models import RiskLevel, ToolDefinition, ToolResult
from ..projects import ProjectManager
from .registry import ToolRegistry


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def s() -> dict[str, Any]: return {"type": "string"}
def b() -> dict[str, Any]: return {"type": "boolean"}
def i(a: int = 0, z: int = 1000) -> dict[str, Any]: return {"type": "integer", "minimum": a, "maximum": z}


def register_project_tools(registry: ToolRegistry, projects: ProjectManager, *, github_config=None) -> None:
    def github_enabled() -> bool:
        return github_config is None or bool(getattr(github_config, "enabled", True))

    async def project_list() -> ToolResult:
        data = projects.list(include_status=True)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "project_list", "List registered projects, active project, paths, app/deployment mappings and per-project Git status.",
        obj({}), project_list, {"project.read"}, RiskLevel.LOW, True,
    ))

    async def project_status(name: str = "") -> ToolResult:
        data = projects.status(name)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "project_status", "Inspect one project (or the active project), including Git branch/HEAD/dirty/ahead-behind state and Project Brain metadata.",
        obj({"name": s()}), project_status, {"project.read"}, RiskLevel.LOW, True,
    ))

    async def project_switch(name: str) -> ToolResult:
        data = projects.switch(name)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "project_switch", "Switch the active term_5 project. Later coding/Git work should default to this project until switched again.",
        obj({"name": s()}, ["name"]), project_switch, {"project.write"}, RiskLevel.MEDIUM, False, True,
    ))

    async def project_import(name: str, path: str, display_name: str = "", app_name: str = "", deployment_name: str = "", domain: str = "", make_active: bool = True) -> ToolResult:
        item = projects.import_existing(name=name, path=path, display_name=display_name, app_name=app_name,
                                        deployment_name=deployment_name, domain=domain, make_active=make_active)
        data = projects.status(item.name)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "project_import", "Adopt an existing workspace directory as an independent project without moving or deleting files. Detects existing Git metadata/remotes.",
        obj({"name": s(), "path": s(), "display_name": s(), "app_name": s(), "deployment_name": s(), "domain": s(), "make_active": b()}, ["name", "path"]),
        project_import, {"project.write"}, RiskLevel.HIGH, False, True,
    ))

    async def project_create(name: str, path: str = "", display_name: str = "", description: str = "", init_git: bool = True, initial_branch: str = "main", make_active: bool = True) -> ToolResult:
        item = projects.create(name=name, path=path, display_name=display_name, description=description,
                               init_git=init_git, initial_branch=initial_branch, make_active=make_active)
        data = projects.status(item.name)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "project_create", "Create a new project directory inside the workspace and optionally initialize its own Git repository. New projects default under projects/<name>.",
        obj({"name": s(), "path": s(), "display_name": s(), "description": s(), "init_git": b(), "initial_branch": s(), "make_active": b()}, ["name"]),
        project_create, {"project.write", "git.write"}, RiskLevel.HIGH, False, True,
    ))

    async def project_clone(name: str, remote_url: str, path: str = "", branch: str = "", make_active: bool = True) -> ToolResult:
        item = projects.clone(name=name, remote_url=remote_url, path=path, branch=branch, make_active=make_active)
        data = projects.status(item.name)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "project_clone", "Clone a Git repository into a new independent project under the workspace. Uses typed git clone argv and the server's configured Git credentials.",
        obj({"name": s(), "remote_url": s(), "path": s(), "branch": s(), "make_active": b()}, ["name", "remote_url"]),
        project_clone, {"project.write", "git.remote"}, RiskLevel.HIGH, False, True,
    ))

    async def project_update(name: str, display_name: str = "", description: str = "", app_name: str = "", deployment_name: str = "", domain: str = "") -> ToolResult:
        fields = {k: v for k, v in {"display_name": display_name, "description": description, "app_name": app_name,
                                     "deployment_name": deployment_name, "domain": domain}.items() if v != ""}
        item = projects.update(name, **fields)
        data = projects.status(item.name)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "project_update", "Update project identity and app/deployment/domain mappings. Does not move files or change Git history.",
        obj({"name": s(), "display_name": s(), "description": s(), "app_name": s(), "deployment_name": s(), "domain": s()}, ["name"]),
        project_update, {"project.write"}, RiskLevel.MEDIUM, False, True,
    ))

    async def project_unregister(name: str) -> ToolResult:
        ok = projects.remove(name)
        return ToolResult(ok, f"Project registration {'removed' if ok else 'not found'}: {name}", data={"removed": ok, "files_deleted": False})
    registry.register(ToolDefinition(
        "project_unregister", "Remove only the term_5 project registration. Source files, Git repository, Docker state and remote repository are preserved.",
        obj({"name": s()}, ["name"]), project_unregister, {"project.write"}, RiskLevel.HIGH, False, True,
    ))

    async def project_goal_add(text: str, project: str = "", status: str = "open") -> ToolResult:
        name = project or projects.registry.active_name
        goal = projects.registry.add_goal(name, text, status=status)
        return ToolResult(True, json.dumps(goal, indent=2), data=goal)
    registry.register(ToolDefinition(
        "project_goal_add", "Add a durable goal to the active/specified project's Project Brain.",
        obj({"text": s(), "project": s(), "status": {"type": "string", "enum": ["open", "done", "deferred"]}}, ["text"]),
        project_goal_add, {"project.write"}, RiskLevel.MEDIUM, False, True,
    ))

    async def project_decision_add(text: str, project: str = "") -> ToolResult:
        name = project or projects.registry.active_name
        decision = projects.registry.add_decision(name, text)
        return ToolResult(True, json.dumps(decision, indent=2), data=decision)
    registry.register(ToolDefinition(
        "project_decision_add", "Record a durable project decision/constraint that future work should respect unless the user explicitly changes it.",
        obj({"text": s(), "project": s()}, ["text"]), project_decision_add, {"project.write"}, RiskLevel.MEDIUM, False, True,
    ))

    async def project_backlog_add(title: str, project: str = "", priority: str = "p2", acceptance: str = "", source: str = "user") -> ToolResult:
        name = project or projects.registry.active_name
        task = projects.registry.add_backlog(name, title, priority=priority, acceptance=acceptance, source=source)
        return ToolResult(True, json.dumps(task, indent=2), data=task)
    registry.register(ToolDefinition(
        "project_backlog_add", "Add a durable prioritized project backlog item with optional acceptance criteria.",
        obj({"title": s(), "project": s(), "priority": {"type": "string", "enum": ["p0", "p1", "p2", "p3"]}, "acceptance": s(), "source": s()}, ["title"]),
        project_backlog_add, {"project.write"}, RiskLevel.MEDIUM, False, True,
    ))

    async def project_backlog_update(task_id: str, status: str, project: str = "") -> ToolResult:
        name = project or projects.registry.active_name
        task = projects.registry.update_backlog(name, task_id, status=status)
        return ToolResult(True, json.dumps(task, indent=2), data=task)
    registry.register(ToolDefinition(
        "project_backlog_update", "Update the lifecycle state of a Project Brain backlog item.",
        obj({"task_id": s(), "status": {"type": "string", "enum": ["open", "in_progress", "blocked", "done", "deferred"]}, "project": s()}, ["task_id", "status"]),
        project_backlog_update, {"project.write"}, RiskLevel.MEDIUM, False, True,
    ))

    async def github_status() -> ToolResult:
        if not github_enabled():
            return ToolResult(False, "GitHub provider integration is disabled by configuration.")
        data = projects.github.status()
        return ToolResult(bool(data.get("available")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "github_status", "Inspect GitHub CLI availability/authentication and current login without exposing credentials.",
        obj({}), github_status, {"git.remote"}, RiskLevel.LOW, True,
    ))

    async def github_repo_list(owner: str = "", limit: int = 50) -> ToolResult:
        if not github_enabled():
            return ToolResult(False, "GitHub provider integration is disabled by configuration.")
        data = projects.github.list_repos(owner or (getattr(github_config, "owner", "") if github_config else ""), limit=limit)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "github_repo_list", "List repositories visible to the authenticated GitHub CLI account/owner.",
        obj({"owner": s(), "limit": i(1, 200)}), github_repo_list, {"git.remote"}, RiskLevel.LOW, True,
    ))

    async def github_repo_view(repo: str) -> ToolResult:
        if not github_enabled():
            return ToolResult(False, "GitHub provider integration is disabled by configuration.")
        data = projects.github.view_repo(repo)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "github_repo_view", "Inspect one GitHub repository using authenticated gh CLI metadata.",
        obj({"repo": s()}, ["repo"]), github_repo_view, {"git.remote"}, RiskLevel.LOW, True,
    ))

    async def github_repo_create(project: str = "", repo: str = "", private: bool | None = None, description: str = "", push: bool = True) -> ToolResult:
        if not github_enabled():
            return ToolResult(False, "GitHub provider integration is disabled by configuration.")
        item = projects._item(project)
        status = projects.github.status()
        owner = (getattr(github_config, "owner", "") if github_config else "") or str(status.get("login") or "")
        slug = repo.strip() or (f"{owner}/{item.name}" if owner else "")
        if slug and "/" not in slug and owner:
            slug = f"{owner}/{slug}"
        if not slug:
            return ToolResult(False, "Cannot determine GitHub repository owner; set github.owner or authenticate gh.")
        priv = bool(getattr(github_config, "default_private", True) if private is None else private)
        gm = projects.git(item.name)
        if not gm.available().get("repository"):
            r = gm.init(item.default_branch or "main")
            if not r.ok:
                return ToolResult(False, r.stdout or "git init failed")
        data = projects.github.create_repo(slug, private=priv, description=description or item.description,
                                           source=projects.path(item.name), remote="origin", push=push)
        if data.get("ok"):
            view = data.get("view") or {}
            item.provider = "github"
            item.provider_repo = slug
            item.remote_url = str(view.get("sshUrl") or f"git@github.com:{slug}.git")
            projects.registry.upsert(item)
            projects.sync_metadata(item.name)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition(
        "github_repo_create", "Create a GitHub repository for the active/specified project through typed gh CLI. Can attach origin and push existing commits. Requires the separate provider-write gate.",
        obj({"project": s(), "repo": s(), "private": b(), "description": s(), "push": b()}),
        github_repo_create, {"git.write", "git.remote", "git.provider.write"}, RiskLevel.CRITICAL, False, True,
    ))
