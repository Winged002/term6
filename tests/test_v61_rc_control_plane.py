from __future__ import annotations

import asyncio
from pathlib import Path

from term5.agents.store import AgentStore
from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.ui.web import HTML
from term5.ui.web_assets import CSS, JS


class FastOwnerProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        system = str((messages[0] if messages else {}).get("content") or "")
        if "Project Owner Agent" in system:
            await asyncio.sleep(0.03)
            return ChatResult(content="owner complete", usage=Usage(prompt_tokens=3, completion_tokens=2))
        return ChatResult(content="central", usage=Usage(prompt_tokens=2, completion_tokens=1))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


class BlockingOwnerProvider(ModelProvider):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        system = str((messages[0] if messages else {}).get("content") or "")
        if "Project Owner Agent" in system:
            self.started.set()
            await self.release.wait()
            return ChatResult(content="late result", usage=Usage(prompt_tokens=3, completion_tokens=2))
        return ChatResult(content="central")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def test_restart_recovery_is_immediate_and_dependency_aware(tmp_path: Path):
    store = AgentStore(tmp_path / "orchestrator.sqlite3")
    store.ensure_agent("sso")
    store.ensure_agent("njs")
    upstream = store.enqueue_task("sso", "Publish contract")
    store.claim_next("sso", "owner:sso:dead", lease_seconds=1800)
    store.update_task(upstream["id"], status="executing")
    downstream = store.enqueue_task("njs", "Consume contract", depends_on=[upstream["id"]])
    assert downstream["status"] == "waiting_dependency"

    recovered = store.recover_after_restart()
    assert [x["id"] for x in recovered] == [upstream["id"]]
    row = store.get_task(upstream["id"])
    assert row["status"] == "queued"
    assert row["lease_owner"] == ""
    assert row["error"] == "Recovered after runtime restart"
    assert store.get_task(downstream["id"])["status"] == "waiting_dependency"


def test_queue_priority_and_runtime_settings_are_durable(tmp_path: Path):
    store = AgentStore(tmp_path / "orchestrator.sqlite3")
    store.ensure_agent("planner")
    task = store.enqueue_task("planner", "Low priority first", priority=3)
    changed = store.reprioritize_task(task["id"], 0)
    assert changed["priority"] == 0
    store.set_setting("max_concurrent_owners", 7)
    store.set_setting("max_concurrent_queries", 5)
    reopened = AgentStore(tmp_path / "orchestrator.sqlite3")
    assert reopened.get_setting("max_concurrent_owners") == 7
    assert reopened.get_setting("max_concurrent_queries") == 5


def test_runtime_concurrency_limits_are_live_and_persisted(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        runtime = AgentRuntime(cfg, provider=FastOwnerProvider())
        result = await runtime.agents.set_concurrency_limits(owners=2, queries=3)
        assert result["owners"]["limit"] == 2
        assert result["queries"]["limit"] == 3
        assert runtime.agents.store.get_setting("max_concurrent_owners") == 2
        assert runtime.agents.store.get_setting("max_concurrent_queries") == 3
    asyncio.run(go())


def test_running_owner_task_can_be_cancelled_cooperatively(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        provider = BlockingOwnerProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        runtime.projects.create(name="sso", path="projects/sso", init_git=False, make_active=True)
        runtime.agents.sync_projects(refresh=True)
        task = runtime.agents.delegate("sso", "Long change")
        await asyncio.wait_for(provider.started.wait(), timeout=3)
        cancelled = await runtime.agents.cancel_task(task["id"])
        assert cancelled["status"] == "cancelled"
        provider.release.set()
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            if runtime.agents.store.get_agent("sso")["status"] == "idle":
                break
            await asyncio.sleep(0.02)
        assert runtime.agents.store.get_task(task["id"])["status"] == "cancelled"
    asyncio.run(go())


def test_three_project_dependency_mesh_executes_and_releases(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        cfg.agents.max_concurrent_owners = 2
        runtime = AgentRuntime(cfg, provider=FastOwnerProvider())
        for i, name in enumerate(["sso", "njs", "files"]):
            runtime.projects.create(name=name, path=f"projects/{name}", init_git=False, make_active=(i == 0))
        runtime.agents.sync_projects(refresh=True)
        upstream = runtime.agents.delegate("sso", "Publish identity contract")
        dependent = runtime.agents.delegate("njs", "Integrate identity contract", depends_on=[upstream["id"]])
        independent = runtime.agents.delegate("files", "Inspect manifest")
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            rows = [runtime.agents.store.get_task(x["id"]) for x in [upstream, dependent, independent]]
            if all(r and r["status"] == "completed" for r in rows):
                break
            await asyncio.sleep(0.03)
        assert runtime.agents.store.get_task(upstream["id"])["status"] == "completed"
        assert runtime.agents.store.get_task(independent["id"])["status"] == "completed"
        assert runtime.agents.store.get_task(dependent["id"])["status"] == "completed"
        graph = runtime.agents.store.task_graph()
        assert {"from": upstream["id"], "to": dependent["id"]} in graph["edges"]
    asyncio.run(go())


def test_global_message_and_graph_views(tmp_path: Path):
    store = AgentStore(tmp_path / "orchestrator.sqlite3")
    store.ensure_agent("sso")
    store.ensure_agent("njs")
    a = store.enqueue_task("sso", "A")
    b = store.enqueue_task("njs", "B", depends_on=[a["id"]])
    store.send_message("njs", "sso", "STATE_QUERY", "state?")
    messages = store.list_messages("", limit=10)
    assert len(messages) == 1 and messages[0]["from_project"] == "njs"
    graph = store.task_graph()
    assert {"from": a["id"], "to": b["id"]} in graph["edges"]


def test_v61_full_project_owner_ui_and_endpoints_are_present():
    # v6.2 supersedes the v6.1 cards with the spatial workspace while preserving
    # the same durable control-plane endpoints and execution semantics.
    for text in ["Organization Workspace", "Systems", "Objectives", "Dependencies", "Agent Inbox"]:
        assert text in HTML
    for text in [".spatial-canvas", ".project-node", ".spatial-worker", ".task-graph-node", ".team-feedback"]:
        assert text in CSS
    for text in [
        "/api/agent-dashboard", "/api/agent-concurrency", "/api/agent-task-priority",
        "renderSpatialWorkspace", "dependencyCanvas", "renderOrganizationTimeline", "addTeamFeedback",
    ]:
        assert text in JS
    # Keep one activity polling loop; workspace updates piggyback on the same event stream.
    assert JS.count("setTimeout(poll") == 1
