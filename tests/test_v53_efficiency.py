import asyncio
from pathlib import Path

from term5.config import load_config
from term5.models import ChatResult, ReasoningMode, ToolCall
from term5.providers.base import ModelProvider
from term5.runtime import AgentRuntime
from term5.state.artifacts import ArtifactStore
from term5.state.episodes import EpisodeStore
from term5.state.working_memory import WorkingMemoryManager


class BudgetProvider(ModelProvider):
    def __init__(self):
        self.calls = 0
        self.max_tokens = []
        self.prompts = []

    async def chat(self, messages, *, tools=None, reasoning=ReasoningMode.NONE, max_tokens=None):
        self.calls += 1
        self.max_tokens.append(max_tokens)
        self.prompts.append(messages)
        if self.calls == 1:
            return ChatResult(
                reasoning_content="private scratch " * 3000,
                tool_calls=[ToolCall("r1", "read_file", {"path": "big.txt", "start_line": 1, "max_lines": 10000})],
            )
        return ChatResult(content="finished")

    async def fim(self, prefix, suffix, *, max_tokens=2048, temperature=0.2):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def test_dynamic_output_budget_never_uses_384k_default(tmp_path: Path):
    cfg = load_config(tmp_path)
    artifacts = ArtifactStore(cfg.state_dir)
    episodes = EpisodeStore(cfg.state_dir)
    wm = WorkingMemoryManager(cfg, artifacts, episodes)
    huge = [{"role": "system", "content": "x" * 2_000_000}]  # ~500k token estimate
    budget = wm.output_budget(huge, [], ReasoningMode.HIGH)
    assert budget <= cfg.context.output_high_tokens == 64000
    assert budget < cfg.model.max_output_tokens
    assert wm.last_budget is not None
    assert wm.last_budget.prompt_tokens >= 490000


def test_large_tool_output_is_archived_and_context_preview_is_bounded(tmp_path: Path):
    cfg = load_config(tmp_path)
    cfg.context.tool_inline_chars = 4000
    cfg.context.tool_preview_chars = 1000
    store = ArtifactStore(cfg.state_dir)
    wm = WorkingMemoryManager(cfg, store, EpisodeStore(cfg.state_dir))
    raw = "line\n" * 10000
    compact = wm.context_tool_result("app_build", raw)
    assert len(compact) < 3000
    assert "archived full tool output" in compact
    artifact_id = compact.split("archived full tool output: ", 1)[1].split(";", 1)[0]
    recovered = store.read(artifact_id, max_chars=50000)
    assert recovered.startswith("line\n")
    assert store.count() == 1


def test_completed_turn_evicts_tool_trace_and_reasoning_but_keeps_episode(tmp_path: Path):
    async def go():
        (tmp_path / "big.txt").write_text("A" * 100000)
        cfg = load_config(tmp_path)
        cfg.executive.auto_plan = False
        provider = BudgetProvider()
        rt = AgentRuntime(cfg, provider=provider)
        out = await rt.run_turn("read big.txt")
        assert out == "finished"
        assert provider.calls == 2
        # Active loop continuity included private reasoning on the second call.
        assert any(m.get("reasoning_content") for m in provider.prompts[1])
        # Future working history does not.
        assert not any(m.get("reasoning_content") for m in rt.messages)
        assert not any(m.get("role") == "tool" for m in rt.messages)
        assert rt.episodes.count() == 1
        assert rt.working_memory.stats(rt.messages)["active_tokens_est"] < 10000
    asyncio.run(go())


def test_legacy_massive_session_is_compacted_to_recent_pairs_and_episodes(tmp_path: Path):
    cfg = load_config(tmp_path)
    cfg.context.soft_compact_tokens = 1000
    cfg.context.active_target_tokens = 500
    # validation is intentionally bypassed here because we're unit-testing migration thresholds.
    wm = WorkingMemoryManager(cfg, ArtifactStore(cfg.state_dir), EpisodeStore(cfg.state_dir))
    messages = [{"role": "system", "content": "sys"}]
    for i in range(10):
        messages.extend([
            {"role": "user", "content": f"request {i} " + "x" * 1000},
            {"role": "assistant", "content": None, "reasoning_content": "r" * 4000,
             "tool_calls": [{"id": f"t{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"t{i}", "content": "log" * 3000},
            {"role": "assistant", "content": f"done {i}"},
        ])
    compacted, saved = wm.migrate_legacy(messages)
    assert saved > 10000
    assert wm.episodes.count() == 10
    assert not any(m.get("role") == "tool" for m in compacted)
    assert len([m for m in compacted if m.get("role") == "user"]) == 4


def test_context_fit_targets_soft_budget_not_physical_window(tmp_path: Path):
    cfg = load_config(tmp_path)
    cfg.context.active_target_tokens = 4000
    cfg.context.soft_compact_tokens = 6000
    cfg.context.hard_input_tokens = 10000
    from term5.security.paths import PathGuard
    from term5.state.memory import MemoryStore
    from term5.workspace.graph import WorkspaceGraph
    from term5.skills import SkillEngine
    from term5.context import ContextCompiler
    guard = PathGuard(tmp_path)
    graph = WorkspaceGraph(guard, cfg.state_dir)
    ctx = ContextCompiler(cfg, graph, MemoryStore(cfg.state_dir / "memory.json"), SkillEngine(cfg.root, cfg.state_dir), EpisodeStore(cfg.state_dir))
    messages = [{"role": "system", "content": ctx.system_prompt}]
    for i in range(20):
        messages.append({"role": "user", "content": f"u{i} " + "x" * 3000})
        messages.append({"role": "assistant", "content": f"a{i} " + "y" * 3000})
    fitted = ctx.fit(messages)
    assert ctx.estimate_tokens(fitted) < ctx.estimate_tokens(messages)
    assert ctx.estimate_tokens(fitted) <= 10000

def test_v52_episode_journal_is_readable_as_legacy_episode(tmp_path: Path):
    state = tmp_path / ".term5"
    state.mkdir()
    (state / "episodes.jsonl").write_text(
        '{"ts":"2026-09-28T10:00:00+00:00","prompt":"fix mongo health","answer":"made healthcheck tolerant","reasoning":"low"}\n',
        encoding="utf-8",
    )
    store = EpisodeStore(state)
    text = store.recall("mongo health", 5, 8000)
    assert "made healthcheck tolerant" in text
    assert store.count() == 1
