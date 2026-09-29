from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..ops.git import GitManager, GitOpsError
from ..ops.github import GitHubManager
from ..security.paths import PathGuard
from .registry import ProjectManifest, ProjectRegistry

_REPO_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ProjectError(RuntimeError):
    pass


class ProjectManager:
    """Persistent project registry and multi-repository Git coordinator.

    The workspace remains the safety boundary, but each project owns an
    independent source root and Git repository. Existing application folders
    can be adopted in place; new/cloned projects default under projects/.
    """

    def __init__(self, root: Path, state_dir: Path, guard: PathGuard, *, config, timeout_s: int = 120, runner=None) -> None:
        self.root = root
        self.state_dir = state_dir
        self.guard = guard
        self.config = config
        self.registry = ProjectRegistry(state_dir / "projects.json")
        self.base_dir = self.guard.resolve(config.base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.runner = runner
        self.github = GitHubManager(timeout_s=timeout_s, runner=runner)

    def _ensure_capacity(self, name: str) -> None:
        if self.registry.get(name) is None and len(self.registry.list()) >= int(self.config.max_projects):
            raise ProjectError(f"project registry limit reached ({self.config.max_projects})")

    def _item(self, name: str = "") -> ProjectManifest:
        target = name or self.registry.active_name
        if not target:
            raise ProjectError("no active project; import, create, clone, or switch a project first")
        item = self.registry.get(target)
        if item is None:
            raise ProjectError(f"unknown project: {target}")
        return item

    def path(self, name: str = "") -> Path:
        item = self._item(name)
        p = self.guard.resolve(item.path)
        if not p.is_dir():
            raise ProjectError(f"project directory does not exist: {item.path}")
        return p

    def git(self, name: str = "") -> GitManager:
        return GitManager(self.path(name), timeout_s=self.timeout_s, runner=self.runner)

    def active(self) -> ProjectManifest | None:
        return self.registry.get(self.registry.active_name) if self.registry.active_name else None

    def switch(self, name: str) -> dict[str, Any]:
        item = self.registry.set_active(name)
        return self.status(item.name)

    def import_existing(self, *, name: str, path: str, display_name: str = "", app_name: str = "", deployment_name: str = "", domain: str = "", make_active: bool = True) -> ProjectManifest:
        self._ensure_capacity(name)
        p = self.guard.resolve(path)
        if not p.is_dir():
            raise ProjectError(f"project path must be an existing workspace directory: {path}")
        rel = p.relative_to(self.root).as_posix() or "."
        item = self.registry.get(name) or ProjectManifest(name=name, path=rel)
        item.path = rel
        item.display_name = display_name.strip() or item.display_name or name
        item.app_name = app_name.strip() or item.app_name
        item.deployment_name = deployment_name.strip() or item.deployment_name
        item.domain = domain.strip() or item.domain
        self._hydrate_git_metadata(item)
        return self.registry.upsert(item, make_active=make_active)

    def create(self, *, name: str, path: str = "", display_name: str = "", description: str = "", init_git: bool = True, initial_branch: str = "main", make_active: bool = True) -> ProjectManifest:
        self._ensure_capacity(name)
        target = self.guard.resolve(path or (Path(self.config.base_dir) / name).as_posix())
        if target.exists() and any(target.iterdir()):
            raise ProjectError(f"project destination is not empty: {target.relative_to(self.root)}")
        target.mkdir(parents=True, exist_ok=True)
        rel = target.relative_to(self.root).as_posix()
        item = ProjectManifest(name=name, path=rel, display_name=display_name or name, description=description)
        if init_git:
            r = GitManager(target, timeout_s=self.timeout_s, runner=self.runner).init(initial_branch)
            if not r.ok:
                raise ProjectError(r.stdout or "git init failed")
            item.default_branch = initial_branch
        self.registry.upsert(item, make_active=make_active)
        return item

    def clone(self, *, name: str, remote_url: str, path: str = "", branch: str = "", make_active: bool = True) -> ProjectManifest:
        self._ensure_capacity(name)
        target = self.guard.resolve(path or (Path(self.config.base_dir) / name).as_posix())
        if target.exists() and any(target.iterdir()):
            raise ProjectError(f"clone destination is not empty: {target.relative_to(self.root)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        r = GitManager.clone(remote_url, target, branch=branch, timeout_s=max(self.timeout_s, 300), runner=self.runner)
        if not r.ok:
            raise ProjectError(r.stdout or "git clone failed")
        rel = target.relative_to(self.root).as_posix()
        item = ProjectManifest(name=name, path=rel, remote_url=remote_url, default_branch=branch or "main")
        self._hydrate_git_metadata(item)
        self.registry.upsert(item, make_active=make_active)
        return item

    def remove(self, name: str) -> bool:
        """Remove only project registration; source files are deliberately preserved."""
        return self.registry.remove(name)

    def update(self, name: str, **fields: Any) -> ProjectManifest:
        item = self._item(name)
        allowed = {"display_name", "description", "provider", "provider_repo", "remote_url", "default_branch", "app_name", "deployment_name", "domain"}
        for key, value in fields.items():
            if key in allowed and value is not None:
                setattr(item, key, str(value).strip())
        self._hydrate_git_metadata(item)
        return self.registry.upsert(item)

    def list(self, *, include_status: bool = True) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.registry.list():
            row = asdict(item)
            row["active"] = item.name == self.registry.active_name
            if include_status:
                try:
                    row["git"] = self.git_status(item.name)
                except Exception as exc:
                    row["git"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
        return rows

    def status(self, name: str = "") -> dict[str, Any]:
        item = self._item(name)
        p = self.path(item.name)
        return {
            **asdict(item),
            "active": item.name == self.registry.active_name,
            "exists": p.is_dir(),
            "git": self.git_status(item.name),
        }

    def git_status(self, name: str = "") -> dict[str, Any]:
        gm = self.git(name)
        available = gm.available()
        out: dict[str, Any] = {"available": available}
        if available.get("repository"):
            out.update({
                "ok": True,
                "head": gm.head(),
                "branch": gm.branch(),
                "status": gm.status(),
                "tracking": gm.tracking(),
                "remotes": gm.remotes().stdout,
            })
        else:
            out["ok"] = False
        return out

    def adopt_apps(self, apps, deployments=None) -> int:
        if not self.config.auto_adopt_apps:
            return 0
        count = 0
        existing_paths = {p.path: p.name for p in self.registry.list()}
        existing_apps = {p.app_name: p.name for p in self.registry.list() if p.app_name}
        for app in apps.registry.list():
            if app.name in existing_apps or app.path in existing_paths:
                continue
            name = app.name
            if self.registry.get(name):
                base = name
                i = 2
                while self.registry.get(f"{base}-{i}"):
                    i += 1
                name = f"{base}-{i}"
            dep_name = ""
            domain = ""
            if deployments is not None:
                for dep in deployments.list():
                    if dep.app_name == app.name:
                        dep_name = dep.name
                        domain = dep.domain
                        break
            try:
                self.import_existing(name=name, path=app.path, display_name=app.name, app_name=app.name,
                                     deployment_name=dep_name, domain=domain, make_active=not bool(self.registry.active_name))
                count += 1
            except Exception:
                continue
        return count

    def project_for_app(self, app_name: str) -> ProjectManifest | None:
        app = None
        for item in self.registry.list():
            if item.app_name == app_name:
                return item
        return app

    def project_for_path(self, path: str) -> ProjectManifest | None:
        try:
            p = self.guard.resolve(path)
        except Exception:
            return None
        best: tuple[int, ProjectManifest] | None = None
        for item in self.registry.list():
            try:
                root = self.guard.resolve(item.path)
                if p == root or root in p.parents:
                    score = len(root.parts)
                    if best is None or score > best[0]:
                        best = (score, item)
            except Exception:
                continue
        return best[1] if best else None

    def context_text(self) -> str:
        item = self.active()
        if item is None:
            return "[term_5 project context]\nNo active project is registered. Prefer project_import/project_clone/project_create before repository-scoped work."
        try:
            st = self.git_status(item.name)
        except Exception as exc:
            st = {"error": str(exc)}
        lines = [
            "[term_5 active project — authoritative project scope]",
            f"Project: {item.display_name} ({item.name})",
            f"Path: {item.path}",
            f"App: {item.app_name or '-'}; deployment: {item.deployment_name or '-'}; domain: {item.domain or '-'}",
            f"Provider repo: {item.provider_repo or '-'}; remote: {item.remote_url or '-'}",
        ]
        if st.get("available", {}).get("repository"):
            status = st.get("status") or {}
            tracking = st.get("tracking") or {}
            lines.append(f"Git: branch={st.get('branch') or '-'} head={(st.get('head') or '-')[:12]} clean={status.get('clean')} ahead={tracking.get('ahead', 0)} behind={tracking.get('behind', 0)}")
        if item.goals:
            lines.append("Goals: " + "; ".join(f"[{g.get('status','open')}] {g.get('text','')}" for g in item.goals[-8:]))
        if item.decisions:
            lines.append("Decisions (treat as project constraints unless user changes them): " + "; ".join(str(d.get('text') or '') for d in item.decisions[-8:]))
        open_tasks = [x for x in item.backlog if x.get("status") not in {"done", "deferred"}]
        if open_tasks:
            lines.append("Open backlog: " + "; ".join(f"{x.get('priority','p2').upper()} {x.get('title','')}" for x in open_tasks[:10]))
        lines.append("For coding/Git/deployment work, prefer this project's path/repository unless the user explicitly names another project.")
        return "\n".join(lines)[:12000]

    def sync_metadata(self, name: str = "") -> ProjectManifest:
        item = self._item(name)
        self._hydrate_git_metadata(item)
        return self.registry.upsert(item)

    def _hydrate_git_metadata(self, item: ProjectManifest) -> None:
        try:
            gm = GitManager(self.guard.resolve(item.path), timeout_s=self.timeout_s, runner=self.runner)
            if gm.available().get("repository"):
                item.default_branch = gm.branch() or item.default_branch
                remotes = gm.remote_urls()
                if remotes.get("origin"):
                    item.remote_url = remotes["origin"]
                    slug = self.github.slug_from_remote(item.remote_url)
                    if slug:
                        item.provider = "github"
                        item.provider_repo = slug
        except Exception:
            pass
