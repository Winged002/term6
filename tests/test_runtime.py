import asyncio
from pathlib import Path

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, ToolCall, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime


class FakeProvider(ModelProvider):
    def __init__(self):
        self.calls = 0
        self.seen = []

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if self.calls == 1 and tools:
            return ChatResult(
                reasoning_content="internal",
                tool_calls=[ToolCall("call_1", "read_file", {"path": "hello.txt"})],
                usage=Usage(prompt_tokens=10, completion_tokens=5),
            )
        return ChatResult(content="done", usage=Usage(prompt_tokens=5, completion_tokens=1))

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "replacement\n", {"prompt_tokens": 1, "completion_tokens": 1}


def test_runtime_tool_loop_preserves_reasoning_content(tmp_path: Path):
    async def go():
        (tmp_path / "hello.txt").write_text("hello\n")
        cfg = load_config(tmp_path)
        provider = FakeProvider()
        runtime = AgentRuntime(cfg, provider=provider)
        out = await runtime.run_turn("read hello.txt")
        assert out == "done"
        # reasoning/tool protocol is preserved while the tool loop is active
        assert any(m.get("reasoning_content") == "internal" for m in provider.seen[1] if m.get("role") == "assistant")
        assert any(m.get("role") == "tool" for m in provider.seen[1])
        # completed-turn GC evicts hidden reasoning/tool traces from future context
        assert not any(m.get("reasoning_content") for m in runtime.messages)
        assert not any(m.get("role") == "tool" for m in runtime.messages)
        assert runtime.episodes.count() == 1
    asyncio.run(go())
