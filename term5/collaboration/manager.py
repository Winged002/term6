from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TASK_FORMAT = 1
RUN_FORMAT = 1
OPEN_STATES = {"open", "viewed", "deferred"}
RESOLVED_STATES = {"resolved", "auto_resolved"}
TIMEOUT_POLICIES = {"AUTO_DECIDE", "DEFER", "BLOCK"}
TASK_KINDS = {"choose", "configure", "approve", "answer", "authorize", "review", "resolve"}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class DurableRunStore:
    """Small durable run ledger used by term_6 resumable collaboration.

    Conversation history remains the model context. This store is orchestration
    metadata only: objective, project, state, pending human tasks, and outcome.
    """

    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "runs_v6.json"
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._runs = {}
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != RUN_FORMAT or not isinstance(raw.get("runs"), dict):
                raise RuntimeError("unsupported or corrupt term_6 durable run store")
            self._runs = raw["runs"]

    def save(self) -> None:
        with self._lock:
            _atomic_json(self.path, {"format": RUN_FORMAT, "runs": self._runs})

    def start(self, objective: str, *, project: str = "", run_id: str = "") -> dict[str, Any]:
        with self._lock:
            rid = str(run_id or "run_" + uuid.uuid4().hex[:12])
            old = self._runs.get(rid)
            if old:
                old["status"] = "running"
                old["updated_at"] = _now()
                old["resume_count"] = int(old.get("resume_count") or 0) + 1
                self.save()
                return dict(old)
            rec = {
                "id": rid,
                "objective": str(objective or "")[:12000],
                "project": str(project or ""),
                "status": "running",
                "checkpoint": "turn_start",
                "human_tasks": [],
                "created_at": _now(),
                "updated_at": _now(),
                "finished_at": "",
                "resume_count": 0,
                "outcome": "",
                "last_error": "",
            }
            self._runs[rid] = rec
            self.save()
            return dict(rec)

    def update(self, run_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            rec = self._runs.get(str(run_id or ""))
            if rec is None:
                return None
            for key, value in changes.items():
                rec[key] = value
            rec["updated_at"] = _now()
            if rec.get("status") in {"completed", "completed_with_pending", "failed", "cancelled"} and not rec.get("finished_at"):
                rec["finished_at"] = _now()
            self.save()
            return dict(rec)

    def attach_task(self, run_id: str, task_id: str, *, blocking: bool) -> None:
        with self._lock:
            rec = self._runs.get(str(run_id or ""))
            if rec is None:
                return
            ids = list(rec.get("human_tasks") or [])
            if task_id not in ids:
                ids.append(task_id)
            rec["human_tasks"] = ids[-100:]
            rec["status"] = "waiting_human" if blocking else "running"
            rec["checkpoint"] = "waiting_human" if blocking else rec.get("checkpoint", "running")
            rec["updated_at"] = _now()
            self.save()

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._runs.get(str(run_id or ""))
            return dict(rec) if rec else None

    def list(self, *, limit: int = 100, status: str = "") -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._runs.values())
        if status:
            rows = [x for x in rows if str(x.get("status")) == status]
        rows.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
        return [dict(x) for x in rows[: max(1, min(int(limit), 500))]]


class HumanTaskManager:
    """Global human task queue for term_6.

    Secrets are never stored in human task responses. Configure tasks carry
    variable *names* only and route the user to the trusted Configuration UI.
    """

    def __init__(self, state_dir: Path, runs: DurableRunStore, *, default_timeout_s: int = 30) -> None:
        self.path = Path(state_dir) / "human_tasks.json"
        self.runs = runs
        self.default_timeout_s = max(5, int(default_timeout_s or 30))
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self.active_run_id = ""
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._tasks = {}
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != TASK_FORMAT or not isinstance(raw.get("tasks"), dict):
                raise RuntimeError("unsupported or corrupt term_6 human task store")
            self._tasks = raw["tasks"]

    def save(self) -> None:
        with self._lock:
            _atomic_json(self.path, {"format": TASK_FORMAT, "tasks": self._tasks})

    def set_active_run(self, run_id: str) -> None:
        self.active_run_id = str(run_id or "")

    def create(self, *, kind: str, title: str, description: str = "", project: str = "", run_id: str = "",
               options: list[dict[str, Any]] | None = None, fields: list[dict[str, Any]] | None = None,
               timeout_policy: str = "BLOCK", timeout_seconds: int | None = None,
               default_option: str = "", blocking: bool = True, resume_instruction: str = "",
               priority: str = "normal", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        kind = str(kind or "answer").lower()
        if kind not in TASK_KINDS:
            raise ValueError(f"unsupported human task kind: {kind}")
        policy = str(timeout_policy or "BLOCK").upper()
        if policy not in TIMEOUT_POLICIES:
            raise ValueError("timeout_policy must be AUTO_DECIDE, DEFER or BLOCK")
        if kind == "approve" and policy == "AUTO_DECIDE":
            raise ValueError("approval tasks must use BLOCK or DEFER; they cannot auto-approve")
        opts = []
        for raw in options or []:
            if not isinstance(raw, dict):
                continue
            oid = str(raw.get("id") or raw.get("label") or "").strip()
            label = str(raw.get("label") or oid).strip()
            if not oid or not label:
                continue
            opts.append({
                "id": oid[:120], "label": label[:240],
                "description": str(raw.get("description") or "")[:2000],
                "artifact_id": str(raw.get("artifact_id") or "")[:80],
                "recommended": bool(raw.get("recommended")),
            })
        flds = []
        for raw in fields or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip().upper()
            if not name:
                continue
            flds.append({
                "name": name[:160], "label": str(raw.get("label") or name)[:240],
                "secret": bool(raw.get("secret")), "required": bool(raw.get("required", True)),
                "service": str(raw.get("service") or "application")[:120],
            })
        seconds = max(5, min(int(timeout_seconds or self.default_timeout_s), 86400))
        deadline = ""
        if policy in {"AUTO_DECIDE", "DEFER"}:
            deadline = (_now_dt() + timedelta(seconds=seconds)).isoformat()
        rid = str(run_id or self.active_run_id or "")
        tid = "ht_" + uuid.uuid4().hex[:12]
        rec = {
            "id": tid, "kind": kind, "title": str(title or kind.title())[:500],
            "description": str(description or "")[:6000], "project": str(project or "")[:240],
            "run_id": rid, "status": "open", "priority": str(priority or "normal")[:40],
            "blocking": bool(blocking), "options": opts[:12], "fields": flds[:100],
            "timeout_policy": policy, "timeout_seconds": seconds, "deadline_at": deadline,
            "default_option": str(default_option or "")[:120], "response": {},
            "resume_instruction": str(resume_instruction or "")[:8000],
            "metadata": metadata or {}, "created_at": _now(), "updated_at": _now(),
            "resolved_at": "", "resumed_at": "",
        }
        with self._lock:
            self._tasks[tid] = rec
            self.save()
        if rid:
            self.runs.attach_task(rid, tid, blocking=bool(blocking))
        return self.public(rec)

    @staticmethod
    def public(rec: dict[str, Any]) -> dict[str, Any]:
        # The queue never contains secret values by design. Return a defensive
        # copy and only the fields useful to the local workbench/model status.
        out = dict(rec)
        out["options"] = [dict(x) for x in rec.get("options") or []]
        out["fields"] = [dict(x) for x in rec.get("fields") or []]
        out["metadata"] = dict(rec.get("metadata") or {})
        out["response"] = dict(rec.get("response") or {})
        return out

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._tasks.get(str(task_id or ""))
            return self.public(rec) if rec else None

    def list(self, *, state: str = "open", project: str = "", limit: int = 200) -> list[dict[str, Any]]:
        self.tick()
        with self._lock:
            rows = list(self._tasks.values())
        if state == "open":
            rows = [x for x in rows if x.get("status") in OPEN_STATES]
        elif state == "completed":
            rows = [x for x in rows if x.get("status") in RESOLVED_STATES | {"cancelled", "expired"}]
        elif state:
            rows = [x for x in rows if x.get("status") == state]
        if project:
            rows = [x for x in rows if str(x.get("project") or "") == project]
        rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        rows.sort(key=lambda x: (
            0 if x.get("blocking") else 1,
            rank.get(str(x.get("priority") or "normal"), 2),
            str(x.get("created_at") or ""),
        ))
        return [self.public(x) for x in rows[: max(1, min(int(limit), 1000))]]

    def stats(self) -> dict[str, int]:
        rows = self.list(state="open", limit=1000)
        return {
            "open": len(rows),
            "blocking": sum(1 for x in rows if x.get("blocking")),
            "configuration": sum(1 for x in rows if x.get("kind") == "configure"),
            "creative": sum(1 for x in rows if x.get("kind") == "choose"),
            "approvals": sum(1 for x in rows if x.get("kind") == "approve"),
        }

    def context_text(self, *, limit: int = 20) -> str:
        rows = self.list(state="open", limit=limit)
        if not rows:
            return ""
        lines = ["[term_6 My Tasks — durable human collaboration state]"]
        for row in rows:
            lines.append(
                f"- {row.get('id')} | {row.get('kind')} | {row.get('title')} | project={row.get('project') or '-'} | "
                f"blocking={bool(row.get('blocking'))} | timeout={row.get('timeout_policy')} | status={row.get('status')}"
            )
        lines.append("Do not duplicate an existing task. Continue autonomous work that is not actually blocked.")
        return "\n".join(lines)

    def mark_viewed(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec.get("status") == "open":
                rec["status"] = "viewed"; rec["updated_at"] = _now(); self.save()
            return self.public(rec) if rec else None

    def resolve(self, task_id: str, *, option_id: str = "", answer: str = "", auto: bool = False) -> dict[str, Any]:
        with self._lock:
            rec = self._tasks.get(str(task_id or ""))
            if rec is None:
                raise KeyError(f"unknown human task: {task_id}")
            if rec.get("kind") == "configure":
                raise ValueError("configuration tasks must be completed through the trusted Configuration UI")
            if rec.get("status") in RESOLVED_STATES:
                return self.public(rec)
            if rec.get("kind") == "approve" and auto:
                raise ValueError("approval tasks cannot be auto-resolved")
            if auto and not option_id and rec.get("options"):
                option_id = str(rec.get("default_option") or "")
                if not option_id:
                    options = rec.get("options") or []
                    preferred = next((x for x in options if x.get("recommended")), options[0])
                    option_id = str(preferred.get("id") or "")
            if option_id:
                valid = {str(x.get("id")) for x in rec.get("options") or []}
                if valid and option_id not in valid:
                    raise ValueError("unknown task option")
            rec["response"] = {"option_id": str(option_id or "")[:120], "answer": str(answer or "")[:8000]}
            rec["status"] = "auto_resolved" if auto else "resolved"
            rec["resolved_at"] = _now(); rec["updated_at"] = _now(); rec["deadline_at"] = ""
            self.save()
            return self.public(rec)

    def defer(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            rec = self._tasks.get(str(task_id or ""))
            if rec is None:
                raise KeyError(f"unknown human task: {task_id}")
            if rec.get("status") in RESOLVED_STATES:
                return self.public(rec)
            rec["status"] = "deferred"; rec["blocking"] = False; rec["deadline_at"] = ""; rec["updated_at"] = _now()
            run_id = str(rec.get("run_id") or "")
            self.save()
            if run_id:
                self.runs.update(run_id, status="completed_with_pending", checkpoint="deferred_human_input")
            return self.public(rec)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            rec = self._tasks.get(str(task_id or ""))
            if rec is None:
                raise KeyError(f"unknown human task: {task_id}")
            rec["status"] = "cancelled"; rec["updated_at"] = _now(); rec["resolved_at"] = _now(); rec["deadline_at"] = ""
            self.save(); return self.public(rec)

    def tick(self) -> list[dict[str, Any]]:
        changed: list[dict[str, Any]] = []
        now = _now_dt()
        with self._lock:
            for rec in self._tasks.values():
                if rec.get("status") not in {"open", "viewed"}:
                    continue
                deadline = _parse(str(rec.get("deadline_at") or ""))
                if deadline is None or deadline > now:
                    continue
                policy = str(rec.get("timeout_policy") or "BLOCK")
                if policy == "AUTO_DECIDE":
                    if rec.get("kind") == "approve":
                        continue
                    options = rec.get("options") or []
                    default = str(rec.get("default_option") or "")
                    if not default and options:
                        preferred = next((x for x in options if x.get("recommended")), options[0])
                        default = str(preferred.get("id") or "")
                    rec["response"] = {"option_id": default, "answer": ""}
                    rec["status"] = "auto_resolved"; rec["resolved_at"] = _now(); rec["updated_at"] = _now(); rec["deadline_at"] = ""
                    changed.append(self.public(rec))
                elif policy == "DEFER":
                    rec["status"] = "deferred"; rec["blocking"] = False; rec["deadline_at"] = ""; rec["updated_at"] = _now()
                    changed.append(self.public(rec))
                # BLOCK deliberately has no timeout mutation.
            if changed:
                self.save()
        for item in changed:
            if item.get("status") == "deferred" and item.get("run_id"):
                self.runs.update(str(item.get("run_id")), status="completed_with_pending", checkpoint="deferred_human_input")
        return changed

    def resumable(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [x for x in self._tasks.values() if x.get("status") in RESOLVED_STATES and x.get("run_id") and not x.get("resumed_at")]
        rows.sort(key=lambda x: str(x.get("resolved_at") or ""))
        return [self.public(x) for x in rows]

    def mark_resumed(self, task_id: str) -> None:
        with self._lock:
            rec = self._tasks.get(str(task_id or ""))
            if rec:
                rec["resumed_at"] = _now(); rec["updated_at"] = _now(); self.save()

    def open_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [self.public(x) for x in self._tasks.values() if x.get("run_id") == run_id and x.get("status") in OPEN_STATES]
        return rows

    def complete_configuration(self, *, project: str, environment: str, configured_keys: set[str]) -> list[dict[str, Any]]:
        completed: list[dict[str, Any]] = []
        with self._lock:
            for rec in self._tasks.values():
                if rec.get("kind") != "configure" or rec.get("status") not in OPEN_STATES:
                    continue
                meta = rec.get("metadata") or {}
                if str(rec.get("project") or "") != str(project or ""):
                    continue
                if str(meta.get("environment") or "production") != str(environment or "production"):
                    continue
                names = {str(x.get("name") or "") for x in rec.get("fields") or [] if x.get("required", True)}
                if names and names.issubset(configured_keys):
                    rec["response"] = {"configured": sorted(names)}
                    rec["status"] = "resolved"; rec["resolved_at"] = _now(); rec["updated_at"] = _now(); rec["deadline_at"] = ""
                    completed.append(self.public(rec))
            if completed:
                self.save()
        return completed
