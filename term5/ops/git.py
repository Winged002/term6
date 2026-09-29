from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .command import CommandResult, TypedCommandRunner

_REF_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}~^:+-]{0,199}$")
_BRANCH_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
_REMOTE_URL_RX = re.compile(r"^(?:git@[^\s:]+:[^\s]+|ssh://[^\s]+|https://[^\s]+)$")


class GitOpsError(RuntimeError):
    pass


class GitManager:
    def __init__(self, root: Path, *, timeout_s: int = 120, runner=None) -> None:
        self.root = root
        self.command = TypedCommandRunner(timeout_s=timeout_s, runner=runner)

    @classmethod
    def clone(cls, remote_url: str, target: Path, *, branch: str = "", timeout_s: int = 300, runner=None) -> CommandResult:
        url = str(remote_url).strip()
        if not _REMOTE_URL_RX.match(url) or url.startswith("-") or "\x00" in url:
            raise GitOpsError("invalid Git remote URL")
        target = Path(target)
        command = TypedCommandRunner(timeout_s=timeout_s, runner=runner)
        argv = ["git", "clone", "--origin", "origin"]
        if branch:
            if not _BRANCH_RX.match(branch) or branch.startswith("-"):
                raise GitOpsError("invalid branch")
            argv += ["--branch", branch]
        argv += [url, str(target)]
        return command.run(argv, cwd=target.parent, timeout_s=timeout_s)

    def _run(self, args: list[str], *, timeout_s: int | None = None) -> CommandResult:
        return self.command.run(["git", *args], cwd=self.root, timeout_s=timeout_s)

    def available(self) -> dict[str, Any]:
        if not self.command.which("git"):
            return {"available": False, "reason": "git CLI not found"}
        v = self._run(["--version"])
        repo = self._run(["rev-parse", "--is-inside-work-tree"])
        return {"available": v.ok, "version": v.stdout, "repository": repo.ok and repo.stdout.strip() == "true"}

    def status(self) -> dict[str, Any]:
        r = self._run(["status", "--porcelain=v1", "--branch"])
        lines = r.stdout.splitlines() if r.stdout else []
        return {"ok": r.ok, "clean": r.ok and len([x for x in lines if not x.startswith("##")]) == 0, "output": r.stdout}

    def head(self) -> str | None:
        r = self._run(["rev-parse", "HEAD"])
        return r.stdout.strip() if r.ok and r.stdout.strip() else None

    def branch(self) -> str | None:
        r = self._run(["branch", "--show-current"])
        return r.stdout.strip() if r.ok and r.stdout.strip() else None

    def log(self, limit: int = 20) -> CommandResult:
        return self._run(["log", f"-{max(1, min(int(limit), 100))}", "--oneline", "--decorate"])

    def diff(self, *, staged: bool = False, path: str = "") -> CommandResult:
        args = ["diff"]
        if staged:
            args.append("--cached")
        if path:
            if path.startswith("-") or "\x00" in path:
                raise GitOpsError("invalid Git path")
            args.extend(["--", path])
        return self._run(args)

    def init(self, initial_branch: str = "main") -> CommandResult:
        if not _BRANCH_RX.match(initial_branch):
            raise GitOpsError("invalid initial branch name")
        return self._run(["init", "-b", initial_branch])

    def add(self, paths: list[str]) -> CommandResult:
        if not paths:
            raise GitOpsError("at least one path is required")
        if len(paths) > 128:
            raise GitOpsError("too many paths")
        for p in paths:
            if p.startswith("-") or "\x00" in p:
                raise GitOpsError("invalid Git path")
        return self._run(["add", "--", *paths])

    def commit(self, message: str) -> CommandResult:
        msg = message.strip()
        if not 1 <= len(msg) <= 500:
            raise GitOpsError("commit message must be 1-500 characters")
        return self._run(["commit", "-m", msg], timeout_s=180)

    def create_branch(self, name: str, *, checkout: bool = True) -> CommandResult:
        if not _BRANCH_RX.match(name) or ".." in name or name.endswith("/"):
            raise GitOpsError("invalid branch name")
        return self._run(["switch", "-c", name] if checkout else ["branch", name])

    def switch(self, ref: str) -> CommandResult:
        if not _REF_RX.match(ref) or ref.startswith("-"):
            raise GitOpsError("invalid Git reference")
        return self._run(["switch", ref])

    def remotes(self) -> CommandResult:
        return self._run(["remote", "-v"])

    def remote_urls(self) -> dict[str, str]:
        r = self._run(["remote", "-v"])
        if not r.ok:
            return {}
        out: dict[str, str] = {}
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] not in out:
                out[parts[0]] = parts[1]
        return out

    def add_remote(self, name: str, url: str) -> CommandResult:
        if not _BRANCH_RX.match(name) or "/" in name or name.startswith("-"):
            raise GitOpsError("invalid remote name")
        value = str(url).strip()
        if not _REMOTE_URL_RX.match(value) or value.startswith("-") or "\x00" in value:
            raise GitOpsError("invalid Git remote URL")
        return self._run(["remote", "add", name, value])

    def set_remote_url(self, name: str, url: str) -> CommandResult:
        if not _BRANCH_RX.match(name) or "/" in name or name.startswith("-"):
            raise GitOpsError("invalid remote name")
        value = str(url).strip()
        if not _REMOTE_URL_RX.match(value) or value.startswith("-") or "\x00" in value:
            raise GitOpsError("invalid Git remote URL")
        return self._run(["remote", "set-url", name, value])

    def tracking(self) -> dict[str, Any]:
        branch = self.branch()
        if not branch:
            return {"branch": None, "upstream": None, "ahead": 0, "behind": 0}
        up = self._run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if not up.ok or not up.stdout.strip():
            return {"branch": branch, "upstream": None, "ahead": 0, "behind": 0}
        upstream = up.stdout.strip()
        counts = self._run(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"])
        behind = ahead = 0
        if counts.ok:
            parts = counts.stdout.split()
            if len(parts) >= 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
        return {"branch": branch, "upstream": upstream, "ahead": ahead, "behind": behind}

    def fetch(self, remote: str = "origin") -> CommandResult:
        if not _BRANCH_RX.match(remote) or "/" in remote or remote.startswith("-"):
            raise GitOpsError("invalid remote name")
        return self._run(["fetch", "--prune", remote], timeout_s=300)

    def pull_ff(self, remote: str = "origin", branch: str = "") -> CommandResult:
        if not _BRANCH_RX.match(remote) or "/" in remote or remote.startswith("-"):
            raise GitOpsError("invalid remote name")
        args = ["pull", "--ff-only", remote]
        if branch:
            if not _BRANCH_RX.match(branch) or branch.startswith("-"):
                raise GitOpsError("invalid branch")
            args.append(branch)
        return self._run(args, timeout_s=300)

    def push(self, remote: str = "origin", refspec: str = "", *, set_upstream: bool = False) -> CommandResult:
        if not _BRANCH_RX.match(remote) or "/" in remote or remote.startswith("-"):
            raise GitOpsError("invalid remote name")
        args = ["push"]
        if set_upstream:
            args.append("--set-upstream")
        args.append(remote)
        if refspec:
            if not _REF_RX.match(refspec) or refspec.startswith("-"):
                raise GitOpsError("invalid refspec")
            args.append(refspec)
        return self._run(args, timeout_s=300)

    def tag(self, name: str, message: str = "") -> CommandResult:
        if not _REF_RX.match(name) or name.startswith("-"):
            raise GitOpsError("invalid tag")
        if message:
            return self._run(["tag", "-a", name, "-m", message[:500]])
        return self._run(["tag", name])

    def restore(self, paths: list[str]) -> CommandResult:
        if not paths or any(p.startswith("-") for p in paths):
            raise GitOpsError("valid paths are required")
        return self._run(["restore", "--", *paths])

    def revert(self, commit: str) -> CommandResult:
        if not _REF_RX.match(commit) or commit.startswith("-"):
            raise GitOpsError("invalid commit reference")
        return self._run(["revert", "--no-edit", commit], timeout_s=180)

    def reset_hard(self, ref: str) -> CommandResult:
        if not _REF_RX.match(ref) or ref.startswith("-"):
            raise GitOpsError("invalid Git reference")
        return self._run(["reset", "--hard", ref], timeout_s=180)
