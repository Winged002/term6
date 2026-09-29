from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .brain.router import AttentionRouter
from .brain.scheduler import DAGScheduler
from .config import Term5Config, validate_config
from .context import ContextCompiler
from .models import ReasoningMode, TaskNode, WorkerResult
from .runtime import AgentRuntime
from .security.network import NetworkGuard


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    critical: bool = False


@dataclass(slots=True)
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def critical_failures(self) -> int:
        return sum(1 for c in self.checks if c.critical and c.status == "FAIL")

    @property
    def failures(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")

    def add(self, name: str, status: str, detail: str, critical: bool = False) -> None:
        self.checks.append(Check(name, status, detail, critical))

    def as_dict(self) -> dict:
        return {
            "version": __version__,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail, "critical": c.critical}
                for c in self.checks
            ],
            "summary": {
                "checks": len(self.checks),
                "failures": self.failures,
                "critical_failures": self.critical_failures,
                "non_failing": len(self.checks) - self.failures,
            },
        }

    def text(self) -> str:
        lines = [f"term_6 {__version__} doctor", ""]
        for c in self.checks:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNKNOWN"}.get(c.status, c.status)
            crit = " [CRITICAL]" if c.critical else ""
            lines.append(f"{mark:7} {c.name}{crit} — {c.detail}")
        lines += ["", f"Summary: {len(self.checks)-self.failures}/{len(self.checks)} non-failing; "
                        f"failures={self.failures}; critical_failures={self.critical_failures}"]
        return "\n".join(lines)


async def run_doctor(runtime: AgentRuntime) -> DoctorReport:
    r = DoctorReport()
    cfg = runtime.config

    try:
        validate_config(cfg)
        r.add("configuration", "PASS", "validated")
        if cfg.diagnostics:
            r.add("config-schema", "WARN", "; ".join(cfg.diagnostics))
        else:
            r.add("config-schema", "PASS", "no unknown configuration keys/sections")
    except Exception as exc:
        r.add("configuration", "FAIL", str(exc), True)

    try:
        escaped = cfg.root.parent / "term5_escape_probe"
        try:
            runtime.guard.resolve(escaped)
            r.add("path-confinement", "FAIL", "outside path was accepted", True)
        except PermissionError:
            r.add("path-confinement", "PASS", "outside path rejected", True)
    except Exception as exc:
        r.add("path-confinement", "FAIL", str(exc), True)

    try:
        try:
            runtime.guard.resolve(".env")
            r.add("protected-paths", "FAIL", ".env was accepted", True)
        except PermissionError:
            r.add("protected-paths", "PASS", "protected path rejected", True)
    except Exception as exc:
        r.add("protected-paths", "FAIL", str(exc), True)

    try:
        guard = NetworkGuard()
        try:
            guard.validate_url("http://127.0.0.1/")
            r.add("network-guard", "FAIL", "loopback URL accepted", True)
        except PermissionError:
            r.add("network-guard", "PASS", "loopback/metadata protection active", True)
    except Exception as exc:
        r.add("network-guard", "FAIL", str(exc), True)

    try:
        before = runtime.context.system_prompt
        after = runtime.context.system_prompt
        if before == after:
            r.add("prompt-stability", "PASS", "stable system prefix is byte-identical")
        else:
            r.add("prompt-stability", "FAIL", "system prefix changed", True)
    except Exception as exc:
        r.add("prompt-stability", "FAIL", str(exc), True)

    if runtime.memory.load_error:
        r.add("memory-store", "FAIL", runtime.memory.load_error, True)
    else:
        r.add("memory-store", "PASS", f"readable; {len(runtime.memory.records)} record(s)")

    try:
        stats = runtime.graph.refresh()
        r.add("workspace-index", "PASS", f"{stats['files']} files / {stats['symbols']} symbols; py={stats.get('python_files',0)} js/ts={stats.get('js_ts_files',0)}")
    except Exception as exc:
        r.add("workspace-index", "FAIL", str(exc))

    try:
        probe = "term5_doctor_probe.txt"
        snap = runtime.transactions.begin(probe)
        runtime.transactions.atomic_write(snap, "term5 doctor probe\n")
        runtime.transactions.rollback(snap)
        p = cfg.root / probe
        if snap.existed or not p.exists():
            r.add("transaction-rollback", "PASS", "snapshot/write/rollback succeeded", True)
        else:
            r.add("transaction-rollback", "FAIL", "probe remained after rollback", True)
    except Exception as exc:
        r.add("transaction-rollback", "FAIL", str(exc), True)

    try:
        names = runtime.tools.names()
        required = {"read_file", "write_file", "write_files", "undo_last", "redo_last", "transaction_history", "parallel_reason", "parallel_dag", "fim_preview", "fim_edit", "parallel_fim_preview", "parallel_fim_edit", "file_relations", "impact_map", "verify_changes", "run_tests", "sqlite_query", "memory_recall", "git_status", "skill_list", "skill_show", "skill_resolve", "skill_reload", "product_plan_current", "product_audit", "product_audit_current", "application_map", "improvement_plan", "improvement_plan_current"}
        if cfg.vision.enabled:
            required |= {"vision_inspect", "vision_compare"}
        if cfg.browser.enabled:
            required |= {"browser_status", "browser_open", "browser_set_viewport", "browser_dom", "browser_screenshot", "browser_wait_for", "browser_diagnostics", "browser_audit_pages", "browser_close"}
            if cfg.browser.allow_interaction:
                required |= {"browser_click", "browser_fill"}
        if cfg.apps.enabled:
            required |= {"docker_status", "app_list", "app_register", "app_scaffold_flask", "app_create_flask", "app_validate", "app_build", "app_start", "app_stop", "app_restart", "app_status", "app_logs", "app_open"}
        if cfg.operations.enabled and cfg.apps.enabled:
            required |= {"ops_status", "server_status", "server_ports", "server_service_status", "server_journal", "server_service_control", "server_audit", "production_readiness", "git_head", "git_diff_staged", "git_init", "git_stage", "git_commit", "git_branch_create", "git_switch", "git_remotes", "git_remote_add", "git_remote_set_url", "git_fetch", "git_pull_ff", "git_push", "git_tag", "git_restore", "git_revert", "nginx_status", "nginx_service_status", "nginx_service_control", "nginx_site_plan", "nginx_site_install", "nginx_site_remove", "nginx_logs", "tls_status", "tls_issue", "tls_renew_dry_run", "deployment_register", "deployment_list", "deployment_status", "deployment_backup", "deployment_dns", "deployment_deploy", "deployment_rollback"}
        if cfg.projects.enabled:
            required |= {"project_list", "project_status", "project_switch", "project_import", "project_create", "project_clone", "project_update", "project_unregister", "project_goal_add", "project_decision_add", "project_backlog_add", "project_backlog_update", "github_status", "github_repo_list", "github_repo_view", "github_repo_create"}
        if cfg.configuration.enabled and cfg.projects.enabled:
            required |= {"configuration_status", "configuration_discover", "configuration_require", "configuration_set", "configuration_test"}
        if cfg.production.enabled and cfg.operations.enabled:
            required |= {"production_overview", "environment_list", "environment_register", "production_snapshot", "production_metrics", "production_logs", "incident_list", "incident_detect", "incident_update", "release_verify", "release_compare"}
        if cfg.autonomy.enabled:
            required |= {"human_task_create", "human_task_list", "durable_run_list", "durable_run_status"}
        if cfg.creative.enabled:
            required |= {"creative_status", "creative_svg_concepts", "creative_image_concepts"}
        if cfg.agents.enabled and cfg.projects.enabled:
            required |= {"agent_list", "agent_status", "agent_delegate", "agent_query", "agent_task_list", "agent_wait", "agent_message_send", "agent_message_list", "agent_capsule_refresh"}
        missing = required - set(names)
        if missing:
            r.add("tool-registry", "FAIL", "missing: " + ", ".join(sorted(missing)), True)
        else:
            r.add("tool-registry", "PASS", f"{len(names)} registered tools")
    except Exception as exc:
        r.add("tool-registry", "FAIL", str(exc), True)

    if cfg.fim.enabled and cfg.fim.hard_max_output_tokens <= 4096:
        r.add("fim-policy", "PASS", f"enabled; hard cap {cfg.fim.hard_max_output_tokens}")
    else:
        r.add("fim-policy", "FAIL", "FIM disabled or hard cap exceeds 4096")

    if 0 <= cfg.fim.repair_attempts <= 2 and cfg.fim.max_parallel >= 1:
        r.add("fim-repair-parallel", "PASS", f"repair_attempts={cfg.fim.repair_attempts}; max_parallel={cfg.fim.max_parallel}")
    else:
        r.add("fim-repair-parallel", "FAIL", "invalid FIM repair/parallel policy", True)

    if 2 <= cfg.executive.max_workers <= min(16, cfg.reasoning.max_parallel_total):
        r.add("executive-policy", "PASS", f"auto_plan={cfg.executive.auto_plan}; max_workers={cfg.executive.max_workers}")
    else:
        r.add("executive-policy", "FAIL", "invalid executive worker budget", True)

    try:
        social = runtime.skills.resolve("create a social media app with Flask")
        needed = {"social-network", "authentication", "file-uploads", "web-ux", "security", "testing"}
        missing = needed - set(social.selected)
        if cfg.skills.enabled and not missing:
            r.add("procedural-skills", "PASS", f"{len(runtime.skills.names())} skills loaded; social archetype expands required capabilities")
        elif not cfg.skills.enabled:
            r.add("procedural-skills", "UNKNOWN", "disabled by configuration")
        else:
            r.add("procedural-skills", "FAIL", "social skill expansion missing: " + ", ".join(sorted(missing)), True)
    except Exception as exc:
        r.add("procedural-skills", "FAIL", str(exc), True)

    if 1000 <= cfg.skills.product_plan_tokens <= 12000 and cfg.skills.context_chars >= 4000:
        r.add("product-planning-policy", "PASS", f"auto={cfg.skills.auto_product_plan}; critique={cfg.skills.critique}; plan_tokens={cfg.skills.product_plan_tokens}")
    else:
        r.add("product-planning-policy", "FAIL", "invalid product planning budget", True)

    if 5 <= cfg.reasoning.worker_timeout_s <= 1800 and 0 <= cfg.reasoning.worker_retries <= 3:
        r.add("worker-reliability", "PASS", f"timeout={cfg.reasoning.worker_timeout_s}s; retries={cfg.reasoning.worker_retries}")
    else:
        r.add("worker-reliability", "FAIL", "invalid worker timeout/retry policy", True)

    try:
        cp = runtime.checkpoints.describe()
        r.add("turn-checkpoint", "PASS", "safe-state checkpoint store readable" + (f"; pending phase={cp['phase']}" if cp else "; no pending turn"))
    except Exception as exc:
        r.add("turn-checkpoint", "FAIL", str(exc), True)

    r.add("transaction-recovery", "PASS", f"startup recovered {len(runtime.recovered_transactions)} open transaction(s)")
    try:
        ok, issues = runtime.transactions.integrity()
        r.add("transaction-history", "PASS" if ok else "FAIL",
              "history metadata consistent" if ok else "; ".join(issues[:8]), not ok)
    except Exception as exc:
        r.add("transaction-history", "FAIL", str(exc), True)

    if hasattr(runtime, "_turn_lock"):
        r.add("turn-reentry-guard", "PASS", "single-runtime turns are serialized")
    else:
        r.add("turn-reentry-guard", "FAIL", "runtime turn lock missing", True)

    try:
        if cfg.autonomy.enabled:
            task_stats = runtime.human_tasks.stats()
            runs = runtime.durable_runs.list(limit=1)
            if isinstance(task_stats, dict) and isinstance(runs, list):
                r.add("durable-collaboration", "PASS", f"My Tasks ready; open={task_stats.get('open', 0)}; durable run store readable")
            else:
                r.add("durable-collaboration", "FAIL", "invalid collaboration stores", True)
        else:
            r.add("durable-collaboration", "UNKNOWN", "disabled by configuration")
    except Exception as exc:
        r.add("durable-collaboration", "FAIL", str(exc), True)

    try:
        if cfg.creative.enabled:
            creative = runtime.creative.status()
            if creative.get("enabled") and creative.get("model"):
                configured = "configured" if creative.get("openai_configured") else "not configured (SVG fallback available)"
                r.add("creative-studio", "PASS", f"model={creative.get('model')}; OpenAI={configured}")
            else:
                r.add("creative-studio", "FAIL", "creative status missing model/enabled state", True)
        else:
            r.add("creative-studio", "UNKNOWN", "disabled by configuration")
    except Exception as exc:
        r.add("creative-studio", "FAIL", str(exc), True)

    try:
        ctx = runtime.graph.query_context("runtime dependency tests")
        if ctx.startswith("Workspace evidence"):
            stats = runtime.graph.stats()
            r.add("workspace-relations", "PASS", f"internal_edges={stats.get('internal_edges', 0)}; test_edges={stats.get('test_edges', 0)}")
        else:
            r.add("workspace-relations", "FAIL", "query context missing")
    except Exception as exc:
        r.add("workspace-relations", "FAIL", str(exc))

    if runtime.verifier.root == cfg.root:
        r.add("verification-pipeline", "PASS", f"tests={'enabled' if runtime.verifier.allow_tests else 'disabled'}")
    else:
        r.add("verification-pipeline", "FAIL", "verification root mismatch", True)

    if cfg.model.max_output_tokens <= 384_000 and cfg.model.context_limit <= 1_000_000:
        r.add("model-bounds", "PASS", f"context={cfg.model.context_limit}, max_output={cfg.model.max_output_tokens}")
    else:
        r.add("model-bounds", "FAIL", "configured bounds exceed model profile")

    try:
        wm = runtime.working_memory
        probe = [{"role": "system", "content": "x" * 2_000_000}]
        dynamic = wm.output_budget(probe, [], ReasoningMode.HIGH)
        if dynamic <= cfg.context.output_high_tokens < cfg.model.max_output_tokens:
            r.add("dynamic-token-budget", "PASS", f"HIGH cap={dynamic}; physical={cfg.model.total_context_tokens}")
        else:
            r.add("dynamic-token-budget", "FAIL", f"unexpected output budget {dynamic}", True)
        if cfg.context.active_target_tokens < cfg.context.soft_compact_tokens < cfg.context.hard_input_tokens < cfg.model.total_context_tokens:
            r.add("working-memory-budget", "PASS", f"target={cfg.context.active_target_tokens}; soft={cfg.context.soft_compact_tokens}; hard={cfg.context.hard_input_tokens}")
        else:
            r.add("working-memory-budget", "FAIL", "working-memory thresholds are not strictly bounded", True)
        sample = "doctor artifact " * 4000
        compact = wm.context_tool_result("doctor_probe", sample)
        if "archived full tool output" in compact and len(compact) < len(sample):
            r.add("artifact-cold-store", "PASS", "verbose tool output is locally archived and model context receives a bounded preview")
        else:
            r.add("artifact-cold-store", "FAIL", "tool output archival did not compact", True)
    except Exception as exc:
        r.add("working-memory", "FAIL", str(exc), True)

    if cfg.api_key:
        try:
            # Importability only; doctor never spends tokens or makes a network call.
            from openai import AsyncOpenAI  # noqa: F401
            r.add("provider-client", "PASS", "API key present and OpenAI-compatible client importable")
        except Exception as exc:
            r.add("provider-client", "FAIL", f"API key present but client unavailable: {exc}")
    else:
        r.add("provider-client", "UNKNOWN", "DEEPSEEK_API_KEY not set; local checks still valid")

    try:
        if runtime.agents is None:
            r.add("project-owner-agents", "UNKNOWN", "project owner agents disabled by configuration")
        else:
            snap = runtime.agents.snapshot()
            db_ok = runtime.agents.store.path.exists()
            if db_ok and isinstance(snap.get("agents"), list):
                r.add("project-owner-agents", "PASS", f"SQLite/WAL orchestration ready; owners={snap.get('counts',{}).get('agents',0)}; max_concurrent={cfg.agents.max_concurrent_owners}; owner_context_target={cfg.agents.owner_context_target_tokens}")
            else:
                r.add("project-owner-agents", "FAIL", "orchestrator store/snapshot invalid", True)
    except Exception as exc:
        r.add("project-owner-agents", "FAIL", str(exc), True)

    try:
        if runtime.agents is None:
            r.add("agent-mesh-contracts", "UNKNOWN", "agent mesh disabled by configuration")
        else:
            with runtime.agents.store._connect() as db:
                tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required_tables = {"messages", "contracts", "contract_consumers", "contract_forecasts"}
            required_tools = {"agent_request_change", "agent_dependency_add", "contract_publish", "contract_forecast", "contract_query", "contract_impact"}
            missing_tables = sorted(required_tables - tables)
            missing_tools = sorted(name for name in required_tools if runtime.tools.get(name) is None)
            if not missing_tables and not missing_tools:
                r.add("agent-mesh-contracts", "PASS", "typed owner mesh + versioned contracts/forecasts ready")
            else:
                r.add("agent-mesh-contracts", "FAIL", f"missing_tables={missing_tables}; missing_tools={missing_tools}", True)
    except Exception as exc:
        r.add("agent-mesh-contracts", "FAIL", str(exc), True)

    try:
        if runtime.agents is None:
            r.add("organization-workspace", "UNKNOWN", "Project Owner workspace disabled")
        else:
            from .ui.web_assets import HTML as _WEB_HTML, CSS as _WEB_CSS, JS as _WEB_JS
            snap = runtime.agents.snapshot()
            with runtime.agents.store._connect() as db:
                tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                task_cols = {str(row[1]) for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            ui_ok = all(x in _WEB_HTML for x in ["Organization Workspace", "Agent Inbox", "Systems", "Objectives", "Dependencies"])
            js_ok = all(x in _WEB_JS for x in ["renderSpatialWorkspace", "objectiveCanvas", "dependencyCanvas", "/api/status-lite", "/api/agent-task-move"])
            css_ok = all(x in _WEB_CSS for x in [".spatial-canvas", ".central-node", ".project-node", ".global-command-dock"])
            conc = snap.get("concurrency") or {}
            recovery = snap.get("recovery") or {}
            schema_ok = {"orchestrator_settings", "objectives"} <= tables and "objective_id" in task_cols
            if schema_ok and ui_ok and js_ok and css_ok and conc.get("owners") and "total" in recovery:
                r.add("organization-workspace", "PASS", f"spatial workspace + objectives + attention/inbox + direct manipulation ready; owner_limit={conc['owners'].get('limit')}; query_limit={conc.get('queries',{}).get('limit')}")
            else:
                r.add("organization-workspace", "FAIL", "v6.2 workspace schema/UI/runtime checks incomplete", True)
    except Exception as exc:
        r.add("organization-workspace", "FAIL", str(exc), True)

    try:
        central = runtime._central_tool_names() if runtime.agents is not None else set()
        if runtime.agents is None:
            r.add("coordinator-routing", "UNKNOWN", "Project Owner coordinator disabled")
        elif cfg.agents.coordinator_only and "agent_delegate" in central and not ({"browser_open", "read_file", "write_file", "run_tests"} & central):
            r.add("coordinator-routing", "PASS", f"central coordinator-only tool fence ready; central_max_iterations={cfg.agents.central_max_iterations}; direct project/browser tools reserved for owners")
        else:
            r.add("coordinator-routing", "FAIL", "central coordinator tool fence is not active", True)
    except Exception as exc:
        r.add("coordinator-routing", "FAIL", str(exc), True)

    try:
        import inspect
        from .agents.owner import ProjectOwnerAgent
        owner_src = inspect.getsource(ProjectOwnerAgent.execute)
        if "owner_max_iterations" not in owner_src and "while True" in owner_src:
            r.add("owner-autonomy-unbounded", "PASS", "Project Owner execution has no hard iteration cap; completion/cancel/wait/failure remain terminal conditions")
        else:
            r.add("owner-autonomy-unbounded", "FAIL", "legacy owner iteration cap still appears in execution path", True)
    except Exception as exc:
        r.add("owner-autonomy-unbounded", "FAIL", str(exc), True)

    try:
        from .ui.web_assets import HTML as _UI_HTML, CSS as _UI_CSS, JS as _UI_JS
        modern_html = all(x in _UI_HTML for x in ['class="icon-dock"', 'data-tip="Workspace"', 'Search, jump to…', 'historyPopover'])
        modern_css = all(x in _UI_CSS for x in ['.compact-rail', '.icon-dock', '.project-symbol', '.history-popover'])
        modern_js = all(x in _UI_JS for x in ['projectLayout(agents)', 'projectIcon(name)', 'timelineOpen', 'historyButton'])
        if modern_html and modern_css and modern_js:
            r.add("ui-modern-shell", "PASS", "compact icon navigation + hover labels + collision-aware progressive workspace ready")
        else:
            r.add("ui-modern-shell", "FAIL", "v6.2-ui2 shell/layout checks incomplete", True)
    except Exception as exc:
        r.add("ui-modern-shell", "FAIL", str(exc), True)

    try:
        from .ui.web_assets import HTML as _UI2_HTML, CSS as _UI2_CSS, JS as _UI2_JS
        import inspect
        from .agents.manager import AgentOrchestrator
        manager_src = inspect.getsource(AgentOrchestrator)
        layout_ok = all(x in manager_src for x in ["workspace_layout", "set_workspace_layout", "reset_workspace_layout"])
        ui_ok = all(x in (_UI2_HTML + _UI2_JS) for x in ["data-drag-project-pos", "/api/workspace-layout", "resetWorkspaceLayout", "syncLiveMessages", "messageSignature"])
        css_ok = all(x in _UI2_CSS for x in [".project-node.dragging", ".page-view:not(.workspace-view)", ".mini-message"])
        if layout_ok and ui_ok and css_ok:
            r.add("ui-live-layout", "PASS", "draggable persistent project layout + audited page shell + live inter-agent response refresh ready")
        else:
            r.add("ui-live-layout", "FAIL", "v6.2-ui2 layout/live-response checks incomplete", True)
    except Exception as exc:
        r.add("ui-live-layout", "FAIL", str(exc), True)

    try:
        from .ui.web_assets import HTML as _UI3_HTML, CSS as _UI3_CSS, JS as _UI3_JS
        import inspect
        from .ui import web as _web_mod
        web_src = inspect.getsource(_web_mod)
        csp_ok = "style-src 'self'" in web_src and "'unsafe-inline'" not in web_src
        inline_ok = "style=" not in _UI3_HTML and "style=" not in _UI3_JS and ".style." not in _UI3_JS
        spatial_ok = all(x in _UI3_JS for x in ["foreignObject", "spatial-svg", "svgFO(x,y,w,h", "data-drag-project-pos"])
        inspector_ok = all(x in _UI3_JS for x in ["Central coordinator", "Current coordination", "Recent objectives", "Recent handoffs"])
        redesign_ok = all(x in (_UI3_HTML + _UI3_CSS + _UI3_JS) for x in ["inbox-modern-layout", "Priority feed", "ops-command-grid", "Evidence console", "place-items:center!important"])
        if csp_ok and inline_ok and spatial_ok and inspector_ok and redesign_ok:
            r.add("ui-csp-layout", "PASS", "CSP-safe SVG workspace + centered icon system + populated coordinator inspector + redesigned Inbox/Operations ready")
        else:
            r.add("ui-csp-layout", "FAIL", "v6.2-ui3 CSP/layout/redesign checks incomplete", True)
    except Exception as exc:
        r.add("ui-csp-layout", "FAIL", str(exc), True)

    try:
        from .ui.web_assets import CSS as _UI4_CSS, JS as _UI4_JS
        feedback_ok = all(x in _UI4_JS for x in [
            "function inspectorFeedback(sel)", "function ingestInspectorEvent(e)",
            "Live feedback", "responseFeedbackForSelection",
            "addEvent(e);ingestInspectorEvent(e);state.seq="
        ])
        feedback_css = all(x in _UI4_CSS for x in [".feedback-stream", ".feedback-row", ".live-indicator"])
        if feedback_ok and feedback_css:
            r.add("ui-inspector-feedback", "PASS", "selection-scoped live operational + message-response feedback streams into Inspector from the existing activity loop")
        else:
            r.add("ui-inspector-feedback", "FAIL", "v6.2-ui4 Inspector feedback checks incomplete", True)
    except Exception as exc:
        r.add("ui-inspector-feedback", "FAIL", str(exc), True)

    try:
        if runtime.projects is None:
            r.add("project-runtime", "UNKNOWN", "projects disabled by configuration")
        else:
            count = len(runtime.projects.registry.list())
            active = runtime.projects.registry.active_name or "none"
            r.add("project-runtime", "PASS", f"project registry initialized; projects={count}; active={active}; base_dir={cfg.projects.base_dir}")
    except Exception as exc:
        r.add("project-runtime", "FAIL", str(exc), True)

    try:
        if runtime.configuration is None:
            r.add("configuration-runtime", "UNKNOWN", "project configuration manager disabled")
        else:
            vault_mode = None
            if runtime.configuration.vault.path.exists():
                vault_mode = oct(runtime.configuration.vault.path.stat().st_mode & 0o777)
            detail = f"enabled; default_environment={cfg.configuration.default_environment}; vault_secrets={runtime.configuration.vault.count()}"
            if vault_mode:
                detail += f"; vault_mode={vault_mode}"
            r.add("configuration-runtime", "PASS", detail)
    except Exception as exc:
        r.add("configuration-runtime", "FAIL", str(exc), True)

    try:
        if runtime.production is None:
            r.add("production-intelligence", "UNKNOWN", "production intelligence disabled")
        else:
            r.add("production-intelligence", "PASS", f"environment_registry={len(runtime.production.registry.environments)}; incidents={len(runtime.production.registry.incidents)}; background_monitor={cfg.production.background_monitor}; interval={cfg.production.monitor_interval_s}s")
    except Exception as exc:
        r.add("production-intelligence", "FAIL", str(exc), True)

    try:
        if runtime.projects is None or not cfg.github.enabled:
            r.add("github-provider", "UNKNOWN", "GitHub project provider disabled")
        else:
            gh = runtime.projects.github.status()
            if gh.get("available") and gh.get("authenticated"):
                r.add("github-provider", "PASS", f"gh authenticated as {gh.get('login') or 'unknown'}")
            elif gh.get("available"):
                r.add("github-provider", "UNKNOWN", "gh installed but not authenticated")
            else:
                r.add("github-provider", "UNKNOWN", gh.get("reason") or "gh unavailable")
    except Exception as exc:
        r.add("github-provider", "UNKNOWN", f"GitHub provider probe unavailable: {exc}")

    try:
        if not cfg.apps.enabled:
            r.add("local-app-runtime", "UNKNOWN", "disabled by configuration")
        else:
            ds = runtime.apps.docker_status(refresh=True)
            if ds.get("available") and ds.get("compose_available"):
                r.add("local-app-runtime", "PASS", f"Docker Engine + Compose available; registered_apps={len(runtime.apps.registry.list())}")
            elif ds.get("available"):
                r.add("local-app-runtime", "UNKNOWN", "Docker Engine reachable but Compose is unavailable")
            else:
                r.add("local-app-runtime", "UNKNOWN", ds.get("reason") or "Docker Engine unavailable; coding features remain available")
    except Exception as exc:
        r.add("local-app-runtime", "UNKNOWN", f"Docker probe unavailable: {exc}")

    try:
        if not cfg.operations.enabled:
            r.add("operations-runtime", "UNKNOWN", "disabled by configuration")
        else:
            op = runtime.operations.status()
            gates = op.get("write_gates", {})
            r.add("operations-runtime", "PASS", f"typed server/Git/Nginx/TLS/deployment runtime initialized; deployments={op.get('deployments',0)}; git_write={gates.get('git')}; server_write={gates.get('server')}; nginx_write={gates.get('nginx')}; tls_write={gates.get('tls')}")
    except Exception as exc:
        r.add("operations-runtime", "FAIL", str(exc), True)

    try:
        if cfg.operations.enabled and cfg.operations.require_git and cfg.operations.require_clean_git:
            r.add("deployment-policy", "PASS", f"git_required; clean_git_required; auto_rollback={cfg.operations.auto_rollback}; nginx_max_body_mb={cfg.operations.nginx_max_body_mb}")
        elif cfg.operations.enabled:
            r.add("deployment-policy", "WARN", "deployments do not require a clean Git working tree")
        else:
            r.add("deployment-policy", "UNKNOWN", "operations disabled")
    except Exception as exc:
        r.add("deployment-policy", "FAIL", str(exc), True)

    try:
        test_name = "term5-doctor-app"
        # Registry format/roundtrip only; no Docker calls and no project mutation.
        from .apps.registry import AppManifest
        original = runtime.apps.registry.get(test_name)
        if original is None:
            runtime.apps.registry.add(AppManifest(name=test_name, path=".", compose_file="compose.yaml"))
            ok = runtime.apps.registry.get(test_name) is not None
            runtime.apps.registry.remove(test_name)
        else:
            ok = True
        r.add("app-registry", "PASS" if ok else "FAIL", "local app registry roundtrip works", not ok)
    except Exception as exc:
        r.add("app-registry", "FAIL", str(exc), True)

    try:
        amap = runtime.application_graph.build()
        r.add("application-graph", "PASS", f"framework={amap.framework}; routes={len(amap.routes)}; templates={len(amap.templates)}; css={len(amap.stylesheets)}; tests={len(amap.tests)}")
    except Exception as exc:
        r.add("application-graph", "FAIL", str(exc), True)

    try:
        if cfg.browser.enabled:
            status = await runtime.browser.status()
            available = bool(status.get("package_available"))
            system = str(status.get("system_chromium") or "")
            detail = str(status.get("package_detail") or "")
            if available and system:
                detail += f"; system Chromium={system}"
            elif available:
                detail += "; Playwright package present; install Chromium binary if launch fails"
            else:
                detail += "; install term5-local[browser] for rendered inspection"
            r.add("browser-path", "PASS" if available else "UNKNOWN", detail)
        else:
            r.add("browser-path", "UNKNOWN", "browser capability disabled by configuration")
    except Exception as exc:
        r.add("browser-path", "UNKNOWN", f"browser probe unavailable: {exc}")

    if cfg.vision.enabled:
        model = cfg.vision.model or cfg.model.model
        r.add("vision-path", "PASS" if cfg.api_key else "UNKNOWN",
              f"multimodal tool path registered; model={model}; live image support is verified on first vision call" + ("" if cfg.api_key else "; API key absent"))
    else:
        r.add("vision-path", "UNKNOWN", "vision disabled by configuration")

    if cfg.improvement.enabled and cfg.improvement.auto_plan:
        r.add("existing-app-improvement", "PASS", f"auto-plan enabled; max_surfaces={cfg.improvement.max_surfaces}; max_batch_files={cfg.improvement.max_batch_files}; before_after={cfg.improvement.prefer_before_after}")
    else:
        r.add("existing-app-improvement", "UNKNOWN", "existing-app improvement planner disabled")
    return r


async def run_selftest(runtime: AgentRuntime) -> DoctorReport:
    report = await run_doctor(runtime)

    router = AttentionRouter()
    if router.decide("show git status").reasoning == ReasoningMode.NONE and \
       router.decide("debug this intermittent security race and prove the root cause").reasoning in {ReasoningMode.HIGH, ReasoningMode.MAX}:
        report.add("reasoning-router", "PASS", "simple vs complex routing behaves as expected")
    else:
        report.add("reasoning-router", "FAIL", "routing invariant failed")

    try:
        product_decision = router.decide("make me a social media app")
        if product_decision.reasoning == ReasoningMode.HIGH and product_decision.parallel_hint >= 2:
            report.add("product-routing", "PASS", "vague product creation escalates to product planning reasoning")
        else:
            report.add("product-routing", "FAIL", f"unexpected routing: {product_decision.reasoning.value}/{product_decision.parallel_hint}")
    except Exception as exc:
        report.add("product-routing", "FAIL", str(exc))

    try:
        resolved = runtime.skills.resolve("build a social media app with flask")
        ctx = runtime.skills.resolution_context(resolved)
        if "file-uploads" in resolved.selected and "Avatar" in ctx or "avatar" in ctx.lower():
            report.add("skill-resolution", "PASS", "product skill expansion carries upload/product requirements")
        else:
            report.add("skill-resolution", "FAIL", "resolved context missing expected social/upload requirements")
    except Exception as exc:
        report.add("skill-resolution", "FAIL", str(exc))

    scheduler = DAGScheduler(max_parallel=4)
    nodes = [TaskNode("a", "a"), TaskNode("b", "b"), TaskNode("c", "c", dependencies={"a", "b"})]

    async def execute(node, results):
        await asyncio.sleep(0.005)
        return WorkerResult(node.id, "success", node.objective)

    try:
        results = await scheduler.run(nodes, execute)
        if set(results) == {"a", "b", "c"}:
            report.add("dag-scheduler", "PASS", "dependency scheduling completed")
        else:
            report.add("dag-scheduler", "FAIL", "missing results")
    except Exception as exc:
        report.add("dag-scheduler", "FAIL", str(exc))

    if runtime.context.turn_context("test").startswith("[term_6 turn context"):
        report.add("turn-context", "PASS", "volatile context is separated from stable system prefix")
    else:
        report.add("turn-context", "FAIL", "turn context header invariant failed")

    try:
        probe_a = "term5_group_a.txt"
        probe_b = "term5_group_b.txt"
        txid = runtime.transactions.write_group([(probe_a, "a\n"), (probe_b, "b\n")])
        (runtime.config.root / probe_a).unlink(missing_ok=True)
        (runtime.config.root / probe_b).unlink(missing_ok=True)
        report.add("group-transactions", "PASS", f"atomic multi-file transaction {txid} committed")
    except Exception as exc:
        report.add("group-transactions", "FAIL", str(exc), True)

    try:
        probe = "term5_stale_guard.txt"
        path = runtime.config.root / probe
        path.write_text("one", encoding="utf-8")
        expected = runtime.transactions.fingerprint(probe)
        path.write_text("external", encoding="utf-8")
        try:
            runtime.transactions.write_one(probe, "agent", expected_sha256=expected)
            report.add("stale-write-guard", "FAIL", "stale write was accepted", True)
        except Exception as exc:
            if "stale write rejected" in str(exc).lower() and path.read_text(encoding="utf-8") == "external":
                report.add("stale-write-guard", "PASS", "external edit preserved and stale commit rejected", True)
            else:
                report.add("stale-write-guard", "FAIL", str(exc), True)
        path.unlink(missing_ok=True)
    except Exception as exc:
        report.add("stale-write-guard", "FAIL", str(exc), True)

    try:
        before_messages = list(runtime.messages)
        runtime.checkpoints.save(phase="turn_start", messages=before_messages, prompt="doctor recovery", session_name="doctor")
        cp = runtime.checkpoints.load()
        runtime.checkpoints.clear()
        if cp is not None and cp.prompt == "doctor recovery" and cp.messages == before_messages:
            report.add("checkpoint-roundtrip", "PASS", "safe-state checkpoint roundtrip succeeded")
        else:
            report.add("checkpoint-roundtrip", "FAIL", "checkpoint did not roundtrip", True)
    except Exception as exc:
        report.add("checkpoint-roundtrip", "FAIL", str(exc), True)

    try:
        runtime.session_store.save(runtime.messages, "doctor-rc1")
        loaded = runtime.session_store.load("doctor-rc1")
        runtime.session_store.delete("doctor-rc1")
        if isinstance(loaded, list):
            report.add("named-sessions", "PASS", "named session roundtrip succeeded")
        else:
            report.add("named-sessions", "FAIL", "named session did not roundtrip")
    except Exception as exc:
        report.add("named-sessions", "FAIL", str(exc))

    try:
        from .ui.web import _host_ok
        if _host_ok("127.0.0.1:9999") and not _host_ok("example.com"):
            report.add("web-host-gate", "PASS", "loopback-only host gate active")
        else:
            report.add("web-host-gate", "FAIL", "web host gate invariant failed", True)
    except Exception as exc:
        report.add("web-host-gate", "FAIL", str(exc), True)

    try:
        decision = router.decide("architect and debug a concurrency sensitive migration with security risks")
        should = runtime.executive.should_plan("architect and debug a concurrency sensitive migration with security risks", decision, True)
        report.add("executive-routing", "PASS" if should else "FAIL", "complex HIGH/MAX turn triggers bounded executive preflight")
    except Exception as exc:
        report.add("executive-routing", "FAIL", str(exc))

    try:
        bad = await runtime.tools.execute("write_files", {"changes": [{"path": "x.py"}]})
        if not bad.ok and "missing required" in bad.content:
            report.add("recursive-tool-schema", "PASS", "nested required fields rejected before handler execution")
        else:
            report.add("recursive-tool-schema", "FAIL", "nested invalid arguments were not rejected")
    except Exception as exc:
        report.add("recursive-tool-schema", "FAIL", str(exc))

    try:
        from .security.paths import PathGuard
        from .state.transactions import TransactionManager
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mgr = TransactionManager(root / ".term5", PathGuard(root))
            (root / "u.txt").write_text("before", encoding="utf-8")
            txid = mgr.write_one("u.txt", "after")
            undone, changed = mgr.undo_last()
            before = (root / "u.txt").read_text(encoding="utf-8")
            redone, _ = mgr.redo_last()
            after = (root / "u.txt").read_text(encoding="utf-8")
            if txid == undone == redone and before == "before" and after == "after" and changed == ["u.txt"]:
                report.add("transaction-undo-redo", "PASS", "reversible transaction roundtrip succeeded")
            else:
                report.add("transaction-undo-redo", "FAIL", "undo/redo roundtrip mismatch", True)
    except Exception as exc:
        report.add("transaction-undo-redo", "FAIL", str(exc), True)

    try:
        plan = runtime.operations.nginx.plan(name="doctor", domain="doctor.example.com", upstream_port=4990)
        if "proxy_pass http://127.0.0.1:4990" in plan["config"] and "managed-by: term_5 v5.2" in plan["config"]:
            report.add("nginx-plan", "PASS", "typed loopback reverse-proxy plan generated without host mutation")
        else:
            report.add("nginx-plan", "FAIL", "unexpected Nginx plan", True)
    except Exception as exc:
        report.add("nginx-plan", "FAIL", str(exc), True)

    return report
