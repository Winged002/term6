from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = 1


@dataclass(slots=True)
class ReleaseRecord:
    commit: str
    status: str
    deployed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: str = ""


@dataclass(slots=True)
class DeploymentManifest:
    name: str
    app_name: str
    project_name: str = ""
    domain: str = ""
    upstream_port: int = 0
    nginx_site: str = ""
    tls: bool = False
    current_commit: str = ""
    previous_commit: str = ""
    migration_kind: str = "none"
    backup_kind: str = "none"
    backup_service: str = ""
    environment: str = "production"
    release_status: str = "unknown"
    last_deployed_at: str = ""
    last_verified_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeploymentManifest":
        keys = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in keys})


class DeploymentRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, DeploymentManifest] = {}
        self.load()

    def load(self) -> None:
        self._items = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("format") != FORMAT or not isinstance(raw.get("deployments"), dict):
            raise RuntimeError("unsupported or corrupt deployment registry")
        for name, item in raw["deployments"].items():
            if isinstance(item, dict):
                self._items[name] = DeploymentManifest.from_dict({**item, "name": item.get("name") or name})

    def save(self) -> None:
        payload = {"format": FORMAT, "deployments": {k: asdict(v) for k, v in sorted(self._items.items())}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def upsert(self, item: DeploymentManifest) -> None:
        self._items[item.name] = item
        self.save()

    def get(self, name: str) -> DeploymentManifest | None:
        return self._items.get(name)

    def list(self) -> list[DeploymentManifest]:
        return [self._items[k] for k in sorted(self._items)]
