from __future__ import annotations

import asyncio
from pathlib import Path

from term5.config import load_config
from term5.context import ContextCompiler
from term5.events import EventBus
from term5.models import ChatResult, ReasoningMode, ToolCall
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.ui.web import HTML
from term5.ui.web_assets import JS


class EndlessToolProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        return ChatResult(tool_calls=[ToolCall("pending_1", "read_file", {"path": "x.txt"})])

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {}


class RecoveryProvider(ModelProvider):
    def __init__(self):
        self.messages = None

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.messages = messages
        return ChatResult(content="recovered")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {}


def test_event_bus_sequence_history():
    async def go():
        bus = EventBus(history_limit=100)
        a = await bus.emit("one", x=1)
        b = await bus.emit("two", x=2)
        assert b.seq == a.seq + 1
        rows = bus.snapshot(after=a.seq)
        assert [r.type for r in rows] == ["two"]
    asyncio.run(go())


def test_protocol_repair_fills_missing_results_and_drops_orphans():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "x", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "y", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
        {"role": "user", "content": "next"},
        {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
    ]
    repaired, count = ContextCompiler.repair_tool_protocol(messages)
    assert count == 2
    assistant_i = next(i for i, m in enumerate(repaired) if m.get("role") == "assistant")
    assert repaired[assistant_i + 1]["tool_call_id"] == "a"
    assert repaired[assistant_i + 2]["tool_call_id"] == "b"
    assert "protocol recovery" in repaired[assistant_i + 2]["content"]
    assert not any(m.get("tool_call_id") == "orphan" for m in repaired)


def test_optional_iteration_limit_records_synthetic_tool_result(tmp_path: Path):
    async def go():
        (tmp_path / "x.txt").write_text("x")
        cfg = load_config(tmp_path)
        cfg.scheduler.max_tool_iterations = 1
        runtime = AgentRuntime(cfg, provider=EndlessToolProvider())
        out = await runtime.run_turn("read x")
        assert "iteration limit" in out.lower()
        # v5.3 records the skipped call in telemetry/episode memory, then removes
        # the raw tool exchange from future working context.
        repaired, count = runtime.context.repair_tool_protocol(runtime.messages)
        assert count == 0
        snap = runtime.activity_snapshot()
        assert any(e["type"] == "tool.skipped" for e in snap["events"])
        assert not any(m.get("tool_calls") for m in runtime.messages)
        assert runtime.episodes.count() == 1
    asyncio.run(go())


def test_loaded_poisoned_session_is_repaired_before_provider(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        provider = RecoveryProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        runtime.messages = [
            {"role": "system", "content": runtime.context.system_prompt},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "broken", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
        ]
        out = await runtime.run_turn("try again", use_tools=False)
        assert out == "recovered"
        assert runtime.protocol_repairs >= 1
        ids = [m.get("tool_call_id") for m in provider.messages if m.get("role") == "tool"]
        assert "broken" in ids
    asyncio.run(go())


def test_web_ui_contains_live_execution_observability():
    for text in ["Live Execution", "Execution Timeline"]:
        assert text in HTML
    for text in ["/api/activity", "loop_suspected", "Protocol repairs"]:
        assert text in JS


class FortyFiveThenDoneProvider(ModelProvider):
    def __init__(self):
        self.n = 0
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.n += 1
        if self.n <= 45:
            return ChatResult(tool_calls=[ToolCall(f"call_{self.n}", "read_file", {"path":"x.txt","start_line":self.n,"max_lines":1})])
        return ChatResult(content="finished after forty-five tool rounds")
    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {}

def test_default_tool_iterations_are_unlimited(tmp_path: Path):
    async def go():
        (tmp_path / "x.txt").write_text("\
".join(str(i) for i in range(100)))
        cfg = load_config(tmp_path)
        assert cfg.scheduler.max_tool_iterations == 0
        runtime = AgentRuntime(cfg, provider=FortyFiveThenDoneProvider())
        out = await runtime.run_turn("keep reading until done")
        assert out == "finished after forty-five tool rounds"
        snap = runtime.activity_snapshot()
        assert snap["activity"]["max_iterations"] is None
        assert not any(e["type"] == "tool.skipped" and e["data"].get("reason") == "iteration_limit" for e in snap["events"])
    asyncio.run(go())

def test_ui_exposes_visual_evidence_pipeline():
    for text in ["visualEvidence", "Vision findings", "unlimited", "/api/artifact/"]:
        assert text in JS


def test_unlimited_mode_still_aborts_obvious_repeated_tool_loop(tmp_path: Path):
    async def go():
        (tmp_path / "x.txt").write_text("x")
        cfg = load_config(tmp_path)
        cfg.scheduler.max_tool_iterations = 0
        cfg.scheduler.loop_abort_repeats = 4
        runtime = AgentRuntime(cfg, provider=EndlessToolProvider())
        out = await runtime.run_turn("loop")
        assert "repeated identical tool loop" in out.lower()
        snap = runtime.activity_snapshot()
        assert any(e["type"] == "loop.aborted" for e in snap["events"])
        assert snap["activity"]["loop_suspected"] is True
    asyncio.run(go())


def test_activity_tracks_browser_screenshot_and_vision_pipeline(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        runtime = AgentRuntime(cfg, provider=RecoveryProvider())
        await runtime.events.emit("turn.started", reasoning="high")
        await runtime.events.emit("browser.screenshot.completed", artifact_id="art_0123456789abcdef", url="http://127.0.0.1:8000/", viewport={"width":1440,"height":1000}, bytes=1234)
        await runtime.events.emit("vision.started", model="vision-model", artifact_ids=["art_0123456789abcdef"])
        await runtime.events.emit("vision.completed", model="vision-model", artifact_ids=["art_0123456789abcdef"], analysis_preview="The page hierarchy is weak; strengthen the primary action.", elapsed_s=1.2)
        snap = runtime.activity_snapshot()
        visual = snap["activity"]["visual"]
        assert visual["vision_state"] == "complete"
        assert visual["vision_model"] == "vision-model"
        assert visual["vision_calls"] == 1
        assert visual["screenshots"][-1]["artifact_id"] == "art_0123456789abcdef"
        assert "hierarchy" in visual["last_vision_preview"]
    asyncio.run(go())
