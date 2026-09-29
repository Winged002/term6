from __future__ import annotations

import asyncio
import base64
import json
import sys
import types
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from term5.collaboration import DurableRunStore, HumanTaskManager
from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.ui.web import LocalWebApp
from term5.ui.web_assets import HTML, JS


class FinalProvider(ModelProvider):
    def __init__(self):
        self.prompts: list[list[dict]] = []

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.prompts.append([dict(m) for m in messages])
        return ChatResult(content="continued", usage=Usage(prompt_tokens=4, completion_tokens=1))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "replacement\n", {"prompt_tokens": 1, "completion_tokens": 1}


def test_human_task_timeout_policies_and_approval_safety(tmp_path: Path):
    runs = DurableRunStore(tmp_path / ".term5")
    run = runs.start("redesign dashboard", project="demo")
    tasks = HumanTaskManager(tmp_path / ".term5", runs, default_timeout_s=30)
    tasks.set_active_run(run["id"])
    choice = tasks.create(
        kind="choose", title="Choose visual", project="demo",
        options=[
            {"id":"a","label":"A"},
            {"id":"b","label":"B","recommended":True},
        ],
        timeout_policy="AUTO_DECIDE", timeout_seconds=5, blocking=True,
    )
    tasks._tasks[choice["id"]]["deadline_at"] = "2000-01-01T00:00:00+00:00"
    changed = tasks.tick()
    resolved = tasks.get(choice["id"])
    assert changed and resolved["status"] == "auto_resolved"
    assert resolved["response"]["option_id"] == "b"

    deferred = tasks.create(kind="answer", title="Optional info", timeout_policy="DEFER", timeout_seconds=5, blocking=False)
    tasks._tasks[deferred["id"]]["deadline_at"] = "2000-01-01T00:00:00+00:00"
    tasks.tick()
    assert tasks.get(deferred["id"])["status"] == "deferred"

    blocked = tasks.create(kind="approve", title="Deploy production", timeout_policy="BLOCK", blocking=True)
    assert tasks.get(blocked["id"])["status"] == "open"
    with pytest.raises(ValueError, match="cannot auto-approve"):
        tasks.create(kind="approve", title="Danger", timeout_policy="AUTO_DECIDE")
    with pytest.raises(ValueError, match="cannot be auto-resolved"):
        tasks.resolve(blocked["id"], auto=True)


def test_let_ai_decide_now_uses_default_or_recommended(tmp_path: Path):
    runs = DurableRunStore(tmp_path / ".term5")
    tasks = HumanTaskManager(tmp_path / ".term5", runs)
    rec = tasks.create(
        kind="choose", title="Choose palette",
        options=[{"id":"one","label":"One"},{"id":"two","label":"Two","recommended":True}],
        timeout_policy="AUTO_DECIDE", default_option="", blocking=True,
    )
    out = tasks.resolve(rec["id"], auto=True)
    assert out["status"] == "auto_resolved"
    assert out["response"]["option_id"] == "two"


def test_durable_run_and_human_task_stores_are_private_and_persistent(tmp_path: Path):
    state = tmp_path / ".term5"
    runs = DurableRunStore(state)
    run = runs.start("prepare private beta", project="demo")
    tasks = HumanTaskManager(state, runs)
    task = tasks.create(kind="answer", title="Need domain", project="demo", run_id=run["id"], timeout_policy="DEFER", blocking=False)
    assert (state / "runs_v6.json").stat().st_mode & 0o777 == 0o600
    assert (state / "human_tasks.json").stat().st_mode & 0o777 == 0o600
    runs2 = DurableRunStore(state); tasks2 = HumanTaskManager(state, runs2)
    assert runs2.get(run["id"])["objective"] == "prepare private beta"
    assert tasks2.get(task["id"])["title"] == "Need domain"


def test_configuration_require_creates_secret_safe_my_task(tmp_path: Path):
    runtime = AgentRuntime(load_config(tmp_path))
    runtime.projects.create(name="demo")
    runtime.human_tasks.set_active_run(runtime.durable_runs.start("email invitations", project="demo")["id"])

    async def go():
        result = await runtime.tools.execute("configuration_require", {
            "keys": ["SMTP_HOST", "SMTP_PASSWORD"],
            "project": "demo", "environment": "production",
            "secret_keys": ["SMTP_PASSWORD"], "reason": "send invitation email", "service": "smtp",
        })
        assert result.ok
        tasks = runtime.human_tasks.list(state="open", project="demo")
        cfg = next(x for x in tasks if x["kind"] == "configure")
        fields = {x["name"]: x for x in cfg["fields"]}
        assert fields["SMTP_PASSWORD"]["secret"] is True
        assert cfg["timeout_policy"] == "DEFER"
        assert "value" not in json.dumps(cfg).lower()
    asyncio.run(go())


def test_config_completion_resolves_task_without_secret_value(tmp_path: Path):
    runtime = AgentRuntime(load_config(tmp_path))
    runtime.projects.create(name="demo")
    task = runtime.human_tasks.create(
        kind="configure", title="SMTP", project="demo",
        fields=[{"name":"SMTP_PASSWORD","secret":True,"required":True,"service":"smtp"}],
        timeout_policy="DEFER", blocking=False, metadata={"environment":"production"},
    )
    secret = "do-not-leak-123456"
    runtime.configuration.set_values([{"name":"SMTP_PASSWORD","value":secret,"secret":True,"service":"smtp"}], project="demo")
    done = runtime.human_tasks.complete_configuration(project="demo", environment="production", configured_keys={"SMTP_PASSWORD"})
    assert done and done[0]["id"] == task["id"]
    assert secret not in json.dumps(runtime.human_tasks.get(task["id"]))
    assert runtime.human_tasks.get(task["id"])["response"] == {"configured":["SMTP_PASSWORD"]}


def test_svg_creative_concepts_create_viewable_artifacts_and_choice(tmp_path: Path):
    runtime = AgentRuntime(load_config(tmp_path))
    runtime.projects.create(name="demo")

    async def go():
        result = await runtime.tools.execute("creative_svg_concepts", {
            "brief":"Choose dashboard visual direction",
            "directions":[
                {"label":"Calm", "description":"Soft blue spacious cards", "palette":["#0F172A","#F8FAFC","#38BDF8"], "recommended":True},
                {"label":"Warm", "description":"Cream and green friendly cards", "palette":["#173A2B","#FFF8EA","#79C99E"]},
            ],
            "project":"demo", "ask_human":True, "timeout_seconds":30,
        })
        assert result.ok
        assert result.data["provider"] == "svg" and result.data["count"] == 2
        task = result.data["human_task"]
        assert task["kind"] == "choose" and task["timeout_policy"] == "AUTO_DECIDE"
        for option in result.data["options"]:
            meta = runtime.artifacts.metadata(option["artifact_id"])
            assert meta["kind"] == "creative_mockup"
            assert runtime.artifacts.list(kind="creative_mockup")[0]["viewable"] is True
            assert runtime.artifacts.path_for(option["artifact_id"]).suffix == ".svg"
    asyncio.run(go())


def test_missing_openai_key_creates_deferred_configuration_task_and_svg_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = AgentRuntime(load_config(tmp_path))
    runtime.projects.create(name="demo")

    async def go():
        result = await runtime.tools.execute("creative_image_concepts", {
            "brief":"Dashboard", "project":"demo",
            "directions":[{"label":"A","prompt":"blue"},{"label":"B","prompt":"green"}],
        })
        assert not result.ok
        assert result.data["fallback"] == "creative_svg_concepts"
        task = result.data["human_task"]
        assert task["kind"] == "configure" and task["blocking"] is False
        assert task["timeout_policy"] == "DEFER"
        assert task["fields"][0]["name"] == "OPENAI_API_KEY"
    asyncio.run(go())


def test_openai_creative_renderer_never_returns_key_and_stores_mockup(monkeypatch, tmp_path: Path):
    # 1x1 PNG; enough to validate the provider boundary/artifact pipeline offline.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZJ7sAAAAASUVORK5CYII=")
    key = "sk-openai-test-never-return"
    monkeypatch.setenv("OPENAI_API_KEY", key)

    class ImageDatum:
        b64_json = base64.b64encode(png).decode()
        url = None
    class Images:
        async def generate(self, **kwargs):
            assert kwargs["n"] == 1
            return types.SimpleNamespace(data=[ImageDatum()])
    class FakeClient:
        def __init__(self, api_key):
            assert api_key == key
            self.images = Images()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=FakeClient))

    runtime = AgentRuntime(load_config(tmp_path))
    runtime.projects.create(name="demo")

    async def go():
        result = await runtime.tools.execute("creative_image_concepts", {
            "brief":"Landing page", "project":"demo", "ask_human":False,
            "directions":[{"label":"Clinical","prompt":"white blue"},{"label":"Warm","prompt":"cream green"}],
        })
        assert result.ok and result.data["provider"] == "openai" and result.data["count"] == 2
        assert key not in json.dumps(result.data)
        assert key not in result.content
        assert runtime.redactor.redact("key=" + key) == "key=[REDACTED]"
        for option in result.data["options"]:
            assert runtime.artifacts.path_for(option["artifact_id"]).read_bytes() == png
    asyncio.run(go())


def test_resolved_task_resumes_same_durable_run(tmp_path: Path):
    async def go():
        provider = FinalProvider()
        runtime = AgentRuntime(load_config(tmp_path), provider=provider)
        run = runtime.durable_runs.start("redesign onboarding", project="demo")
        task = runtime.human_tasks.create(
            kind="choose", title="Choose design", project="demo", run_id=run["id"],
            options=[{"id":"a","label":"A"},{"id":"b","label":"B","artifact_id":"art_deadbeefdeadbeef"}],
            timeout_policy="AUTO_DECIDE", blocking=True, default_option="a",
            resume_instruction="Implement selected design.",
        )
        runtime.human_tasks.resolve(task["id"], option_id="a")
        out = await runtime.resume_human_task(task["id"])
        assert out == "continued"
        after = runtime.durable_runs.get(run["id"])
        assert after["id"] == run["id"] and after["resume_count"] >= 1
        assert runtime.human_tasks.get(task["id"])["resumed_at"]
        flattened = json.dumps(provider.prompts)
        assert "Original objective: redesign onboarding" in flattened
        assert "Selected option: A (a)" in flattened
    asyncio.run(go())


def test_v6_tools_skills_and_ui_are_exposed(tmp_path: Path):
    runtime = AgentRuntime(load_config(tmp_path))
    names = set(runtime.tools.names())
    assert {"human_task_create","human_task_list","durable_run_list","durable_run_status","creative_status","creative_svg_concepts","creative_image_concepts"}.issubset(names)
    assert runtime.security.capabilities.has("human.task")
    assert runtime.security.capabilities.has("creative.generate")
    assert "collaborative-autonomy" in runtime.skills.resolve("ask me to choose and resume the durable run").selected
    assert "creative-studio" in runtime.skills.resolve("generate visual mockup choices for the UI design").selected
    for text in ["My Tasks", "taskBadge", "AUTO_DECIDE", "Let AI decide now", "Configure", "Completed"]:
        assert text in HTML or text in JS
    assert "/api/human-tasks" in JS and "/api/human-task-resolve" in JS
    assert JS.count("setTimeout(poll") == 1
    assert "setInterval(" not in JS


def test_my_tasks_api_and_creative_artifact_route(tmp_path: Path):
    runtime = AgentRuntime(load_config(tmp_path))
    runtime.projects.create(name="demo")
    task = runtime.human_tasks.create(kind="answer", title="Pick a domain", project="demo", timeout_policy="DEFER", blocking=False)
    svg = runtime.creative.generate_svg(brief="demo", directions=[{"label":"A"},{"label":"B"}], project="demo")
    aid = svg["options"][0]["artifact_id"]

    loop = asyncio.new_event_loop()
    web = LocalWebApp(runtime, loop, port=0)
    base = web.start()
    try:
        parsed = urlparse(base); token = parse_qs(parsed.query)["token"][0]
        with urllib.request.urlopen(f"http://127.0.0.1:{parsed.port}/api/human-tasks?token={token}&state=open", timeout=5) as resp:
            data = json.loads(resp.read().decode())
        assert any(x["id"] == task["id"] for x in data["tasks"])
        with urllib.request.urlopen(f"http://127.0.0.1:{parsed.port}/api/artifact/{aid}?token={token}", timeout=5) as resp:
            payload = resp.read(); content_type = resp.headers.get("Content-Type", "")
        assert payload.startswith(b"<svg") and "image/svg+xml" in content_type
    finally:
        web.close(); loop.close()


def test_autonomy_scheduler_auto_decides_and_resumes_waiting_run(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        cfg.autonomy.scheduler_interval_s = 1
        provider = FinalProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        run = runtime.durable_runs.start("pick and implement dashboard direction", project="demo")
        task = runtime.human_tasks.create(
            kind="choose", title="Direction", project="demo", run_id=run["id"],
            options=[{"id":"a","label":"A"},{"id":"b","label":"B","recommended":True}],
            timeout_policy="AUTO_DECIDE", timeout_seconds=5, blocking=True,
            resume_instruction="Continue with selected direction.",
        )
        runtime.human_tasks._tasks[task["id"]]["deadline_at"] = "2000-01-01T00:00:00+00:00"
        runner = asyncio.create_task(runtime.autonomy_loop())
        try:
            for _ in range(30):
                await asyncio.sleep(0.1)
                current = runtime.human_tasks.get(task["id"])
                if current.get("resumed_at"):
                    break
            current = runtime.human_tasks.get(task["id"])
            assert current["status"] == "auto_resolved"
            assert current["response"]["option_id"] == "b"
            assert current["resumed_at"]
            assert runtime.durable_runs.get(run["id"])["status"] == "completed"
            assert provider.prompts
        finally:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
    asyncio.run(go())
