from __future__ import annotations

import asyncio
import json
import time
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .brain import AttentionRouter, DAGCortex, ExecutivePlanner, FimEditor, ParallelCortex, ExistingAppImprover
from .brain.product import ProductPlanner
from .config import Term5Config
from .context import ContextCompiler
from .events import Event, EventBus
from .models import ChatResult, ReasoningMode, RiskLevel, ToolDefinition, ToolResult, Usage
from .providers import DeepSeekProvider, ModelProvider
from .security import PathGuard, SecurityPolicy, SecretRedactor
from .state.memory import MemoryStore
from .state.artifacts import ArtifactStore
from .state.episodes import EpisodeStore
from .state.working_memory import WorkingMemoryManager
from .state.metrics import Pricing, UsageLedger
from .state.sessions import SessionStore
from .state.transactions import TransactionManager
from .state.checkpoints import CheckpointStore
from .tools import ToolRegistry, register_builtin_tools, register_operations_tools, register_browser_tools, register_project_tools, register_configuration_tools, register_production_tools, register_collaboration_tools, register_creative_tools, register_agent_tools
from .tools.apps import register_app_tools
from .tools.knowledge import register_knowledge_tools
from .apps import AppRuntime
from .ops import OperationsRuntime
from .skills import SkillEngine, SkillResolution
from .workspace import VerificationPipeline, WorkspaceGraph, ExistingApplicationGraph
from .browser import BrowserRuntime
from .projects import ProjectManager
from .configuration import ConfigurationManager
from .production import ProductionIntelligence
from .collaboration import DurableRunStore, HumanTaskManager
from .creative import CreativeStudio
from .agents import AgentOrchestrator


class AgentRuntime:
    def __init__(
        self,
        config: Term5Config,
        provider: ModelProvider | None = None,
        *,
        resume: bool = False,
        session_name: str | None = None,
        recover_checkpoint: bool = False,
    ) -> None:
        self.config = config
        self.started = time.time()
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.guard = PathGuard(config.root)
        capabilities = {"workspace.read", "workspace.write", "memory.read", "memory.write", "git.read", "model.reason", "model.fim"}
        if config.security.allow_process_tests:
            capabilities.add("process.tests")
        if config.security.allow_network:
            capabilities.add("network.fetch")
        if config.browser.enabled:
            capabilities.add("browser.inspect")
            if config.browser.allow_interaction:
                capabilities.add("browser.interact")
        if config.vision.enabled:
            capabilities.add("model.vision")
        if config.apps.enabled:
            capabilities.update({"app.manage", "docker.inspect", "docker.build", "docker.lifecycle", "host.browser"})
        if config.security.allow_git_write:
            capabilities.add("git.write")
        if config.security.allow_git_remote:
            capabilities.add("git.remote")
        if config.security.allow_git_provider_write:
            capabilities.add("git.provider.write")
        if config.projects.enabled:
            capabilities.update({"project.read", "project.write"})
        if config.autonomy.enabled:
            capabilities.add("human.task")
        if config.creative.enabled:
            capabilities.add("creative.generate")
        if config.agents.enabled:
            capabilities.update({"agent.read", "agent.coordinate"})
        if config.configuration.enabled:
            capabilities.update({"config.read", "config.write"})
        if config.operations.enabled:
            capabilities.update({"ops.read", "ops.server.read", "ops.nginx.read", "ops.tls.read", "ops.deploy"})
        if config.production.enabled and config.operations.enabled:
            capabilities.update({"production.read", "production.manage"})
            if config.security.allow_server_write:
                capabilities.add("ops.server.write")
            if config.security.allow_nginx_write:
                capabilities.add("ops.nginx.write")
            if config.security.allow_tls_issue:
                capabilities.add("ops.tls.write")
        self.security = SecurityPolicy(capabilities)
        self.redactor = SecretRedactor([config.api_key] if config.api_key else [])
        self.events = EventBus()
        self.audit_path = config.state_dir / "audit.jsonl"
        self.event_path = config.state_dir / "events.jsonl"
        self.journal_path = config.state_dir / "journal.jsonl"
        self._activity_lock = threading.RLock()
        self._activity: dict[str, Any] = {
            "state": "idle", "phase": "idle", "started_at": None, "finished_at": None,
            "reasoning": "none", "iteration": 0,
            "max_iterations": (config.scheduler.max_tool_iterations or None),
            "iteration_policy": (f"max {config.scheduler.max_tool_iterations}" if config.scheduler.max_tool_iterations else "unlimited"),
            "tool_calls_total": 0, "tools_completed": 0, "tools_failed": 0,
            "active_tools": {}, "last_error": None, "last_event": None,
            "loop_suspected": False, "recent_tool_signatures": [],
            "context": {},
            "next_actions": [],
            "visual": {
                "browser_state": "idle", "vision_state": "idle",
                "vision_model": config.vision.model or config.model.model,
                "vision_calls": 0, "screenshots": [], "last_vision_preview": "",
                "last_url": "", "audit_count": 0,
            },
        }
        self.events.subscribe("*", self._trace_event)
        self.events.subscribe("*", self._observe_event)

        self.memory = MemoryStore(config.state_dir / "memory.json")
        self.artifacts = ArtifactStore(config.state_dir)
        self.episodes = EpisodeStore(config.state_dir)
        self.working_memory = WorkingMemoryManager(config, self.artifacts, self.episodes)
        self.durable_runs = DurableRunStore(config.state_dir)
        self.human_tasks = HumanTaskManager(config.state_dir, self.durable_runs, default_timeout_s=config.autonomy.default_human_timeout_s)
        self._active_run_id = ""
        self._autonomy_future = None
        self._agents_future = None
        self._resuming_tasks: set[str] = set()
        self.transactions = TransactionManager(config.state_dir, self.guard)
        self.recovered_transactions = self.transactions.recover_open()
        self.graph = WorkspaceGraph(self.guard, config.state_dir)
        self.application_graph = ExistingApplicationGraph(self.guard)
        self.provider = provider or DeepSeekProvider(config)
        self.improver = ExistingAppImprover(
            self.provider, max_tokens=config.improvement.plan_tokens,
            max_surfaces=config.improvement.max_surfaces,
            max_batch_files=config.improvement.max_batch_files,
            require_coverage=config.improvement.require_coverage,
            prefer_before_after=config.improvement.prefer_before_after,
            visible_impact_min_surfaces=config.improvement.visible_impact_min_surfaces,
        )
        self.browser = BrowserRuntime(config.browser, self.artifacts)
        self.router = AttentionRouter()
        self.parallel = ParallelCortex(
            self.provider,
            config.reasoning.max_parallel_total,
            config.reasoning.max_parallel_high,
            timeout_s=config.reasoning.worker_timeout_s,
            retries=config.reasoning.worker_retries,
        )
        self.dag = DAGCortex(self.parallel, max_parallel=config.reasoning.max_parallel_total)
        self.executive = ExecutivePlanner(
            self.provider, self.dag,
            max_workers=config.executive.max_workers,
            max_plan_tokens=config.executive.max_plan_tokens,
        )
        self.skills = SkillEngine(config.root, config.state_dir)
        self.product = ProductPlanner(
            self.provider, self.parallel, self.skills, config.state_dir, root=config.root,
            max_tokens=config.skills.product_plan_tokens, critique=config.skills.critique,
        )
        self.verifier = VerificationPipeline(
            config.root, allow_tests=config.security.allow_process_tests,
            timeout_s=config.scheduler.verification_timeout_s,
        )
        self.fim = FimEditor(config, self.provider, self.guard, self.transactions, self.graph)

        self.ledger = UsageLedger(Pricing(
            config.pricing.cache_hit_per_million,
            config.pricing.cache_miss_per_million,
            config.pricing.output_per_million,
        ))
        self.tools = ToolRegistry(self.security, config.scheduler.max_local_reads)
        register_builtin_tools(self.tools, self.guard, self.memory, self.graph, self.transactions, self.verifier)
        if config.browser.enabled:
            register_browser_tools(self.tools, self.browser, self.events)
        self.apps = AppRuntime(config.root, config.state_dir, self.guard, self.transactions, docker_timeout_s=config.apps.docker_timeout_s)
        self.projects = ProjectManager(
            config.root, config.state_dir, self.guard, config=config.projects,
            timeout_s=config.operations.command_timeout_s,
        ) if config.projects.enabled else None
        if config.apps.enabled:
            register_app_tools(self.tools, self.apps, self.product, self.projects)
        self.configuration = ConfigurationManager(
            config.root, config.state_dir, self.projects, config=config.configuration, redactor=self.redactor,
        ) if (config.configuration.enabled and self.projects is not None) else None
        if self.configuration is not None:
            register_configuration_tools(self.tools, self.configuration, self.events, self.human_tasks, self.projects)
        self.creative = CreativeStudio(config.creative, self.artifacts, configuration=self.configuration, projects=self.projects, redactor=self.redactor) if config.creative.enabled else None
        if config.autonomy.enabled:
            register_collaboration_tools(self.tools, self.human_tasks, self.durable_runs, projects=self.projects, events=self.events, config=config.autonomy)
        if self.creative is not None:
            register_creative_tools(self.tools, self.creative, self.human_tasks, projects=self.projects, configuration=self.configuration, events=self.events)
        self.operations = OperationsRuntime(
            config.root, config.state_dir, self.apps, config=config.operations, security_config=config.security,
            projects=self.projects, configuration=self.configuration,
        )
        self.production = ProductionIntelligence(config.state_dir, self.operations, config=config.production, events=self.events) if (config.production.enabled and config.operations.enabled) else None
        if self.production is not None:
            self.operations.attach_production(self.production)
            register_production_tools(self.tools, self.production)
        if self.projects is not None:
            try:
                self.projects.adopt_apps(self.apps, self.operations.registry)
                if self.production is not None:
                    self.production.sync_deployments()
            except Exception:
                pass
            register_project_tools(self.tools, self.projects, github_config=config.github)
        if config.operations.enabled and config.apps.enabled:
            register_operations_tools(self.tools, self.operations)
        register_knowledge_tools(self.tools, self.skills, self.product)
        self._register_cognitive_tools()
        self.agents = AgentOrchestrator(
            config=config, projects=self.projects, provider=self.provider, tools=self.tools,
            events=self.events, redactor=self.redactor,
        ) if (config.agents.enabled and self.projects is not None) else None
        if self.agents is not None:
            register_agent_tools(self.tools, self.agents)
        self.context = ContextCompiler(config, self.graph, self.memory, self.skills, self.episodes)

        self.session_store = SessionStore(config.state_dir / "session.json", runtime_version=__version__)
        self.checkpoints = CheckpointStore(config.state_dir, runtime_version=__version__)
        self.session_name = session_name
        self.recovered_checkpoint = False
        self.recovery_prompt = ""
        loaded = self.session_store.load(session_name) if resume else None
        if recover_checkpoint:
            cp = self.checkpoints.load()
            if cp is not None and (session_name is None or cp.session_name in {None, session_name}):
                loaded = cp.messages
                self.session_name = cp.session_name or session_name
                self.recovered_checkpoint = True
                self.recovery_prompt = cp.prompt
        self.messages: list[dict[str, Any]] = loaded or [{"role": "system", "content": self.context.system_prompt}]
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": self.context.system_prompt})
        else:
            self.messages[0] = {"role": "system", "content": self.context.system_prompt}
        self.messages, self.protocol_repairs = self.context.repair_tool_protocol(self.messages)
        self.messages, self.legacy_context_tokens_saved = self.working_memory.migrate_legacy(self.messages)

        self.last_reasoning = ReasoningMode.NONE
        self.forced_reasoning: ReasoningMode = config.default_reasoning
        self.turns = 0
        self.executive_runs = 0
        self.last_executive_plan: dict[str, Any] | None = None
        self.last_verification: list[str] = []
        self.product_plan_runs = 0
        self.last_skill_resolution: list[str] = []
        self.last_product_plan: dict[str, Any] | None = None
        self.last_product_critics: dict[str, str] = {}
        self.improvement_plan_runs = 0
        self.last_improvement_plan: dict[str, Any] | None = None
        self.last_application_map: dict[str, Any] | None = None
        self.write_conflicts = 0
        self.checkpoint_writes = 0
        self._turn_lock = asyncio.Lock()

    def _checkpoint(self, phase: str, prompt: str = "", **metadata: Any) -> None:
        if not self.config.session.checkpoint_turns:
            return
        self.checkpoints.save(
            phase=phase, messages=self.messages, prompt=prompt,
            session_name=self.session_name, metadata=metadata,
        )
        self.checkpoint_writes += 1

    def _trace_event(self, event: Event) -> None:
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": event.ts, "type": event.type, "data": event.data}
        safe = self.redactor.redact(json.dumps(rec, ensure_ascii=False, default=str))
        with self.event_path.open("a", encoding="utf-8") as fh:
            fh.write(safe + "\n")

    def _safe_event_data(self, data: Any) -> Any:
        """Redact secrets and bound payloads before exposing telemetry to the UI."""
        try:
            raw = json.dumps(data, ensure_ascii=False, default=str)
            safe = self.redactor.redact(raw)
            if len(safe) > 16_000:
                safe = safe[:16_000] + "…"
            return json.loads(safe)
        except Exception:
            return {"summary": self.redactor.redact(str(data))[:4_000]}

    def _observe_event(self, event: Event) -> None:
        with self._activity_lock:
            a = self._activity
            a["last_event"] = {"seq": event.seq, "type": event.type, "ts": event.ts}
            typ = event.type
            data = event.data
            if typ == "turn.started":
                a.update({
                    "state": "running", "phase": "routing", "started_at": event.ts, "finished_at": None,
                    "reasoning": str(data.get("reasoning") or "auto"), "iteration": 0,
                    "tool_calls_total": 0, "tools_completed": 0, "tools_failed": 0,
                    "active_tools": {}, "last_error": None, "loop_suspected": False,
                    "recent_tool_signatures": [], "next_actions": [],
                    "visual": {
                        "browser_state": "idle", "vision_state": "idle",
                        "vision_model": self.config.vision.model or self.config.model.model,
                        "vision_calls": 0, "screenshots": [], "last_vision_preview": "",
                        "last_url": "", "audit_count": 0,
                    },
                })
            elif typ == "skills.resolved":
                a["phase"] = "resolving skills"
            elif typ == "product_plan.started":
                a["phase"] = "product planning"
            elif typ == "product_plan.finished":
                a["phase"] = "product plan ready"
            elif typ == "improvement_plan.started":
                a["phase"] = "mapping existing application"
            elif typ == "improvement_plan.finished":
                a["phase"] = "improvement plan ready"
            elif typ == "executive.started":
                a["phase"] = "executive planning"
            elif typ == "executive.finished":
                a["phase"] = "executive plan ready"
            elif typ == "executive.failed":
                a["phase"] = "executive fallback"
                a["last_error"] = str(data.get("error") or "")[:2_000]
            elif typ == "context.budget":
                a["context"] = self._safe_event_data(data)
            elif typ == "context.compacted":
                a["context"] = {**(a.get("context") or {}), **self._safe_event_data(data)}
            elif typ == "production.snapshot":
                a["phase"] = "production monitoring"
            elif typ in {"release.started", "release.finished", "release.verified"}:
                a["phase"] = "release verification"
            elif typ in {"incident.opened", "incident.updated"}:
                a["phase"] = "incident analysis"
            elif typ == "model.started":
                a["phase"] = "model reasoning"
                a["iteration"] = int(data.get("iteration") or 0)
                a["reasoning"] = str(data.get("reasoning") or a.get("reasoning") or "auto")
            elif typ == "model.finished":
                count = int(data.get("tool_calls") or 0)
                a["phase"] = "dispatching tools" if count else "finalizing"
                a["tool_calls_total"] = int(a.get("tool_calls_total") or 0) + count
            elif typ == "tool.started":
                call_id = str(data.get("call_id") or data.get("name") or "tool")
                entry = {
                    "call_id": call_id, "name": str(data.get("name") or "tool"),
                    "arguments": self._safe_event_data(data.get("arguments") or {}),
                    "started_at": event.ts, "iteration": data.get("iteration"),
                }
                active = dict(a.get("active_tools") or {})
                active[call_id] = entry
                a["active_tools"] = active
                a["phase"] = "running tools"
                signature = json.dumps([entry["name"], entry["arguments"]], ensure_ascii=False, sort_keys=True, default=str)[:2_000]
                recent = list(a.get("recent_tool_signatures") or [])[-7:] + [signature]
                a["recent_tool_signatures"] = recent
                if len(recent) >= 3 and recent[-1] == recent[-2] == recent[-3]:
                    a["loop_suspected"] = True
            elif typ in {"tool.finished", "tool.skipped"}:
                call_id = str(data.get("call_id") or "")
                active = dict(a.get("active_tools") or {})
                if call_id:
                    active.pop(call_id, None)
                a["active_tools"] = active
                a["tools_completed"] = int(a.get("tools_completed") or 0) + 1
                if not bool(data.get("ok", typ == "tool.finished")):
                    a["tools_failed"] = int(a.get("tools_failed") or 0) + 1
                if not active:
                    a["phase"] = "returning tool results to model"
            elif typ == "agent.next_action":
                a["next_actions"] = [str(x) for x in (data.get("tools") or [])][:12]
            elif typ == "browser.open.started":
                a["phase"] = "opening rendered application"
                a["visual"]["browser_state"] = "opening"
                a["visual"]["last_url"] = str(data.get("url") or "")
            elif typ == "browser.open.completed":
                a["phase"] = "browser inspection"
                a["visual"]["browser_state"] = "open" if data.get("ok", True) else "error"
                a["visual"]["last_url"] = str(data.get("url") or a["visual"].get("last_url") or "")
            elif typ == "browser.screenshot.started":
                a["phase"] = "capturing screenshot"
                a["visual"]["browser_state"] = "capturing"
            elif typ == "browser.screenshot.completed":
                a["phase"] = "screenshot captured"
                a["visual"]["browser_state"] = "screenshot captured"
                shot = self._safe_event_data({
                    "artifact_id": data.get("artifact_id"), "url": data.get("url"),
                    "viewport": data.get("viewport"), "bytes": data.get("bytes"),
                    "name": data.get("name"),
                })
                shots = list(a["visual"].get("screenshots") or [])
                shots.append(shot)
                a["visual"]["screenshots"] = shots[-12:]
            elif typ == "browser.audit.started":
                a["phase"] = "auditing rendered pages"
                a["visual"]["browser_state"] = "auditing"
            elif typ == "browser.audit.completed":
                a["phase"] = "browser audit complete"
                a["visual"]["browser_state"] = "audit complete"
                a["visual"]["audit_count"] = int(data.get("count") or 0)
                shots = list(a["visual"].get("screenshots") or [])
                for item in (data.get("screenshots") or []):
                    shots.append(self._safe_event_data(item))
                a["visual"]["screenshots"] = shots[-12:]
            elif typ == "vision.started":
                a["phase"] = "vision model inspecting screenshots"
                a["visual"]["vision_state"] = "running"
                a["visual"]["vision_model"] = str(data.get("model") or a["visual"].get("vision_model") or "")
                a["visual"]["vision_artifacts"] = list(data.get("artifact_ids") or [])
            elif typ == "vision.completed":
                a["phase"] = "vision findings returned"
                a["visual"]["vision_state"] = "complete"
                a["visual"]["vision_model"] = str(data.get("model") or a["visual"].get("vision_model") or "")
                a["visual"]["vision_calls"] = int(a["visual"].get("vision_calls") or 0) + 1
                a["visual"]["last_vision_preview"] = str(data.get("analysis_preview") or "")[:4000]
            elif typ == "vision.failed":
                a["phase"] = "vision unavailable / failed"
                a["visual"]["vision_state"] = "failed"
                a["visual"]["last_vision_preview"] = str(data.get("error") or "")[:4000]
            elif typ == "loop.warning":
                a["loop_suspected"] = True
            elif typ == "loop.aborted":
                a["phase"] = "repeated tool loop stopped"
                a["loop_suspected"] = True
            elif typ == "protocol.repaired":
                a["phase"] = "repairing tool protocol"
            elif typ == "human_task.created":
                if bool(data.get("blocking")):
                    a["state"] = "waiting_input"
                a["phase"] = "waiting for human" if bool(data.get("blocking")) else "human task created"
                a["human_task"] = self._safe_event_data(data)
            elif typ in {"human_task.resolved", "human_task.timeout"}:
                a["phase"] = "human decision received"
                a["human_task"] = self._safe_event_data(data)
            elif typ == "human_task.resumed":
                a["phase"] = "resuming durable objective"
                a["human_task"] = self._safe_event_data(data)
            elif typ == "configuration.required":
                a["state"] = "waiting_input"
                a["phase"] = "waiting for configuration"
                a["configuration"] = self._safe_event_data(data)
            elif typ == "configuration.updated":
                a["phase"] = "configuration updated"
                a["configuration"] = self._safe_event_data(data)
            elif typ == "configuration.test":
                a["phase"] = "configuration connection test"
                a["configuration_test"] = self._safe_event_data(data)
            elif typ == "turn.finished":
                open_tasks = self.human_tasks.list(state="open", limit=500) if self.config.autonomy.enabled else []
                waiting = any(bool(x.get("blocking")) for x in open_tasks)
                if not waiting and getattr(self, "configuration", None) is not None and self.configuration.has_pending():
                    waiting = True
                a["state"] = "waiting_input" if waiting else "completed"
                a["phase"] = "waiting for human" if waiting else "complete"
                a["finished_at"] = event.ts
                a["active_tools"] = {}
            elif typ == "turn.failed":
                a["state"] = "failed"
                a["phase"] = "failed"
                a["finished_at"] = event.ts
                a["active_tools"] = {}
                a["last_error"] = str(data.get("error") or "")[:4_000]

    def activity_snapshot(self, *, after: int = 0, limit: int = 250) -> dict[str, Any]:
        with self._activity_lock:
            activity = json.loads(json.dumps(self._activity, ensure_ascii=False, default=str))
        events = []
        for event in self.events.snapshot(after=after, limit=limit):
            events.append({
                "seq": event.seq, "ts": event.ts, "type": event.type,
                "data": self._safe_event_data(event.data),
            })
        return {"activity": activity, "events": events, "last_seq": self.events.last_seq}

    def _repair_protocol_history(self) -> int:
        repaired, count = self.context.repair_tool_protocol(self.messages)
        if count:
            self.messages = repaired
            self.protocol_repairs += count
        return count

    def _register_cognitive_tools(self) -> None:
        worker_max = min(64, self.config.reasoning.max_parallel_total)

        async def parallel_reason(tasks: list[dict[str, Any]], shared_context: str = "") -> ToolResult:
            if len(tasks) > worker_max:
                return ToolResult(False, f"Too many workers requested; max is {worker_max}")
            results = await self.parallel.run(tasks, shared_context=shared_context[:16_000])
            body = []
            for result in results:
                body.append({
                    "task_id": result.task_id,
                    "status": result.status,
                    "conclusion": result.conclusion,
                    "metrics": result.metrics,
                })
                self.ledger.add(Usage(
                    prompt_tokens=int(result.metrics.get("prompt_tokens") or 0),
                    completion_tokens=int(result.metrics.get("completion_tokens") or 0),
                    cache_hit_tokens=(None if result.metrics.get("cache_hit_tokens") is None else int(result.metrics.get("cache_hit_tokens") or 0)),
                    cache_miss_tokens=(None if result.metrics.get("cache_miss_tokens") is None else int(result.metrics.get("cache_miss_tokens") or 0)),
                ), float(result.metrics.get("elapsed_s") or 0.0))
            return ToolResult(True, json.dumps(body, ensure_ascii=False, indent=2), data=body)

        self.tools.register(ToolDefinition(
            "parallel_reason",
            "Run independent temporary no-tools reasoning workers concurrently. Use for independent evidence/hypotheses, not for sequential dependencies.",
            {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array", "minItems": 1, "maxItems": worker_max,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "objective": {"type": "string"},
                                "reasoning": {"type": "string", "enum": ["none", "low", "high", "max"]},
                            },
                            "required": ["objective"], "additionalProperties": False,
                        },
                    },
                    "shared_context": {"type": "string"},
                },
                "required": ["tasks"], "additionalProperties": False,
            },
            parallel_reason,
            {"model.reason"}, RiskLevel.MEDIUM, True, False,
        ))

        async def parallel_dag(tasks: list[dict[str, Any]], shared_context: str = "") -> ToolResult:
            if len(tasks) > worker_max:
                return ToolResult(False, f"Too many DAG nodes requested; max is {worker_max}")
            try:
                results = await self.dag.run(tasks, shared_context=shared_context[:16_000])
            except Exception as exc:
                return ToolResult(False, f"DAG rejected: {exc}")
            body = []
            for task_id, result in results.items():
                body.append({
                    "task_id": task_id,
                    "status": result.status,
                    "conclusion": result.conclusion,
                    "metrics": result.metrics,
                })
                self.ledger.add(Usage(
                    prompt_tokens=int(result.metrics.get("prompt_tokens") or 0),
                    completion_tokens=int(result.metrics.get("completion_tokens") or 0),
                    cache_hit_tokens=(None if result.metrics.get("cache_hit_tokens") is None else int(result.metrics.get("cache_hit_tokens") or 0)),
                    cache_miss_tokens=(None if result.metrics.get("cache_miss_tokens") is None else int(result.metrics.get("cache_miss_tokens") or 0)),
                ), float(result.metrics.get("elapsed_s") or 0.0))
            return ToolResult(True, json.dumps(body, ensure_ascii=False, indent=2), data=body)

        self.tools.register(ToolDefinition(
            "parallel_dag",
            "Run a dependency-aware DAG of temporary reasoning workers. Ready nodes execute concurrently; dependent nodes receive compact predecessor conclusions.",
            {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array", "minItems": 1, "maxItems": worker_max,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "objective": {"type": "string"},
                                "reasoning": {"type": "string", "enum": ["none", "low", "high", "max"]},
                                "depends_on": {"type": "array", "items": {"type": "string"}, "maxItems": worker_max},
                            },
                            "required": ["id", "objective"], "additionalProperties": False,
                        },
                    },
                    "shared_context": {"type": "string"},
                },
                "required": ["tasks"], "additionalProperties": False,
            },
            parallel_dag,
            {"model.reason"}, RiskLevel.MEDIUM, True, False,
        ))

        async def fim_preview(path: str, start_line: int, end_line: int, instruction: str,
                              max_tokens: int | None = None) -> ToolResult:
            result = await self.fim.preview(path, start_line, end_line, instruction, max_tokens)
            self._record_fim_usage(result)
            return result

        self.tools.register(ToolDefinition(
            "fim_preview",
            "Generate and locally validate a non-thinking FIM candidate for a bounded line region without writing it. Returns a unified diff.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "instruction": {"type": "string"},
                    "max_tokens": {"type": "integer", "minimum": 1, "maximum": 4096},
                },
                "required": ["path", "start_line", "end_line", "instruction"],
                "additionalProperties": False,
            },
            fim_preview,
            {"model.fim", "workspace.read"}, RiskLevel.MEDIUM, True, False,
        ))

        async def fim_edit(path: str, start_line: int, end_line: int, instruction: str,
                           max_tokens: int | None = None) -> ToolResult:
            result = await self.fim.edit(path, start_line, end_line, instruction, max_tokens)
            self._record_fim_usage(result)
            return result

        self.tools.register(ToolDefinition(
            "fim_edit",
            "Replace a bounded existing line region using non-thinking FIM after intent is understood. Candidate is locally validated before transactional commit.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "instruction": {"type": "string"},
                    "max_tokens": {"type": "integer", "minimum": 1, "maximum": 4096},
                },
                "required": ["path", "start_line", "end_line", "instruction"],
                "additionalProperties": False,
            },
            fim_edit,
            {"model.fim", "workspace.write"}, RiskLevel.HIGH, False, False,
        ))

        fim_item_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "instruction": {"type": "string"},
                "max_tokens": {"type": "integer", "minimum": 1, "maximum": 4096},
            },
            "required": ["path", "start_line", "end_line", "instruction"],
            "additionalProperties": False,
        }

        async def parallel_fim_preview(changes: list[dict[str, Any]]) -> ToolResult:
            result = await self.fim.batch(changes, commit=False)
            self._record_fim_usage(result)
            return result

        self.tools.register(ToolDefinition(
            "parallel_fim_preview",
            "Generate and validate independent FIM candidates concurrently for unique files without writing them.",
            {
                "type": "object",
                "properties": {"changes": {"type": "array", "minItems": 1, "maxItems": min(32, self.config.fim.max_parallel), "items": fim_item_schema}},
                "required": ["changes"], "additionalProperties": False,
            },
            parallel_fim_preview,
            {"model.fim", "workspace.read"}, RiskLevel.MEDIUM, True, False,
        ))

        async def parallel_fim_edit(changes: list[dict[str, Any]]) -> ToolResult:
            result = await self.fim.batch(changes, commit=True)
            self._record_fim_usage(result)
            return result

        self.tools.register(ToolDefinition(
            "parallel_fim_edit",
            "Generate independent FIM candidates concurrently for unique files, validate all, then commit all files atomically under one rollback boundary.",
            {
                "type": "object",
                "properties": {"changes": {"type": "array", "minItems": 1, "maxItems": min(32, self.config.fim.max_parallel), "items": fim_item_schema}},
                "required": ["changes"], "additionalProperties": False,
            },
            parallel_fim_edit,
            {"model.fim", "workspace.write"}, RiskLevel.HIGH, False, True,
        ))


        async def episode_recall(query: str, k: int = 5) -> ToolResult:
            text = self.episodes.recall(query, max(1, min(int(k), 12)), self.config.context.episode_recall_chars)
            return ToolResult(bool(text), text or "No matching completed-task episodes.")

        self.tools.register(ToolDefinition(
            "episode_recall",
            "Recall compact completed-task episodes relevant to a query. Use when prior project work matters but raw historical execution detail should not be loaded.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "minimum": 1, "maximum": 12},
                },
                "required": ["query"], "additionalProperties": False,
            },
            episode_recall, {"memory.read"}, RiskLevel.LOW, True, False,
        ))

        async def artifact_read(artifact_id: str, query: str = "", max_chars: int = 12000) -> ToolResult:
            try:
                text = self.artifacts.read(artifact_id, query=query, max_chars=max_chars)
                return ToolResult(True, text)
            except Exception as exc:
                return ToolResult(False, f"artifact_read failed: {type(exc).__name__}: {exc}")

        self.tools.register(ToolDefinition(
            "artifact_read",
            "Read or search a locally archived verbose tool result by artifact id. Use only when omitted details from a compact tool observation are actually needed.",
            {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "query": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 50000},
                },
                "required": ["artifact_id"], "additionalProperties": False,
            },
            artifact_read, {"workspace.read"}, RiskLevel.LOW, True, False,
        ))

        async def application_map() -> ToolResult:
            active_project = self.projects.active() if self.projects is not None else None
            amap = self.application_graph.build(active_project.path if active_project is not None else "")
            data = amap.as_dict()
            self.last_application_map = data
            return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

        self.tools.register(ToolDefinition(
            "application_map",
            "Build a deterministic web-application map across routes, route→template relationships, Jinja shared shells/includes, CSS selectors, JS selectors and tests. Use before broad edits to existing applications so the change is repository-scale instead of one-file guesswork.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            application_map, {"workspace.read"}, RiskLevel.LOW, True, False,
        ))

        async def improvement_plan(request: str) -> ToolResult:
            amap = self.application_graph.build()
            self.last_application_map = amap.as_dict()
            started = time.monotonic()
            plan = await self.improver.plan(request, amap, amap.context_text())
            if plan.usage.prompt_tokens or plan.usage.completion_tokens:
                self.ledger.add(plan.usage, time.monotonic() - started)
            self.improvement_plan_runs += 1
            self.last_improvement_plan = plan.as_dict()
            return ToolResult(True, json.dumps(self.last_improvement_plan, ensure_ascii=False, indent=2), data=self.last_improvement_plan)

        self.tools.register(ToolDefinition(
            "improvement_plan",
            "Create a high-impact improvement plan for an existing app using the deterministic application map. Requires route/surface coverage, coherent batches, visible-impact goals and browser/vision verification when available.",
            {"type":"object","properties":{"request":{"type":"string"}},"required":["request"],"additionalProperties":False},
            improvement_plan, {"workspace.read", "model.reason"}, RiskLevel.MEDIUM, True, False,
        ))

        async def improvement_plan_current() -> ToolResult:
            if self.last_improvement_plan is None:
                return ToolResult(False, "No existing-application improvement plan has run in this runtime yet.")
            return ToolResult(True, json.dumps(self.last_improvement_plan, ensure_ascii=False, indent=2), data=self.last_improvement_plan)

        self.tools.register(ToolDefinition(
            "improvement_plan_current", "Return the most recent existing-application improvement plan without spending another model call.",
            {"type":"object","properties":{},"additionalProperties":False}, improvement_plan_current,
            set(), RiskLevel.LOW, True, False,
        ))

        def _image_payload(artifact_ids: list[str]) -> tuple[list[tuple[bytes, str]], list[dict[str, Any]]]:
            images: list[tuple[bytes, str]] = []
            metas: list[dict[str, Any]] = []
            if len(artifact_ids) > self.config.vision.max_images:
                raise ValueError(f"too many image artifacts; max is {self.config.vision.max_images}")
            for aid in artifact_ids:
                meta = self.artifacts.metadata(aid)
                path = self.artifacts.path_for(aid)
                if str(meta.get("kind")) != "screenshot" and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError(f"artifact {aid} is not an image")
                raw = path.read_bytes()
                if len(raw) > self.config.vision.max_image_bytes:
                    raise ValueError(f"artifact {aid} exceeds vision image byte cap")
                mime = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".webp":"image/webp"}.get(path.suffix.lower(), "image/png")
                images.append((raw, mime))
                metas.append({"id": aid, "name": meta.get("name"), "metadata": meta.get("metadata") or {}})
            return images, metas

        async def _run_vision(artifact_ids: list[str], prompt: str, *, mode: str) -> ToolResult:
            if not self.config.vision.enabled:
                await self.events.emit("vision.failed", mode=mode, error="Vision is disabled in term5 configuration.")
                return ToolResult(False, "Vision is disabled in term5 configuration.")
            model_name = self.config.vision.model or self.config.model.model
            await self.events.emit(
                "vision.started", mode=mode, model=model_name, artifact_ids=list(artifact_ids),
                prompt_preview=str(prompt or "")[:500],
            )
            try:
                images, metas = _image_payload(artifact_ids)
                started = time.monotonic()
                result = await self.provider.vision(images, prompt, max_tokens=self.config.vision.max_tokens)
                elapsed = time.monotonic() - started
                self.ledger.add(result.usage, elapsed)
                resolved_model = result.model or model_name
                await self.events.emit(
                    "vision.completed", mode=mode, model=resolved_model, artifact_ids=list(artifact_ids),
                    images=len(images), elapsed_s=round(elapsed, 4),
                    prompt_tokens=result.usage.prompt_tokens, completion_tokens=result.usage.completion_tokens,
                    analysis_preview=str(result.content or "")[:2000],
                )
                data = {"artifacts": metas, "analysis": result.content, "model": resolved_model, "mode": mode}
                return ToolResult(True, result.content, data=data)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                await self.events.emit("vision.failed", mode=mode, model=model_name, artifact_ids=list(artifact_ids), error=error)
                return ToolResult(False, f"vision_{mode} failed: {error}")

        async def vision_inspect(artifact_ids: list[str], prompt: str = "Audit this rendered application UI for visual hierarchy, usability, polish, responsive problems and concrete high-impact improvements.") -> ToolResult:
            return await _run_vision(artifact_ids, prompt, mode="inspect")

        self.tools.register(ToolDefinition(
            "vision_inspect",
            "Inspect one or more local browser screenshot artifacts with the configured multimodal model. Use for rendered UI/UX quality, layout, hierarchy and visual defects; screenshot bytes stay out of normal conversation history.",
            {"type":"object","properties":{
                "artifact_ids":{"type":"array","minItems":1,"maxItems":self.config.vision.max_images,"items":{"type":"string"}},
                "prompt":{"type":"string","maxLength":24000}},"required":["artifact_ids"],"additionalProperties":False},
            vision_inspect, {"model.vision", "workspace.read"}, RiskLevel.MEDIUM, True, False,
        ))

        async def vision_compare(before_artifact_id: str, after_artifact_id: str,
                                 objective: str = "Compare before and after. Determine whether the requested improvement is materially visible, what improved, what regressed, and what still needs work.") -> ToolResult:
            return await _run_vision(
                [before_artifact_id, after_artifact_id],
                "Image 1 is BEFORE and image 2 is AFTER. " + objective, mode="compare",
            )

        self.tools.register(ToolDefinition(
            "vision_compare",
            "Compare before/after browser screenshots using vision. Use as a visible-impact gate so a broad redesign cannot be declared complete when the rendered delta is negligible.",
            {"type":"object","properties":{
                "before_artifact_id":{"type":"string"},"after_artifact_id":{"type":"string"},
                "objective":{"type":"string","maxLength":12000}},
             "required":["before_artifact_id","after_artifact_id"],"additionalProperties":False},
            vision_compare, {"model.vision", "workspace.read"}, RiskLevel.MEDIUM, True, False,
        ))

        async def product_audit() -> ToolResult:
            audit, usage = await self.product.audit(self.graph)
            if usage.prompt_tokens or usage.completion_tokens:
                self.ledger.add(usage, 0.0)
            verdict = str(audit.get("verdict") or "uncertain")
            return ToolResult(verdict == "ready", json.dumps(audit, ensure_ascii=False, indent=2), data=audit)

        self.tools.register(ToolDefinition(
            "product_audit",
            "Audit the current product blueprint against a bounded text/code snapshot. Uses HIGH reasoning, no vision, and reports evidence-backed done/partial/missing/uncertain features.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            product_audit, {"model.reason", "workspace.read"}, RiskLevel.MEDIUM, True, False,
        ))

        async def product_audit_current() -> ToolResult:
            audit = self.product.current_audit()
            if audit is None:
                return ToolResult(False, "No product audit has run in this runtime yet.")
            return ToolResult(str(audit.get("verdict")) == "ready", json.dumps(audit, ensure_ascii=False, indent=2), data=audit)

        self.tools.register(ToolDefinition(
            "product_audit_current", "Return the most recent product-completeness audit without running a new model call.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            product_audit_current, set(), RiskLevel.LOW, True, False,
        ))

    def _record_fim_usage(self, result: ToolResult) -> None:
        if isinstance(result.data, dict) and isinstance(result.data.get("usage"), dict):
            u = result.data["usage"]
            self.ledger.add(Usage(
                prompt_tokens=int(u.get("prompt_tokens") or 0),
                completion_tokens=int(u.get("completion_tokens") or 0),
            ))

    async def _audit(self, event: str, **data: Any) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        safe = self.redactor.redact(json.dumps(rec, ensure_ascii=False, default=str))
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(safe + "\n")

    def _assistant_message(self, result: ChatResult, *, turn_id: str = "") -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": result.content or None}
        if turn_id:
            msg["_term5_turn"] = turn_id
            msg["_term5_kind"] = "assistant"
        if result.reasoning_content:
            msg["reasoning_content"] = result.reasoning_content
        if result.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in result.tool_calls
            ]
        return msg

    def _central_tool_names(self) -> set[str]:
        """Tools exposed to the central coordinator when Project Owners exist.

        Project implementation, browser inspection, repository/file mutation and
        deployment execution belong to Project Owners. The central model keeps
        orchestration, project registration, state/contract queries, human tasks
        and bounded environment/readiness inspection.
        """
        exact = {
            "project_list", "project_status", "project_switch", "project_create", "project_import", "project_clone", "project_update",
            "agent_list", "agent_status", "agent_delegate", "agent_query", "agent_task_list", "agent_request_change", "agent_dependency_add",
            "agent_message_send", "agent_message_list", "agent_task_cancel", "agent_task_retry", "agent_capsule_refresh",
            "contract_query", "contract_list", "contract_impact", "contract_subscribe",
            "human_task_create", "human_task_list", "durable_run_list", "durable_run_status",
            "configuration_status", "configuration_discover", "configuration_require",
            "app_list", "app_status", "deployment_list", "deployment_status", "ops_status", "server_status", "production_overview", "production_readiness",
            "memory_recall", "memory_list", "memory_store", "episode_recall",
            "skill_list", "skill_resolve", "skill_show", "product_plan_current", "improvement_plan_current",
        }
        return {name for name in exact if self.tools.get(name) is not None}

    def _coordinator_mode(self) -> bool:
        if not (self.config.agents.enabled and self.config.agents.coordinator_only and self.agents is not None):
            return False
        try:
            return bool(self.agents.store.list_agents())
        except Exception:
            return False

    async def run_turn(self, user_text: str, *, reasoning: ReasoningMode | None = None,
                       use_tools: bool = True, _run_id: str = "") -> str:
        # v6.2 central intake is queued rather than rejected. The primary
        # conversation remains serialized while Project Owner queues run independently.
        async with self._turn_lock:
            try:
                return await self._run_turn_unlocked(user_text, reasoning=reasoning, use_tools=use_tools, run_id=_run_id)
            except Exception as exc:
                if self._active_run_id:
                    self.durable_runs.update(self._active_run_id, status="failed", last_error=self.redactor.redact(f"{type(exc).__name__}: {exc}"))
                await self.events.emit("turn.failed", error=self.redactor.redact(f"{type(exc).__name__}: {exc}"), run_id=self._active_run_id)
                self._active_run_id = ""
                self.human_tasks.set_active_run("")
                raise

    async def _run_turn_unlocked(self, user_text: str, *, reasoning: ReasoningMode | None = None,
                                 use_tools: bool = True, run_id: str = "") -> str:
        turn_id = "turn_" + uuid.uuid4().hex[:12]
        active_project_name = self.projects.registry.active_name if self.projects is not None else ""
        durable = self.durable_runs.start(user_text, project=active_project_name, run_id=run_id) if self.config.autonomy.enabled else None
        durable_run_id = str((durable or {}).get("id") or "")
        self._active_run_id = durable_run_id
        self.human_tasks.set_active_run(durable_run_id)
        objective_id = ""
        if self.agents is not None:
            try:
                objective_id = str(self.agents.begin_objective(user_text, project_hint=active_project_name, run_id=durable_run_id).get("id") or "")
                await self.events.emit("objective.created", objective_id=objective_id, title=user_text.strip().splitlines()[0][:180], project=active_project_name)
            except Exception as exc:
                await self.events.emit("objective.create_failed", error=self.redactor.redact(f"{type(exc).__name__}: {exc}"))
        turn_tools: list[str] = []
        turn_changed: list[str] = []
        turn_failures: list[str] = []
        self.memory.refresh_staleness(self.config.root)
        self.product.active = False
        decision = self.router.decide(user_text, reasoning or self.forced_reasoning)
        self.last_reasoning = decision.reasoning
        coordinator_mode = self._coordinator_mode()
        await self.events.emit("turn.started", prompt=user_text, reasoning=decision.reasoning.value,
                               routing_reason=decision.reason)
        await self._audit("turn_started", reasoning=decision.reasoning.value, prompt_chars=len(user_text))
        # RC1 safe-state checkpoint: preserve the *pre-turn* message state plus
        # the pending prompt. Recovery can therefore replay the turn without
        # duplicating an incomplete assistant/tool sequence.
        self._checkpoint("turn_start", user_text, reasoning=decision.reasoning.value)
        repaired = self._repair_protocol_history()
        if repaired:
            await self.events.emit("protocol.repaired", repaired_messages=repaired, source="pre_turn_history")

        # 5.2 procedural preflight: resolve declarative local/product/operations knowledge
        # first, then build/critique a product blueprint for vague product-build
        # requests before the general executive creates its implementation DAG.
        resolution = SkillResolution()
        skill_context = ""
        product_context = ""
        if self.config.skills.enabled and self.config.skills.auto_resolve:
            resolution = self.skills.resolve(user_text)
            self.last_skill_resolution = list(resolution.selected)
            skill_context = self.skills.resolution_context(resolution, max_chars=self.config.skills.context_chars)
            await self.events.emit("skills.resolved", selected=resolution.selected, direct=resolution.direct)
        if (not coordinator_mode) and self.product.should_plan(user_text, resolution, self.config.skills.auto_product_plan):
            self.product.active = True
            await self.events.emit("product_plan.started", skills=resolution.selected)
            plan_started = time.monotonic()
            plan = await self.product.plan(user_text, resolution, skill_context)
            if plan.usage.prompt_tokens or plan.usage.completion_tokens:
                self.ledger.add(plan.usage, time.monotonic() - plan_started)
            critics = await self.product.critique(plan, skill_context)
            for wr in critics:
                metrics = wr.metrics or {}
                self.ledger.add(Usage(
                    prompt_tokens=int(metrics.get("prompt_tokens") or 0),
                    completion_tokens=int(metrics.get("completion_tokens") or 0),
                    cache_hit_tokens=(None if metrics.get("cache_hit_tokens") is None else int(metrics.get("cache_hit_tokens") or 0)),
                    cache_miss_tokens=(None if metrics.get("cache_miss_tokens") is None else int(metrics.get("cache_miss_tokens") or 0)),
                ), float(metrics.get("elapsed_s") or 0.0))
            product_context = plan.context_text(critics, max_chars=self.config.skills.context_chars)
            self.product_plan_runs += 1
            self.last_product_plan = plan.as_dict()
            self.last_product_critics = {wr.task_id: wr.status for wr in critics}
            await self.events.emit("product_plan.finished", product_type=plan.product_type, critics=len(critics), generated_by_model=plan.generated_by_model)

        improvement_context = ""
        improvement_plan = None
        if (not coordinator_mode) and self.config.improvement.enabled and self.improver.should_plan(user_text, self.config.improvement.auto_plan):
            await self.events.emit("improvement_plan.started")
            amap = self.application_graph.build()
            self.last_application_map = amap.as_dict()
            started = time.monotonic()
            improvement_plan = await self.improver.plan(user_text, amap, amap.context_text())
            if improvement_plan.usage.prompt_tokens or improvement_plan.usage.completion_tokens:
                self.ledger.add(improvement_plan.usage, time.monotonic() - started)
            self.improvement_plan_runs += 1
            self.last_improvement_plan = improvement_plan.as_dict()
            improvement_context = improvement_plan.context_text(max_chars=self.config.skills.context_chars)
            await self.events.emit(
                "improvement_plan.finished", surfaces=len(improvement_plan.surfaces),
                batches=len(improvement_plan.batches), generated_by_model=improvement_plan.generated_by_model,
            )

        project_context = self.projects.context_text() if self.projects is not None else ""
        configuration_context = self.configuration.context_text() if self.configuration is not None else ""
        human_context = self.human_tasks.context_text() if self.config.autonomy.enabled else ""
        agent_context = self.agents.context_text(user_text) if self.agents is not None else ""
        procedural_context = "\n\n".join(x for x in (agent_context, project_context, configuration_context, human_context, skill_context, product_context, improvement_context) if x)
        self.messages.append({"role": "user", "content": self.context.turn_context(user_text, procedural_context), "_term5_turn": turn_id, "_term5_kind": "context"})

        # Executive preflight: complex HIGH/MAX turns may be decomposed
        # into a small no-tools DAG. The primary model remains authoritative.
        if (not coordinator_mode) and self.executive.should_plan(user_text, decision, self.config.executive.auto_plan):
            active_project = self.projects.active() if self.projects is not None else None
            workspace_context = self.graph.query_context(user_text, prefix=(active_project.path if active_project is not None else ""))
            if procedural_context:
                workspace_context = (procedural_context + "\n\n" + workspace_context)[:48000]
            await self.events.emit("executive.started", parallel_hint=decision.parallel_hint)
            plan_started = time.monotonic()
            plan = await self.executive.plan(user_text, workspace_context, decision.reasoning)
            if plan.usage.prompt_tokens or plan.usage.completion_tokens:
                self.ledger.add(plan.usage, time.monotonic() - plan_started)
            try:
                worker_results = await self.executive.execute(plan, workspace_context)
                for wr in worker_results.values():
                    metrics = wr.metrics or {}
                    self.ledger.add(Usage(
                        prompt_tokens=int(metrics.get("prompt_tokens") or 0),
                        completion_tokens=int(metrics.get("completion_tokens") or 0),
                        cache_hit_tokens=(None if metrics.get("cache_hit_tokens") is None else int(metrics.get("cache_hit_tokens") or 0)),
                        cache_miss_tokens=(None if metrics.get("cache_miss_tokens") is None else int(metrics.get("cache_miss_tokens") or 0)),
                    ), float(metrics.get("elapsed_s") or 0.0))
                evidence = self.executive.evidence_block(plan, worker_results)[:self.config.executive.inject_evidence_chars]
                self.messages.append({"role": "user", "content": evidence, "_term5_turn": turn_id, "_term5_kind": "evidence"})
                self.executive_runs += 1
                self.last_executive_plan = {
                    "rationale": plan.rationale,
                    "generated_by_model": plan.generated_by_model,
                    "tasks": plan.tasks,
                    "results": {k: v.status for k, v in worker_results.items()},
                }
                await self.events.emit("executive.finished", tasks=len(plan.tasks), generated_by_model=plan.generated_by_model)
            except Exception as exc:
                await self.events.emit("executive.failed", error=f"{type(exc).__name__}: {exc}")

        self.messages.append({"role": "user", "content": user_text, "_term5_turn": turn_id, "_term5_kind": "user"})
        central_names = self._central_tool_names() if coordinator_mode else None
        tools = self.tools.schemas(central_names) if use_tools else None
        if coordinator_mode:
            await self.events.emit("coordinator.mode", objective_id=objective_id, tools=len(tools or []), projects=len(self.agents.store.list_agents()))
        final = ""
        improvement_completion_reviewed = False
        iteration = 0
        last_batch_signature = ""
        repeated_batch_count = 0
        turn_started_monotonic = time.monotonic()
        while True:
            current_iteration = iteration
            if self.config.scheduler.max_turn_seconds and (time.monotonic() - turn_started_monotonic) >= self.config.scheduler.max_turn_seconds:
                final = final or "Turn safety timeout reached; current state was preserved."
                await self.events.emit("turn.safety_stop", reason="max_turn_seconds", iteration=current_iteration)
                break
            request = self.context.fit(self.messages)
            await self.events.emit("model.started", iteration=current_iteration, reasoning=decision.reasoning.value)
            started = time.monotonic()
            try:
                max_out = self.working_memory.output_budget(request, tools, decision.reasoning)
                await self.events.emit("context.budget", **self.working_memory.stats(self.messages))
                result = await self.provider.chat(request, tools=tools, reasoning=decision.reasoning, max_tokens=max_out)
            except Exception as exc:
                text = str(exc).lower()
                if "tool_calls" in text and ("tool_call_id" in text or "tool messages" in text or "insufficient tool" in text):
                    repaired = self._repair_protocol_history()
                    await self.events.emit("protocol.repaired", repaired_messages=repaired, source="provider_400_retry")
                    request = self.context.fit(self.messages)
                    max_out = self.working_memory.output_budget(request, tools, decision.reasoning)
                    await self.events.emit("context.budget", **self.working_memory.stats(self.messages))
                    result = await self.provider.chat(request, tools=tools, reasoning=decision.reasoning, max_tokens=max_out)
                else:
                    raise
            elapsed = time.monotonic() - started
            self.ledger.add(result.usage, elapsed)
            self.messages.append(self._assistant_message(result, turn_id=turn_id))
            await self.events.emit(
                "model.finished", iteration=current_iteration, tool_calls=len(result.tool_calls),
                content_chars=len(result.content), reasoning_chars=len(result.reasoning_content),
                elapsed_s=round(elapsed, 4),
            )
            if result.content:
                final = result.content
            await self.events.emit(
                "agent.next_action", iteration=current_iteration,
                tools=[c.name for c in result.tool_calls],
                summary=("final response" if not result.tool_calls else " → ".join(c.name for c in result.tool_calls[:8])),
            )
            iteration += 1
            if not result.tool_calls:
                if improvement_plan is not None and not improvement_completion_reviewed and use_tools:
                    improvement_completion_reviewed = True
                    changed = sorted(set(turn_changed))
                    used = sorted(set(turn_tools))
                    visual_requested = any(k in user_text.lower() for k in ("ui", "ux", "visual", "design", "polish", "redesign", "page", "pretty", "professional"))
                    browser_evidence = any(k in used for k in ("browser_audit_pages", "browser_dom", "browser_screenshot", "vision_inspect", "vision_compare"))
                    review = [
                        "[term_6 existing-application completion gate — local runtime evidence]",
                        f"Discovered improvement surfaces: {len(improvement_plan.surfaces)}",
                        f"Files changed this turn: {len(changed)}" + ((" — " + ", ".join(changed[:30])) if changed else ""),
                        "Tools used: " + (", ".join(used) if used else "none"),
                        f"Rendered/visual evidence gathered: {'yes' if browser_evidence else 'no'}",
                        "Re-read the improvement plan and decide whether the original request is materially satisfied. Do not defend a tiny diff merely because tests pass.",
                    ]
                    if visual_requested and self.config.browser.enabled and not browser_evidence:
                        review.append(
                            "This was a visual/UI request and browser inspection is configured, but no rendered evidence was gathered. "
                            "Use browser_status/browser_audit_pages (and vision when available) now, unless the browser tool reports that Chromium is unavailable."
                        )
                    if len(improvement_plan.surfaces) >= self.config.improvement.visible_impact_min_surfaces:
                        review.append(
                            "For a broad request, account for the discovered surfaces and prefer a coherent shared-component/multi-file improvement. "
                            "If coverage or visible impact is weak, continue editing and verifying instead of finalizing."
                        )
                    self.messages.append({"role":"user", "content":"\n".join(review), "_term5_turn":turn_id, "_term5_kind":"improvement_gate"})
                    continue
                break
            if coordinator_mode and current_iteration >= int(self.config.agents.central_max_iterations):
                for call in result.tool_calls:
                    content = "Tool NOT executed: central coordinator iteration budget reached. Project implementation belongs to delegated Project Owners; summarize current delegation state instead."
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content, "_term5_turn": turn_id, "_term5_kind": "tool"})
                    turn_tools.append(call.name)
                    turn_failures.append(f"{call.name}: coordinator iteration budget")
                    await self.events.emit("tool.skipped", call_id=call.id, name=call.name, ok=False, reason="coordinator_iteration_budget", iteration=current_iteration)
                final = final or "Coordination handed off to Project Owners; monitor progress in Workspace / Objectives."
                await self.events.emit("coordinator.handoff", objective_id=objective_id, iteration=current_iteration)
                break

            if self.config.scheduler.max_tool_iterations and current_iteration >= self.config.scheduler.max_tool_iterations:
                # Keep provider history structurally valid even though these calls
                # are deliberately not executed. Older builds left the assistant
                # tool_calls dangling here and poisoned every later turn.
                for call in result.tool_calls:
                    content = "Tool NOT executed: term_6 tool-iteration limit reached. Inspect current state before retrying."
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content, "_term5_turn": turn_id, "_term5_kind": "tool"})
                    turn_tools.append(call.name)
                    turn_failures.append(f"{call.name}: iteration limit")
                    await self.events.emit("tool.skipped", call_id=call.id, name=call.name, ok=False, reason="iteration_limit", iteration=current_iteration)
                final = final or "Tool iteration limit reached; pending tool calls were not executed."
                break

            # Safety is loop-based rather than a fixed iteration ceiling.
            batch_signature = json.dumps(
                [[c.name, c.arguments] for c in result.tool_calls],
                ensure_ascii=False, sort_keys=True, default=str,
            )[:16000]
            if batch_signature == last_batch_signature:
                repeated_batch_count += 1
            else:
                repeated_batch_count = 1
                last_batch_signature = batch_signature
            if repeated_batch_count == 3:
                await self.events.emit("loop.warning", repeats=repeated_batch_count, tools=[c.name for c in result.tool_calls])
            if repeated_batch_count >= self.config.scheduler.loop_abort_repeats:
                for call in result.tool_calls:
                    content = f"Tool NOT executed: repeated identical tool batch detected {repeated_batch_count} times. Inspect current state and choose a different action."
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content, "_term5_turn": turn_id, "_term5_kind": "tool"})
                    turn_tools.append(call.name)
                    turn_failures.append(f"{call.name}: repeated loop safety stop")
                    await self.events.emit("tool.skipped", call_id=call.id, name=call.name, ok=False, reason="repeated_tool_loop", iteration=current_iteration)
                await self.events.emit("loop.aborted", repeats=repeated_batch_count, tools=[c.name for c in result.tool_calls])
                final = final or "Repeated identical tool loop stopped; current state was preserved."
                break

            batch: list[tuple[str, dict[str, Any]]] = []
            invalid_calls: dict[int, str] = {}
            for idx, call in enumerate(result.tool_calls):
                if "_invalid" in call.arguments:
                    invalid_calls[idx] = "Tool arguments were not a JSON object. Retry with valid arguments."
                    batch.append((call.name, {}))
                else:
                    batch.append((call.name, call.arguments))
            await self.events.emit("tools.started", count=len(batch), names=[n for n, _ in batch], iteration=current_iteration)
            tool_started_at: dict[int, float] = {}
            for idx, call in enumerate(result.tool_calls):
                tool_started_at[idx] = time.monotonic()
                await self.events.emit(
                    "tool.started", call_id=call.id, name=call.name,
                    arguments=call.arguments, iteration=current_iteration,
                )
            if coordinator_mode:
                fenced=[]
                allowed=central_names or set()
                for name,args in batch:
                    if name not in allowed:
                        fenced.append(ToolResult(False, f"Central coordinator cannot execute {name}; delegate project-owned work to a Project Owner."))
                    else:
                        fenced.append(None)
                if any(x is not None for x in fenced):
                    tool_results=[]
                    for (name,args),blocked in zip(batch,fenced):
                        tool_results.append(blocked if blocked is not None else await self.tools.execute(name,args))
                else:
                    tool_results = await self.tools.execute_many(batch)
            else:
                tool_results = await self.tools.execute_many(batch)
            for idx, (call, tool_result) in enumerate(zip(result.tool_calls, tool_results)):
                if idx in invalid_calls:
                    tool_result = ToolResult(False, invalid_calls[idx])
                # Every committed file mutation gets an immediate local post-write
                # verification observation before the model continues.
                if tool_result.ok and tool_result.changed_files:
                    paths = [self.guard.resolve(p, allow_root=False) for p in tool_result.changed_files]
                    report = self.verifier.files(paths)
                    self.last_verification = report.checks
                    tool_result.verification.extend(x for x in report.checks if x not in tool_result.verification)
                    tool_result.content += "\n[post-write verification]\n" + report.text()
                    if not report.ok:
                        tool_result.ok = False
                if not tool_result.ok and "stale write rejected" in tool_result.content.lower():
                    self.write_conflicts += 1
                full_content = self.redactor.redact(tool_result.content)
                content = self.working_memory.context_tool_result(call.name, full_content)
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content,
                                      "_term5_turn": turn_id, "_term5_kind": "tool"})
                turn_tools.append(call.name)
                turn_changed.extend(tool_result.changed_files)
                if not tool_result.ok:
                    turn_failures.append(f"{call.name}: {full_content[:500]}")
                await self._audit("tool", name=call.name, ok=tool_result.ok,
                                  changed_files=tool_result.changed_files)
                await self.events.emit(
                    "tool.finished", call_id=call.id, name=call.name, ok=tool_result.ok,
                    changed_files=tool_result.changed_files, iteration=current_iteration,
                    elapsed_s=round(time.monotonic() - tool_started_at.get(idx, time.monotonic()), 4),
                    result_preview=content[:1600],
                )
            await self.events.emit("tools.finished", count=len(batch), iteration=current_iteration)
        self.turns += 1
        before_tokens = self.working_memory.estimate_tokens(self.messages)
        self.messages = self.working_memory.finish_turn(
            self.messages, turn_id, prompt=user_text, outcome=final, reasoning=decision.reasoning,
            tools=turn_tools, changed_files=turn_changed, failures=turn_failures,
        )
        after_tokens = self.working_memory.estimate_tokens(self.messages)
        await self.events.emit("context.compacted", before_tokens=before_tokens, after_tokens=after_tokens,
                               saved_tokens=max(0, before_tokens-after_tokens),
                               episodes=self.episodes.count(), artifacts=self.artifacts.count())
        if self.config.session.autosave and self.config.session.autosave_every_turn:
            self.session_store.save(self.messages, self.session_name, metadata={"turns": self.turns})
        self._journal(user_text, final, decision.reasoning)
        if durable_run_id:
            pending = self.human_tasks.open_for_run(durable_run_id)
            if any(bool(x.get("blocking")) for x in pending):
                self.durable_runs.update(durable_run_id, status="waiting_human", checkpoint="waiting_human", outcome=final)
            elif pending:
                self.durable_runs.update(durable_run_id, status="completed_with_pending", checkpoint="complete", outcome=final)
            else:
                self.durable_runs.update(durable_run_id, status="completed", checkpoint="complete", outcome=final)
        await self._audit("turn_finished", response_chars=len(final), run_id=durable_run_id)
        await self.events.emit("turn.finished", content=final, run_id=durable_run_id, objective_id=objective_id)
        if objective_id and self.agents is not None:
            try:
                obj = self.agents.finish_central_objective(objective_id, final)
                await self.events.emit("objective.updated", objective_id=objective_id, status=(obj or {}).get("status",""), tasks=len((obj or {}).get("tasks") or []))
            except Exception as exc:
                await self.events.emit("objective.update_failed", objective_id=objective_id, error=self.redactor.redact(f"{type(exc).__name__}: {exc}"))
        self.checkpoints.clear()
        self.product.active = False
        self._active_run_id = ""
        self.human_tasks.set_active_run("")
        return final

    async def resume_human_task(self, task_id: str) -> str:
        task = self.human_tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown human task: {task_id}")
        if task.get("status") not in {"resolved", "auto_resolved"}:
            raise RuntimeError("human task is not resolved")
        if task_id in self._resuming_tasks:
            return "Resume already scheduled."
        run_id = str(task.get("run_id") or "")
        run = self.durable_runs.get(run_id) if run_id else None
        response = task.get("response") or {}
        selected = str(response.get("option_id") or "")
        selected_info = ""
        if selected:
            option = next((x for x in task.get("options") or [] if str(x.get("id")) == selected), None)
            if option:
                selected_info = f"Selected option: {option.get('label')} ({selected})"
                if option.get("artifact_id"):
                    selected_info += f"\nSelected artifact: {option.get('artifact_id')}"
        answer = str(response.get("answer") or "")
        if answer:
            selected_info += ("\n" if selected_info else "") + "Human response: " + answer[:6000]
        objective = str((run or {}).get("objective") or "")
        prompt = (
            "[term_6 durable human-task continuation]\n"
            f"Task {task_id} is resolved. {selected_info}\n"
            f"Original objective: {objective}\n"
            f"Resume instruction: {task.get('resume_instruction') or 'Continue the blocked work using the human decision/input.'}\n"
            "Do not re-ask the resolved question. Re-inspect concrete state where needed, continue autonomously, and create another human task only if a genuinely new human decision is material."
        )
        self._resuming_tasks.add(task_id)
        try:
            if run_id:
                self.durable_runs.update(run_id, status="running", checkpoint="resuming")
            result = await self.run_turn(prompt, reasoning=ReasoningMode.AUTO, use_tools=True, _run_id=run_id)
            self.human_tasks.mark_resumed(task_id)
            await self.events.emit("human_task.resumed", task_id=task_id, run_id=run_id)
            return result
        finally:
            self._resuming_tasks.discard(task_id)

    async def autonomy_loop(self) -> None:
        interval = max(1, int(self.config.autonomy.scheduler_interval_s or 1))
        while True:
            await asyncio.sleep(interval)
            changed = self.human_tasks.tick()
            for task in changed:
                await self.events.emit("human_task.timeout", task_id=task.get("id"), status=task.get("status"), timeout_policy=task.get("timeout_policy"), response=task.get("response"))
            if not self.config.autonomy.auto_resume or self._turn_lock.locked():
                continue
            resumable = self.human_tasks.resumable()
            if resumable:
                task = resumable[0]
                try:
                    await self.resume_human_task(str(task.get("id")))
                except Exception as exc:
                    await self.events.emit("human_task.resume_failed", task_id=task.get("id"), error=self.redactor.redact(f"{type(exc).__name__}: {exc}"))

    def _journal(self, prompt: str, answer: str, reasoning: ReasoningMode) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt,
            "answer": answer,
            "reasoning": reasoning.value,
        }
        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    async def fim_edit(self, path: str, start_line: int, end_line: int, instruction: str) -> ToolResult:
        result = await self.fim.edit(path, start_line, end_line, instruction)
        self._record_fim_usage(result)
        if not result.ok and "stale write rejected" in result.content.lower():
            self.write_conflicts += 1
        return result

    async def fim_preview(self, path: str, start_line: int, end_line: int, instruction: str) -> ToolResult:
        result = await self.fim.preview(path, start_line, end_line, instruction)
        self._record_fim_usage(result)
        return result

    def clear_session(self) -> None:
        self.messages = [{"role": "system", "content": self.context.system_prompt}]
        self.checkpoints.clear()
        if self.config.session.autosave:
            self.session_store.save(self.messages, self.session_name, metadata={"turns": self.turns})

    def save_session(self, name: str | None = None) -> str:
        target = self.session_store.save(
            self.messages, name or self.session_name, metadata={"turns": self.turns}
        )
        if name:
            self.session_name = name
        return str(target)

    def load_session(self, name: str | None = None) -> bool:
        loaded = self.session_store.load(name)
        if loaded is None:
            return False
        self.messages = loaded
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": self.context.system_prompt})
        else:
            self.messages[0] = {"role": "system", "content": self.context.system_prompt}
        repaired, count = self.context.repair_tool_protocol(self.messages)
        self.messages = repaired
        self.protocol_repairs += count
        self.session_name = name
        return True

    def set_reasoning(self, mode: ReasoningMode) -> None:
        self.forced_reasoning = mode

    def status_lite(self) -> dict[str, Any]:
        """Fast UI heartbeat: avoid Docker/operations/config probes on every poll."""
        agents = self.agents.snapshot() if self.agents is not None else {"enabled": False}
        return {
            "version": __version__,
            "root": str(self.config.root),
            "model": self.config.model.model,
            "reasoning_policy": self.forced_reasoning.value,
            "last_reasoning": self.last_reasoning.value,
            "turns": self.turns,
            "uptime_s": int(time.time() - self.started),
            "turn_busy": self._turn_lock.locked(),
            "coordinator_mode": self._coordinator_mode(),
            "projects_enabled": bool(self.projects is not None),
            "projects": len(self.projects.registry.list()) if self.projects is not None else 0,
            "active_project": (self.projects.registry.active_name if self.projects is not None else ""),
            "registered_apps": len(self.apps.registry.list()) if self.config.apps.enabled else 0,
            "deployments": len(self.operations.registry.list()) if self.config.operations.enabled else 0,
            "autonomy": {"enabled": bool(self.config.autonomy.enabled), **self.human_tasks.stats(), "durable_runs": len(self.durable_runs.list(limit=500))},
            "agents": agents,
            "working_memory": self.working_memory.stats(self.messages),
        }

    def status(self) -> dict[str, Any]:
        stats = self.graph.stats()
        stale = sum(1 for r in self.memory.records if r.stale)
        return {
            "version": __version__,
            "root": str(self.config.root),
            "model": self.config.model.model,
            "reasoning_policy": self.forced_reasoning.value,
            "last_reasoning": self.last_reasoning.value,
            "turns": self.turns,
            "metrics": self.ledger.snapshot(),
            "tools": len(self.tools.names()),
            "memories": len(self.memory.records),
            "stale_memories": stale,
            "workspace_files": stats["files"],
            "workspace_symbols": stats["symbols"],
            "uptime_s": int(time.time() - self.started),
            "turn_busy": self._turn_lock.locked(),
            "activity": self.activity_snapshot(after=self.events.last_seq, limit=1)["activity"],
            "protocol_repairs": self.protocol_repairs,
            "parallel_total": self.config.reasoning.max_parallel_total,
            "parallel_high": self.config.reasoning.max_parallel_high,
            "fim": self.config.fim.enabled,
            "session": self.session_name or "default",
            "web_enabled": self.config.ui.web_enabled,
            "executive_auto_plan": self.config.executive.auto_plan,
            "executive_runs": self.executive_runs,
            "last_executive_plan": self.last_executive_plan,
            "last_verification": self.last_verification,
            "workspace_internal_edges": stats.get("internal_edges", 0),
            "workspace_test_edges": stats.get("test_edges", 0),
            "workspace_python_files": stats.get("python_files", 0),
            "workspace_js_ts_files": stats.get("js_ts_files", 0),
            "worker_timeout_s": self.config.reasoning.worker_timeout_s,
            "worker_retries": self.config.reasoning.worker_retries,
            "checkpoint": self.checkpoints.describe(),
            "checkpoint_writes": self.checkpoint_writes,
            "recovered_checkpoint": self.recovered_checkpoint,
            "recovery_prompt_chars": len(self.recovery_prompt),
            "recovered_transactions": list(self.recovered_transactions),
            "write_conflicts": self.write_conflicts,
            "transaction_history": self.transactions.history(limit=8),
            "config_warnings": list(self.config.diagnostics),
            "procedural_skills_enabled": self.config.skills.enabled,
            "skill_count": len(self.skills.names()),
            "last_skills": list(self.last_skill_resolution),
            "product_plan_runs": self.product_plan_runs,
            "last_product_plan": self.last_product_plan,
            "last_product_critics": self.last_product_critics,
            "last_product_audit": self.product.current_audit(),
            "improvement_enabled": self.config.improvement.enabled,
            "improvement_plan_runs": self.improvement_plan_runs,
            "last_improvement_plan": self.last_improvement_plan,
            "application_map": (self.last_application_map or {}).get("summary") if isinstance(self.last_application_map, dict) else None,
            "browser": {"enabled": self.config.browser.enabled, "headless": self.config.browser.headless},
            "vision": {"enabled": self.config.vision.enabled, "model": self.config.vision.model or self.config.model.model},
            "app_runtime_enabled": self.config.apps.enabled,
            "registered_apps": len(self.apps.registry.list()),
            "docker": self.apps.docker_status() if self.config.apps.enabled else {"available": False, "reason": "app runtime disabled"},
            "operations_enabled": self.config.operations.enabled,
            "deployments": len(self.operations.registry.list()) if self.config.operations.enabled else 0,
            "operations": self.operations.status() if self.config.operations.enabled else {"enabled": False},
            "projects_enabled": bool(self.projects is not None),
            "projects": len(self.projects.registry.list()) if self.projects is not None else 0,
            "active_project": (self.projects.registry.active_name if self.projects is not None else ""),
            "configuration": (self.configuration.status(discover=False) if self.configuration is not None and self.projects is not None and self.projects.registry.active_name else {"enabled": bool(self.configuration is not None)}),
            "autonomy": {"enabled": bool(self.config.autonomy.enabled), **self.human_tasks.stats(), "durable_runs": len(self.durable_runs.list(limit=500))},
            "agents": (self.agents.snapshot() if self.agents is not None else {"enabled": False}),
            "creative": (self.creative.status() if self.creative is not None else {"enabled": False}),
            "production": ({
                "enabled": True,
                "environments": len(self.production.registry.environments),
                "open_incidents": len([x for x in self.production.registry.incidents.values() if x.status != "resolved"]),
                "background_monitor": bool(self.config.production.background_monitor),
                "monitor_interval_s": int(self.config.production.monitor_interval_s),
            } if self.production is not None else {"enabled": False}),
            "working_memory": {**self.working_memory.stats(self.messages), "legacy_tokens_saved": self.legacy_context_tokens_saved},
        }
