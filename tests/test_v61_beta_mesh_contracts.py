from __future__ import annotations

import asyncio
import json
from pathlib import Path

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.agents.store import AgentStore


class QuietProvider(ModelProvider):
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.calls += 1
        return ChatResult(content="read-only answer", usage=Usage(prompt_tokens=4, completion_tokens=2))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def make_runtime(tmp_path: Path):
    cfg = load_config(tmp_path)
    provider = QuietProvider()
    runtime = AgentRuntime(cfg, provider=provider)
    runtime.projects.create(name="sso", path="projects/sso", init_git=False, make_active=True)
    runtime.projects.create(name="product-a", path="projects/product-a", init_git=False, make_active=False)
    runtime.agents.sync_projects(refresh=True)
    return runtime, provider


def test_typed_state_query_does_not_need_target_mutation_lane(tmp_path: Path):
    async def go():
        runtime, provider = make_runtime(tmp_path)
        runtime.agents.store.set_agent_state("sso", status="working", current_task_id="busy")
        message = await runtime.agents.send_message(
            "product-a", "sso", "STATE_QUERY", "What is SSO's current and planned state?", subject="SSO state"
        )
        assert message["status"] == "answered"
        payload = json.loads(message["response"])
        assert payload["project"] == "sso"
        assert payload["answer_mode"] == "capsule"
        assert "current_truth" in payload and "planned" in payload
        assert provider.calls == 0
    asyncio.run(go())


def test_change_request_creates_target_owner_task(tmp_path: Path):
    async def go():
        runtime, _ = make_runtime(tmp_path)
        result = await runtime.agents.request_change(
            "product-a", "sso", "Expose organization discovery", acceptance="Document endpoint and entitlement behavior"
        )
        task = result["task"]
        assert task and task["project"] == "sso"
        assert task["source"] == "agent:product-a"
        assert task["status"] == "queued"
        message = result["message"]
        assert message["kind"] == "CHANGE_REQUEST"
        assert message["status"] == "answered"
    asyncio.run(go())


def test_contract_publish_forecast_impact_and_notifications(tmp_path: Path):
    async def go():
        runtime, _ = make_runtime(tmp_path)
        first = await runtime.agents.publish_contract(
            "syntal.identity.oidc", "sso",
            summary="OIDC and organization context",
            interface={"authorize": "/authorize", "token": "/token", "claims": ["org_id", "njs.access"]},
            consumers=["product-a"], compatibility="compatible",
        )
        assert first["revision"] == 1
        assert first["authoritative"] is True
        inbox = runtime.agents.store.list_messages("product-a", direction="in", limit=20)
        assert any(m["kind"] == "CONTRACT_PUBLISHED" and m["status"] == "delivered" for m in inbox)

        forecast = await runtime.agents.forecast_contract(
            "syntal.identity.oidc", "sso", state="planned",
            expected_change="Add organization application discovery",
            interface_delta={"add": ["GET /api/v1/organizations/{id}/applications"]},
        )
        assert forecast["authoritative"] is False
        query = runtime.agents.contract_query("syntal.identity.oidc")
        assert query["current"]["revision"] == 1
        assert len(query["forecasts"]) == 1
        assert query["forecasts"][0]["status"] == "active"

        impact = runtime.agents.contract_impact("syntal.identity.oidc")
        assert impact["impact_count"] == 1
        assert impact["consumers"][0]["project"] == "product-a"

        second = await runtime.agents.publish_contract(
            "syntal.identity.oidc", "sso",
            summary="OIDC, organization context and application discovery",
            interface={"authorize": "/authorize", "token": "/token", "applications": "/api/v1/organizations/{id}/applications"},
            consumers=["product-a"], compatibility="additive",
        )
        assert second["revision"] == 2
        query2 = runtime.agents.contract_query("syntal.identity.oidc")
        assert query2["forecasts"] == []
        inbox2 = runtime.agents.store.list_messages("product-a", direction="in", limit=30)
        assert any(m["kind"] == "CONTRACT_CHANGED" for m in inbox2)
        capsule = runtime.agents.capsules.build("product-a")
        assert capsule["consumed_contracts"][0]["contract_key"] == "syntal.identity.oidc"
        assert capsule["consumed_contracts"][0]["revision"] == 2
    asyncio.run(go())


def test_owner_cross_project_reads_and_identity_binding(tmp_path: Path):
    runtime, _ = make_runtime(tmp_path)
    owner = runtime.agents._owner("product-a")

    args, err = owner._scope_args("agent_query", {"project": "sso", "question": "OIDC contract?"})
    assert err is None
    assert args["project"] == "sso"

    args, err = owner._scope_args("agent_message_send", {
        "to_project": "sso", "from_project": "central", "kind": "STATE_QUERY", "body": "state?"
    })
    assert err is None
    assert args["from_project"] == "product-a"

    args, err = owner._scope_args("contract_publish", {
        "contract_key": "product.api", "owner_project": "sso", "summary": "x"
    })
    assert err and "cannot publish contracts" in err

    args, err = owner._scope_args("contract_publish", {
        "contract_key": "product.api", "summary": "x"
    })
    assert err is None
    assert args["owner_project"] == "product-a"


def test_dependency_graph_can_span_projects(tmp_path: Path):
    store = AgentStore(tmp_path / "orchestrator.sqlite3")
    store.ensure_agent("sso")
    store.ensure_agent("product-a")
    upstream = store.enqueue_task("sso", "Publish SSO contract")
    downstream = store.enqueue_task("product-a", "Integrate contract")
    updated = store.add_dependency(downstream["id"], upstream["id"])
    assert updated["status"] == "waiting_dependency"
    assert store.dependency_state(downstream["id"])["ready"] is False
    store.update_task(upstream["id"], status="completed", result="published")
    released = store.release_ready_dependencies_detail()
    assert [x["id"] for x in released] == [downstream["id"]]
    assert store.dependency_state(downstream["id"])["ready"] is True


def test_beta_tools_registered(tmp_path: Path):
    runtime, _ = make_runtime(tmp_path)
    expected = {
        "agent_request_change", "agent_dependency_add", "contract_publish", "contract_forecast",
        "contract_query", "contract_list", "contract_subscribe", "contract_impact",
    }
    assert all(runtime.tools.get(name) is not None for name in expected)
