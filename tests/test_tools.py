import asyncio
from pathlib import Path

from term5.config import load_config
from term5.security.paths import PathGuard
from term5.security.policy import SecurityPolicy
from term5.state.memory import MemoryStore
from term5.state.transactions import TransactionManager
from term5.tools import ToolRegistry, register_builtin_tools
from term5.workspace.graph import WorkspaceGraph


def make_registry(tmp_path: Path):
    (tmp_path / "x.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    guard = PathGuard(tmp_path)
    mem = MemoryStore(cfg.state_dir / "memory.json")
    graph = WorkspaceGraph(guard, cfg.state_dir)
    graph.refresh()
    tx = TransactionManager(cfg.state_dir, guard)
    reg = ToolRegistry(SecurityPolicy(), 8)
    register_builtin_tools(reg, guard, mem, graph, tx)
    return reg


def test_read_and_write_tools(tmp_path: Path):
    async def go():
        reg = make_registry(tmp_path)
        read = await reg.execute("read_file", {"path": "x.py"})
        assert read.ok and "def hello" in read.content
        write = await reg.execute("write_file", {"path": "y.py", "content": "VALUE = 2\n"})
        assert write.ok
        assert (tmp_path / "y.py").read_text() == "VALUE = 2\n"
    asyncio.run(go())


def test_invalid_python_not_committed(tmp_path: Path):
    async def go():
        reg = make_registry(tmp_path)
        bad = await reg.execute("write_file", {"path": "bad.py", "content": "def nope(:\n"})
        assert not bad.ok
        assert not (tmp_path / "bad.py").exists()
    asyncio.run(go())
