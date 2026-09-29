from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class ReasoningMode(str, Enum):
    AUTO = "auto"
    NONE = "none"
    LOW = "low"
    HIGH = "high"
    MAX = "max"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskKind(str, Enum):
    LOCAL = "local"
    CHAT = "chat"
    REASON = "reason"
    FIM = "fim"
    TOOL = "tool"
    VERIFY = "verify"


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        if other.cache_hit_tokens is not None:
            self.cache_hit_tokens = (self.cache_hit_tokens or 0) + other.cache_hit_tokens
        if other.cache_miss_tokens is not None:
            self.cache_miss_tokens = (self.cache_miss_tokens or 0) + other.cache_miss_tokens


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ChatResult:
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    model: str | None = None


@dataclass(slots=True)
class ToolResult:
    ok: bool
    content: str
    data: Any = None
    changed_files: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContextBlock:
    id: str
    content: str
    priority: int = 50
    max_tokens: int = 0
    cache_policy: str = "turn"
    source: str = "runtime"
    sensitivity: str = "project"


@dataclass(slots=True)
class TaskNode:
    id: str
    objective: str
    kind: TaskKind = TaskKind.REASON
    reasoning: ReasoningMode = ReasoningMode.HIGH
    dependencies: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    risk: RiskLevel = RiskLevel.LOW
    token_budget: int | None = None
    timeout_s: float | None = None
    transaction_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkerResult:
    task_id: str
    status: str
    conclusion: str
    evidence: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[ToolResult]]
    capabilities: set[str] = field(default_factory=set)
    risk: RiskLevel = RiskLevel.LOW
    read_only: bool = True
    stateful: bool = False
