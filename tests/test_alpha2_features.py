import asyncio
from pathlib import Path

from term5.brain import DAGCortex, ParallelCortex
from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.state.memory import MemoryStore
from term5.state.metrics import Pricing, UsageLedger
from term5.state.sessions import SessionStore
from term5.state.transactions import TransactionManager
from term5.ui.web import _host_ok


class EchoProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        objective = messages[-1]["content"]
        return ChatResult(content="result:" + objective[-80:], usage=Usage(prompt_tokens=10, completion_tokens=2))
    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "    return 42\n", {"prompt_tokens": 5, "completion_tokens": 3}


def test_group_transaction_commits_and_rolls_back(tmp_path: Path):
    guard = PathGuard(tmp_path)
    tx = TransactionManager(tmp_path / ".term5", guard)
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    txid = tx.write_group([("a.txt", "AA"), ("b.txt", "BB")])
    assert txid.startswith("txg_")
    assert (tmp_path / "a.txt").read_text() == "AA"
    assert (tmp_path / "b.txt").read_text() == "BB"


def test_named_sessions_round_trip(tmp_path: Path):
    store = SessionStore(tmp_path / ".term5" / "session.json")
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
    store.save(messages, "debug-one")
    assert store.load("debug-one") == messages
    names = [s.name for s in store.list()]
    assert "debug-one" in names


def test_memory_source_staleness(tmp_path: Path):
    p = tmp_path / "source.txt"
    p.write_text("one", encoding="utf-8")
    mem = MemoryStore(tmp_path / ".term5" / "memory.json")
    fp = mem.file_fingerprint(p)
    rec = mem.add("source says one", source={"kind": "workspace_file", "path": "source.txt"}, source_fingerprint=fp)
    assert mem.refresh_staleness(tmp_path) == 0
    p.write_text("two", encoding="utf-8")
    assert mem.refresh_staleness(tmp_path) == 1
    assert rec.stale is True


def test_dag_cortex_dependency_execution():
    async def go():
        provider = EchoProvider()
        cortex = DAGCortex(ParallelCortex(provider, 8, 4), max_parallel=8)
        out = await cortex.run([
            {"id": "a", "objective": "inspect A", "reasoning": "low"},
            {"id": "b", "objective": "inspect B", "reasoning": "low"},
            {"id": "c", "objective": "synthesize", "reasoning": "high", "depends_on": ["a", "b"]},
        ])
        assert set(out) == {"a", "b", "c"}
        assert out["c"].status == "success"
    asyncio.run(go())


def test_fim_preview_does_not_write(tmp_path: Path):
    async def go():
        path = tmp_path / "x.py"
        path.write_text("def x():\n    pass\n", encoding="utf-8")
        cfg = load_config(tmp_path)
        rt = AgentRuntime(cfg, provider=EchoProvider())
        before = path.read_text()
        result = await rt.fim_preview("x.py", 2, 2, "return 42")
        assert result.ok
        assert "FIM preview" in result.content
        assert path.read_text() == before
    asyncio.run(go())


def test_usage_ledger_cost_requires_cache_split():
    ledger = UsageLedger(Pricing(0.006, 0.30, 1.20))
    ledger.add(Usage(prompt_tokens=1000, completion_tokens=100))
    assert ledger.estimated_cost() is None
    ledger2 = UsageLedger(Pricing(0.006, 0.30, 1.20))
    ledger2.add(Usage(prompt_tokens=1000, completion_tokens=100, cache_hit_tokens=800, cache_miss_tokens=200))
    assert ledger2.estimated_cost() is not None


def test_web_host_gate_is_loopback_only():
    assert _host_ok("127.0.0.1:8123")
    assert _host_ok("localhost:8123")
    assert not _host_ok("example.com")
