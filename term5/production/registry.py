from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = 1
ENVIRONMENTS = {"development", "staging", "production"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_environment(value: str) -> str:
    env = str(value or "production").strip().lower()
    if env not in ENVIRONMENTS:
        raise ValueError("environment must be development, staging, or production")
    return env


@dataclass(slots=True)
class EnvironmentRecord:
    project: str
    environment: str
    deployment: str = ""
    app_name: str = ""
    url: str = ""
    current_commit: str = ""
    status: str = "unknown"
    last_deployed_at: str = ""
    last_observed_at: str = ""
    last_verified_at: str = ""
    updated_at: str = field(default_factory=now_iso)

    @property
    def key(self) -> str:
        return f"{self.project}:{self.environment}"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EnvironmentRecord":
        keys = set(cls.__dataclass_fields__)
        item = cls(**{k: v for k, v in raw.items() if k in keys})
        item.environment = clean_environment(item.environment)
        return item


@dataclass(slots=True)
class IncidentRecord:
    id: str
    title: str
    project: str = ""
    environment: str = "production"
    deployment: str = ""
    severity: str = "P2"
    status: str = "open"
    source: str = "production"
    summary: str = ""
    commit: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    resolved_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "IncidentRecord":
        keys = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in keys})


class ProductionRegistry:
    """Durable environments, incidents, metrics snapshots and release markers."""

    def __init__(self, state_dir: Path, *, max_snapshots: int = 5000) -> None:
        self.dir = Path(state_dir) / "production"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "registry.json"
        self.metrics_path = self.dir / "metrics.jsonl"
        self.releases_path = self.dir / "releases.jsonl"
        self.max_snapshots = max(100, int(max_snapshots))
        self.environments: dict[str, EnvironmentRecord] = {}
        self.incidents: dict[str, IncidentRecord] = {}
        self.load()

    def load(self) -> None:
        self.environments = {}
        self.incidents = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("format") != FORMAT:
            raise RuntimeError("unsupported or corrupt production registry")
        for key, item in (raw.get("environments") or {}).items():
            if isinstance(item, dict):
                rec = EnvironmentRecord.from_dict(item)
                self.environments[key] = rec
        for key, item in (raw.get("incidents") or {}).items():
            if isinstance(item, dict):
                self.incidents[key] = IncidentRecord.from_dict(item)

    def save(self) -> None:
        payload = {
            "format": FORMAT,
            "environments": {k: asdict(v) for k, v in sorted(self.environments.items())},
            "incidents": {k: asdict(v) for k, v in sorted(self.incidents.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def upsert_environment(self, item: EnvironmentRecord) -> EnvironmentRecord:
        item.environment = clean_environment(item.environment)
        item.updated_at = now_iso()
        self.environments[item.key] = item
        self.save()
        return item

    def get_environment(self, project: str, environment: str) -> EnvironmentRecord | None:
        return self.environments.get(f"{project}:{clean_environment(environment)}")

    def list_environments(self, project: str = "") -> list[EnvironmentRecord]:
        rows = list(self.environments.values())
        if project:
            rows = [x for x in rows if x.project == project]
        order = {"production": 0, "staging": 1, "development": 2}
        return sorted(rows, key=lambda x: (x.project, order.get(x.environment, 9)))

    def _write_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def append_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._write_jsonl(self.metrics_path, snapshot)
        # Bound the local history without touching it on every snapshot until it grows materially.
        try:
            lines = self.metrics_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > self.max_snapshots + 500:
                tmp = self.metrics_path.with_suffix(".tmp")
                tmp.write_text("\n".join(lines[-self.max_snapshots:]) + "\n", encoding="utf-8")
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                tmp.replace(self.metrics_path)
                try:
                    os.chmod(self.metrics_path, 0o600)
                except OSError:
                    pass
        except OSError:
            pass

    def snapshots(self, *, deployment: str = "", project: str = "", environment: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if not self.metrics_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in reversed(self.metrics_path.read_text(encoding="utf-8", errors="replace").splitlines()):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if deployment and rec.get("deployment") != deployment:
                continue
            if project and rec.get("project") != project:
                continue
            if environment and rec.get("environment") != environment:
                continue
            rows.append(rec)
            if len(rows) >= max(1, min(int(limit), 1000)):
                break
        rows.reverse()
        return rows

    def release_marker(self, row: dict[str, Any]) -> None:
        payload = {"ts": now_iso(), **row}
        self._write_jsonl(self.releases_path, payload)

    def releases(self, deployment: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if not self.releases_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in reversed(self.releases_path.read_text(encoding="utf-8", errors="replace").splitlines()):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if deployment and rec.get("deployment") != deployment:
                continue
            rows.append(rec)
            if len(rows) >= max(1, min(int(limit), 500)):
                break
        rows.reverse()
        return rows

    def find_open_incident(self, *, project: str, environment: str, title: str) -> IncidentRecord | None:
        for inc in self.incidents.values():
            if inc.status != "resolved" and inc.project == project and inc.environment == environment and inc.title == title:
                return inc
        return None

    def open_incident(self, *, title: str, project: str = "", environment: str = "production", deployment: str = "",
                      severity: str = "P2", source: str = "production", summary: str = "", commit: str = "",
                      evidence: dict[str, Any] | None = None) -> tuple[IncidentRecord, bool]:
        environment = clean_environment(environment)
        current = self.find_open_incident(project=project, environment=environment, title=title)
        if current is not None:
            current.updated_at = now_iso()
            current.severity = severity or current.severity
            current.summary = summary or current.summary
            current.commit = commit or current.commit
            current.deployment = deployment or current.deployment
            if evidence:
                current.evidence = dict(evidence)
            self.save()
            return current, False
        incident = IncidentRecord(
            id="INC-" + uuid.uuid4().hex[:8].upper(), title=title, project=project,
            environment=environment, deployment=deployment, severity=severity, source=source,
            summary=summary, commit=commit, evidence=dict(evidence or {}),
        )
        self.incidents[incident.id] = incident
        self.save()
        return incident, True

    def update_incident(self, incident_id: str, *, status: str = "", severity: str = "", note: str = "") -> IncidentRecord:
        item = self.incidents.get(str(incident_id))
        if item is None:
            raise KeyError(f"unknown incident: {incident_id}")
        if status:
            if status not in {"open", "investigating", "mitigated", "resolved"}:
                raise ValueError("status must be open, investigating, mitigated, or resolved")
            item.status = status
            if status == "resolved":
                item.resolved_at = now_iso()
        if severity:
            if severity not in {"P0", "P1", "P2", "P3"}:
                raise ValueError("severity must be P0, P1, P2, or P3")
            item.severity = severity
        if note:
            item.actions.append({"ts": now_iso(), "note": str(note)[:4000]})
            item.actions = item.actions[-100:]
        item.updated_at = now_iso()
        self.save()
        return item

    def list_incidents(self, *, project: str = "", environment: str = "", status: str = "", limit: int = 200) -> list[IncidentRecord]:
        rows = list(self.incidents.values())
        if project:
            rows = [x for x in rows if x.project == project]
        if environment:
            env = clean_environment(environment)
            rows = [x for x in rows if x.environment == env]
        if status:
            rows = [x for x in rows if x.status == status]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows[:max(1, min(int(limit), 1000))]
