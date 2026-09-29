from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.state.checkpoints import CheckpointStore
from term5.state.transactions import StaleWriteError, TransactionManager
from term5.workspace.graph import WorkspaceGraph
from term5.brain.parallel import ParallelCortex


class StaticProvider(ModelProvider):
    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        return ChatResult(content="ok")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "    return 1\n", {"prompt_tokens": 2, "completion_tokens": 2}


def test_transaction_rejects_stale_sha256(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("x = 1\n")
    tx = TransactionManager(tmp_path / ".term5", PathGuard(tmp_path))
    expected = tx.fingerprint("x.py")
    p.write_text("x = 2\n")
    with pytest.raises(StaleWriteError):
        tx.write_one("x.py", "x = 3\n", expected_sha256=expected)
    assert p.read_text() == "x = 2\n"


def test_recover_open_group_rolls_back_partial_crash(tmp_path: Path):
    (tmp_path / "a.txt").write_text("old-a")
    (tmp_path / "b.txt").write_text("old-b")
    tx = TransactionManager(tmp_path / ".term5", PathGuard(tmp_path))
    group = tx.begin_group(["a.txt", "b.txt"])
    by_path = {snap.target.name: snap for snap in group.snapshots}
    tx.atomic_write(by_path["a.txt"], "new-a")
    tx.atomic_write(by_path["b.txt"], "new-b")
    # Simulate process death: no commit/rollback call.
    recovered = TransactionManager(tmp_path / ".term5", PathGuard(tmp_path)).recover_open()
    assert group.transaction_id in recovered
    assert (tmp_path / "a.txt").read_text() == "old-a"
    assert (tmp_path / "b.txt").read_text() == "old-b"


def test_checkpoint_roundtrip_and_runtime_recovery(tmp_path: Path):
    store = CheckpointStore(tmp_path / ".term5")
    messages = [{"role": "system", "content": "s"}, {"role": "assistant", "content": "done"}]
    store.save(phase="turn_start", messages=messages, prompt="retry me", session_name="work")
    cp = store.load()
    assert cp and cp.prompt == "retry me" and cp.phase == "turn_start"

    cfg = load_config(tmp_path)
    rt = AgentRuntime(cfg, provider=StaticProvider(), recover_checkpoint=True)
    assert rt.recovered_checkpoint
    assert rt.recovery_prompt == "retry me"
    assert rt.messages[-1]["content"] == "done"


def test_js_ts_workspace_graph_relations(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "util.ts").write_text("export function value() { return 1 }\n")
    (tmp_path / "src" / "main.ts").write_text("import { value } from './util'\nexport const result = value()\n")
    (tmp_path / "tests" / "main.test.ts").write_text("import { result } from '../src/main'\nconsole.log(result)\n")
    graph = WorkspaceGraph(PathGuard(tmp_path), tmp_path / ".term5")
    stats = graph.refresh()
    assert stats["js_ts_files"] == 3
    util = graph.file_relations("src/util.ts")
    assert "src/main.ts" in util["imported_by"]
    main = graph.file_relations("src/main.ts")
    assert "tests/main.test.ts" in main["imported_by"]
    assert "tests/main.test.ts" in main["likely_tests"]


def test_parallel_worker_retry_succeeds_on_second_attempt():
    class Flaky(ModelProvider):
        def __init__(self): self.calls = 0
        async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return ChatResult(content="recovered")
        async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
            return "", {}

    async def go():
        provider = Flaky()
        cortex = ParallelCortex(provider, max_total=2, max_high=1, timeout_s=2, retries=1)
        result = await cortex._one("w", "do work", ReasoningMode.HIGH)
        assert result.status == "success"
        assert result.conclusion == "recovered"
        assert result.metrics["attempts"] == 2
    asyncio.run(go())


def test_fim_rejects_external_change_between_generation_and_commit(tmp_path: Path):
    class RacingProvider(ModelProvider):
        def __init__(self, target: Path): self.target = target
        async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
            return ChatResult(content="ok")
        async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
            # External editor changes the file while FIM is generating.
            self.target.write_text("def x():\n    return 99\n")
            return "    return 1\n", {"prompt_tokens": 2, "completion_tokens": 2}

    async def go():
        target = tmp_path / "x.py"
        target.write_text("def x():\n    pass\n")
        cfg = load_config(tmp_path)
        rt = AgentRuntime(cfg, provider=RacingProvider(target))
        result = await rt.fim_edit("x.py", 2, 2, "return one")
        assert not result.ok
        assert "stale write rejected" in result.content.lower()
        assert "return 99" in target.read_text()
    asyncio.run(go())
