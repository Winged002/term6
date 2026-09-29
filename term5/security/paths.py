from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_BLOCKLIST = [
    r"(^|/)\.env($|\.)",
    r"(^|/)\.git($|/)",
    r"(^|/)\.ssh($|/)",
    r"(^|/)\.aws($|/)",
    r"(^|/)\.credentials($|/)",
    r"(^|/)\.kube($|/)",
    r"(^|/)id_rsa($|\.)",
    r"(^|/)id_ed25519($|\.)",
    r"\.(key|pem|p12|pfx|pcap)$",
    r"(^|/)(node_modules|__pycache__|\.venv|venv|dist|build|\.tox)(/|$)",
    r"\.(pyc|pyo)$",
    r"(^|/)\.term[0-9_]*($|/)",
    r"(^|/)\.term5($|/)",
]


class PathGuard:
    """Deterministic workspace confinement.

    The guard resolves symlinks before checking containment. Non-existing leaf
    paths are still safe because Path.resolve(strict=False) resolves all
    existing parents and normalizes `..` segments.
    """

    def __init__(self, root: str | Path, patterns: list[str] | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.blocklist = [re.compile(p, re.IGNORECASE if os.name == "nt" else 0)
                          for p in (patterns or DEFAULT_BLOCKLIST)]

    def is_blocked_relative(self, rel: str | Path) -> bool:
        text = Path(rel).as_posix()
        return any(rx.search(text) for rx in self.blocklist)

    def resolve(self, path: str | Path, *, allow_root: bool = True) -> Path:
        raw = Path(str(path)).expanduser()
        candidate = raw.resolve(strict=False) if raw.is_absolute() else (self.root / raw).resolve(strict=False)
        try:
            rel = candidate.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"Blocked path outside workspace: {path}") from exc
        if not allow_root and candidate == self.root:
            raise PermissionError("Workspace root itself is not a valid target for this operation")
        if candidate != self.root and self.is_blocked_relative(rel):
            raise PermissionError(f"Blocked protected path: {path}")
        return candidate

    def relative(self, path: str | Path) -> str:
        return self.resolve(path).relative_to(self.root).as_posix()

    def iter_files(self, start: str | Path = ".", *, include_hidden: bool = False):
        root = self.resolve(start)
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = p.resolve(strict=False).relative_to(self.root)
            except ValueError:
                continue
            if not include_hidden and any(part.startswith(".") for part in rel.parts):
                continue
            if self.is_blocked_relative(rel):
                continue
            yield p, rel
