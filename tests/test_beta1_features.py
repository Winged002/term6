import asyncio
import json
import sqlite3
from pathlib import Path

from term5.brain import ExecutivePlanner
from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, ToolCall, Usage
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.security.paths import PathGuard
from term5.workspace.graph import WorkspaceGraph


class ExecutiveProvider(ModelProvider):
    def __init__(self):
        self.chat_calls = 0

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.chat_calls += 1
        system = str(messages[0].get("content") or "")
        if "executive planning cortex" in system:
            return ChatResult(
                content=json.dumps({
                    "rationale": "independent architecture and risk lenses",
                    "tasks": [
                        {"id": "architecture", "objective": "analyze architecture constraints", "reasoning": "high", "depends_on": []},
                        {"id": "risk", "objective": "analyze regression and security risk", "reasoning": "high", "depends_on": []},
                    ],
                }),
                usage=Usage(prompt_tokens=20, completion_tokens=10, cache_hit_tokens=0, cache_miss_tokens=20),
            )
        if "temporary cognitive worker" in system:
            return ChatResult(
                content="grounded worker conclusion",
                usage=Usage(prompt_tokens=10, completion_tokens=3, cache_hit_tokens=0, cache_miss_tokens=10),
            )
        return ChatResult(
            content="primary final",
            usage=Usage(prompt_tokens=30, completion_tokens=4, cache_hit_tokens=0, cache_miss_tokens=30),
        )

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "pass\n", {"prompt_tokens": 1, "completion_tokens": 1}


class RepairFimProvider(ModelProvider):
    def __init__(self):
        self.fim_calls = 0

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        return ChatResult(content="ok")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        self.fim_calls += 1
        if self.fim_calls == 1:
            return "if\n", {"prompt_tokens": 5, "completion_tokens": 1}
        return "    return 42\n", {"prompt_tokens": 6, "completion_tokens": 2}


def test_workspace_graph_relations_and_impact(tmp_path: Path):
    (tmp_path / "b.py").write_text("def value():\n    return 1\n")
    (tmp_path / "a.py").write_text("import b\n\ndef use():\n    return b.value()\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("import a\n\ndef test_use():\n    assert a.use() == 1\n")
    graph = WorkspaceGraph(PathGuard(tmp_path), tmp_path / ".term5")
    stats = graph.refresh()
    assert stats["internal_edges"] >= 2
    b = graph.file_relations("b.py")
    assert "a.py" in b["imported_by"]
    a = graph.file_relations("a.py")
    assert "tests/test_a.py" in a["likely_tests"]
    impact = graph.impact_map("b.py", depth=2)
    paths = {x["path"] for x in impact["affected"]}
    assert "b.py" in paths and "a.py" in paths


def test_runtime_automatic_executive_preflight(tmp_path: Path):
    async def go():
        (tmp_path / "service.py").write_text("def run():\n    return True\n")
        cfg = load_config(tmp_path)
        cfg.executive.auto_plan = True
        provider = ExecutiveProvider()
        rt = AgentRuntime(cfg, provider=provider)
        answer = await rt.run_turn(
            "Architect and review a concurrency-sensitive refactor of service.py with regression and security risk analysis.",
            use_tools=False,
        )
        assert answer == "primary final"
        assert rt.executive_runs == 1
        assert rt.last_executive_plan is not None
        assert len(rt.last_executive_plan["tasks"]) == 2
        assert any(e["type"] == "executive.finished" for e in rt.activity_snapshot()["events"])
        assert rt.episodes.count() == 1
        assert rt.ledger.calls >= 4  # planner + two workers + primary
    asyncio.run(go())


def test_fim_local_repair_retry(tmp_path: Path):
    async def go():
        path = tmp_path / "x.py"
        path.write_text("def x():\n    pass\n")
        cfg = load_config(tmp_path)
        cfg.fim.repair_attempts = 1
        provider = RepairFimProvider()
        rt = AgentRuntime(cfg, provider=provider)
        result = await rt.fim_preview("x.py", 2, 2, "return 42")
        assert result.ok
        assert provider.fim_calls == 2
        assert "return 42" in result.data["candidate"]
    asyncio.run(go())


def test_nested_tool_schema_validation_and_sqlite_readonly(tmp_path: Path):
    async def go():
        cfg = load_config(tmp_path)
        rt = AgentRuntime(cfg, provider=ExecutiveProvider())
        bad = await rt.tools.execute("write_files", {"changes": [{"path": "x.py"}]})
        assert not bad.ok
        assert "missing required" in bad.content

        db = tmp_path / "data.db"
        con = sqlite3.connect(db)
        con.execute("create table t(x integer)")
        con.execute("insert into t values (7)")
        con.commit(); con.close()
        good = await rt.tools.execute("sqlite_query", {"path": "data.db", "query": "select x from t"})
        assert good.ok and good.data == [{"x": 7}]
        denied = await rt.tools.execute("sqlite_query", {"path": "data.db", "query": "delete from t"})
        assert not denied.ok
    asyncio.run(go())


def test_post_write_verification_is_returned_to_model(tmp_path: Path):
    class Writer(ModelProvider):
        def __init__(self): self.calls = 0
        async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
            self.calls += 1
            if self.calls == 1:
                return ChatResult(tool_calls=[ToolCall("w1", "write_file", {"path": "new.py", "content": "x = 1\n"})])
            return ChatResult(content="done")
        async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
            return "", {"prompt_tokens": 0, "completion_tokens": 0}

    async def go():
        cfg = load_config(tmp_path)
        cfg.executive.auto_plan = False
        rt = AgentRuntime(cfg, provider=Writer())
        out = await rt.run_turn("add new.py")
        assert out == "done"
        events = rt.activity_snapshot()["events"]
        finished = [e for e in events if e["type"] == "tool.finished" and e["data"].get("name") == "write_file"]
        assert finished and "post-write verification" in str(finished[-1]["data"].get("result_preview") or "")
        assert rt.last_verification
    asyncio.run(go())

def test_parallel_fim_atomic_commit(tmp_path: Path):
    class BatchFimProvider(ModelProvider):
        async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
            return ChatResult(content="ok")
        async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
            if "return one" in prefix:
                return "    return 1\n", {"prompt_tokens": 2, "completion_tokens": 2}
            return "    return 2\n", {"prompt_tokens": 2, "completion_tokens": 2}

    async def go():
        (tmp_path / "a.py").write_text("def a():\n    pass\n")
        (tmp_path / "b.py").write_text("def b():\n    pass\n")
        cfg = load_config(tmp_path)
        rt = AgentRuntime(cfg, provider=BatchFimProvider())
        result = await rt.tools.execute("parallel_fim_edit", {"changes": [
            {"path": "a.py", "start_line": 2, "end_line": 2, "instruction": "return one"},
            {"path": "b.py", "start_line": 2, "end_line": 2, "instruction": "return two"},
        ]})
        assert result.ok
        assert "return 1" in (tmp_path / "a.py").read_text()
        assert "return 2" in (tmp_path / "b.py").read_text()
        assert result.data["transaction_id"].startswith("txg_")
    asyncio.run(go())
