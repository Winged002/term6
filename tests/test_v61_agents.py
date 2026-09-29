from __future__ import annotations

import asyncio
from pathlib import Path

from term5.agents.store import AgentStore
from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, ToolCall, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime


class OwnerProvider(ModelProvider):
    def __init__(self):
        self.owner_calls = 0
        self.central_calls = 0

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        system = str((messages[0] if messages else {}).get("content") or "")
        if "Project Owner Agent" in system:
            self.owner_calls += 1
            if self.owner_calls == 1:
                return ChatResult(
                    tool_calls=[ToolCall("owner_write", "write_file", {"path": "projects/demo/owner.txt", "content": "owned\n"})],
                    usage=Usage(prompt_tokens=20, completion_tokens=5),
                )
            return ChatResult(content="Implemented and verified owner.txt", usage=Usage(prompt_tokens=10, completion_tokens=4))
        self.central_calls += 1
        return ChatResult(content="central", usage=Usage(prompt_tokens=5, completion_tokens=1))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def test_agent_store_dependency_and_recovery(tmp_path: Path):
    store = AgentStore(tmp_path / "orchestrator.sqlite3")
    store.ensure_agent("sso", "SSO Owner")
    upstream = store.enqueue_task("sso", "Publish contract")
    downstream = store.enqueue_task("sso", "Consume contract", depends_on=[upstream["id"]])
    assert downstream["status"] == "waiting_dependency"
    assert store.claim_next("sso", "w1", lease_seconds=60)["id"] == upstream["id"]
    store.update_task(upstream["id"], status="executing")
    store.update_task(upstream["id"], status="completed", result="done")
    assert store.release_ready_dependencies("sso") == 1
    assert store.get_task(downstream["id"])["status"] == "queued"


def test_project_owner_executes_durable_queue(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        cfg.agents.scheduler_interval_s = 1
        provider = OwnerProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        runtime.projects.create(name="demo", path="projects/demo", init_git=False, make_active=True)
        runtime.agents.sync_projects(refresh=True)
        task = runtime.agents.delegate("demo", "Create owner.txt", acceptance="owner.txt contains owned")
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            row = runtime.agents.store.get_task(task["id"])
            if row and row["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.05)
        row = runtime.agents.store.get_task(task["id"])
        assert row is not None and row["status"] == "completed", row
        assert (tmp_path / "projects/demo/owner.txt").read_text() == "owned\n"
        capsule = runtime.agents.query_capsule("demo", "What is happening?")
        assert capsule["current_truth"]["identity"]["name"] == "demo"
        assert capsule["revision"] >= 1
        assert provider.owner_calls >= 2
    asyncio.run(go())


def test_agent_tools_and_central_context(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=OwnerProvider())
    runtime.projects.create(name="sso", path="projects/sso", init_git=False, make_active=True)
    runtime.projects.create(name="product-a", path="projects/product-a", init_git=False, make_active=False)
    runtime.agents.sync_projects(refresh=True)
    for name in ["agent_list", "agent_status", "agent_delegate", "agent_query", "agent_task_list", "agent_wait", "agent_message_send", "agent_message_list"]:
        assert runtime.tools.get(name) is not None
    context = runtime.agents.context_text("integrate product A with SSO")
    assert "central coordinator" in context
    assert "sso" in context and "product-a" in context
    assert "forecast" in context.lower()


def test_project_owner_rejects_cross_project_mutations(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=OwnerProvider())
    runtime.projects.create(name="demo", path="projects/demo", init_git=False, make_active=True)
    runtime.projects.create(name="other", path="projects/other", init_git=False, make_active=False)
    runtime.agents.sync_projects(refresh=True)
    owner = runtime.agents._owner("demo")

    _, err = owner._scope_args("write_file", {"path": "projects/other/x.txt", "content": "x"})
    assert err and "rejected" in err

    _, err = owner._scope_args("write_files", {"changes": [{"path": "projects/other/x.txt", "content": "x"}]})
    assert err and "rejected" in err

    _, err = owner._scope_args("parallel_fim_edit", {"changes": [{"path": "projects/other/x.py", "start_line": 1, "end_line": 1, "instruction": "x"}]})
    assert err and "rejected" in err


def test_different_project_owners_can_execute_concurrently(tmp_path: Path):
    class ConcurrentProvider(OwnerProvider):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
            system = str((messages[0] if messages else {}).get("content") or "")
            if "Project Owner Agent" in system:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.12)
                self.active -= 1
                return ChatResult(content="done", usage=Usage(prompt_tokens=5, completion_tokens=1))
            return await super().chat(messages, tools=tools, reasoning=reasoning, max_tokens=max_tokens)

    async def go():
        cfg = load_config(tmp_path)
        cfg.agents.max_concurrent_owners = 4
        provider = ConcurrentProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        runtime.projects.create(name="alpha", path="projects/alpha", init_git=False, make_active=True)
        runtime.projects.create(name="beta", path="projects/beta", init_git=False, make_active=False)
        runtime.agents.sync_projects(refresh=True)
        a = runtime.agents.delegate("alpha", "Alpha work")
        b = runtime.agents.delegate("beta", "Beta work")
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            rows = [runtime.agents.store.get_task(a["id"]), runtime.agents.store.get_task(b["id"])]
            if all(row and row["status"] == "completed" for row in rows):
                break
            await asyncio.sleep(0.02)
        assert runtime.agents.store.get_task(a["id"])["status"] == "completed"
        assert runtime.agents.store.get_task(b["id"])["status"] == "completed"
        assert provider.max_active >= 2

    asyncio.run(go())
