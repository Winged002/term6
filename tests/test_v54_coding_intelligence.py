from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

from term5.browser import BrowserRuntime
from term5.brain.improvement import ExistingAppImprover
from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, Usage
from term5.providers.base import ModelProvider
from term5.providers.deepseek import DeepSeekProvider
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.state.artifacts import ArtifactStore
from term5.workspace.application import ExistingApplicationGraph


class InvalidPlanProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        return ChatResult(content="not json", usage=Usage(prompt_tokens=3, completion_tokens=1))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def test_application_graph_maps_routes_templates_css_and_tests(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "from flask import Flask, render_template\napp=Flask(__name__)\n"
        "@app.route('/')\ndef home(): return render_template('home.html')\n"
        "@app.route('/settings')\ndef settings(): return render_template('settings.html')\n",
        encoding="utf-8",
    )
    t = tmp_path / "templates"
    t.mkdir()
    (t / "base.html").write_text('<nav class="shell nav"></nav>{% block body %}{% endblock %}', encoding="utf-8")
    (t / "home.html").write_text('{% extends "base.html" %}{% block body %}<h1 class="hero">Home</h1>{% endblock %}', encoding="utf-8")
    (t / "settings.html").write_text('{% extends "base.html" %}{% block body %}<form class="card"><button>Save</button></form>{% endblock %}', encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.css").write_text('.shell{display:grid}.hero{font-size:2rem}.card{padding:1rem}', encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text('def test_ok(): assert True\n', encoding="utf-8")

    amap = ExistingApplicationGraph(PathGuard(tmp_path)).build()
    routes = {r.route: r.template for r in amap.routes}
    assert routes["/"] == "templates/home.html"
    assert routes["/settings"] == "templates/settings.html"
    assert "templates/base.html" in amap.shared_templates
    assert "static/app.css" in amap.stylesheets
    assert "tests/test_app.py" in amap.tests


def test_improvement_planner_fallback_requires_app_wide_coverage(tmp_path: Path):
    async def go():
        (tmp_path / "app.py").write_text(
            "from flask import Flask, render_template\napp=Flask(__name__)\n"
            "@app.route('/')\ndef a(): return render_template('a.html')\n"
            "@app.route('/b')\ndef b(): return render_template('b.html')\n"
            "@app.route('/c')\ndef c(): return render_template('c.html')\n",
            encoding="utf-8",
        )
        td = tmp_path / "templates"; td.mkdir()
        for n in "abc": (td / f"{n}.html").write_text(f"<h1>{n}</h1>", encoding="utf-8")
        amap = ExistingApplicationGraph(PathGuard(tmp_path)).build()
        imp = ExistingAppImprover(InvalidPlanProvider(), prefer_before_after=True)
        assert imp.should_plan("audit every page and make the UI polished")
        plan = await imp.plan("audit every page and make the UI polished", amap, amap.context_text())
        assert set(plan.surfaces) >= {"/", "/b", "/c"}
        assert any("before/after" in x.lower() for x in plan.verification)
        assert any("do not declare" in x.lower() for x in plan.stop_conditions)
    asyncio.run(go())


def test_binary_screenshot_artifacts_are_not_read_as_text(tmp_path: Path):
    store = ArtifactStore(tmp_path / ".term5")
    ref = store.put_bytes(b"\x89PNG\r\n\x1a\nabc", kind="screenshot", name="ui", extension=".png")
    assert store.path_for(ref.id).suffix == ".png"
    assert store.metadata(ref.id)["bytes"] == 11
    try:
        store.read(ref.id)
        assert False, "binary artifact should not be exposed as text"
    except TypeError:
        pass


def test_browser_guard_allows_loopback_but_blocks_public_by_default(tmp_path: Path):
    cfg = load_config(tmp_path)
    browser = BrowserRuntime(cfg.browser, ArtifactStore(tmp_path / ".term5"))
    assert browser._validate_url("http://127.0.0.1:8000/") == "127.0.0.1"
    try:
        browser._validate_url("https://example.com/")
        assert False, "public target should require explicit permission"
    except PermissionError:
        pass


def test_runtime_registers_coding_intelligence_tools(tmp_path: Path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg, provider=InvalidPlanProvider())
    names = set(runtime.tools.names())
    assert {"application_map", "improvement_plan", "improvement_plan_current"} <= names
    assert {"browser_status", "browser_audit_pages", "browser_screenshot"} <= names
    assert {"vision_inspect", "vision_compare"} <= names


class FakeCompletions:
    def __init__(self, owner):
        self.owner = owner
    async def create(self, **kwargs):
        self.owner.calls.append(kwargs)
        msg = types.SimpleNamespace(content="visual analysis", reasoning_content="", tool_calls=None)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")],
            usage=types.SimpleNamespace(prompt_tokens=20, completion_tokens=5),
            model=kwargs.get("model"),
        )


class FakeVisionClient:
    instances = []
    def __init__(self, api_key, base_url):
        self.base_url = base_url
        self.calls = []
        self.chat = types.SimpleNamespace(completions=FakeCompletions(self))
        self.completions = types.SimpleNamespace(create=None)
        FakeVisionClient.instances.append(self)


def test_deepseek_vision_uses_local_data_url_payload(tmp_path: Path, monkeypatch):
    fake_module = types.ModuleType("openai")
    fake_module.AsyncOpenAI = FakeVisionClient
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    FakeVisionClient.instances.clear()

    async def go():
        cfg = load_config(tmp_path)
        cfg.api_key = "test-key"
        cfg.vision.model = "deepseek-vision-test"
        provider = DeepSeekProvider(cfg)
        out = await provider.vision([(b"pngbytes", "image/png")], "inspect", max_tokens=1024)
        assert out.content == "visual analysis"
    asyncio.run(go())
    client = FakeVisionClient.instances[0]
    call = client.calls[0]
    content = call["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "inspect"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert call["model"] == "deepseek-vision-test"

class CompletionGateProvider(ModelProvider):
    def __init__(self):
        self.calls = []
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        system = str(messages[0].get("content") or "") if messages else ""
        if "existing-application improvement planner" in system:
            return ChatResult(content='{"objective":"improve","surfaces":["/","/settings","/tasks"],"shared_changes":["shell"],"page_changes":["pages"],"code_quality_changes":[],"visible_impact":["clear"],"verification":["browser"],"batches":[["base.html","app.css"]],"stop_conditions":["verify"]}')
        return ChatResult(content="reviewed" if any("completion gate" in str(m.get("content") or "") for m in messages) else "initial complete")
    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens":0,"completion_tokens":0}


def test_broad_improvement_gets_completion_review_round(tmp_path: Path):
    async def go():
        (tmp_path / "app.py").write_text("from flask import Flask\napp=Flask(__name__)\n@app.route('/')\ndef x(): return 'x'\n", encoding="utf-8")
        cfg = load_config(tmp_path)
        cfg.executive.auto_plan = False
        cfg.skills.enabled = False
        p = CompletionGateProvider()
        rt = AgentRuntime(cfg, provider=p)
        out = await rt.run_turn("improve the existing app UI and make every page polished")
        assert out == "reviewed"
        assert any(any("completion gate" in str(m.get("content") or "") for m in call) for call in p.calls)
        assert rt.improvement_plan_runs == 1
    asyncio.run(go())
