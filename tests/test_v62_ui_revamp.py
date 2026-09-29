from __future__ import annotations

import asyncio
from pathlib import Path

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, ToolCall, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.ui.web_assets import HTML, CSS, JS


class LongOwnerProvider(ModelProvider):
    def __init__(self, loops: int = 30):
        self.loops = loops
        self.owner_calls = 0

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        system = str((messages[0] if messages else {}).get("content") or "")
        if system.startswith("You are the persistent Project Owner Agent"):
            self.owner_calls += 1
            if self.owner_calls <= self.loops:
                return ChatResult(
                    tool_calls=[ToolCall(f"status-{self.owner_calls}", "agent_list", {})],
                    usage=Usage(prompt_tokens=3, completion_tokens=1),
                )
            return ChatResult(content="owner finished after extended autonomous work", usage=Usage(prompt_tokens=3, completion_tokens=2))
        return ChatResult(content="central")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def test_project_owner_iteration_cap_is_removed(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        # Compatibility field may still be populated by an old config, but v6.2-ui ignores it.
        cfg.agents.owner_max_iterations = 2
        provider = LongOwnerProvider(loops=30)
        runtime = AgentRuntime(cfg, provider=provider)
        runtime.projects.create(name="demo", path="projects/demo", init_git=False, make_active=True)
        runtime.agents.sync_projects(refresh=True)
        task = runtime.agents.delegate("demo", "Perform extended autonomous work")
        deadline = asyncio.get_running_loop().time() + 8
        while asyncio.get_running_loop().time() < deadline:
            row = runtime.agents.store.get_task(task["id"])
            if row and row["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.02)
        row = runtime.agents.store.get_task(task["id"])
        assert row is not None and row["status"] == "completed", row
        assert provider.owner_calls == 31
        assert "extended autonomous work" in row["result"]
    asyncio.run(go())


def test_ui_revamp_uses_compact_icon_navigation_and_hover_labels():
    assert 'class="icon-dock"' in HTML
    assert 'data-tip="Workspace"' in HTML
    assert 'data-tip="Inbox"' in HTML
    assert 'data-tip="History"' in HTML
    assert 'Search, jump to…' in HTML
    assert '<div class="work-island"' not in HTML
    assert '.icon-dock button[data-tip]::after' in CSS
    assert '.compact-rail' in CSS
    assert '.history-popover' in CSS


def test_workspace_revamp_uses_collision_aware_layout_and_progressive_detail():
    assert 'function projectLayout(agents)' in JS
    assert 'const stepX=360,stepY=280' in JS
    assert 'collides=(x,y)' in JS
    assert 'workspacePositions' in JS
    assert 'data-label="${esc(a.project)}"' in JS
    assert 'state.workspaceZoom>95' in JS
    assert '.spatial-canvas.zoom-low .project-node' in CSS
    assert '.project-agent-chip' in CSS
    assert '.project-symbol' in CSS


def test_ui_revamp_keeps_direct_manipulation_and_command_dock():
    for text in [
        'data-drag-task', '/api/agent-task-move', '/api/agent-dependency',
        'globalCommandDock', 'selectionChips', 'workspaceInspector',
        'timelineOpen', 'historyPopover',
    ]:
        assert text in (HTML + JS)


def test_owner_runtime_has_no_hard_iteration_failure_string():
    source = (Path(__file__).resolve().parents[1] / "term5/agents/owner.py").read_text(encoding="utf-8")
    assert "hit owner_max_iterations" not in source
    assert "range(1, max_iter" not in source
    assert "while True" in source
