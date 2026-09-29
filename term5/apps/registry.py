from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_FORMAT = 1
_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


@dataclass(slots=True)
class AppManifest:
    name: str
    path: str
    runtime: str = "docker-compose"
    compose_file: str = "compose.yaml"
    compose_project: str = ""
    entry_service: str = "web"
    url: str = ""
    health_url: str = ""
    services: list[str] = field(default_factory=list)
    framework: str = "custom"
    created_by: str = "term_5"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NAME_RX.match(self.name):
            raise ValueError("app name must be 1-80 chars using letters, numbers, '.', '_' or '-'")
        if not self.compose_project:
            self.compose_project = re.sub(r"[^a-z0-9_-]+", "-", self.name.lower()).strip("-") or "term5-app"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppManifest":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in fields})


class AppRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._apps: dict[str, AppManifest] = {}
        self.load()

    def load(self) -> None:
        self._apps = {}
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"app registry is unreadable: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("format") != REGISTRY_FORMAT or not isinstance(raw.get("apps", {}), dict):
            raise RuntimeError("unsupported or corrupt app registry format")
        for name, item in raw["apps"].items():
            if isinstance(item, dict):
                app = AppManifest.from_dict({**item, "name": item.get("name") or name})
                self._apps[app.name] = app

    def save(self) -> None:
        payload = {
            "format": REGISTRY_FORMAT,
            "apps": {name: asdict(app) for name, app in sorted(self._apps.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, app: AppManifest, *, replace: bool = False) -> None:
        if app.name in self._apps and not replace:
            raise ValueError(f"app already registered: {app.name}")
        self._apps[app.name] = app
        self.save()

    def remove(self, name: str) -> bool:
        if name not in self._apps:
            return False
        del self._apps[name]
        self.save()
        return True

    def get(self, name: str) -> AppManifest | None:
        return self._apps.get(name)

    def list(self) -> list[AppManifest]:
        return [self._apps[k] for k in sorted(self._apps)]
