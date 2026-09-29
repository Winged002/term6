import asyncio
import json
from pathlib import Path

import pytest

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.state.checkpoints import CheckpointCorruptionError, CheckpointStore
from term5.state.sessions import SessionCorruptionError, SessionStore
from term5.state.transactions import StaleWriteError, TransactionHistoryError, TransactionManager


class BlockingProvider(ModelProvider):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.started.set()
        await self.release.wait()
        return ChatResult(content="done")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "pass\n", {"prompt_tokens": 0, "completion_tokens": 0}


def test_transaction_undo_redo_group_and_new_file(tmp_path: Path):
    guard = PathGuard(tmp_path)
    tx = TransactionManager(tmp_path / ".term5", guard)
    (tmp_path / "a.txt").write_text("before", encoding="utf-8")
    txid = tx.write_group([("a.txt", "after"), ("b.txt", "new")])
    assert (tmp_path / "a.txt").read_text() == "after"
    assert (tmp_path / "b.txt").read_text() == "new"

    undone, changed = tx.undo_last()
    assert undone == txid
    assert set(changed) == {"a.txt", "b.txt"}
    assert (tmp_path / "a.txt").read_text() == "before"
    assert not (tmp_path / "b.txt").exists()

    redone, changed2 = tx.redo_last()
    assert redone == txid
    assert set(changed2) == {"a.txt", "b.txt"}
    assert (tmp_path / "a.txt").read_text() == "after"
    assert (tmp_path / "b.txt").read_text() == "new"


def test_undo_refuses_external_edit(tmp_path: Path):
    tx = TransactionManager(tmp_path / ".term5", PathGuard(tmp_path))
    (tmp_path / "x.txt").write_text("one", encoding="utf-8")
    tx.write_one("x.txt", "two")
    (tmp_path / "x.txt").write_text("external", encoding="utf-8")
    with pytest.raises(StaleWriteError):
        tx.undo_last()
    assert (tmp_path / "x.txt").read_text() == "external"


def test_new_commit_invalidates_redo_branch(tmp_path: Path):
    tx = TransactionManager(tmp_path / ".term5", PathGuard(tmp_path))
    (tmp_path / "x.txt").write_text("one", encoding="utf-8")
    tx.write_one("x.txt", "two")
    tx.undo_last()
    tx.write_one("x.txt", "three")
    with pytest.raises(TransactionHistoryError):
        tx.redo_last()
    assert (tmp_path / "x.txt").read_text() == "three"


def test_session_v2_compatibility_and_corruption_is_loud(tmp_path: Path):
    path = tmp_path / ".term5" / "session.json"
    path.parent.mkdir(parents=True)
    messages = [{"role": "system", "content": "x"}]
    path.write_text(json.dumps({"format": 2, "messages": messages}), encoding="utf-8")
    store = SessionStore(path, runtime_version="5.0.0-rc1")
    assert store.load() == messages
    store.save(messages)
    raw = json.loads(path.read_text())
    assert raw["format"] == 3
    assert raw["runtime_version"] == "5.0.0-rc1"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SessionCorruptionError):
        store.load()


def test_checkpoint_v1_compatibility_and_corruption_is_loud(tmp_path: Path):
    state = tmp_path / ".term5"
    state.mkdir()
    path = state / "turn_checkpoint.json"
    messages = [{"role": "system", "content": "x"}]
    path.write_text(json.dumps({"format": 1, "phase": "turn_start", "messages": messages, "prompt": "p"}), encoding="utf-8")
    store = CheckpointStore(state, runtime_version="5.0.0-rc1")
    cp = store.load()
    assert cp is not None and cp.format == 1 and cp.prompt == "p"
    store.save(phase="turn_start", messages=messages, prompt="new")
    assert json.loads(path.read_text())["format"] == 2
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CheckpointCorruptionError):
        store.load()


def test_runtime_queues_reentrant_turn(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        cfg.executive.auto_plan = False
        provider = BlockingProvider()
        rt = AgentRuntime(cfg, provider=provider)
        first = asyncio.create_task(rt.run_turn("first", use_tools=False))
        await provider.started.wait()
        second = asyncio.create_task(rt.run_turn("second", use_tools=False))
        await asyncio.sleep(0.05)
        assert not second.done()
        provider.release.set()
        assert await first == "done"
        assert await second == "done"
    asyncio.run(go())


def test_config_unknown_key_diagnostic(tmp_path: Path):
    (tmp_path / "term5.toml").write_text("[reasoning]\nworker_retriez = 2\n[mystery]\nx = 1\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    joined = " | ".join(cfg.diagnostics)
    assert "reasoning.worker_retriez" in joined
    assert "mystery" in joined
