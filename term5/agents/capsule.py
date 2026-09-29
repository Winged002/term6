from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .store import AgentStore


class ProjectCapsuleBuilder:
    """Build a compact current+forecast state capsule for one project owner."""

    def __init__(self, projects, store: AgentStore, state_dir: Path, *, task_limit: int = 20, message_limit: int = 20) -> None:
        self.projects = projects
        self.store = store
        self.state_dir = state_dir
        self.task_limit = max(4, int(task_limit))
        self.message_limit = max(4, int(message_limit))

    def build(self, project: str) -> dict[str, Any]:
        item = self.projects.registry.get(project) if self.projects is not None else None
        if item is None:
            raise KeyError(f"unknown project: {project}")
        try:
            git = self.projects.git_status(project)
        except Exception as exc:
            git = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        tasks = self.store.list_tasks(project, limit=self.task_limit)
        active = [t for t in tasks if t.get("status") in {"claimed", "executing", "verifying", "waiting_agent", "waiting_human"}]
        queued = [t for t in tasks if t.get("status") in {"queued", "waiting_dependency", "planned", "created"}]
        messages = self.store.list_messages(project, limit=self.message_limit)
        manifest = asdict(item)

        current_truth = {
            "identity": {
                "name": item.name,
                "display_name": item.display_name,
                "description": item.description,
                "path": item.path,
                "app_name": item.app_name,
                "deployment_name": item.deployment_name,
                "domain": item.domain,
                "provider_repo": item.provider_repo,
                "remote_url": item.remote_url,
            },
            "git": git,
            "goals": item.goals[-12:],
            "decisions": item.decisions[-12:],
            "project_backlog": [x for x in item.backlog if x.get("status") not in {"done", "deferred"}][:20],
        }
        in_flight = [self._task_view(t) for t in active]
        planned = [self._task_view(t) for t in queued]
        projected = [
            {
                "task_id": t.get("id"),
                "status": t.get("status"),
                "expected_change": t.get("objective"),
                "acceptance": t.get("acceptance") or "",
                "authoritative": False,
            }
            for t in queued
        ]
        owned_contracts = self.store.list_contracts(owner_project=project)
        consumed_contracts = self.store.list_contracts(consumer_project=project)
        contract_forecasts = self.store.list_contract_forecasts(owner_project=project, active_only=True, limit=self.task_limit)
        capsule = {
            "owner": f"{item.display_name or item.name} Project Owner",
            "current_truth": current_truth,
            "in_flight": in_flight,
            "planned": planned,
            "projected_changes": projected,
            "owned_contracts": owned_contracts,
            "consumed_contracts": consumed_contracts,
            "contract_forecasts": contract_forecasts,
            "recent_agent_messages": [self._message_view(m) for m in messages],
            "forecast_rule": "current_truth and published contracts are authoritative; in_flight/planned/projected_changes and contract_forecasts are forecasts until verified and published",
            "manifest_metadata": manifest.get("metadata") or {},
        }
        saved = self.store.save_capsule(project, capsule)
        self._mirror(project, saved)
        return saved

    @staticmethod
    def _task_view(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": task.get("id"), "status": task.get("status"), "priority": task.get("priority"),
            "objective": task.get("objective"), "acceptance": task.get("acceptance"),
            "depends_on": task.get("depends_on") or [], "source": task.get("source"),
            "updated_at": task.get("updated_at"),
        }

    @staticmethod
    def _message_view(message: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": message.get("id"), "from": message.get("from_project"), "to": message.get("to_project"),
            "kind": message.get("kind"), "subject": message.get("subject"), "body": str(message.get("body") or "")[:4000],
            "status": message.get("status"), "response": str(message.get("response") or "")[:4000],
            "created_at": message.get("created_at"),
        }

    def _mirror(self, project: str, payload: dict[str, Any]) -> None:
        import json
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in project)
        path = self.state_dir / "agents" / safe / "capsule.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
