from __future__ import annotations

import asyncio
import json
from pathlib import Path

from term5.brain.product import ProductPlanner
from term5.brain.parallel import ParallelCortex
from term5.brain.router import AttentionRouter
from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.skills import SkillEngine


class FailingProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        raise RuntimeError("offline")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        raise RuntimeError("offline")


class PlanningProvider(ModelProvider):
    def __init__(self):
        self.calls = []

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.calls.append({"messages": messages, "tools": tools, "reasoning": reasoning})
        # Product planner is the first call. Primary answer is the second because
        # tests disable executive + product critique.
        if len(self.calls) == 1:
            payload = {
                "title": "Community",
                "product_type": "social-network",
                "scope": "usable MVP",
                "features": {
                    "core": ["registration and login/logout", "user profiles", "posts"],
                    "expected": ["notifications"],
                    "optional": ["video"],
                },
                "pages": ["/feed", "/profile/<username>"],
                "entities": ["User", "Post"],
                "journeys": ["register -> profile -> post"],
                "quality_requirements": ["responsive UI"],
                "definition_of_done": ["critical flow works"],
                "implementation_phases": ["model", "UI", "verify"],
                "assumptions": [],
                "non_goals": [],
            }
            return ChatResult(content=json.dumps(payload), usage=Usage(prompt_tokens=20, completion_tokens=10))
        return ChatResult(content="done", usage=Usage(prompt_tokens=10, completion_tokens=2))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def test_social_skill_resolution_expands_product_capabilities(tmp_path: Path):
    engine = SkillEngine(tmp_path, tmp_path / ".term5")
    res = engine.resolve("Create a social media app with Flask")
    expected = {"social-network", "authentication", "file-uploads", "realtime", "notifications", "search", "web-ux", "security", "testing", "flask"}
    assert expected.issubset(set(res.selected))
    assert "booking" not in res.selected
    assert "ecommerce" not in res.selected
    ctx = engine.resolution_context(res)
    assert "upload an avatar" in ctx.lower()
    assert "empty, loading" in ctx.lower()


def test_local_skill_override_is_discovered(tmp_path: Path):
    manifest = tmp_path / ".term5" / "skills" / "product" / "kanban" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "name": "kanban-product",
        "category": "product",
        "description": "Kanban workspace",
        "triggers": ["kanban board"],
        "features": {"core": ["columns", "cards"]},
    }))
    engine = SkillEngine(tmp_path, tmp_path / ".term5")
    res = engine.resolve("build a kanban board")
    assert "kanban-product" in res.selected
    assert engine.get("kanban-product").source.startswith("workspace:")


def test_product_router_escalates_vague_app_request():
    decision = AttentionRouter().decide("make me a social media app")
    assert decision.reasoning == ReasoningMode.HIGH
    assert decision.parallel_hint >= 2


def test_product_planner_fallback_preserves_skill_requirements(tmp_path: Path):
    engine = SkillEngine(tmp_path, tmp_path / ".term5")
    res = engine.resolve("create a social media app with Flask")
    provider = FailingProvider()
    parallel = ParallelCortex(provider, timeout_s=5, retries=0)
    planner = ProductPlanner(provider, parallel, engine, tmp_path / ".term5", critique=False)

    async def go():
        plan = await planner.plan("create a social media app with Flask", res, engine.resolution_context(res))
        assert plan.generated_by_model is False
        joined = " ".join(plan.core_features + plan.expected_features + plan.definition_of_done).lower()
        assert "avatar" in joined
        assert "image" in joined or "media" in joined
        assert "follow" in joined
        assert "comment" in joined
        assert plan.scope == "usable MVP"
    asyncio.run(go())


def test_runtime_injects_product_blueprint_before_primary_call(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        cfg.executive.auto_plan = False
        cfg.skills.critique = False
        provider = PlanningProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        out = await runtime.run_turn("create a social media app with Flask", use_tools=False)
        assert out == "done"
        assert runtime.product_plan_runs == 1
        assert "social-network" in runtime.last_skill_resolution
        # The primary request must see the prepared blueprint and the fallback
        # procedural requirements the model planner is not allowed to erase.
        primary_messages = provider.calls[-1]["messages"]
        joined = "\n".join(str(m.get("content") or "") for m in primary_messages)
        assert "term_5 product blueprint" in joined
        assert "attach uploaded images to posts" in joined
        assert "Definition of done" in joined
    asyncio.run(go())


def test_generic_one_shot_flask_launch_blocked_when_product_plan_active(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        cfg.skills.critique = False
        provider = FailingProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        res = runtime.skills.resolve("create a social media app with flask")
        runtime.product.last_plan = runtime.product._fallback("create a social media app with flask", res)
        runtime.product.active = True
        result = await runtime.tools.execute("app_create_flask", {
            "name": "social", "path": "apps/social", "port": 4990,
            "celery": True, "mongodb": True, "start": True,
            "open_browser": True, "wait_timeout_s": 60,
        })
        assert result.ok is False
        assert "active product blueprint" in result.content.lower()
        assert not (tmp_path / "apps" / "social").exists()
    asyncio.run(go())

class AuditProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        system = str(messages[0].get("content") or "")
        if "product-completeness auditor" in system:
            return ChatResult(content=json.dumps({
                "verdict": "incomplete",
                "core": [{"feature": "avatar uploads", "status": "missing", "evidence": "no upload handler"}],
                "expected": [],
                "ux_gaps": ["missing empty feed state"],
                "security_data_gaps": ["upload validation missing"],
                "blockers": ["avatar uploads"],
                "recommended_next_actions": ["implement upload lifecycle"]
            }), usage=Usage(prompt_tokens=30, completion_tokens=12))
        return ChatResult(content="{}", usage=Usage())

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def test_product_audit_uses_source_snapshot_and_reports_gaps(tmp_path: Path):
    async def go():
        (tmp_path / "app.py").write_text("from flask import Flask\napp=Flask(__name__)\n")
        cfg = load_config(tmp_path)
        cfg.skills.critique = False
        provider = AuditProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        res = runtime.skills.resolve("create a social media app with flask")
        runtime.product.last_plan = runtime.product._fallback("create a social media app with flask", res)
        runtime.product.active = True
        runtime.graph.refresh()
        result = await runtime.tools.execute("product_audit", {})
        assert result.ok is False
        assert result.data["verdict"] == "incomplete"
        assert "avatar uploads" in json.dumps(result.data)
        current = await runtime.tools.execute("product_audit_current", {})
        assert current.data["verdict"] == "incomplete"
    asyncio.run(go())
