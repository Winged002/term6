from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TASK_STATES = {
    "created", "planned", "queued", "claimed", "executing", "waiting_agent",
    "waiting_dependency", "waiting_human", "verifying", "completed", "failed", "cancelled",
}
MESSAGE_KINDS = {
    "STATE_QUERY", "ANALYSIS_QUERY", "CHANGE_REQUEST", "DEPENDENCY_REQUEST",
    "CONTRACT_PUBLISHED", "CONTRACT_CHANGED", "CONTRACT_FORECAST", "TASK_BLOCKED", "TASK_COMPLETED", "RISK_NOTICE",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class AgentStore:
    """SQLite/WAL durable orchestration state for project-owner agents.

    SQLite owns small coordination records only. Source trees, artifacts and
    verbose model/tool traces remain on disk in their existing stores.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    project TEXT PRIMARY KEY,
                    owner_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    current_task_id TEXT NOT NULL DEFAULT '',
                    capsule_revision INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    acceptance TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 2,
                    source TEXT NOT NULL DEFAULT 'central',
                    parent_task_id TEXT NOT NULL DEFAULT '',
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
                    result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    changed_files_json TEXT NOT NULL DEFAULT '[]',
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY(project) REFERENCES agents(project) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_project_status
                    ON tasks(project, status, priority, created_at);

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    from_project TEXT NOT NULL,
                    to_project TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    task_id TEXT NOT NULL DEFAULT '',
                    correlation_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'sent',
                    response TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    responded_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_messages_to
                    ON messages(to_project, status, created_at);

                CREATE TABLE IF NOT EXISTS capsules (
                    project TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project) REFERENCES agents(project) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS contracts (
                    contract_key TEXT PRIMARY KEY,
                    owner_project TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    interface_json TEXT NOT NULL DEFAULT '{}',
                    compatibility TEXT NOT NULL DEFAULT 'compatible',
                    source_task_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'published',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_project) REFERENCES agents(project) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_contract_owner ON contracts(owner_project, updated_at);

                CREATE TABLE IF NOT EXISTS contract_consumers (
                    contract_key TEXT NOT NULL,
                    project TEXT NOT NULL,
                    last_notified_revision INTEGER NOT NULL DEFAULT 0,
                    subscribed_at TEXT NOT NULL,
                    PRIMARY KEY(contract_key, project),
                    FOREIGN KEY(contract_key) REFERENCES contracts(contract_key) ON DELETE CASCADE,
                    FOREIGN KEY(project) REFERENCES agents(project) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_contract_consumer_project ON contract_consumers(project, contract_key);

                CREATE TABLE IF NOT EXISTS contract_forecasts (
                    id TEXT PRIMARY KEY,
                    contract_key TEXT NOT NULL,
                    owner_project TEXT NOT NULL,
                    state TEXT NOT NULL,
                    expected_change TEXT NOT NULL,
                    interface_delta_json TEXT NOT NULL DEFAULT '{}',
                    task_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_project) REFERENCES agents(project) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_contract_forecasts_key ON contract_forecasts(contract_key, status, created_at);

                CREATE TABLE IF NOT EXISTS orchestrator_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS objectives (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_prompt TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    project_hint TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_objectives_status ON objectives(status, created_at);
                """
            )
            cols = {str(row[1]) for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            if "objective_id" not in cols:
                db.execute("ALTER TABLE tasks ADD COLUMN objective_id TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS idx_agent_tasks_objective ON tasks(objective_id, status, created_at)")

    @staticmethod
    def _task(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        out["depends_on"] = _loads(out.pop("depends_on_json", "[]"), [])
        out["changed_files"] = _loads(out.pop("changed_files_json", "[]"), [])
        return out

    @staticmethod
    def _message(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    def ensure_agent(self, project: str, owner_name: str = "") -> dict[str, Any]:
        project = str(project).strip()
        if not project:
            raise ValueError("project is required")
        owner = owner_name.strip() or f"{project} Project Owner"
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO agents(project, owner_name, status, current_task_id, capsule_revision, last_error, created_at, updated_at)
                   VALUES(?, ?, 'idle', '', 0, '', ?, ?)
                   ON CONFLICT(project) DO UPDATE SET owner_name=excluded.owner_name, updated_at=excluded.updated_at""",
                (project, owner, now, now),
            )
            db.commit()
        return self.get_agent(project) or {}

    def get_agent(self, project: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM agents WHERE project=?", (project,)).fetchone()
        return None if row is None else dict(row)

    def list_agents(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM agents ORDER BY project").fetchall()
            out = []
            for row in rows:
                item = dict(row)
                counts = db.execute(
                    "SELECT status, COUNT(*) n FROM tasks WHERE project=? GROUP BY status", (item["project"],)
                ).fetchall()
                item["queue"] = {str(x["status"]): int(x["n"]) for x in counts}
                out.append(item)
            return out

    def set_agent_state(self, project: str, *, status: str, current_task_id: str = "", last_error: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE agents SET status=?, current_task_id=?, last_error=?, updated_at=? WHERE project=?",
                (status, current_task_id, last_error[:8000], _now(), project),
            )

    def enqueue_task(
        self, project: str, objective: str, *, acceptance: str = "", priority: int = 2,
        source: str = "central", parent_task_id: str = "", depends_on: list[str] | None = None,
        objective_id: str = "",
    ) -> dict[str, Any]:
        objective = str(objective).strip()
        if not objective:
            raise ValueError("objective is required")
        self.ensure_agent(project)
        task_id = "agtask_" + uuid.uuid4().hex[:12]
        now = _now()
        deps = [str(x) for x in (depends_on or []) if str(x).strip()]
        status = "waiting_dependency" if deps else "queued"
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO tasks(id, project, objective, acceptance, status, priority, source, parent_task_id,
                       depends_on_json, created_at, updated_at, objective_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, project, objective[:12000], str(acceptance)[:12000], status,
                 max(0, min(int(priority), 3)), str(source)[:200], str(parent_task_id)[:100], _json(deps), now, now, str(objective_id)[:100]),
            )
        return self.get_task(task_id) or {}

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return self._task(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def list_tasks(self, project: str = "", *, statuses: list[str] | None = None, objective_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks"
        args: list[Any] = []
        where: list[str] = []
        if project:
            where.append("project=?")
            args.append(project)
        if statuses:
            clean = [x for x in statuses if x in TASK_STATES]
            if clean:
                where.append("status IN (" + ",".join("?" for _ in clean) + ")")
                args.extend(clean)
        if objective_id:
            where.append("objective_id=?")
            args.append(objective_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY CASE status WHEN 'executing' THEN 0 WHEN 'claimed' THEN 1 WHEN 'queued' THEN 2 WHEN 'waiting_dependency' THEN 3 ELSE 4 END, priority, created_at LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self._connect() as db:
            return [self._task(r) or {} for r in db.execute(sql, args).fetchall()]

    def _dependencies_complete(self, db: sqlite3.Connection, task: sqlite3.Row) -> bool:
        deps = _loads(task["depends_on_json"], [])
        if not deps:
            return True
        rows = db.execute(
            "SELECT id,status FROM tasks WHERE id IN (" + ",".join("?" for _ in deps) + ")", deps
        ).fetchall()
        states = {str(r["id"]): str(r["status"]) for r in rows}
        return all(states.get(dep) == "completed" for dep in deps)

    def release_ready_dependencies(self, project: str = "") -> int:
        return len(self.release_ready_dependencies_detail(project))

    def claim_next(self, project: str, worker_id: str, *, lease_seconds: int = 1800) -> dict[str, Any] | None:
        self.release_ready_dependencies(project)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=max(60, int(lease_seconds)))).isoformat()
        now_s = now.isoformat()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM tasks WHERE project=? AND status='queued' ORDER BY priority, created_at", (project,)
            ).fetchall()
            selected = next((row for row in rows if self._dependencies_complete(db, row)), None)
            if selected is None:
                db.commit()
                return None
            db.execute(
                """UPDATE tasks SET status='claimed', lease_owner=?, lease_expires_at=?, updated_at=?
                   WHERE id=? AND status='queued'""",
                (worker_id, expires, now_s, selected["id"]),
            )
            db.commit()
        return self.get_task(str(selected["id"]))

    def update_task(self, task_id: str, *, status: str | None = None, result: str | None = None,
                    error: str | None = None, changed_files: list[str] | None = None) -> dict[str, Any]:
        if status is not None and status not in TASK_STATES:
            raise ValueError(f"unsupported task status: {status}")
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown agent task: {task_id}")
        fields = ["updated_at=?"]
        args: list[Any] = [_now()]
        if status is not None:
            fields.append("status=?"); args.append(status)
            if status == "executing" and not task.get("started_at"):
                fields.append("started_at=?"); args.append(_now())
            if status in {"completed", "failed", "cancelled"}:
                fields.append("finished_at=?"); args.append(_now())
                fields.append("lease_owner=''")
                fields.append("lease_expires_at=NULL")
        if result is not None:
            fields.append("result=?"); args.append(str(result)[:100000])
        if error is not None:
            fields.append("error=?"); args.append(str(error)[:20000])
        if changed_files is not None:
            fields.append("changed_files_json=?"); args.append(_json(sorted(set(changed_files))))
        args.append(task_id)
        with self._lock, self._connect() as db:
            db.execute("UPDATE tasks SET " + ",".join(fields) + " WHERE id=?", args)
        return self.get_task(task_id) or {}

    def heartbeat(self, task_id: str, worker_id: str, *, lease_seconds: int = 1800) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(seconds=max(60, int(lease_seconds)))).isoformat()
        with self._connect() as db:
            db.execute(
                "UPDATE tasks SET lease_expires_at=?, updated_at=? WHERE id=? AND lease_owner=? AND status IN ('claimed','executing','verifying')",
                (expires, _now(), task_id, worker_id),
            )

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown agent task: {task_id}")
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        return self.update_task(task_id, status="cancelled")

    def retry_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown agent task: {task_id}")
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE tasks SET status='queued', result='', error='', changed_files_json='[]', lease_owner='',
                   lease_expires_at=NULL, started_at=NULL, finished_at=NULL, updated_at=? WHERE id=?""",
                (_now(), task_id),
            )
        return self.get_task(task_id) or {}

    def create_objective(self, title: str, *, source_prompt: str = "", project_hint: str = "", run_id: str = "") -> dict[str, Any]:
        objective_id = "obj_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO objectives(id,title,source_prompt,status,project_hint,run_id,outcome,created_at,updated_at)
                   VALUES(?,?,?,'active',?,?, '',?,?)""",
                (objective_id, str(title)[:500], str(source_prompt)[:16000], str(project_hint)[:200], str(run_id)[:100], now, now),
            )
        return self.get_objective(objective_id) or {}

    def get_objective(self, objective_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM objectives WHERE id=?", (objective_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["tasks"] = self.list_tasks(objective_id=objective_id, limit=1000)
        return out

    def list_objectives(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM objectives ORDER BY created_at DESC LIMIT ?", (max(1,min(int(limit),1000)),)).fetchall()
        out=[]
        for row in rows:
            item=dict(row); tasks=self.list_tasks(objective_id=str(item["id"]),limit=1000)
            item["tasks"]=tasks
            counts={}
            for task in tasks: counts[str(task.get("status") or "unknown")]=counts.get(str(task.get("status") or "unknown"),0)+1
            item["counts"]=counts
            out.append(item)
        return out

    def update_objective(self, objective_id: str, *, status: str | None = None, outcome: str | None = None) -> dict[str, Any]:
        current=self.get_objective(objective_id)
        if current is None: raise KeyError(f"unknown objective: {objective_id}")
        fields=["updated_at=?"]; args=[_now()]
        if status is not None:
            fields.append("status=?"); args.append(str(status))
            if status in {"completed","failed","cancelled"}: fields.append("finished_at=?"); args.append(_now())
        if outcome is not None: fields.append("outcome=?"); args.append(str(outcome)[:50000])
        args.append(objective_id)
        with self._connect() as db: db.execute("UPDATE objectives SET "+",".join(fields)+" WHERE id=?",args)
        return self.get_objective(objective_id) or {}

    def sync_objective_status(self, objective_id: str) -> dict[str, Any] | None:
        obj=self.get_objective(objective_id)
        if obj is None: return None
        tasks=obj.get("tasks") or []
        if not tasks: return obj
        states={str(t.get("status") or "") for t in tasks}
        if any(x in states for x in {"queued","claimed","executing","waiting_agent","waiting_dependency","waiting_human","verifying","planned","created"}): status="active"
        elif states and states <= {"completed"}: status="completed"
        elif "failed" in states: status="failed"
        elif states <= {"cancelled","completed"}: status="cancelled"
        else: status="active"
        if status != obj.get("status"): return self.update_objective(objective_id,status=status)
        return obj

    def move_task(self, task_id: str, project: str) -> dict[str, Any]:
        task=self.get_task(task_id)
        if task is None: raise KeyError(f"unknown agent task: {task_id}")
        self.ensure_agent(project)
        if task.get("status") in {"claimed","executing","verifying","completed"}:
            raise ValueError("only queued, waiting, failed, or cancelled tasks may be moved")
        status=str(task.get("status") or "queued")
        if status in {"failed","cancelled"}: status="queued"
        with self._connect() as db:
            db.execute("UPDATE tasks SET project=?, status=?, lease_owner='', lease_expires_at=NULL, updated_at=? WHERE id=?",(project,status,_now(),task_id))
        return self.get_task(task_id) or {}

    def reprioritize_task(self, task_id: str, priority: int) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown agent task: {task_id}")
        if task["status"] in {"completed", "cancelled"}:
            return task
        priority = max(0, min(int(priority), 3))
        with self._connect() as db:
            db.execute("UPDATE tasks SET priority=?, updated_at=? WHERE id=?", (priority, _now(), task_id))
        return self.get_task(task_id) or {}

    def recover_after_restart(self) -> list[dict[str, Any]]:
        # The loopback runtime is single-instance. A new orchestrator process
        # therefore owns any in-flight lease left behind by the previous one.
        now = _now()
        recovered: list[dict[str, Any]] = []
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM tasks WHERE status IN ('claimed','executing','verifying') ORDER BY updated_at"
            ).fetchall()
            for row in rows:
                next_status = "queued" if self._dependencies_complete(db, row) else "waiting_dependency"
                db.execute(
                    """UPDATE tasks SET status=?, lease_owner='', lease_expires_at=NULL,
                       error='Recovered after runtime restart', updated_at=? WHERE id=?""",
                    (next_status, now, row["id"]),
                )
                item = self._task(row) or {}
                item["previous_status"] = str(row["status"])
                item["status"] = next_status
                recovered.append(item)
            if rows:
                db.execute(
                    "UPDATE agents SET status='idle', current_task_id='', last_error='', updated_at=? WHERE status IN ('working','recovering') OR current_task_id<>''",
                    (now,),
                )
            db.commit()
        return recovered

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connect() as db:
            row = db.execute("SELECT value_json FROM orchestrator_settings WHERE key=?", (str(key),)).fetchone()
        if row is None:
            return default
        return _loads(str(row["value_json"]), default)

    def set_setting(self, key: str, value: Any) -> Any:
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO orchestrator_settings(key,value_json,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (str(key), _json(value), now),
            )
        return value

    def settings(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT key,value_json FROM orchestrator_settings ORDER BY key").fetchall()
        return {str(r["key"]): _loads(str(r["value_json"]), None) for r in rows}

    def recover_stale_leases(self) -> int:
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id FROM tasks WHERE status IN ('claimed','executing','verifying') AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (now,),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE tasks SET status='queued', lease_owner='', lease_expires_at=NULL, error='Recovered after stale worker lease', updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
            if rows:
                db.execute(
                    "UPDATE agents SET status='idle', current_task_id='', updated_at=? WHERE current_task_id IN (" + ",".join("?" for _ in rows) + ")",
                    [now, *[r["id"] for r in rows]],
                )
            db.commit()
            return len(rows)

    def send_message(self, from_project: str, to_project: str, kind: str, body: str, *,
                     subject: str = "", task_id: str = "", correlation_id: str = "") -> dict[str, Any]:
        kind = kind.upper().strip()
        if kind not in MESSAGE_KINDS:
            raise ValueError("unsupported agent message kind")
        msg_id = "agmsg_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO messages(id,from_project,to_project,kind,subject,body,task_id,correlation_id,status,response,created_at)
                   VALUES(?,?,?,?,?,?,?,?, 'sent','',?)""",
                (msg_id, from_project, to_project, kind, subject[:500], body[:20000], task_id[:100], correlation_id[:100], now),
            )
        return self.get_message(msg_id) or {}

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return self._message(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def list_messages(self, project: str = "", *, direction: str = "both", limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        if not project:
            sql, args = "SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,)
        elif direction == "in":
            sql, args = "SELECT * FROM messages WHERE to_project=? ORDER BY created_at DESC LIMIT ?", (project, limit)
        elif direction == "out":
            sql, args = "SELECT * FROM messages WHERE from_project=? ORDER BY created_at DESC LIMIT ?", (project, limit)
        else:
            sql, args = "SELECT * FROM messages WHERE from_project=? OR to_project=? ORDER BY created_at DESC LIMIT ?", (project, project, limit)
        with self._connect() as db:
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    def task_graph(self, *, limit: int = 500) -> dict[str, Any]:
        tasks = self.list_tasks("", limit=max(1, min(int(limit), 1000)))
        ids = {str(t.get("id") or "") for t in tasks}
        edges: list[dict[str, str]] = []
        for task in tasks:
            target = str(task.get("id") or "")
            for dep in task.get("depends_on") or []:
                if str(dep) in ids:
                    edges.append({"from": str(dep), "to": target})
        return {"nodes": tasks, "edges": edges}

    def respond_message(self, message_id: str, response: str) -> dict[str, Any]:
        with self._connect() as db:
            db.execute(
                "UPDATE messages SET status='answered', response=?, responded_at=? WHERE id=?",
                (str(response)[:30000], _now(), message_id),
            )
        return self.get_message(message_id) or {}

    def save_capsule(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_agent(project)
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM capsules WHERE project=?", (project,)).fetchone()
            revision = int(row["revision"] if row else 0) + 1
            enriched = dict(payload)
            enriched["project"] = project
            enriched["revision"] = revision
            enriched["updated_at"] = now
            db.execute(
                """INSERT INTO capsules(project,revision,payload_json,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(project) DO UPDATE SET revision=excluded.revision,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (project, revision, _json(enriched), now),
            )
            db.execute("UPDATE agents SET capsule_revision=?, updated_at=? WHERE project=?", (revision, now, project))
            db.commit()
        return enriched

    def get_capsule(self, project: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM capsules WHERE project=?", (project,)).fetchone()
        return None if row is None else _loads(row["payload_json"], {})

    def add_dependency(self, task_id: str, depends_on_id: str) -> dict[str, Any]:
        if task_id == depends_on_id:
            raise ValueError("a task cannot depend on itself")
        task = self.get_task(task_id)
        dep = self.get_task(depends_on_id)
        if task is None:
            raise KeyError(f"unknown agent task: {task_id}")
        if dep is None:
            raise KeyError(f"unknown dependency task: {depends_on_id}")
        deps = list(task.get("depends_on") or [])
        if depends_on_id not in deps:
            deps.append(depends_on_id)
        status = str(task.get("status") or "queued")
        if status not in {"completed", "failed", "cancelled", "executing", "verifying"} and dep.get("status") != "completed":
            status = "waiting_dependency"
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE tasks SET depends_on_json=?, status=?, updated_at=? WHERE id=?",
                (_json(deps), status, _now(), task_id),
            )
        return self.get_task(task_id) or {}

    def dependency_state(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown agent task: {task_id}")
        deps = list(task.get("depends_on") or [])
        rows = [self.get_task(dep) for dep in deps]
        return {
            "task_id": task_id,
            "ready": all(row and row.get("status") == "completed" for row in rows),
            "dependencies": [row for row in rows if row is not None],
            "missing": [dep for dep, row in zip(deps, rows) if row is None],
        }

    def release_ready_dependencies_detail(self, project: str = "") -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM tasks WHERE status='waiting_dependency'" + (" AND project=?" if project else ""),
                ((project,) if project else ()),
            ).fetchall()
            released: list[str] = []
            for row in rows:
                if self._dependencies_complete(db, row):
                    db.execute("UPDATE tasks SET status='queued', error='', updated_at=? WHERE id=?", (_now(), row["id"]))
                    released.append(str(row["id"]))
            db.commit()
        return [self.get_task(task_id) or {} for task_id in released]

    def list_pending_messages(self, project: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM messages WHERE status='sent'"
        args: list[Any] = []
        if project:
            sql += " AND to_project=?"
            args.append(project)
        sql += " ORDER BY created_at LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self._connect() as db:
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    def mark_message_status(self, message_id: str, status: str) -> dict[str, Any]:
        if status not in {"sent", "delivered", "answered", "failed"}:
            raise ValueError("unsupported message status")
        with self._connect() as db:
            db.execute("UPDATE messages SET status=? WHERE id=?", (status, message_id))
        msg = self.get_message(message_id)
        if msg is None:
            raise KeyError(f"unknown agent message: {message_id}")
        return msg

    @staticmethod
    def _contract(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        out["interface"] = _loads(out.pop("interface_json", "{}"), {})
        return out

    @staticmethod
    def _forecast(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        out["interface_delta"] = _loads(out.pop("interface_delta_json", "{}"), {})
        out["authoritative"] = False
        return out

    def publish_contract(self, contract_key: str, owner_project: str, *, summary: str = "",
                         interface: dict[str, Any] | None = None, consumers: list[str] | None = None,
                         compatibility: str = "compatible", source_task_id: str = "") -> dict[str, Any]:
        key = str(contract_key).strip()
        if not key:
            raise ValueError("contract_key is required")
        self.ensure_agent(owner_project)
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM contracts WHERE contract_key=?", (key,)).fetchone()
            if existing is not None and str(existing["owner_project"]) != owner_project:
                db.rollback()
                raise ValueError(f"contract {key} is owned by {existing['owner_project']}")
            revision = int(existing["revision"] if existing else 0) + 1
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """INSERT INTO contracts(contract_key,owner_project,revision,summary,interface_json,compatibility,source_task_id,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'published',?,?)
                   ON CONFLICT(contract_key) DO UPDATE SET owner_project=excluded.owner_project,revision=excluded.revision,
                   summary=excluded.summary,interface_json=excluded.interface_json,compatibility=excluded.compatibility,
                   source_task_id=excluded.source_task_id,status='published',updated_at=excluded.updated_at""",
                (key, owner_project, revision, str(summary)[:12000], _json(interface or {}), str(compatibility)[:100],
                 str(source_task_id)[:100], created_at, now),
            )
            for project in sorted(set(str(x).strip() for x in (consumers or []) if str(x).strip())):
                if project != owner_project:
                    # Consumers should already be registered projects/agents; fail early on typos.
                    found = db.execute("SELECT 1 FROM agents WHERE project=?", (project,)).fetchone()
                    if found is None:
                        db.rollback()
                        raise KeyError(f"unknown contract consumer project: {project}")
                    db.execute(
                        """INSERT INTO contract_consumers(contract_key,project,last_notified_revision,subscribed_at)
                           VALUES(?,?,0,?) ON CONFLICT(contract_key,project) DO NOTHING""",
                        (key, project, now),
                    )
            db.execute(
                "UPDATE contract_forecasts SET status='resolved', updated_at=? WHERE contract_key=? AND owner_project=? AND status='active'",
                (now, key, owner_project),
            )
            db.commit()
        out = self.get_contract(key) or {}
        out["change_kind"] = "published" if existing is None else "changed"
        return out

    def get_contract(self, contract_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM contracts WHERE contract_key=?", (contract_key,)).fetchone()
            out = self._contract(row)
            if out is None:
                return None
            consumers = db.execute(
                "SELECT project,last_notified_revision,subscribed_at FROM contract_consumers WHERE contract_key=? ORDER BY project",
                (contract_key,),
            ).fetchall()
        out["consumers"] = [dict(x) for x in consumers]
        out["authoritative"] = True
        return out

    def list_contracts(self, *, owner_project: str = "", consumer_project: str = "") -> list[dict[str, Any]]:
        with self._connect() as db:
            if owner_project:
                rows = db.execute("SELECT * FROM contracts WHERE owner_project=? ORDER BY contract_key", (owner_project,)).fetchall()
            elif consumer_project:
                rows = db.execute(
                    """SELECT c.* FROM contracts c JOIN contract_consumers cc ON cc.contract_key=c.contract_key
                       WHERE cc.project=? ORDER BY c.contract_key""", (consumer_project,),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM contracts ORDER BY contract_key").fetchall()
        return [self.get_contract(str(row["contract_key"])) or {} for row in rows]

    def subscribe_contract(self, contract_key: str, project: str) -> dict[str, Any]:
        contract = self.get_contract(contract_key)
        if contract is None:
            raise KeyError(f"unknown contract: {contract_key}")
        self.ensure_agent(project)
        if project == contract.get("owner_project"):
            return contract
        with self._connect() as db:
            db.execute(
                """INSERT INTO contract_consumers(contract_key,project,last_notified_revision,subscribed_at)
                   VALUES(?,?,0,?) ON CONFLICT(contract_key,project) DO NOTHING""",
                (contract_key, project, _now()),
            )
        return self.get_contract(contract_key) or {}

    def mark_contract_notified(self, contract_key: str, project: str, revision: int) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE contract_consumers SET last_notified_revision=? WHERE contract_key=? AND project=?",
                (int(revision), contract_key, project),
            )

    def save_contract_forecast(self, contract_key: str, owner_project: str, *, state: str,
                               expected_change: str, interface_delta: dict[str, Any] | None = None,
                               task_id: str = "") -> dict[str, Any]:
        state = str(state).strip().lower()
        if state not in {"in_flight", "planned", "projected"}:
            raise ValueError("forecast state must be in_flight, planned, or projected")
        self.ensure_agent(owner_project)
        current = self.get_contract(contract_key)
        if current is not None and current.get("owner_project") != owner_project:
            raise ValueError(f"contract {contract_key} is owned by {current.get('owner_project')}")
        forecast_id = "agfc_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO contract_forecasts(id,contract_key,owner_project,state,expected_change,interface_delta_json,task_id,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'active',?,?)""",
                (forecast_id, contract_key, owner_project, state, str(expected_change)[:16000],
                 _json(interface_delta or {}), str(task_id)[:100], now, now),
            )
        return self.get_contract_forecast(forecast_id) or {}

    def get_contract_forecast(self, forecast_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return self._forecast(db.execute("SELECT * FROM contract_forecasts WHERE id=?", (forecast_id,)).fetchone())

    def list_contract_forecasts(self, *, contract_key: str = "", owner_project: str = "", active_only: bool = True,
                                limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM contract_forecasts"
        where: list[str] = []
        args: list[Any] = []
        if contract_key:
            where.append("contract_key=?"); args.append(contract_key)
        if owner_project:
            where.append("owner_project=?"); args.append(owner_project)
        if active_only:
            where.append("status='active'")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self._connect() as db:
            return [self._forecast(r) or {} for r in db.execute(sql, args).fetchall()]

    def contract_impact(self, contract_key: str) -> dict[str, Any]:
        contract = self.get_contract(contract_key)
        if contract is None:
            raise KeyError(f"unknown contract: {contract_key}")
        consumers = []
        for sub in contract.get("consumers") or []:
            project = str(sub.get("project") or "")
            agent = self.get_agent(project) or {}
            active = self.list_tasks(project, statuses=["queued", "claimed", "executing", "waiting_dependency", "waiting_agent", "verifying"], limit=50)
            consumers.append({"project": project, "agent_status": agent.get("status", "unknown"),
                              "current_task_id": agent.get("current_task_id", ""), "active_tasks": active,
                              "last_notified_revision": sub.get("last_notified_revision", 0)})
        return {
            "contract": contract,
            "forecasts": self.list_contract_forecasts(contract_key=contract_key, active_only=True),
            "consumers": consumers,
            "impact_count": len(consumers),
        }

