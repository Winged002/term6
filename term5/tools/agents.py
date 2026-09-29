from __future__ import annotations

import asyncio
import json

from ..models import RiskLevel, ToolDefinition, ToolResult


def register_agent_tools(registry, orchestrator) -> None:
    def s(): return {"type": "string"}
    def b(): return {"type": "boolean"}
    def i(a=0, z=1000): return {"type": "integer", "minimum": a, "maximum": z}
    def arr(): return {"type": "array", "items": {"type": "string"}, "maxItems": 64}
    def obj(props, req=None):
        d = {"type": "object", "properties": props, "additionalProperties": False}
        if req: d["required"] = req
        return d

    async def agent_list() -> ToolResult:
        data = orchestrator.snapshot()
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_list", "List persistent Project Owner Agents, their current task and durable queue counts.",
        obj({}), agent_list, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def agent_status(project: str) -> ToolResult:
        agent = orchestrator.store.get_agent(project)
        if agent is None:
            return ToolResult(False, f"Unknown Project Owner: {project}")
        data = {"agent": agent, "tasks": orchestrator.store.list_tasks(project, limit=50),
                "capsule": orchestrator.store.get_capsule(project)}
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_status", "Inspect one Project Owner Agent, including its current work, queue and compact state capsule.",
        obj({"project": s()}, ["project"]), agent_status, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def agent_delegate(project: str, objective: str, acceptance: str = "", priority: int = 2,
                             depends_on: list[str] | None = None) -> ToolResult:
        task = orchestrator.delegate(project, objective, acceptance=acceptance, priority=priority,
                                     depends_on=depends_on or [], source="central")
        return ToolResult(True, json.dumps(task, ensure_ascii=False, indent=2), data=task)
    registry.register(ToolDefinition(
        "agent_delegate",
        "Delegate project-owned implementation work to that project's persistent Project Owner queue. The owner runs independently; use depends_on for explicit cross-project ordering. Different projects may execute concurrently, while each project has one mutation lane.",
        obj({"project": s(), "objective": s(), "acceptance": s(), "priority": i(0,3), "depends_on": arr()}, ["project", "objective"]),
        agent_delegate, {"agent.coordinate"}, RiskLevel.MEDIUM, False, True,
    ))

    async def agent_query(project: str, question: str, deep: bool = False, from_project: str = "central") -> ToolResult:
        if deep:
            data = await orchestrator.deep_query(project, question, from_project=from_project or "central")
        else:
            data = orchestrator.query_capsule(project, question)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_query",
        "Ask a Project Owner for CURRENT + IN-FLIGHT + PLANNED state. Default uses its instant durable capsule and does not interrupt current work. Set deep=true only when the cached state needs owner reasoning.",
        obj({"project": s(), "question": s(), "deep": b(), "from_project": s()}, ["project", "question"]),
        agent_query, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def agent_task_list(project: str = "", status: str = "", limit: int = 100) -> ToolResult:
        statuses = [status] if status else None
        data = orchestrator.store.list_tasks(project, statuses=statuses, limit=limit)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_task_list", "List durable Project Owner tasks globally or for one project.",
        obj({"project": s(), "status": s(), "limit": i(1,1000)}), agent_task_list, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def agent_task_cancel(task_id: str) -> ToolResult:
        data = orchestrator.store.cancel_task(task_id)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_task_cancel", "Cancel a durable owner task that has not already reached a terminal state.",
        obj({"task_id": s()}, ["task_id"]), agent_task_cancel, {"agent.coordinate"}, RiskLevel.HIGH, False, True,
    ))

    async def agent_task_retry(task_id: str) -> ToolResult:
        data = orchestrator.store.retry_task(task_id)
        orchestrator.kick(str(data.get("project") or ""))
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_task_retry", "Requeue a failed/cancelled Project Owner task after inspecting why it failed.",
        obj({"task_id": s()}, ["task_id"]), agent_task_retry, {"agent.coordinate"}, RiskLevel.MEDIUM, False, True,
    ))

    async def agent_message_send(to_project: str, kind: str, body: str, from_project: str = "central",
                                 subject: str = "", task_id: str = "") -> ToolResult:
        data = await orchestrator.send_message(from_project or "central", to_project, kind, body,
                                               subject=subject, task_id=task_id)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_message_send", "Send an auditable typed message to another Project Owner inbox without mutating that project's source tree.",
        obj({"to_project": s(), "kind": {"type":"string","enum":["STATE_QUERY","ANALYSIS_QUERY","CHANGE_REQUEST","DEPENDENCY_REQUEST","CONTRACT_PUBLISHED","CONTRACT_CHANGED","CONTRACT_FORECAST","TASK_BLOCKED","TASK_COMPLETED","RISK_NOTICE"]},
             "body": s(), "from_project": s(), "subject": s(), "task_id": s()}, ["to_project", "kind", "body"]),
        agent_message_send, {"agent.coordinate"}, RiskLevel.LOW, False, True,
    ))

    async def agent_message_list(project: str, direction: str = "both", limit: int = 50) -> ToolResult:
        if direction not in {"in", "out", "both"}:
            return ToolResult(False, "direction must be in, out, or both")
        data = orchestrator.store.list_messages(project, direction=direction, limit=max(1, min(int(limit), 200)))
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_message_list", "Inspect the auditable inter-agent inbox/outbox for one Project Owner.",
        obj({"project": s(), "direction": {"type":"string","enum":["in","out","both"]}, "limit": i(1,200)}, ["project"]),
        agent_message_list, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def agent_wait(task_ids: list[str], timeout_s: int = 300, from_project: str = "") -> ToolResult:
        ids = [str(x) for x in task_ids if str(x).strip()]
        if not ids:
            return ToolResult(False, "task_ids is required")
        timeout = max(1, min(int(timeout_s), 1800))
        caller_task = ""
        if from_project and from_project != "central":
            agent = orchestrator.store.get_agent(from_project) or {}
            caller_task = str(agent.get("current_task_id") or "")
            if caller_task:
                try:
                    orchestrator.store.update_task(caller_task, status="waiting_agent")
                    orchestrator.store.set_agent_state(from_project, status="waiting_agent", current_task_id=caller_task)
                except Exception:
                    caller_task = ""
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                rows = [orchestrator.store.get_task(tid) for tid in ids]
                missing = [tid for tid, row in zip(ids, rows) if row is None]
                if missing:
                    return ToolResult(False, "Unknown agent task(s): " + ", ".join(missing))
                if all(str(row.get("status")) in {"completed", "failed", "cancelled"} for row in rows if row):
                    data = [row for row in rows if row]
                    return ToolResult(all(r.get("status") == "completed" for r in data), json.dumps(data, ensure_ascii=False, indent=2), data=data)
                if asyncio.get_running_loop().time() >= deadline:
                    data = [row for row in rows if row]
                    return ToolResult(True, json.dumps({"timed_out": True, "tasks": data}, ensure_ascii=False, indent=2), data={"timed_out": True, "tasks": data})
                await asyncio.sleep(0.5)
        finally:
            if caller_task:
                current = orchestrator.store.get_task(caller_task) or {}
                if current.get("status") == "waiting_agent":
                    orchestrator.store.update_task(caller_task, status="executing")
                    orchestrator.store.set_agent_state(from_project, status="working", current_task_id=caller_task)
    registry.register(ToolDefinition(
        "agent_wait", "Wait efficiently for delegated Project Owner tasks to reach a terminal state without model polling. Owners continue working concurrently while this tool waits.",
        obj({"task_ids": arr(), "timeout_s": i(1,1800), "from_project": s()}, ["task_ids"]), agent_wait, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def agent_request_change(to_project: str, objective: str, acceptance: str = "", priority: int = 2,
                                   depends_on: list[str] | None = None, from_project: str = "central",
                                   parent_task_id: str = "") -> ToolResult:
        data = await orchestrator.request_change(
            from_project or "central", to_project, objective, acceptance=acceptance, priority=priority,
            depends_on=depends_on or [], parent_task_id=parent_task_id,
        )
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_request_change", "Ask another Project Owner to perform project-owned work. Creates an auditable CHANGE_REQUEST and a durable task on the target owner's queue without granting cross-project write access.",
        obj({"to_project": s(), "objective": s(), "acceptance": s(), "priority": i(0,3), "depends_on": arr(), "from_project": s(), "parent_task_id": s()}, ["to_project", "objective"]),
        agent_request_change, {"agent.coordinate"}, RiskLevel.MEDIUM, False, True,
    ))

    async def agent_dependency_add(task_id: str, depends_on: str) -> ToolResult:
        data = orchestrator.store.add_dependency(task_id, depends_on)
        state = orchestrator.store.dependency_state(task_id)
        payload = {"task": data, "dependency_state": state}
        return ToolResult(True, json.dumps(payload, ensure_ascii=False, indent=2), data=payload)
    registry.register(ToolDefinition(
        "agent_dependency_add", "Add an explicit durable dependency edge between Project Owner tasks. The dependent task will not be claimable until the prerequisite completes.",
        obj({"task_id": s(), "depends_on": s()}, ["task_id", "depends_on"]),
        agent_dependency_add, {"agent.coordinate"}, RiskLevel.MEDIUM, False, True,
    ))

    async def contract_publish(contract_key: str, owner_project: str, summary: str = "", interface: dict | None = None,
                               consumers: list[str] | None = None, compatibility: str = "compatible",
                               source_task_id: str = "") -> ToolResult:
        data = await orchestrator.publish_contract(
            contract_key, owner_project, summary=summary, interface=interface or {}, consumers=consumers or [],
            compatibility=compatibility, source_task_id=source_task_id,
        )
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "contract_publish", "Publish or revise an authoritative cross-project integration contract owned by a Project Owner. Revision increments atomically and subscribed consumers are notified automatically.",
        obj({"contract_key": s(), "owner_project": s(), "summary": s(), "interface": {"type":"object","additionalProperties":True},
             "consumers": arr(), "compatibility": {"type":"string","enum":["compatible","additive","breaking","unknown"]}, "source_task_id": s()},
            ["contract_key", "owner_project"]),
        contract_publish, {"agent.contract"}, RiskLevel.MEDIUM, False, True,
    ))

    async def contract_forecast(contract_key: str, owner_project: str, state: str, expected_change: str,
                                interface_delta: dict | None = None, task_id: str = "") -> ToolResult:
        data = await orchestrator.forecast_contract(
            contract_key, owner_project, state=state, expected_change=expected_change,
            interface_delta=interface_delta or {}, task_id=task_id,
        )
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "contract_forecast", "Publish a non-authoritative expected future change for an owned integration contract. Consumers can plan against it but must not treat it as implemented truth.",
        obj({"contract_key": s(), "owner_project": s(), "state": {"type":"string","enum":["in_flight","planned","projected"]},
             "expected_change": s(), "interface_delta": {"type":"object","additionalProperties":True}, "task_id": s()},
            ["contract_key", "owner_project", "state", "expected_change"]),
        contract_forecast, {"agent.contract"}, RiskLevel.LOW, False, True,
    ))

    async def contract_query(contract_key: str) -> ToolResult:
        data = orchestrator.contract_query(contract_key)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "contract_query", "Read the authoritative current revision and non-authoritative future forecasts for a cross-project integration contract.",
        obj({"contract_key": s()}, ["contract_key"]), contract_query, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def contract_list(owner_project: str = "", consumer_project: str = "") -> ToolResult:
        data = orchestrator.store.list_contracts(owner_project=owner_project, consumer_project=consumer_project)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "contract_list", "List published integration contracts globally, by owning project, or by consuming project.",
        obj({"owner_project": s(), "consumer_project": s()}), contract_list, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def contract_subscribe(contract_key: str, project: str) -> ToolResult:
        data = orchestrator.store.subscribe_contract(contract_key, project)
        try:
            orchestrator.capsules.build(project)
        except Exception:
            pass
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "contract_subscribe", "Register a project as a consumer of a published contract so future revisions and forecasts are delivered to its Project Owner inbox.",
        obj({"contract_key": s(), "project": s()}, ["contract_key", "project"]), contract_subscribe, {"agent.contract"}, RiskLevel.LOW, False, True,
    ))

    async def contract_impact(contract_key: str) -> ToolResult:
        data = orchestrator.contract_impact(contract_key)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "contract_impact", "Show every subscribed consumer potentially affected by a contract revision, including each owner's active queue state and active forecasts.",
        obj({"contract_key": s()}, ["contract_key"]), contract_impact, {"agent.read"}, RiskLevel.LOW, True, False,
    ))

    async def agent_capsule_refresh(project: str) -> ToolResult:
        data = orchestrator.capsules.build(project)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "agent_capsule_refresh", "Rebuild a Project Owner's compact current+forecast Project State Capsule from durable project, Git, queue and message state.",
        obj({"project": s()}, ["project"]), agent_capsule_refresh, {"agent.read"}, RiskLevel.LOW, True, False,
    ))
