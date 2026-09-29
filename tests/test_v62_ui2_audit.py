from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from pathlib import Path

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.ui.web_assets import HTML, CSS, JS


class QuietProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        return ChatResult(content="ok")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


class AuditParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.views = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if "id" in data:
            self.ids.append(data["id"])
        if data.get("data-view"):
            self.views.append(data["data-view"])


def test_all_workbench_pages_have_unique_ids_and_are_in_audited_shell():
    parser = AuditParser(); parser.feed(HTML)
    assert len(parser.ids) == len(set(parser.ids))
    expected = {"chat","team","inbox","my-tasks","project","configuration","operations","files","tool-logs","runs","apps","deployments","product","memory"}
    assert expected.issubset(set(parser.views))
    for marker in [
        ".page-view:not(.workspace-view)", ".stats{", ".grid{", ".project-columns", ".memory-columns",
        ".config-layout", ".inbox-layout", "overflow-wrap:anywhere", "@media(max-width:900px)",
    ]:
        assert marker in CSS


def test_workspace_projects_are_draggable_collision_aware_and_persistent():
    for marker in [
        "data-drag-project-pos", "resolveDropPosition", "collides=(x,y)", "pointerdown", "pointermove", "pointerup",
        "/api/workspace-layout", "persistWorkspaceLayout", "resetWorkspaceLayout", "workspacePositions",
    ]:
        assert marker in (HTML + JS)
    assert ".project-node.dragging" in CSS
    assert "cursor:grab" in CSS


def test_workspace_layout_persists_through_orchestrator_store(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        runtime = AgentRuntime(cfg, provider=QuietProvider())
        runtime.projects.create(name="sso", path="projects/sso", init_git=False, make_active=True)
        runtime.agents.sync_projects(refresh=True)
        layout = await runtime.agents.set_workspace_position("sso", 812.5, 463.0)
        assert layout["sso"] == {"x": 812.5, "y": 463.0}
        runtime2 = AgentRuntime(load_config(tmp_path), provider=QuietProvider())
        assert runtime2.agents.workspace_layout()["sso"] == {"x": 812.5, "y": 463.0}
        await runtime2.agents.reset_workspace_layout()
        assert runtime2.agents.workspace_layout() == {}
    asyncio.run(go())


def test_inter_agent_responses_live_sync_without_manual_refresh():
    assert "function syncLiveMessages" in JS
    assert "if(['team','inbox'].includes(state.route))syncLiveMessages()" in JS
    assert "response-preview" in JS
    assert "message-response" in JS
    assert "messageSignature" in JS
    # Keep a single scheduler loop: live message sync piggybacks on activity polling.
    assert JS.count("setTimeout(poll") == 1


def test_workspace_footer_explains_project_dragging():
    assert "Drag projects to arrange" in HTML
    assert 'id="resetWorkspaceLayout"' in HTML
