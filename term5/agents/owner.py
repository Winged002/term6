from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from ..models import ReasoningMode, ToolResult, Usage
from ..state.artifacts import ArtifactStore
from ..state.episodes import EpisodeStore
from ..state.working_memory import WorkingMemoryManager
from .store import AgentStore


class ProjectOwnerAgent:
    """Ephemeral model worker backed by durable per-project owner state.

    Owner identity/memory/queue persists. The model process does not: each task
    starts from a compact project capsule plus relevant project episodes.
    """

    DENY_TOOLS = {
        "agent_delegate", "agent_task_cancel", "agent_task_retry",
        "project_switch", "project_create", "project_clone", "project_import", "project_unregister",
        "memory_forget",
    }

    def __init__(self, *, project: str, config, provider, tools, projects, store: AgentStore,
                 capsules, events, redactor) -> None:
        self.project = project
        self.config = config
        self.provider = provider
        self.tools = tools
        self.projects = projects
        self.store = store
        self.capsules = capsules
        self.events = events
        self.redactor = redactor
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in project)
        self.state_dir = config.state_dir / "agents" / safe
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.state_dir)
        self.episodes = EpisodeStore(self.state_dir)
        self.working_memory = WorkingMemoryManager(config, self.artifacts, self.episodes)

    def _tool_defs(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        defs: dict[str, Any] = {}
        for name in self.tools.names():
            if name in self.DENY_TOOLS:
                continue
            tool = self.tools.get(name)
            if tool is None:
                continue
            schemas.append({"type": "function", "function": {
                "name": tool.name, "description": tool.description, "parameters": tool.parameters,
            }})
            defs[name] = tool
        return schemas, defs

    def _scope_args(self, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Bind project-aware tools and reject obvious cross-project file paths."""
        tool = self.tools.get(name)
        out = dict(args)
        cross_project_read_tools = {"agent_query", "agent_status"}
        if tool is not None:
            props = (tool.parameters or {}).get("properties") or {}
            if "project" in props and name not in cross_project_read_tools:
                supplied = str(out.get("project") or "")
                if supplied and supplied != self.project:
                    return out, f"Project Owner {self.project} cannot target project {supplied}."
                out["project"] = self.project

        # Inter-agent communication is cross-project by design, but an owner may
        # never spoof the sender identity. Likewise, owned contracts may only be
        # published/forecast by their owning Project Owner.
        agent_state = self.store.get_agent(self.project) or {}
        current_task_id = str(agent_state.get("current_task_id") or "")
        if name in {"agent_message_send", "agent_request_change", "agent_wait"}:
            out["from_project"] = self.project
        if name == "agent_request_change" and not str(out.get("parent_task_id") or ""):
            out["parent_task_id"] = current_task_id
        if name in {"contract_publish", "contract_forecast"}:
            supplied = str(out.get("owner_project") or "")
            if supplied and supplied != self.project:
                return out, f"Project Owner {self.project} cannot publish contracts for {supplied}."
            out["owner_project"] = self.project
            key = "source_task_id" if name == "contract_publish" else "task_id"
            if not str(out.get(key) or ""):
                out[key] = current_task_id
        if name == "contract_subscribe":
            out["project"] = self.project
        if name == "agent_dependency_add":
            target = self.store.get_task(str(out.get("task_id") or ""))
            if target is None or str(target.get("project") or "") != self.project:
                return out, f"Project Owner {self.project} may only add dependencies to its own tasks."

        item = self.projects.registry.get(self.project) if self.projects is not None else None
        if item is None:
            return out, f"Project {self.project} is no longer registered."
        prefix = str(item.path).rstrip("/")
        path_tools = {
            "read_file", "list_dir", "file_info", "tree", "sha256_file", "code_outline", "check_file",
            "write_file", "replace_text", "file_relations", "impact_map", "sqlite_query", "zip_list",
        }
        if name in path_tools and "path" in out:
            p = str(out.get("path") or ".").strip()
            if p in {"", "."}:
                out["path"] = prefix
            elif not (p == prefix or p.startswith(prefix + "/")):
                return out, f"Project Owner {self.project} is scoped to {prefix}; rejected path {p}."
        if name in {"verify_changes", "git_stage", "git_restore"} and isinstance(out.get("paths"), list):
            for p in out["paths"]:
                ps = str(p)
                if name == "verify_changes" and not (ps == prefix or ps.startswith(prefix + "/")):
                    return out, f"Project Owner {self.project} rejected cross-project path {ps}."
        if name in {"write_files", "parallel_fim_edit"} and isinstance(out.get("changes"), list):
            for change in out["changes"]:
                ps = str((change or {}).get("path") or "")
                if not (ps == prefix or ps.startswith(prefix + "/")):
                    return out, f"Project Owner {self.project} rejected cross-project path {ps}."

        # Defense in depth for newly-added mutating tools: any workspace.write
        # tool carrying explicit path(s) must remain inside this owner's root.
        # Git path lists are repository-relative and are validated by the project-bound
        # git implementation after we inject the owner project above.
        if tool is not None and "workspace.write" in set(tool.capabilities or set()):
            if name not in {"git_stage", "git_restore"}:
                if isinstance(out.get("path"), str):
                    ps = str(out.get("path") or "")
                    if ps and not (ps == prefix or ps.startswith(prefix + "/")):
                        return out, f"Project Owner {self.project} rejected cross-project path {ps}."
                if isinstance(out.get("paths"), list):
                    for raw in out["paths"]:
                        ps = str(raw or "")
                        if ps and not (ps == prefix or ps.startswith(prefix + "/")):
                            return out, f"Project Owner {self.project} rejected cross-project path {ps}."
                if isinstance(out.get("changes"), list):
                    for change in out["changes"]:
                        ps = str((change or {}).get("path") or "")
                        if ps and not (ps == prefix or ps.startswith(prefix + "/")):
                            return out, f"Project Owner {self.project} rejected cross-project path {ps}."
        return out, None

    def _fit_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bound an owner's hot context without mixing in central-chat history."""
        target = max(4096, int(self.config.agents.owner_context_target_tokens))
        if self.working_memory.estimate_tokens(messages) <= target:
            return messages
        if len(messages) <= 2:
            return messages
        head = messages[:2]
        rest = messages[2:]
        units: list[list[dict[str, Any]]] = []
        i = 0
        while i < len(rest):
            msg = rest[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                unit = [msg]; i += 1
                while i < len(rest) and rest[i].get("role") == "tool":
                    unit.append(rest[i]); i += 1
                units.append(unit)
            else:
                units.append([msg]); i += 1
        head_tokens = self.working_memory.estimate_tokens(head)
        budget_chars = max(8000, (target - head_tokens - 1000) * 4)
        chosen: list[list[dict[str, Any]]] = []
        used = 0
        for unit in reversed(units):
            size = sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or "")) for m in unit)
            if chosen and used + size > budget_chars:
                break
            chosen.append(unit); used += size
        chosen.reverse()
        return [*head, {"role": "user", "content": "[Older owner execution trace compacted locally. Current project truth remains in the Project State Capsule and exact large tool outputs are in the owner artifact store.]"}, *[m for unit in chosen for m in unit]]

    def _system_prompt(self) -> str:
        item = self.projects.registry.get(self.project)
        path = item.path if item is not None else ""
        return f"""
You are the persistent Project Owner Agent for project {self.project}.
Your authoritative source root is {path}. You own this project's implementation queue, technical decisions, verification and integration obligations.

Rules:
- Work only on project {self.project} unless reading another owner's published state through agent_query/agent_message_send.
- Current repository/runtime evidence outranks memory. Inspect before mutating.
- Treat capsule current_truth as authoritative cached knowledge; treat in_flight/planned/projected_changes as forecasts, never completed truth.
- If another project owns an interface you depend on, use agent_query and contract_query rather than guessing its current or planned contract.
- Use agent_request_change when another owner must implement something; use agent_wait only when your current work truly cannot proceed without that result.
- Publish externally consumed interfaces with contract_publish. Before implementation is complete, use contract_forecast so consumers can see expected future behavior without confusing it with current truth.
- Before a breaking contract revision, inspect contract_impact and explicitly account for affected consumers.
- Do not switch the global active project or create/delete project registrations.
- Keep changes scoped, verify them, and report changed files and unresolved dependencies.
- You may answer inter-agent questions while work is queued because queries are served from durable capsules; do not block your mutation lane just to repeat known state.
- Finish with a concise task outcome suitable for durable episodic memory.
""".strip()

    async def execute(self, task: dict[str, Any], *, worker_id: str) -> str:
        task_id = str(task["id"])
        self.store.update_task(task_id, status="executing")
        self.store.set_agent_state(self.project, status="working", current_task_id=task_id)
        await self.events.emit("agent.task.started", project=self.project, task_id=task_id, objective=task.get("objective"))
        capsule = self.capsules.build(self.project)
        recall = self.episodes.recall(str(task.get("objective") or ""), 6, 10000)
        inbox = self.store.list_messages(self.project, direction="in", limit=12)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": (
                "[Project State Capsule]\n" + json.dumps(capsule, ensure_ascii=False, indent=2)[:30000]
                + ("\n\n[Relevant project episodes]\n" + recall if recall else "")
                + ("\n\n[Recent inter-agent inbox]\n" + json.dumps(inbox, ensure_ascii=False, indent=2)[:12000] if inbox else "")
                + "\n\n[Assigned task]\n" + str(task.get("objective") or "")
                + ("\nAcceptance criteria:\n" + str(task.get("acceptance") or "") if task.get("acceptance") else "")
                + "\nExecute the task autonomously using the available tools."
            )},
        ]
        schemas, _ = self._tool_defs()
        changed: list[str] = []
        used_tools: list[str] = []
        failures: list[str] = []
        final = ""
        reasoning = ReasoningMode(str(self.config.agents.owner_reasoning).lower())
        async def finish_cancelled() -> str:
            self.store.set_agent_state(self.project, status="idle", current_task_id="", last_error="")
            try:
                self.capsules.build(self.project)
            except Exception:
                pass
            await self.events.emit("agent.task.cancelled", project=self.project, task_id=task_id)
            return "Task cancelled."

        try:
            iteration = 0
            while True:
                iteration += 1
                current = self.store.get_task(task_id)
                if current is not None and current.get("status") == "cancelled":
                    return await finish_cancelled()
                self.store.heartbeat(task_id, worker_id, lease_seconds=self.config.agents.lease_seconds)
                # Owner contexts are deliberately rebuilt per task and kept far below the primary runtime's long-window budget.
                max_tokens = max(1024, min(int(self.config.agents.owner_max_output_tokens), int(self.config.model.max_output_tokens)))
                started = time.monotonic()
                request = self._fit_messages(messages)
                context_tokens = self.working_memory.estimate_tokens(request)
                await self.events.emit("agent.context.budget", project=self.project, task_id=task_id, iteration=iteration, estimated_tokens=context_tokens, target_tokens=self.config.agents.owner_context_target_tokens, messages=len(request))
                await self.events.emit("agent.model.started", project=self.project, task_id=task_id, iteration=iteration, reasoning=reasoning.value)
                result = await self.provider.chat(request, tools=schemas, reasoning=reasoning, max_tokens=max_tokens)
                current = self.store.get_task(task_id)
                if current is not None and current.get("status") == "cancelled":
                    return await finish_cancelled()
                await self.events.emit("agent.model.finished", project=self.project, task_id=task_id,
                                       iteration=iteration, tool_calls=len(result.tool_calls), elapsed_s=round(time.monotonic()-started, 4))
                assistant: dict[str, Any] = {"role": "assistant", "content": result.content or None}
                if result.reasoning_content:
                    assistant["reasoning_content"] = result.reasoning_content
                if result.tool_calls:
                    assistant["tool_calls"] = [{
                        "id": c.id, "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                    } for c in result.tool_calls]
                messages.append(assistant)
                if result.content:
                    final = result.content
                if not result.tool_calls:
                    break
                for call in result.tool_calls:
                    args, scope_error = self._scope_args(call.name, call.arguments if isinstance(call.arguments, dict) else {})
                    await self.events.emit("agent.tool.started", project=self.project, task_id=task_id, call_id=call.id, name=call.name, iteration=iteration)
                    if scope_error:
                        tr = ToolResult(False, scope_error)
                    else:
                        tr = await self.tools.execute(call.name, args)
                    used_tools.append(call.name)
                    changed.extend(tr.changed_files)
                    if not tr.ok:
                        failures.append(f"{call.name}: {tr.content[:500]}")
                    content = self.redactor.redact(str(tr.content or ""))
                    content = self.working_memory.context_tool_result(call.name, content)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
                    await self.events.emit("agent.tool.finished", project=self.project, task_id=task_id,
                                           name=call.name, ok=tr.ok, changed_files=tr.changed_files)
            current = self.store.get_task(task_id)
            if current is not None and current.get("status") == "cancelled":
                return await finish_cancelled()
            self.store.update_task(task_id, status="verifying", result=final, changed_files=changed)
            # Keep completion truthful: a tool failure is evidence, but not automatically fatal if the owner recovered.
            outcome = final or "Task completed without a textual final response."
            self.episodes.add(str(task.get("objective") or ""), outcome,
                              reasoning=reasoning.value, tools=used_tools,
                              changed_files=sorted(set(changed)), failures=failures)
            self.store.update_task(task_id, status="completed", result=outcome, changed_files=changed)
            self.store.set_agent_state(self.project, status="idle", current_task_id="", last_error="")
            self.capsules.build(self.project)
            await self.events.emit("agent.task.completed", project=self.project, task_id=task_id,
                                   changed_files=sorted(set(changed)), failures=len(failures))
            return outcome
        except asyncio.CancelledError:
            # Lease recovery will safely make this runnable again after restart.
            self.store.set_agent_state(self.project, status="recovering", current_task_id=task_id)
            raise
        except Exception as exc:
            err = self.redactor.redact(f"{type(exc).__name__}: {exc}")
            self.store.update_task(task_id, status="failed", result=final, error=err, changed_files=changed)
            self.store.set_agent_state(self.project, status="error", current_task_id="", last_error=err)
            try:
                self.capsules.build(self.project)
            except Exception:
                pass
            await self.events.emit("agent.task.failed", project=self.project, task_id=task_id, error=err)
            raise
