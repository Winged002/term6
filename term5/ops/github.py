from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .command import CommandResult, TypedCommandRunner

_SLUG_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(RuntimeError):
    pass


class GitHubManager:
    """Typed GitHub CLI adapter.

    This intentionally exposes a small, explicit surface instead of giving the
    model arbitrary `gh` or shell execution.
    """

    def __init__(self, *, timeout_s: int = 120, runner=None) -> None:
        self.command = TypedCommandRunner(timeout_s=timeout_s, runner=runner)

    def _run(self, args: list[str], *, cwd: Path | None = None, timeout_s: int | None = None) -> CommandResult:
        return self.command.run(["gh", *args], cwd=cwd, timeout_s=timeout_s)

    @staticmethod
    def _slug(value: str) -> str:
        slug = str(value or "").strip()
        if not _SLUG_RX.match(slug) or slug.startswith("-"):
            raise GitHubError("repository must be OWNER/NAME")
        return slug

    def status(self) -> dict[str, Any]:
        if not self.command.which("gh"):
            return {"available": False, "authenticated": False, "reason": "GitHub CLI (gh) not found"}
        ver = self._run(["--version"])
        auth = self._run(["auth", "status", "--hostname", "github.com"])
        login = ""
        if auth.ok:
            who = self._run(["api", "user", "--jq", ".login"])
            if who.ok:
                login = who.stdout.strip()
        return {
            "available": bool(ver.ok),
            "authenticated": bool(auth.ok),
            "version": (ver.stdout.splitlines()[0] if ver.stdout else ""),
            "login": login,
            "auth_output": auth.stdout[-4000:],
        }

    def list_repos(self, owner: str = "", *, limit: int = 50) -> dict[str, Any]:
        st = self.status()
        if not st.get("authenticated"):
            return {"ok": False, "repos": [], "error": st.get("reason") or st.get("auth_output") or "GitHub CLI is not authenticated"}
        target = owner.strip() or str(st.get("login") or "")
        if not re.match(r"^[A-Za-z0-9_.-]{1,100}$", target):
            raise GitHubError("invalid GitHub owner")
        n = max(1, min(int(limit), 200))
        r = self._run(["repo", "list", target, "--limit", str(n), "--json", "name,nameWithOwner,url,sshUrl,isPrivate,defaultBranchRef,updatedAt"])
        if not r.ok:
            return {"ok": False, "repos": [], "error": r.stdout}
        try:
            rows = json.loads(r.stdout or "[]")
        except Exception as exc:
            return {"ok": False, "repos": [], "error": f"invalid gh JSON: {exc}"}
        return {"ok": True, "owner": target, "repos": rows}

    def view_repo(self, repo: str) -> dict[str, Any]:
        slug = self._slug(repo)
        r = self._run(["repo", "view", slug, "--json", "nameWithOwner,url,sshUrl,isPrivate,defaultBranchRef,description,updatedAt"])
        if not r.ok:
            return {"ok": False, "repo": slug, "error": r.stdout}
        try:
            return {"ok": True, "repo": json.loads(r.stdout)}
        except Exception as exc:
            return {"ok": False, "repo": slug, "error": f"invalid gh JSON: {exc}"}

    def create_repo(self, repo: str, *, private: bool = True, description: str = "", source: Path | None = None,
                    remote: str = "origin", push: bool = False) -> dict[str, Any]:
        slug = self._slug(repo)
        if remote and not re.match(r"^[A-Za-z0-9_.-]{1,64}$", remote):
            raise GitHubError("invalid remote name")
        args = ["repo", "create", slug, "--private" if private else "--public"]
        if description.strip():
            args += ["--description", description.strip()[:500]]
        if source is not None:
            args += ["--source", str(Path(source)), "--remote", remote]
            if push:
                args.append("--push")
        r = self._run(args, cwd=source, timeout_s=300)
        if not r.ok:
            return {"ok": False, "repo": slug, "output": r.stdout, "argv": r.argv}
        view = self.view_repo(slug)
        return {"ok": True, "repo": slug, "output": r.stdout, "view": view.get("repo") if view.get("ok") else None}

    @staticmethod
    def slug_from_remote(url: str) -> str:
        value = str(url or "").strip()
        patterns = [
            r"^git@github\.com:([^/\s]+/[^/\s]+?)(?:\.git)?$",
            r"^ssh://git@github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?$",
            r"^https://github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
        ]
        for pattern in patterns:
            m = re.match(pattern, value, re.I)
            if m:
                slug = m.group(1)
                return slug[:-4] if slug.endswith(".git") else slug
        return ""
