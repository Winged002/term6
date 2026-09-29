from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CheckpointCorruptionError(RuntimeError):
    pass


@dataclass(slots=True)
class Checkpoint:
    phase: str
    messages: list[dict[str, Any]]
    prompt: str = ""
    session_name: str | None = None
    metadata: dict[str, Any] | None = None
    saved_at: str = ""
    format: int = 1
    runtime_version: str = ""


class CheckpointStore:
    """Crash-safe, versioned local turn checkpoint.

    RC1 reads rc1 format 1 and writes format 2. Malformed checkpoints fail
    loudly; recovery must never silently convert corrupt state into a fresh turn.
    """

    CURRENT_FORMAT = 2

    def __init__(self, state_dir: Path, *, runtime_version: str = "") -> None:
        self.path = state_dir / "turn_checkpoint.json"
        self.runtime_version = runtime_version

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save(self, *, phase: str, messages: list[dict[str, Any]], prompt: str = "",
             session_name: str | None = None, metadata: dict[str, Any] | None = None) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": self.CURRENT_FORMAT,
            "runtime_version": self.runtime_version,
            "saved_at": self._now(),
            "phase": str(phase),
            "prompt": str(prompt),
            "session_name": session_name,
            "metadata": metadata or {},
            "messages": messages,
        }
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        return self.path

    def load(self) -> Checkpoint | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CheckpointCorruptionError(f"checkpoint is unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise CheckpointCorruptionError("checkpoint root must be an object")
        fmt = raw.get("format", 1)
        if fmt not in {1, 2}:
            raise CheckpointCorruptionError(f"unsupported checkpoint format {fmt!r}")
        messages = raw.get("messages")
        if not isinstance(messages, list) or not all(isinstance(x, dict) for x in messages):
            raise CheckpointCorruptionError("checkpoint messages are invalid")
        return Checkpoint(
            phase=str(raw.get("phase") or "unknown"),
            messages=messages,
            prompt=str(raw.get("prompt") or ""),
            session_name=(None if raw.get("session_name") is None else str(raw.get("session_name"))),
            metadata=(raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}),
            saved_at=str(raw.get("saved_at") or ""),
            format=int(fmt),
            runtime_version=str(raw.get("runtime_version") or ""),
        )

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def describe(self) -> dict[str, Any] | None:
        cp = self.load()
        if cp is None:
            return None
        return {
            "format": cp.format,
            "runtime_version": cp.runtime_version,
            "phase": cp.phase,
            "prompt_chars": len(cp.prompt),
            "messages": len(cp.messages),
            "session_name": cp.session_name,
            "saved_at": cp.saved_at,
            "metadata": cp.metadata or {},
        }
