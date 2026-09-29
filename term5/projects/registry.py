from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = 1
_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_PRIORITY = {"p0", "p1", "p2", "p3"}
_TASK_STATUS = {"open", "in_progress", "blocked", "done", "deferred"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ProjectManifest:
    name: str
    path: str
    display_name: str = ""
    description: str = ""
    provider: str = "github"
    provider_repo: str = ""
    remote_url: str = ""
    default_branch: str = "main"
    app_name: str = ""
    deployment_name: str = ""
    domain: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    goals: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    backlog: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NAME_RX.match(self.name):
            raise ValueError("project name must be 1-80 chars using letters, numbers, '.', '_' or '-'")
        if not self.display_name:
            self.display_name = self.name

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProjectManifest":
        keys = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in keys})


class ProjectRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, ProjectManifest] = {}
        self._active: str = ""
        self.load()

    @property
    def active_name(self) -> str:
        return self._active if self._active in self._items else ""

    def load(self) -> None:
        self._items = {}
        self._active = ""
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"project registry is unreadable: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("format") != FORMAT or not isinstance(raw.get("projects", {}), dict):
            raise RuntimeError("unsupported or corrupt project registry format")
        for name, item in raw["projects"].items():
            if isinstance(item, dict):
                manifest = ProjectManifest.from_dict({**item, "name": item.get("name") or name})
                self._items[manifest.name] = manifest
        active = str(raw.get("active") or "")
        if active in self._items:
            self._active = active

    def save(self) -> None:
        payload = {
            "format": FORMAT,
            "active": self.active_name,
            "projects": {name: asdict(item) for name, item in sorted(self._items.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, name: str) -> ProjectManifest | None:
        return self._items.get(name)

    def list(self) -> list[ProjectManifest]:
        return [self._items[k] for k in sorted(self._items)]

    def upsert(self, item: ProjectManifest, *, make_active: bool = False) -> ProjectManifest:
        item.updated_at = _now()
        self._items[item.name] = item
        if make_active or not self.active_name:
            self._active = item.name
        self.save()
        return item

    def remove(self, name: str) -> bool:
        if name not in self._items:
            return False
        del self._items[name]
        if self._active == name:
            self._active = next(iter(sorted(self._items)), "")
        self.save()
        return True

    def set_active(self, name: str) -> ProjectManifest:
        item = self.get(name)
        if item is None:
            raise KeyError(f"unknown project: {name}")
        self._active = name
        self.save()
        return item

    def add_goal(self, name: str, text: str, *, status: str = "open") -> dict[str, Any]:
        item = self._require(name)
        status = status if status in {"open", "done", "deferred"} else "open"
        goal = {"id": "goal_" + uuid.uuid4().hex[:10], "text": text.strip()[:1000], "status": status, "created_at": _now()}
        if not goal["text"]:
            raise ValueError("goal text is required")
        item.goals.append(goal)
        self.upsert(item)
        return goal

    def add_decision(self, name: str, text: str) -> dict[str, Any]:
        item = self._require(name)
        decision = {"id": "decision_" + uuid.uuid4().hex[:10], "text": text.strip()[:2000], "created_at": _now()}
        if not decision["text"]:
            raise ValueError("decision text is required")
        item.decisions.append(decision)
        self.upsert(item)
        return decision

    def add_backlog(self, name: str, title: str, *, priority: str = "p2", acceptance: str = "", source: str = "user") -> dict[str, Any]:
        item = self._require(name)
        p = priority.lower()
        if p not in _PRIORITY:
            raise ValueError("priority must be p0, p1, p2, or p3")
        task = {
            "id": "task_" + uuid.uuid4().hex[:10], "title": title.strip()[:1000], "priority": p,
            "status": "open", "acceptance": acceptance.strip()[:3000], "source": source.strip()[:200],
            "created_at": _now(), "updated_at": _now(),
        }
        if not task["title"]:
            raise ValueError("backlog title is required")
        item.backlog.append(task)
        self.upsert(item)
        return task

    def update_backlog(self, name: str, task_id: str, *, status: str) -> dict[str, Any]:
        item = self._require(name)
        if status not in _TASK_STATUS:
            raise ValueError("status must be open, in_progress, blocked, done, or deferred")
        for task in item.backlog:
            if task.get("id") == task_id:
                task["status"] = status
                task["updated_at"] = _now()
                self.upsert(item)
                return task
        raise KeyError(f"unknown backlog item: {task_id}")

    def _require(self, name: str) -> ProjectManifest:
        item = self.get(name)
        if item is None:
            raise KeyError(f"unknown project: {name}")
        return item
