from __future__ import annotations

import json
from typing import Any

from ..models import RiskLevel, ToolDefinition, ToolResult
from ..ops import OperationsRuntime
from .registry import ToolRegistry


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def s() -> dict[str, Any]: return {"type": "string"}
def b() -> dict[str, Any]: return {"type": "boolean"}
def i(a: int = 0, z: int = 65535) -> dict[str, Any]: return {"type": "integer", "minimum": a, "maximum": z}
def arr(max_items: int = 128) -> dict[str, Any]: return {"type": "array", "maxItems": max_items, "items": {"type": "string"}}


def register_operations_tools(registry: ToolRegistry, ops: OperationsRuntime) -> None:
    async def ops_status() -> ToolResult:
        data = ops.status()
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("ops_status", "Inspect Git, Nginx, Certbot and deployment runtime availability/readiness without changing the host.", obj({}), ops_status, {"ops.read"}, RiskLevel.LOW, True))

    async def server_status() -> ToolResult:
        data = ops.server.status()
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("server_status", "Inspect host identity, uptime, load, memory, disk pressure and server-management capability without changing the host.", obj({}), server_status, {"ops.server.read"}, RiskLevel.LOW, True))

    async def server_ports(lines: int = 300) -> ToolResult:
        data = ops.server.ports(lines=lines)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("server_ports", "Inspect bounded listening TCP/UDP sockets using typed ss argv. No shell is exposed.", obj({"lines": i(1,2000)}), server_ports, {"ops.server.read"}, RiskLevel.LOW, True))

    async def server_service_status(name: str) -> ToolResult:
        data = ops.server.service_status(name)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("server_service_status", "Inspect an allowlisted systemd service such as Docker or Nginx.", obj({"name": s()}, ["name"]), server_service_status, {"ops.server.read"}, RiskLevel.LOW, True))

    async def server_journal(name: str, lines: int = 200) -> ToolResult:
        data = ops.server.journal(name, lines=lines)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("server_journal", "Read bounded journal entries for an allowlisted systemd service.", obj({"name": s(), "lines": i(1,5000)}, ["name"]), server_journal, {"ops.server.read"}, RiskLevel.LOW, True))

    async def server_service_control(name: str, action: str) -> ToolResult:
        data = ops.server.service_control(name, action)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("server_service_control", "Start, stop, restart, or reload only an explicitly allowlisted systemd service. Requires the independent server-write gate.", obj({"name": s(), "action": {"type":"string","enum":["start","stop","restart","reload"]}}, ["name","action"]), server_service_control, {"ops.server.write"}, RiskLevel.CRITICAL, False, True))

    async def server_audit() -> ToolResult:
        data = ops.server.audit()
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("server_audit", "Run a compact read-only host audit: load, memory, disk, allowlisted services and listening sockets.", obj({}), server_audit, {"ops.server.read"}, RiskLevel.LOW, True))

    async def production_readiness() -> ToolResult:
        data = ops.production_readiness()
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("production_readiness", "Aggregate host, Docker applications, Git, Nginx, Certbot and registered deployment readiness before production work.", obj({}), production_readiness, {"ops.read", "ops.server.read"}, RiskLevel.LOW, True))

    async def git_head(project: str = "") -> ToolResult:
        gm = ops.git_manager(project)
        data = {"project": project or (ops.projects.registry.active_name if ops.projects is not None else "workspace"),
                "head": gm.head(), "branch": gm.branch(), "status": gm.status(), "tracking": gm.tracking()}
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("git_head", "Show Git HEAD, branch, clean/dirty and upstream ahead/behind state for the active or specified project.", obj({"project":s()}), git_head, {"git.read"}, RiskLevel.LOW, True))

    async def git_diff_staged(path: str = "", project: str = "") -> ToolResult:
        r = ops.git_manager(project).diff(staged=True, path=path)
        return ToolResult(r.ok, r.stdout or "(no staged diff)")
    registry.register(ToolDefinition("git_diff_staged", "Review the staged Git diff for the active/specified project, optionally for one explicit path.", obj({"path":s(),"project":s()}), git_diff_staged, {"git.read"}, RiskLevel.LOW, True))

    async def git_init(initial_branch: str = "main", project: str = "") -> ToolResult:
        r = ops.git_manager(project).init(initial_branch)
        return ToolResult(r.ok, r.stdout or "Git repository initialized.")
    registry.register(ToolDefinition("git_init", "Initialize the active/specified project directory as its own Git repository.", obj({"initial_branch": s(),"project":s()}), git_init, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_stage(paths: list[str], project: str = "") -> ToolResult:
        r = ops.git_manager(project).add(paths)
        return ToolResult(r.ok, r.stdout or f"Staged {len(paths)} path(s).")
    registry.register(ToolDefinition("git_stage", "Stage explicit paths in the active/specified project repository; no shell or wildcard expansion.", obj({"paths": arr(),"project":s()}, ["paths"]), git_stage, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_commit(message: str, project: str = "") -> ToolResult:
        gm = ops.git_manager(project)
        r = gm.commit(message)
        return ToolResult(r.ok, r.stdout or "Commit created.", data={"head": gm.head(), "project": project or (ops.projects.registry.active_name if ops.projects is not None else "workspace")})
    registry.register(ToolDefinition("git_commit", "Create a Git commit from already-staged changes in the active/specified project.", obj({"message": s(),"project":s()}, ["message"]), git_commit, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_branch_create(name: str, checkout: bool = True, project: str = "") -> ToolResult:
        r = ops.git_manager(project).create_branch(name, checkout=checkout)
        return ToolResult(r.ok, r.stdout or f"Created branch {name}.")
    registry.register(ToolDefinition("git_branch_create", "Create a validated Git branch in the active/specified project and optionally switch to it.", obj({"name": s(), "checkout": b(),"project":s()}, ["name"]), git_branch_create, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_switch(ref: str, project: str = "") -> ToolResult:
        r = ops.git_manager(project).switch(ref)
        return ToolResult(r.ok, r.stdout or f"Switched to {ref}.")
    registry.register(ToolDefinition("git_switch", "Switch the active/specified project repository to an existing validated branch/reference.", obj({"ref": s(),"project":s()}, ["ref"]), git_switch, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_remotes(project: str = "") -> ToolResult:
        r = ops.git_manager(project).remotes(); return ToolResult(r.ok, r.stdout or "(no remotes)")
    registry.register(ToolDefinition("git_remotes", "Show configured Git remotes for the active/specified project without network access.", obj({"project":s()}), git_remotes, {"git.read"}, RiskLevel.LOW, True))

    async def git_remote_add(url: str, name: str = "origin", project: str = "") -> ToolResult:
        r = ops.git_manager(project).add_remote(name, url); return ToolResult(r.ok, r.stdout or f"Added remote {name}.")
    registry.register(ToolDefinition("git_remote_add", "Add a validated Git remote URL to the active/specified project.", obj({"url":s(),"name":s(),"project":s()}, ["url"]), git_remote_add, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_remote_set_url(url: str, name: str = "origin", project: str = "") -> ToolResult:
        r = ops.git_manager(project).set_remote_url(name, url); return ToolResult(r.ok, r.stdout or f"Updated remote {name}.")
    registry.register(ToolDefinition("git_remote_set_url", "Replace a validated Git remote URL for the active/specified project.", obj({"url":s(),"name":s(),"project":s()}, ["url"]), git_remote_set_url, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_fetch(remote: str = "origin", project: str = "") -> ToolResult:
        r = ops.git_manager(project).fetch(remote); return ToolResult(r.ok, r.stdout or f"Fetched {remote}.")
    registry.register(ToolDefinition("git_fetch", "Fetch/prune one validated remote for the active/specified project.", obj({"remote":s(),"project":s()}), git_fetch, {"git.remote"}, RiskLevel.MEDIUM, False, True))

    async def git_pull_ff(remote: str = "origin", branch: str = "", project: str = "") -> ToolResult:
        r = ops.git_manager(project).pull_ff(remote, branch); return ToolResult(r.ok, r.stdout or "Fast-forward pull completed.")
    registry.register(ToolDefinition("git_pull_ff", "Fast-forward-only pull for the active/specified project; never creates an automatic merge commit.", obj({"remote":s(),"branch":s(),"project":s()}), git_pull_ff, {"git.remote", "git.write"}, RiskLevel.HIGH, False, True))

    async def git_push(remote: str = "origin", refspec: str = "", set_upstream: bool = False, project: str = "") -> ToolResult:
        r = ops.git_manager(project).push(remote, refspec, set_upstream=set_upstream); return ToolResult(r.ok, r.stdout or f"Pushed to {remote}.")
    registry.register(ToolDefinition("git_push", "Push a validated branch/refspec from the active/specified project. Use set_upstream=true for a project's first branch push. Separately gated from local Git writes.", obj({"remote":s(),"refspec":s(),"set_upstream":b(),"project":s()}), git_push, {"git.remote"}, RiskLevel.HIGH, False, True))

    async def git_tag(name: str, message: str = "", project: str = "") -> ToolResult:
        r = ops.git_manager(project).tag(name, message)
        return ToolResult(r.ok, r.stdout or f"Created tag {name}.")
    registry.register(ToolDefinition("git_tag", "Create a lightweight or annotated release tag in the active/specified project.", obj({"name": s(), "message": s(),"project":s()}, ["name"]), git_tag, {"git.write"}, RiskLevel.HIGH, False, True))

    async def git_restore(paths: list[str], project: str = "") -> ToolResult:
        r = ops.git_manager(project).restore(paths)
        return ToolResult(r.ok, r.stdout or "Restored requested paths.")
    registry.register(ToolDefinition("git_restore", "Discard unstaged changes for explicit paths in the active/specified project.", obj({"paths": arr(),"project":s()}, ["paths"]), git_restore, {"git.write"}, RiskLevel.CRITICAL, False, True))

    async def git_revert(commit: str, project: str = "") -> ToolResult:
        gm = ops.git_manager(project)
        r = gm.revert(commit)
        return ToolResult(r.ok, r.stdout or f"Reverted {commit}.", data={"head": gm.head()})
    registry.register(ToolDefinition("git_revert", "Create a new commit reverting one commit in the active/specified project.", obj({"commit": s(),"project":s()}, ["commit"]), git_revert, {"git.write"}, RiskLevel.CRITICAL, False, True))

    async def nginx_status() -> ToolResult:
        data = ops.nginx.status(); data["managed_sites"] = ops.nginx.managed_sites()
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("nginx_status", "Inspect Nginx availability, config validity and term_5-managed sites.", obj({}), nginx_status, {"ops.nginx.read"}, RiskLevel.LOW, True))

    async def nginx_service_status() -> ToolResult:
        data = ops.nginx.service_status(); return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("nginx_service_status", "Inspect whether the host Nginx service/process is active.", obj({}), nginx_service_status, {"ops.nginx.read"}, RiskLevel.LOW, True))

    async def nginx_service_control(action: str) -> ToolResult:
        data = ops.nginx.service_control(action); return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("nginx_service_control", "Start, reload, or restart the host Nginx service using typed systemctl/nginx argv only.", obj({"action":{"type":"string","enum":["start","reload","restart"]}}, ["action"]), nginx_service_control, {"ops.nginx.write"}, RiskLevel.CRITICAL, False, True))

    async def nginx_site_plan(name: str, domain: str, upstream_port: int, websocket: bool = True, max_body_mb: int = 64) -> ToolResult:
        data = ops.nginx.plan(name=name, domain=domain, upstream_port=upstream_port, websocket=websocket, max_body_mb=max_body_mb)
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("nginx_site_plan", "Generate a loopback reverse-proxy Nginx server block without writing /etc/nginx.", obj({"name": s(), "domain": s(), "upstream_port": i(1,65535), "websocket": b(), "max_body_mb": i(1,4096)}, ["name","domain","upstream_port"]), nginx_site_plan, {"ops.nginx.read"}, RiskLevel.LOW, True))

    async def nginx_site_install(name: str, domain: str, upstream_port: int, websocket: bool = True, max_body_mb: int = 64) -> ToolResult:
        r = ops.nginx.install(name=name, domain=domain, upstream_port=upstream_port, websocket=websocket, max_body_mb=max_body_mb, reload=True)
        data = {"ok": r.ok, "output": r.output, "site_path": r.site_path, "enabled_path": r.enabled_path, "rolled_back": r.rolled_back}
        return ToolResult(r.ok, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("nginx_site_install", "Install a term_5-managed Nginx reverse-proxy site, run nginx -t, rollback invalid config, and reload only after validation.", obj({"name": s(), "domain": s(), "upstream_port": i(1,65535), "websocket": b(), "max_body_mb": i(1,4096)}, ["name","domain","upstream_port"]), nginx_site_install, {"ops.nginx.write"}, RiskLevel.CRITICAL, False, True))

    async def nginx_site_remove(name: str) -> ToolResult:
        data = ops.nginx.remove(name, reload=True)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("nginx_site_remove", "Remove only a term_5-managed Nginx site, validate remaining config, and reload. Refuses unmanaged configs.", obj({"name":s()}, ["name"]), nginx_site_remove, {"ops.nginx.write"}, RiskLevel.CRITICAL, False, True))

    async def nginx_logs(kind: str = "error", lines: int = 200) -> ToolResult:
        if kind not in {"error", "access"}:
            return ToolResult(False, "kind must be error or access")
        return ToolResult(True, ops.nginx.logs(kind=kind, lines=lines))
    registry.register(ToolDefinition("nginx_logs", "Read bounded host Nginx access/error logs.", obj({"kind": {"type":"string","enum":["error","access"]}, "lines": i(1,5000)}), nginx_logs, {"ops.nginx.read"}, RiskLevel.LOW, True))

    async def tls_status(domain: str = "") -> ToolResult:
        data = ops.tls.status(domain)
        return ToolResult(bool(data.get("available")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("tls_status", "Inspect Certbot and existing certificate state.", obj({"domain": s()}), tls_status, {"ops.tls.read"}, RiskLevel.LOW, True))

    async def tls_issue(domain: str, email: str, redirect: bool = True, staging: bool = False) -> ToolResult:
        data = ops.tls.issue(domain=domain, email=email, redirect=redirect, staging=staging)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("tls_issue", "Issue/configure a Let's Encrypt certificate through Certbot's Nginx plugin. Requires explicit TLS-write capability.", obj({"domain": s(), "email": s(), "redirect": b(), "staging": b()}, ["domain","email"]), tls_issue, {"ops.tls.write"}, RiskLevel.CRITICAL, False, True))

    async def tls_renew_dry_run(domain: str = "") -> ToolResult:
        data = ops.tls.renew_dry_run(domain)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("tls_renew_dry_run", "Run Certbot renewal dry-run without replacing a live certificate.", obj({"domain": s()}), tls_renew_dry_run, {"ops.tls.read"}, RiskLevel.MEDIUM, True))

    async def deployment_register(name: str, app_name: str, domain: str = "", upstream_port: int = 0, tls: bool = False,
                                  migration_kind: str = "none", backup_kind: str = "none", backup_service: str = "",
                                  environment: str = "production") -> ToolResult:
        item = ops.register(name=name, app_name=app_name, domain=domain, upstream_port=upstream_port, tls=tls,
                            migration_kind=migration_kind, backup_kind=backup_kind, backup_service=backup_service, environment=environment)
        data = {"name": item.name, "app_name": item.app_name, "project_name": item.project_name, "domain": item.domain, "upstream_port": item.upstream_port,
                "tls": item.tls, "migration_kind": item.migration_kind, "backup_kind": item.backup_kind,
                "environment": item.environment, "release_status": item.release_status}
        return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("deployment_register", "Register a durable environment deployment mapping from a term_5 app to development/staging/production, domain/upstream/TLS/migration policy.", obj({"name":s(),"app_name":s(),"domain":s(),"upstream_port":i(0,65535),"tls":b(),"migration_kind":{"type":"string","enum":["none","flask-db","django"]},"backup_kind":{"type":"string","enum":["none","mongodb","postgres"]},"backup_service":s(),"environment":{"type":"string","enum":["development","staging","production"]}}, ["name","app_name"]), deployment_register, {"ops.deploy"}, RiskLevel.HIGH, False, True))

    async def deployment_list() -> ToolResult:
        data = ops.list(); return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("deployment_list", "List durable deployment records and current/previous release commits.", obj({}), deployment_list, {"ops.read"}, RiskLevel.LOW, True))

    async def deployment_status(name: str) -> ToolResult:
        data = ops.deployment_status(name); return ToolResult(True, json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("deployment_status", "Inspect one deployment's app health, release commits, DNS, TLS state and recent release history.", obj({"name":s()}, ["name"]), deployment_status, {"ops.read"}, RiskLevel.LOW, True))

    async def deployment_backup(name: str) -> ToolResult:
        data = ops.backup(name); return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("deployment_backup", "Create the configured MongoDB/PostgreSQL pre-migration backup for a deployment without changing code or routing.", obj({"name":s()}, ["name"]), deployment_backup, {"ops.deploy"}, RiskLevel.HIGH, False, True))

    async def deployment_dns(domain: str) -> ToolResult:
        data = ops.dns_status(domain); return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("deployment_dns", "Resolve a deployment hostname before Nginx/TLS work.", obj({"domain":s()}, ["domain"]), deployment_dns, {"ops.read"}, RiskLevel.LOW, True))

    async def deployment_deploy(name: str, build: bool = True, configure_nginx: bool = True, issue_tls: bool = False,
                                email: str = "", staging_tls: bool = False, auto_rollback: bool = False) -> ToolResult:
        data = ops.deploy(name, build=build, configure_nginx=configure_nginx, issue_tls=issue_tls, email=email,
                          staging_tls=staging_tls, auto_rollback=auto_rollback)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("deployment_deploy", "Health-gated deployment using the deployment project's Git repository: require clean Git, build/start app, optional known migration, validated Nginx install, optional Certbot TLS, public probe, then record release. Can rollback to previous recorded Git release when enabled.", obj({"name":s(),"build":b(),"configure_nginx":b(),"issue_tls":b(),"email":s(),"staging_tls":b(),"auto_rollback":b()}, ["name"]), deployment_deploy, {"ops.deploy"}, RiskLevel.CRITICAL, False, True))

    async def deployment_rollback(name: str, target: str = "") -> ToolResult:
        data = ops.rollback(name, target=target)
        return ToolResult(bool(data.get("ok")), json.dumps(data, indent=2), data=data)
    registry.register(ToolDefinition("deployment_rollback", "Rollback a registered deployment to its previous (or explicit) Git commit using a hard reset only when the working tree is clean, then rebuild/start and health-check.", obj({"name":s(),"target":s()}, ["name"]), deployment_rollback, {"ops.deploy", "git.write"}, RiskLevel.CRITICAL, False, True))
