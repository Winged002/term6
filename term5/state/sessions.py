from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionCorruptionError(RuntimeError):
    pass


@dataclass(slots=True)
class SessionInfo:
    name: str
    path: Path
    modified_at: str
    messages: int
    format: int | None = None


class SessionStore:
    """Versioned local session store.

    RC1 reads alpha1 list-root sessions plus beta format 2 and RC format 3.
    Existing but malformed session files fail loudly instead of being treated as
    an empty/missing session. Writes use replace-on-complete and fsync.
    """

    NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    CURRENT_FORMAT = 3

    def __init__(self, path: Path, *, runtime_version: str = "") -> None:
        self.path = path
        self.directory = path.parent / "sessions"
        self.runtime_version = runtime_version

    def _path(self, name: str | None = None) -> Path:
        if not name or name in {"default", "autosave"}:
            return self.path
        if not self.NAME_RX.fullmatch(name):
            raise ValueError("session name must be 1-64 characters: letters, digits, dot, underscore, hyphen")
        return self.directory / f"{name}.json"

    @staticmethod
    def _atomic_json(target: Path, payload: Any) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)

    def save(self, messages: list[dict[str, Any]], name: str | None = None,
             *, metadata: dict[str, Any] | None = None) -> Path:
        target = self._path(name)
        payload = {
            "format": self.CURRENT_FORMAT,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "runtime_version": self.runtime_version,
            "metadata": metadata or {},
            "messages": messages,
        }
        self._atomic_json(target, payload)
        return target

    def _decode(self, raw: Any, target: Path) -> tuple[list[dict[str, Any]], int | None]:
        # alpha1 compatibility: root was directly the messages list.
        if isinstance(raw, list):
            if not all(isinstance(x, dict) for x in raw):
                raise SessionCorruptionError(f"session {target} contains non-object messages")
            return raw, 1
        if not isinstance(raw, dict):
            raise SessionCorruptionError(f"session {target} root must be an object or legacy list")
        fmt = raw.get("format")
        if fmt not in {2, 3}:
            raise SessionCorruptionError(f"session {target} has unsupported format {fmt!r}")
        messages = raw.get("messages")
        if not isinstance(messages, list) or not all(isinstance(x, dict) for x in messages):
            raise SessionCorruptionError(f"session {target} has invalid messages")
        return messages, int(fmt)

    def load(self, name: str | None = None) -> list[dict[str, Any]] | None:
        target = self._path(name)
        if not target.exists():
            return None
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SessionCorruptionError(f"session {target} is unreadable: {exc}") from exc
        messages, _ = self._decode(raw, target)
        return messages

    def inspect(self, name: str | None = None) -> dict[str, Any] | None:
        target = self._path(name)
        if not target.exists():
            return None
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SessionCorruptionError(f"session {target} is unreadable: {exc}") from exc
        messages, fmt = self._decode(raw, target)
        return {
            "path": str(target),
            "format": fmt,
            "messages": len(messages),
            "runtime_version": raw.get("runtime_version") if isinstance(raw, dict) else None,
            "saved_at": raw.get("saved_at") if isinstance(raw, dict) else None,
        }

    def delete(self, name: str) -> bool:
        target = self._path(name)
        if target == self.path:
            raise ValueError("default autosave session cannot be deleted through named-session API")
        if target.exists():
            target.unlink()
            return True
        return False

    def list(self) -> list[SessionInfo]:
        paths: list[tuple[str, Path]] = []
        if self.path.exists():
            paths.append(("default", self.path))
        if self.directory.exists():
            paths.extend((p.stem, p) for p in self.directory.glob("*.json") if p.is_file())
        out: list[SessionInfo] = []
        for name, p in paths:
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                messages, fmt = self._decode(raw, p)
                out.append(SessionInfo(
                    name=name,
                    path=p,
                    modified_at=datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
                    messages=len(messages),
                    format=fmt,
                ))
            except SessionCorruptionError:
                raise
            except Exception as exc:
                raise SessionCorruptionError(f"session {p} is unreadable: {exc}") from exc
        return sorted(out, key=lambda x: x.modified_at, reverse=True)
