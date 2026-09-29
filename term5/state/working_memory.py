from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models import ReasoningMode
from .artifacts import ArtifactStore
from .episodes import EpisodeStore


@dataclass(slots=True)
class ContextBudget:
    prompt_tokens: int
    tool_schema_tokens: int
    output_tokens: int
    safety_tokens: int
    total_limit: int


class WorkingMemoryManager:
    """Bounded active memory + local cold storage for long-running agents."""

    def __init__(self, config, artifacts: ArtifactStore, episodes: EpisodeStore) -> None:
        self.config = config
        self.artifacts = artifacts
        self.episodes = episodes
        self.compactions = 0
        self.archived_tool_results = 0
        self.evicted_reasoning_chars = 0
        self.last_budget: ContextBudget | None = None

    @staticmethod
    def estimate_tokens(messages: list[dict[str, Any]]) -> int:
        chars = 0
        for m in messages:
            chars += len(str(m.get("content") or ""))
            chars += len(str(m.get("reasoning_content") or ""))
            chars += len(str(m.get("tool_calls") or ""))
        return max(1, chars // 4)

    @staticmethod
    def schema_tokens(tools: list[dict[str, Any]] | None) -> int:
        if not tools:
            return 0
        return max(1, len(json.dumps(tools, ensure_ascii=False, separators=(",", ":"))) // 4)

    def output_budget(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                      reasoning: ReasoningMode) -> int:
        prompt = self.estimate_tokens(messages)
        schema = self.schema_tokens(tools)
        caps = {
            ReasoningMode.NONE: self.config.context.output_none_tokens,
            ReasoningMode.LOW: self.config.context.output_low_tokens,
            ReasoningMode.HIGH: self.config.context.output_high_tokens,
            ReasoningMode.MAX: self.config.context.output_max_tokens,
            ReasoningMode.AUTO: self.config.context.output_high_tokens,
        }
        mode_cap = max(1024, int(caps.get(reasoning, self.config.context.output_high_tokens)))
        available = max(1024, int(self.config.model.total_context_tokens) - prompt - schema - int(self.config.context.safety_tokens))
        budget = max(1024, min(int(self.config.model.max_output_tokens), mode_cap, available))
        self.last_budget = ContextBudget(prompt, schema, budget, int(self.config.context.safety_tokens), int(self.config.model.total_context_tokens))
        return budget

    def context_tool_result(self, name: str, content: str) -> str:
        text = str(content or "")
        cap = int(self.config.context.tool_inline_chars)
        if len(text) <= cap:
            return text
        ref = self.artifacts.put(text, kind="tool", name=name)
        self.archived_tool_results += 1
        preview = int(self.config.context.tool_preview_chars)
        head = max(500, preview * 2 // 3)
        tail = max(300, preview - head)
        snippet = text[:head] + "\n…\n" + text[-tail:]
        return (
            f"{snippet}\n\n[term_5 archived full tool output: {ref.id}; "
            f"{ref.chars:,} chars. Use artifact_read if exact omitted details are needed.]"
        )

    def finish_turn(self, messages: list[dict[str, Any]], turn_id: str, *, prompt: str, outcome: str,
                    reasoning: ReasoningMode, tools: list[str], changed_files: list[str], failures: list[str]) -> list[dict[str, Any]]:
        self.episodes.add(prompt, outcome, reasoning=reasoning.value, tools=tools,
                          changed_files=changed_files, failures=failures)
        # Keep only the actual user request and the final no-tool assistant answer
        # from a completed turn. Tool traces/context/evidence are cold-store data.
        kept_current: list[dict[str, Any]] = []
        candidates = [m for m in messages if m.get("_term5_turn") == turn_id]
        user_msg = next((m for m in candidates if m.get("_term5_kind") == "user"), None)
        final_msg = next((m for m in reversed(candidates)
                          if m.get("role") == "assistant" and not m.get("tool_calls")), None)
        if user_msg is not None:
            kept_current.append(user_msg)
        if final_msg is not None:
            kept_current.append(final_msg)

        out = [m for m in messages if m.get("_term5_turn") != turn_id]
        out.extend(kept_current)

        # Completed hidden reasoning has no future execution value. Remove it.
        for m in out:
            if m.get("role") == "assistant" and m.get("reasoning_content"):
                self.evicted_reasoning_chars += len(str(m.get("reasoning_content") or ""))
                m.pop("reasoning_content", None)

        # Retain only a small conversational recency window. Older completed
        # work is already represented in EpisodeStore and can be recalled by relevance.
        system = out[:1] if out and out[0].get("role") == "system" else []
        body = out[1:] if system else out
        pairs: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for m in body:
            if m.get("role") == "user" and m.get("_term5_kind") == "user":
                if current:
                    pairs.append(current)
                current = [m]
            elif current:
                current.append(m)
            else:
                # legacy/non-turn message: keep with next/last unit conservatively
                current = [m]
        if current:
            pairs.append(current)
        keep_n = max(1, int(self.config.context.recent_full_turns))
        if len(pairs) > keep_n:
            pairs = pairs[-keep_n:]
            self.compactions += 1
        compacted = system + [m for pair in pairs for m in pair]
        return compacted


    def migrate_legacy(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Compact pre-v5.3 raw sessions once while preserving conversational continuity.

        Legacy sessions have no _term5_turn metadata and can contain every tool
        exchange/reasoning trace. We distill user/final-assistant pairs into the
        episode store and retain only the most recent conversational pairs.
        """
        if not messages or any(m.get("_term5_turn") for m in messages):
            return messages, 0
        before = self.estimate_tokens(messages)
        if before < int(self.config.context.soft_compact_tokens):
            return messages, 0
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        current_user: dict[str, Any] | None = None
        current_final: dict[str, Any] | None = None
        for m in messages[1 if system else 0:]:
            role = m.get("role")
            content = str(m.get("content") or "")
            if role == "user":
                if content.startswith("[term_5 turn context") or content.startswith("[term_5 executive preflight") or content.startswith("[term_5 product blueprint"):
                    continue
                if current_user is not None:
                    pairs.append((current_user, current_final))
                current_user = {"role": "user", "content": content, "_term5_kind": "user"}
                current_final = None
            elif role == "assistant" and not m.get("tool_calls") and current_user is not None and content.strip():
                current_final = {"role": "assistant", "content": content}
        if current_user is not None:
            pairs.append((current_user, current_final))
        if not pairs:
            return messages, 0
        keep_n = max(1, int(self.config.context.recent_full_turns))
        recent = pairs[-keep_n:]
        # Distill all legacy conversational pairs before discarding execution
        # traces, including the recent pairs retained verbatim for continuity.
        for user, assistant in pairs:
            self.episodes.add(str(user.get("content") or ""), str((assistant or {}).get("content") or ""), reasoning="legacy")
        compacted: list[dict[str, Any]] = [system] if system is not None else []
        for user, assistant in recent:
            compacted.append(user)
            if assistant is not None:
                compacted.append(assistant)
        after = self.estimate_tokens(compacted)
        if after < before:
            self.compactions += 1
            self.evicted_reasoning_chars += sum(len(str(m.get("reasoning_content") or "")) for m in messages)
            return compacted, max(0, before - after)
        return messages, 0

    def stats(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        b = self.last_budget
        return {
            "active_tokens_est": self.estimate_tokens(messages),
            "target_tokens": self.config.context.active_target_tokens,
            "soft_compact_tokens": self.config.context.soft_compact_tokens,
            "hard_input_tokens": self.config.context.hard_input_tokens,
            "compactions": self.compactions,
            "episodes": self.episodes.count(),
            "artifacts": self.artifacts.count(),
            "archived_tool_results": self.archived_tool_results,
            "evicted_reasoning_chars": self.evicted_reasoning_chars,
            "last_prompt_tokens_est": None if b is None else b.prompt_tokens,
            "last_tool_schema_tokens_est": None if b is None else b.tool_schema_tokens,
            "last_output_budget": None if b is None else b.output_tokens,
            "safety_tokens": self.config.context.safety_tokens,
            "total_context_tokens": self.config.model.total_context_tokens,
        }
