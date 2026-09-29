from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, ToolCall, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.ui.web import _agent_dashboard
from term5.ui.web_assets import HTML, CSS, JS


class CoordinatorProvider(ModelProvider):
    def __init__(self):
        self.central_calls = 0
        self.owner_calls = 0
        self.central_tools: list[str] = []

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        system = str((messages[0] if messages else {}).get("content") or "")
        if system.startswith("You are the persistent Project Owner Agent"):
            self.owner_calls += 1
            return ChatResult(content="owner finished", usage=Usage(prompt_tokens=5, completion_tokens=2))
        self.central_calls += 1
        self.central_tools = [str((x.get("function") or {}).get("name") or "") for x in (tools or [])]
        if self.central_calls == 1:
            return ChatResult(
                tool_calls=[ToolCall("d1", "agent_delegate", {"project": "demo", "objective": "Inspect and improve the demo project"})],
                usage=Usage(prompt_tokens=5, completion_tokens=2),
            )
        return ChatResult(content="Delegated to demo Project Owner.", usage=Usage(prompt_tokens=4, completion_tokens=2))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


class BlockingOwnerProvider(ModelProvider):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        system = str((messages[0] if messages else {}).get("content") or "")
        if system.startswith("You are the persistent Project Owner Agent"):
            self.started.set()
            await self.release.wait()
            return ChatResult(content="done")
        return ChatResult(content="central")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {}


def test_central_ai_is_coordinator_only_when_owner_exists(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        cfg.executive.auto_plan = False
        cfg.skills.auto_product_plan = False
        cfg.improvement.auto_plan = False
        provider = CoordinatorProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        runtime.projects.create(name="demo", path="projects/demo", init_git=False, make_active=True)
        runtime.agents.sync_projects(refresh=True)
        answer = await runtime.run_turn("Improve the demo application", reasoning=ReasoningMode.LOW)
        assert "Delegated" in answer
        assert "agent_delegate" in provider.central_tools
        assert "browser_open" not in provider.central_tools
        assert "read_file" not in provider.central_tools
        assert "write_file" not in provider.central_tools
        assert "run_tests" not in provider.central_tools
        assert "agent_wait" not in provider.central_tools
        assert provider.central_calls <= cfg.agents.central_max_iterations
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            tasks = runtime.agents.store.list_tasks("demo", limit=10)
            if tasks and tasks[0]["status"] == "completed":
                break
            await asyncio.sleep(0.02)
        tasks = runtime.agents.store.list_tasks("demo", limit=10)
        assert tasks and tasks[0]["status"] == "completed"
        assert provider.owner_calls >= 1
    asyncio.run(go())


def test_no_registered_owner_keeps_bootstrap_tool_surface(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=CoordinatorProvider())
    assert runtime._coordinator_mode() is False
    assert "browser_open" in {x["function"]["name"] for x in runtime.tools.schemas()}


def test_objective_durably_groups_delegated_tasks(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=CoordinatorProvider())
    runtime.projects.create(name="a", path="projects/a", init_git=False, make_active=True)
    runtime.projects.create(name="b", path="projects/b", init_git=False, make_active=False)
    runtime.agents.sync_projects(refresh=True)
    obj = runtime.agents.begin_objective("Unify authentication", project_hint="a")
    a = runtime.agents.delegate("a", "Publish auth contract")
    b = runtime.agents.delegate("b", "Consume auth contract", depends_on=[a["id"]])
    runtime.agents.finish_central_objective(obj["id"], "delegated")
    row = runtime.agents.store.get_objective(obj["id"])
    assert row and row["status"] == "active"
    assert {t["id"] for t in row["tasks"]} == {a["id"], b["id"]}
    assert all(t["objective_id"] == obj["id"] for t in row["tasks"])


def test_owner_to_owner_change_inherits_parent_objective(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        runtime = AgentRuntime(cfg, provider=CoordinatorProvider())
        runtime.projects.create(name="a", path="projects/a", init_git=False, make_active=True)
        runtime.projects.create(name="b", path="projects/b", init_git=False, make_active=False)
        runtime.agents.sync_projects(refresh=True)
        obj = runtime.agents.begin_objective("Cross-project integration")
        parent = runtime.agents.delegate("a", "Prepare integration")
        runtime.agents.finish_central_objective(obj["id"], "delegated")
        result = await runtime.agents.request_change("a", "b", "Implement consumer", parent_task_id=parent["id"])
        child = result["task"]
        assert child and child["objective_id"] == obj["id"]
    asyncio.run(go())


def test_task_direct_manipulation_move_preserves_objective(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=CoordinatorProvider())
    runtime.projects.create(name="a", path="projects/a", init_git=False, make_active=True)
    runtime.projects.create(name="b", path="projects/b", init_git=False, make_active=False)
    runtime.agents.sync_projects(refresh=True)
    obj = runtime.agents.begin_objective("Moveable work")
    task = runtime.agents.delegate("a", "Queued work")
    # cancel the asynchronously kicked worker if the test loop is not running; task remains queued here.
    moved = runtime.agents.store.move_task(task["id"], "b")
    assert moved["project"] == "b"
    assert moved["objective_id"] == obj["id"]


def test_attention_model_marks_dependency_wait_as_coordinated(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=CoordinatorProvider())
    runtime.projects.create(name="a", path="projects/a", init_git=False, make_active=True)
    runtime.projects.create(name="b", path="projects/b", init_git=False, make_active=False)
    runtime.agents.sync_projects(refresh=True)
    upstream = runtime.agents.store.enqueue_task("a", "upstream")
    runtime.agents.store.enqueue_task("b", "downstream", depends_on=[upstream["id"]])
    snap = runtime.agents.attention_snapshot([])
    by = {x["project"]: x for x in snap["projects"]}
    assert by["b"]["attention"] == "coordinated"
    assert by["a"]["attention"] == "autonomous"


def test_worker_dashboard_exposes_operational_context_not_hidden_reasoning(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        provider = BlockingOwnerProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        runtime.projects.create(name="demo", path="projects/demo", init_git=False, make_active=True)
        runtime.agents.sync_projects(refresh=True)
        task = runtime.agents.delegate("demo", "Long task")
        await asyncio.wait_for(provider.started.wait(), timeout=3)
        dashboard = _agent_dashboard(runtime)
        worker = next(x for x in dashboard["workers"] if x["task_id"] == task["id"])
        assert worker["project"] == "demo"
        assert worker["context"]["estimated_tokens"] >= 0
        assert "reasoning_content" not in worker
        provider.release.set()
    asyncio.run(go())


def test_fast_status_is_coordinator_safe_and_avoids_heavy_probe_fields(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=CoordinatorProvider())
    runtime.projects.create(name="demo", path="projects/demo", init_git=False, make_active=True)
    runtime.agents.sync_projects(refresh=True)
    status = runtime.status_lite()
    assert status["coordinator_mode"] is True
    assert status["projects"] == 1
    assert "docker" not in status
    assert "operations" not in status
    assert "configuration" not in status


def test_v62_spatial_ui_status_abort_fix_and_direct_manipulation_present():
    for text in [
        "Organization Workspace", "Agent Inbox", "Systems", "Objectives", "Dependencies",
        "Central coordinator", "Shift-click projects", "drag queued tasks",
    ]:
        assert text in HTML
    for text in [
        ".spatial-canvas", ".central-node", ".project-node", ".spatial-worker",
        ".workspace-inspector", ".global-command-dock", ".attention-mark",
    ]:
        assert text in CSS
    for text in [
        "/api/status-lite", "request timed out", "renderSpatialWorkspace", "objectiveCanvas",
        "dependencyCanvas", "renderWorkspaceInspector", "renderInbox", "renderOrganizationTimeline",
        "/api/agent-task-move", "/api/agent-dependency", "data-drag-task",
    ]:
        assert text in JS
    assert "Status unavailable" not in JS
    assert JS.count("setTimeout(poll") == 1
