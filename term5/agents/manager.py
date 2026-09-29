from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..models import ReasoningMode
from ..state.episodes import EpisodeStore
from .capsule import ProjectCapsuleBuilder
from .owner import ProjectOwnerAgent
from .store import AgentStore


class AsyncCapacityLimiter:
    """Runtime-adjustable async capacity limiter with observable active/waiting counts."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.active = 0
        self.waiting = 0
        self._condition = asyncio.Condition()

    async def __aenter__(self):
        async with self._condition:
            self.waiting += 1
            try:
                await self._condition.wait_for(lambda: self.active < self.limit)
                self.active += 1
            finally:
                self.waiting = max(0, self.waiting - 1)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        async with self._condition:
            self.active = max(0, self.active - 1)
            self._condition.notify_all()

    async def set_limit(self, limit: int) -> int:
        async with self._condition:
            self.limit = max(1, min(int(limit), 64))
            self._condition.notify_all()
            return self.limit

    def snapshot(self) -> dict[str, int]:
        return {"limit": int(self.limit), "active": int(self.active), "waiting": int(self.waiting)}


class AgentOrchestrator:
    """Central coordinator and typed message mesh for persistent Project Owners."""

    def __init__(self, *, config, projects, provider, tools, events, redactor) -> None:
        self.config = config
        self.projects = projects
        self.provider = provider
        self.tools = tools
        self.events = events
        self.redactor = redactor
        self.store = AgentStore(config.state_dir / "orchestrator.sqlite3")
        self.capsules = ProjectCapsuleBuilder(
            projects, self.store, config.state_dir,
            task_limit=config.agents.capsule_task_limit,
            message_limit=config.agents.capsule_message_limit,
        )
        self._owners: dict[str, ProjectOwnerAgent] = {}
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._worker_tasks: dict[str, asyncio.Task] = {}
        self._scheduler_stop = asyncio.Event()
        owner_limit = int(self.store.get_setting("max_concurrent_owners", config.agents.max_concurrent_owners))
        query_limit = int(self.store.get_setting("max_concurrent_queries", getattr(config.agents, "max_concurrent_queries", 4)))
        self._concurrency = AsyncCapacityLimiter(owner_limit)
        self._query_concurrency = AsyncCapacityLimiter(query_limit)
        self._startup_recovered = self.store.recover_after_restart()
        self._recovery_total = len(self._startup_recovered)
        self._last_recovery_at = datetime.now(timezone.utc).isoformat() if self._startup_recovered else ""
        self._active_objective_id = ""
        self.sync_projects(refresh=True)

    def sync_projects(self, *, refresh: bool = False) -> int:
        if self.projects is None:
            return 0
        count = 0
        for item in self.projects.registry.list():
            self.store.ensure_agent(item.name, f"{item.display_name or item.name} Project Owner")
            if refresh or self.store.get_capsule(item.name) is None:
                try:
                    self.capsules.build(item.name)
                except Exception:
                    pass
            count += 1
        return count

    def _owner(self, project: str) -> ProjectOwnerAgent:
        if self.projects is None or self.projects.registry.get(project) is None:
            raise KeyError(f"unknown project: {project}")
        if project not in self._owners:
            self._owners[project] = ProjectOwnerAgent(
                project=project, config=self.config, provider=self.provider, tools=self.tools,
                projects=self.projects, store=self.store, capsules=self.capsules,
                events=self.events, redactor=self.redactor,
            )
        return self._owners[project]

    def delegate(self, project: str, objective: str, *, acceptance: str = "", priority: int = 2,
                 source: str = "central", depends_on: list[str] | None = None, parent_task_id: str = "",
                 objective_id: str = "") -> dict[str, Any]:
        self.sync_projects()
        if self.projects is None or self.projects.registry.get(project) is None:
            raise KeyError(f"unknown project: {project}")
        task = self.store.enqueue_task(project, objective, acceptance=acceptance, priority=priority,
                                       source=source, depends_on=depends_on, parent_task_id=parent_task_id,
                                       objective_id=(objective_id or self._active_objective_id))
        try:
            self.capsules.build(project)
        except Exception:
            pass
        self.kick(project)
        return task

    def begin_objective(self, prompt: str, *, project_hint: str = "", run_id: str = "") -> dict[str, Any]:
        title = str(prompt).strip().splitlines()[0][:180] or "Untitled objective"
        obj = self.store.create_objective(title, source_prompt=prompt, project_hint=project_hint, run_id=run_id)
        self._active_objective_id = str(obj.get("id") or "")
        return obj

    def finish_central_objective(self, objective_id: str, outcome: str = "") -> dict[str, Any] | None:
        obj = self.store.get_objective(objective_id)
        self._active_objective_id = ""
        if obj is None: return None
        tasks = obj.get("tasks") or []
        if not tasks:
            return self.store.update_objective(objective_id, status="completed", outcome=outcome)
        if outcome:
            self.store.update_objective(objective_id, outcome=outcome)
        return self.store.sync_objective_status(objective_id)

    def objectives(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_objectives(limit=limit)

    def attention_snapshot(self, human_tasks=None) -> dict[str, Any]:
        humans = list(human_tasks or [])
        human_projects = {str(x.get("project") or "") for x in humans if str(x.get("status") or "") == "open"}
        items=[]
        for agent in self.store.list_agents():
            project=str(agent.get("project") or ""); q=agent.get("queue") or {}
            if project in human_projects: level="human"
            elif int(q.get("waiting_dependency",0))+int(q.get("waiting_agent",0))>0: level="coordinated"
            else: level="autonomous"
            items.append({"project":project,"attention":level,"status":agent.get("status"),"current_task_id":agent.get("current_task_id"),"queue":q})
        return {"projects":items,"human_count":len(humans),"coordinated_count":sum(1 for x in items if x["attention"]=="coordinated"),"autonomous_count":sum(1 for x in items if x["attention"]=="autonomous")}

    async def move_task(self, task_id: str, project: str) -> dict[str, Any]:
        before=self.store.get_task(task_id) or {}
        task=self.store.move_task(task_id,project)
        await self.events.emit("agent.task.moved", task_id=task_id, from_project=before.get("project"), project=project, objective_id=task.get("objective_id"))
        self.kick(project)
        return task

    async def request_change(self, from_project: str, to_project: str, objective: str, *, acceptance: str = "",
                             priority: int = 2, depends_on: list[str] | None = None,
                             parent_task_id: str = "") -> dict[str, Any]:
        payload = {
            "objective": objective,
            "acceptance": acceptance,
            "priority": int(priority),
            "depends_on": list(depends_on or []),
            "parent_task_id": parent_task_id,
        }
        message = await self.send_message(
            from_project or "central", to_project, "CHANGE_REQUEST", json.dumps(payload, ensure_ascii=False),
            subject=objective[:300], task_id=parent_task_id,
        )
        try:
            response = json.loads(str(message.get("response") or "{}"))
        except Exception:
            response = {}
        return {"message": message, "task": response.get("task")}

    def kick(self, project: str = "") -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        projects = [project] if project else [a["project"] for a in self.store.list_agents()]
        for name in projects:
            if not name:
                continue
            existing = self._worker_tasks.get(name)
            if existing is None or existing.done():
                self._worker_tasks[name] = loop.create_task(self._run_project_queue(name), name=f"term6-owner-{name}")

    async def _run_project_queue(self, project: str) -> None:
        lock = self._project_locks.setdefault(project, asyncio.Lock())
        if lock.locked():
            return
        async with lock, self._concurrency:
            worker_id = f"owner:{project}:{uuid.uuid4().hex[:8]}"
            while True:
                task = self.store.claim_next(project, worker_id, lease_seconds=self.config.agents.lease_seconds)
                if task is None:
                    break
                owner = self._owner(project)
                try:
                    await owner.execute(task, worker_id=worker_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Failure is durable; notify dependents/source before moving on.
                    current = self.store.get_task(str(task["id"])) or task
                    await self._on_task_terminal(current)
                    continue
                current = self.store.get_task(str(task["id"])) or task
                await self._on_task_terminal(current)

    async def _on_task_terminal(self, task: dict[str, Any]) -> None:
        status = str(task.get("status") or "")
        project = str(task.get("project") or "")
        source = str(task.get("source") or "")
        if source.startswith("agent:"):
            target = source.split(":", 1)[1]
            kind = "TASK_COMPLETED" if status == "completed" else "TASK_BLOCKED"
            body = json.dumps({
                "task_id": task.get("id"), "project": project, "status": status,
                "result": str(task.get("result") or "")[:12000], "error": str(task.get("error") or "")[:4000],
            }, ensure_ascii=False)
            try:
                await self.send_message(project, target, kind, body, subject=f"{project} task {status}", task_id=str(task.get("id") or ""))
            except Exception:
                pass
        released = self.store.release_ready_dependencies_detail()
        for row in released:
            dep_project = str(row.get("project") or "")
            if dep_project:
                self.kick(dep_project)
                await self.events.emit("agent.dependency.released", project=dep_project, task_id=row.get("id"), depends_on=row.get("depends_on") or [])
        objective_id = str(task.get("objective_id") or "")
        if objective_id:
            self.store.sync_objective_status(objective_id)
        if project:
            try:
                self.capsules.build(project)
            except Exception:
                pass

    async def scheduler_loop(self) -> None:
        self._scheduler_stop.clear()
        if self._startup_recovered:
            await self.events.emit(
                "agents.restart.recovered",
                task_ids=[x.get("id") for x in self._startup_recovered],
                recovered=len(self._startup_recovered),
            )
            self._startup_recovered = []
        while not self._scheduler_stop.is_set():
            try:
                recovered = self.store.recover_stale_leases()
                if recovered:
                    self._recovery_total += int(recovered)
                    self._last_recovery_at = datetime.now(timezone.utc).isoformat()
                released = self.store.release_ready_dependencies_detail()
                self.sync_projects(refresh=False)
                await self.process_pending_messages(limit=50)
                self.kick()
                for row in released:
                    if row.get("project"):
                        self.kick(str(row["project"]))
                if recovered:
                    await self.events.emit("agents.recovered", stale_leases=recovered)
                if released:
                    await self.events.emit("agents.dependencies.released", task_ids=[r.get("id") for r in released])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.events.emit("agents.scheduler.error", error=self.redactor.redact(f"{type(exc).__name__}: {exc}"))
            try:
                await asyncio.wait_for(self._scheduler_stop.wait(), timeout=max(1, int(self.config.agents.scheduler_interval_s)))
            except asyncio.TimeoutError:
                pass

    async def set_concurrency_limits(self, *, owners: int | None = None, queries: int | None = None) -> dict[str, Any]:
        if owners is not None:
            value = await self._concurrency.set_limit(owners)
            self.config.agents.max_concurrent_owners = value
            self.store.set_setting("max_concurrent_owners", value)
        if queries is not None:
            value = await self._query_concurrency.set_limit(queries)
            self.config.agents.max_concurrent_queries = value
            self.store.set_setting("max_concurrent_queries", value)
        await self.events.emit("agents.concurrency.changed", **self.concurrency_snapshot())
        self.kick()
        return self.concurrency_snapshot()

    def concurrency_snapshot(self) -> dict[str, Any]:
        return {"owners": self._concurrency.snapshot(), "queries": self._query_concurrency.snapshot()}

    def workspace_layout(self) -> dict[str, dict[str, float]]:
        raw = self.store.get_setting("workspace_layout_v2", {})
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, float]] = {}
        for project, pos in raw.items():
            if self.projects is None or self.projects.registry.get(str(project)) is None or not isinstance(pos, dict):
                continue
            try:
                x = float(pos.get("x"))
                y = float(pos.get("y"))
            except Exception:
                continue
            out[str(project)] = {"x": max(120.0, min(x, 10000.0)), "y": max(100.0, min(y, 10000.0))}
        return out

    async def set_workspace_position(self, project: str, x: float, y: float) -> dict[str, dict[str, float]]:
        if self.projects is None or self.projects.registry.get(project) is None:
            raise KeyError(f"unknown project: {project}")
        layout = self.workspace_layout()
        layout[project] = {"x": max(120.0, min(float(x), 10000.0)), "y": max(100.0, min(float(y), 10000.0))}
        self.store.set_setting("workspace_layout_v2", layout)
        await self.events.emit("agents.workspace.position", project=project, x=layout[project]["x"], y=layout[project]["y"])
        return layout

    async def set_workspace_layout(self, positions: dict[str, Any]) -> dict[str, dict[str, float]]:
        if not isinstance(positions, dict):
            raise ValueError("positions must be an object")
        layout: dict[str, dict[str, float]] = {}
        for project, pos in positions.items():
            project = str(project)
            if self.projects is None or self.projects.registry.get(project) is None or not isinstance(pos, dict):
                continue
            try:
                x = max(120.0, min(float(pos.get("x")), 10000.0))
                y = max(100.0, min(float(pos.get("y")), 10000.0))
            except Exception:
                continue
            layout[project] = {"x": x, "y": y}
        self.store.set_setting("workspace_layout_v2", layout)
        await self.events.emit("agents.workspace.layout_saved", projects=sorted(layout))
        return layout

    async def reset_workspace_layout(self) -> dict[str, dict[str, float]]:
        self.store.set_setting("workspace_layout_v2", {})
        await self.events.emit("agents.workspace.layout_reset")
        return {}

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.cancel_task(task_id)
        project = str(task.get("project") or "")
        await self.events.emit("agent.task.cancel.requested", project=project, task_id=task_id, status=task.get("status"))
        if project:
            self.kick(project)
            try:
                self.capsules.build(project)
            except Exception:
                pass
        return task

    async def retry_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.retry_task(task_id)
        project = str(task.get("project") or "")
        await self.events.emit("agent.task.retry", project=project, task_id=task_id)
        if project:
            self.kick(project)
        return task

    async def reprioritize_task(self, task_id: str, priority: int) -> dict[str, Any]:
        task = self.store.reprioritize_task(task_id, priority)
        await self.events.emit("agent.task.priority", project=task.get("project"), task_id=task_id, priority=task.get("priority"))
        if task.get("project"):
            self.kick(str(task["project"]))
        return task

    async def stop(self) -> None:
        self._scheduler_stop.set()
        pending = [t for t in self._worker_tasks.values() if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def snapshot(self) -> dict[str, Any]:
        self.sync_projects()
        agents = self.store.list_agents()
        contracts = self.store.list_contracts()
        pending_messages = self.store.list_pending_messages(limit=1000)
        return {
            "enabled": True,
            "database": str(self.store.path),
            "agents": agents,
            "counts": {
                "agents": len(agents),
                "working": sum(1 for a in agents if a.get("status") in {"working", "waiting_agent"}),
                "queued": sum(int((a.get("queue") or {}).get("queued", 0)) for a in agents),
                "waiting_dependency": sum(int((a.get("queue") or {}).get("waiting_dependency", 0)) for a in agents),
                "pending_messages": len(pending_messages),
                "contracts": len(contracts),
                "contract_forecasts": len(self.store.list_contract_forecasts(active_only=True, limit=1000)),
            },
            "concurrency": self.concurrency_snapshot(),
            "recovery": {"total": self._recovery_total, "last_at": self._last_recovery_at},
            "objectives": self.store.list_objectives(limit=100),
        }

    def context_text(self, user_text: str = "") -> str:
        snap = self.snapshot()
        rows = [
            "[term_6.1-beta central coordinator — Project Owner mesh]",
            "You are the central coordinator. Delegate project-owned implementation with agent_delegate; use agent_request_change for explicit owner-to-owner work.",
            "Each owner has a durable queue and Project State Capsule. current_truth and published contracts are authoritative; queued work and contract forecasts are not yet truth.",
            "Use agent_query for cross-project state, contract_query for owned integration interfaces, contract_impact before coordinating breaking interface changes, and task dependencies when ordering matters.",
            "Typed messages are auditable. Different projects may mutate concurrently; each project retains one mutation lane.",
            f"Published contracts={snap['counts']['contracts']} active forecasts={snap['counts']['contract_forecasts']} pending messages={snap['counts']['pending_messages']}",
        ]
        for agent in snap["agents"][:80]:
            q = agent.get("queue") or {}
            rows.append(
                f"- {agent['project']}: {agent.get('status','idle')}; current={agent.get('current_task_id') or '-'}; "
                f"queued={q.get('queued',0)} waiting_dep={q.get('waiting_dependency',0)} completed={q.get('completed',0)} capsule=r{agent.get('capsule_revision',0)}"
            )
        return "\n".join(rows)[:18000]

    def query_capsule(self, project: str, question: str = "") -> dict[str, Any]:
        capsule = self.capsules.build(project)
        if not question:
            return {"project": project, "answer_mode": "capsule", "capsule": capsule}
        return {
            "project": project,
            "answer_mode": "capsule",
            "question": question,
            "current_truth": capsule.get("current_truth"),
            "in_flight": capsule.get("in_flight"),
            "planned": capsule.get("planned"),
            "projected_changes": capsule.get("projected_changes"),
            "owned_contracts": capsule.get("owned_contracts"),
            "consumed_contracts": capsule.get("consumed_contracts"),
            "contract_forecasts": capsule.get("contract_forecasts"),
            "revision": capsule.get("revision"),
            "updated_at": capsule.get("updated_at"),
            "caveat": capsule.get("forecast_rule"),
        }

    async def _answer_deep_query(self, project: str, question: str) -> dict[str, Any]:
        capsule = self.store.get_capsule(project) or self.capsules.build(project)
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in project)
        episodes = EpisodeStore(self.config.state_dir / "agents" / safe)
        recall = episodes.recall(question, 6, 10000)
        async with self._query_concurrency:
            result = await self.provider.chat([
                {"role": "system", "content": (
                    f"You are the read-only knowledge lane of the {project} Project Owner. "
                    "Answer only from the supplied Project State Capsule, published contracts and relevant project episodes. "
                    "Clearly separate CURRENT facts from IN-FLIGHT/PLANNED forecasts. Never claim queued work or a contract forecast is implemented."
                )},
                {"role": "user", "content": "Capsule:\n" + json.dumps(capsule, ensure_ascii=False, indent=2)[:42000]
                 + ("\n\nRelevant episodes:\n" + recall if recall else "") + "\n\nQuestion:\n" + question},
            ], reasoning=ReasoningMode.LOW, max_tokens=int(self.config.agents.deep_query_tokens))
        return {
            "project": project, "answer_mode": "deep", "question": question, "answer": str(result.content or ""),
            "capsule_revision": capsule.get("revision"), "capsule_updated_at": capsule.get("updated_at"),
        }

    async def deep_query(self, project: str, question: str, *, from_project: str = "central") -> dict[str, Any]:
        message = self.store.send_message(from_project, project, "ANALYSIS_QUERY", question, subject="Deep owner query")
        try:
            data = await self._answer_deep_query(project, question)
            self.store.respond_message(message["id"], str(data.get("answer") or ""))
            data["message_id"] = message["id"]
            return data
        except Exception as exc:
            self.store.mark_message_status(message["id"], "failed")
            raise exc

    async def send_message(self, from_project: str, to_project: str, kind: str, body: str, *,
                           subject: str = "", task_id: str = "", correlation_id: str = "") -> dict[str, Any]:
        if to_project != "central":
            self.sync_projects()
            if self.store.get_agent(to_project) is None:
                raise KeyError(f"unknown Project Owner: {to_project}")
        message = self.store.send_message(from_project, to_project, kind, body, subject=subject,
                                          task_id=task_id, correlation_id=correlation_id)
        await self.events.emit("agent.message.sent", message_id=message.get("id"), from_project=from_project,
                               to_project=to_project, kind=kind)
        await self._process_message(message)
        return self.store.get_message(str(message["id"])) or message

    async def _process_message(self, message: dict[str, Any]) -> None:
        if str(message.get("status")) != "sent":
            return
        kind = str(message.get("kind") or "")
        to_project = str(message.get("to_project") or "")
        body = str(message.get("body") or "")
        try:
            if kind == "STATE_QUERY":
                answer = self.query_capsule(to_project, body)
                self.store.respond_message(str(message["id"]), json.dumps(answer, ensure_ascii=False))
            elif kind == "ANALYSIS_QUERY":
                answer = await self._answer_deep_query(to_project, body)
                self.store.respond_message(str(message["id"]), str(answer.get("answer") or ""))
            elif kind == "CHANGE_REQUEST":
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {"objective": body}
                parent_id = str(payload.get("parent_task_id") or message.get("task_id") or "")
                parent = self.store.get_task(parent_id) if parent_id else None
                task = self.delegate(
                    to_project, str(payload.get("objective") or body), acceptance=str(payload.get("acceptance") or ""),
                    priority=int(payload.get("priority", 2)), source=f"agent:{message.get('from_project') or 'central'}",
                    depends_on=[str(x) for x in (payload.get("depends_on") or [])],
                    parent_task_id=parent_id, objective_id=str((parent or {}).get("objective_id") or ""),
                )
                self.store.respond_message(str(message["id"]), json.dumps({"accepted": True, "task": task}, ensure_ascii=False))
            elif kind == "DEPENDENCY_REQUEST":
                payload = json.loads(body)
                updated = self.store.add_dependency(str(payload["task_id"]), str(payload["depends_on"]))
                self.store.respond_message(str(message["id"]), json.dumps({"updated": updated}, ensure_ascii=False))
                if updated.get("project"):
                    self.kick(str(updated["project"]))
            else:
                self.store.mark_message_status(str(message["id"]), "delivered")
            await self.events.emit("agent.message.processed", message_id=message.get("id"), kind=kind,
                                   to_project=to_project, status=(self.store.get_message(str(message["id"])) or {}).get("status"))
        except Exception as exc:
            self.store.mark_message_status(str(message["id"]), "failed")
            await self.events.emit("agent.message.failed", message_id=message.get("id"), kind=kind,
                                   error=self.redactor.redact(f"{type(exc).__name__}: {exc}"))

    async def process_pending_messages(self, *, project: str = "", limit: int = 100) -> int:
        rows = self.store.list_pending_messages(project, limit=limit)
        for row in rows:
            await self._process_message(row)
        return len(rows)

    async def publish_contract(self, contract_key: str, owner_project: str, *, summary: str = "",
                               interface: dict[str, Any] | None = None, consumers: list[str] | None = None,
                               compatibility: str = "compatible", source_task_id: str = "") -> dict[str, Any]:
        self.sync_projects()
        contract = self.store.publish_contract(
            contract_key, owner_project, summary=summary, interface=interface or {}, consumers=consumers or [],
            compatibility=compatibility, source_task_id=source_task_id,
        )
        kind = "CONTRACT_PUBLISHED" if contract.get("change_kind") == "published" else "CONTRACT_CHANGED"
        notification = json.dumps({
            "contract_key": contract_key, "owner_project": owner_project, "revision": contract.get("revision"),
            "summary": contract.get("summary"), "compatibility": contract.get("compatibility"),
            "interface": contract.get("interface"), "authoritative": True,
        }, ensure_ascii=False)
        for sub in contract.get("consumers") or []:
            consumer = str(sub.get("project") or "")
            if not consumer:
                continue
            await self.send_message(owner_project, consumer, kind, notification,
                                    subject=f"{contract_key} r{contract.get('revision')}", task_id=source_task_id)
            self.store.mark_contract_notified(contract_key, consumer, int(contract.get("revision") or 0))
            try:
                self.capsules.build(consumer)
            except Exception:
                pass
        try:
            self.capsules.build(owner_project)
        except Exception:
            pass
        await self.events.emit("agent.contract.published", contract_key=contract_key, owner_project=owner_project,
                               revision=contract.get("revision"), consumers=[x.get("project") for x in contract.get("consumers") or []],
                               change_kind=contract.get("change_kind"))
        return self.store.get_contract(contract_key) or contract

    async def forecast_contract(self, contract_key: str, owner_project: str, *, state: str,
                                expected_change: str, interface_delta: dict[str, Any] | None = None,
                                task_id: str = "") -> dict[str, Any]:
        forecast = self.store.save_contract_forecast(
            contract_key, owner_project, state=state, expected_change=expected_change,
            interface_delta=interface_delta or {}, task_id=task_id,
        )
        current = self.store.get_contract(contract_key)
        consumers = [str(x.get("project") or "") for x in (current or {}).get("consumers", [])]
        notice = json.dumps({
            "contract_key": contract_key, "owner_project": owner_project, "forecast": forecast,
            "authoritative": False, "rule": "forecast only; do not treat as implemented until a published contract revision exists",
        }, ensure_ascii=False)
        for consumer in consumers:
            if consumer:
                await self.send_message(owner_project, consumer, "CONTRACT_FORECAST", notice,
                                        subject=f"Forecast: {contract_key}", task_id=task_id)
                try:
                    self.capsules.build(consumer)
                except Exception:
                    pass
        try:
            self.capsules.build(owner_project)
        except Exception:
            pass
        await self.events.emit("agent.contract.forecast", contract_key=contract_key, owner_project=owner_project,
                               forecast_id=forecast.get("id"), state=state, consumers=consumers)
        return forecast

    def contract_query(self, contract_key: str) -> dict[str, Any]:
        contract = self.store.get_contract(contract_key)
        forecasts = self.store.list_contract_forecasts(contract_key=contract_key, active_only=True)
        if contract is None and not forecasts:
            raise KeyError(f"unknown contract: {contract_key}")
        return {
            "contract_key": contract_key,
            "current": contract,
            "forecasts": forecasts,
            "truth_rule": "current is authoritative when present; forecasts are non-authoritative expected future changes",
        }

    def contract_impact(self, contract_key: str) -> dict[str, Any]:
        return self.store.contract_impact(contract_key)
