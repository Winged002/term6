import asyncio
import sys
import types
from pathlib import Path

from term5.config import load_config
from term5.models import ReasoningMode
from term5.providers.deepseek import DeepSeekProvider


class FakeCompletions:
    def __init__(self, owner, kind):
        self.owner = owner
        self.kind = kind

    async def create(self, **kwargs):
        self.owner.calls.append((self.kind, kwargs))
        if self.kind == "fim":
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(text="middle")],
                usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )
        msg = types.SimpleNamespace(content="ok", reasoning_content="r", tool_calls=None)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")],
            usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=4,
                                        prompt_cache_hit_tokens=6, prompt_cache_miss_tokens=4),
            model="deepseek-flash",
        )


class FakeClient:
    instances = []
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url
        self.calls = []
        kind = "fim" if base_url.endswith("/beta") else "chat"
        self.chat = types.SimpleNamespace(completions=FakeCompletions(self, "chat"))
        self.completions = FakeCompletions(self, "fim")
        FakeClient.instances.append(self)


def test_deepseek_reasoning_and_fim_contract(tmp_path: Path, monkeypatch):
    fake_module = types.ModuleType("openai")
    fake_module.AsyncOpenAI = FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    FakeClient.instances.clear()

    async def go():
        cfg = load_config(tmp_path)
        cfg.api_key = "test-key"
        p = DeepSeekProvider(cfg)
        high = await p.chat([{"role": "user", "content": "x"}], reasoning=ReasoningMode.HIGH)
        none = await p.chat([{"role": "user", "content": "x"}], reasoning=ReasoningMode.NONE)
        text, usage = await p.fim("prefix", "suffix", max_tokens=99999)
        assert high.content == "ok"
        assert high.usage.cache_hit_tokens == 6
        assert none.content == "ok"
        assert text == "middle"
        assert usage["completion_tokens"] == 2

    asyncio.run(go())
    chat_client = next(c for c in FakeClient.instances if not c.base_url.endswith("/beta"))
    fim_client = next(c for c in FakeClient.instances if c.base_url.endswith("/beta"))
    high_kwargs = chat_client.calls[0][1]
    none_kwargs = chat_client.calls[1][1]
    fim_kwargs = fim_client.calls[0][1]
    assert high_kwargs["reasoning_effort"] == "high"
    assert high_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in high_kwargs
    assert none_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert fim_kwargs["max_tokens"] == 4096
    assert fim_kwargs["suffix"] == "suffix"
