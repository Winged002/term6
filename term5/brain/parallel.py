from __future__ import annotations

import asyncio
import time
from typing import Any

from ..models import ReasoningMode, WorkerResult
from ..providers.base import ModelProvider


class ParallelCortex:
    """Bounded temporary reasoning workers.

    Workers have no tools and no writable authority. RC1 adds per-worker
    timeout/retry handling so one transient provider failure does not poison a
    dependency graph or force the primary turn to fail.
    """

    def __init__(self, provider: ModelProvider, max_total: int = 32, max_high: int = 8,
                 timeout_s: int = 120, retries: int = 1) -> None:
        self.provider = provider
        self.total_sem = asyncio.Semaphore(max(1, max_total))
        self.high_sem = asyncio.Semaphore(max(1, max_high))
        self.timeout_s = max(5, min(int(timeout_s), 1800))
        self.retries = max(0, min(int(retries), 3))

    async def _one(self, task_id: str, objective: str, reasoning: ReasoningMode,
                   shared_context: str = "") -> WorkerResult:
        highish = reasoning in {ReasoningMode.HIGH, ReasoningMode.MAX}
        async with self.total_sem:
            sem = self.high_sem if highish else _NullAsyncContext()
            async with sem:
                messages = [
                    {"role": "system", "content": (
                        "You are a temporary cognitive worker inside a local agent runtime. "
                        "Solve only the assigned objective. Do not invent tool observations. "
                        "Return a concise conclusion, concrete evidence/assumptions, and unresolved uncertainty."
                    )},
                    {"role": "user", "content": (
                        (("Shared context:\n" + shared_context + "\n\n") if shared_context else "")
                        + "Objective:\n" + objective
                    )},
                ]
                started = time.monotonic()
                errors: list[str] = []
                for attempt in range(self.retries + 1):
                    try:
                        result = await asyncio.wait_for(
                            self.provider.chat(messages, tools=None, reasoning=reasoning),
                            timeout=self.timeout_s,
                        )
                        return WorkerResult(
                            task_id=task_id,
                            status="success",
                            conclusion=result.content,
                            evidence=[],
                            metrics={
                                "reasoning": reasoning.value,
                                "attempts": attempt + 1,
                                "prompt_tokens": result.usage.prompt_tokens,
                                "completion_tokens": result.usage.completion_tokens,
                                "cache_hit_tokens": result.usage.cache_hit_tokens,
                                "cache_miss_tokens": result.usage.cache_miss_tokens,
                                "elapsed_s": round(time.monotonic() - started, 4),
                            },
                        )
                    except asyncio.TimeoutError:
                        errors.append(f"attempt {attempt+1}: timeout after {self.timeout_s}s")
                    except Exception as exc:
                        errors.append(f"attempt {attempt+1}: {type(exc).__name__}: {exc}")
                return WorkerResult(
                    task_id=task_id,
                    status="failed",
                    conclusion="; ".join(errors)[-4000:],
                    metrics={
                        "reasoning": reasoning.value,
                        "attempts": self.retries + 1,
                        "elapsed_s": round(time.monotonic() - started, 4),
                    },
                )

    async def run(self, tasks: list[dict[str, Any]], shared_context: str = "") -> list[WorkerResult]:
        jobs = []
        for idx, task in enumerate(tasks):
            objective = str(task.get("objective", "")).strip()
            if not objective:
                continue
            raw = str(task.get("reasoning", "low")).lower()
            try:
                mode = ReasoningMode(raw)
            except Exception:
                mode = ReasoningMode.LOW
            if mode == ReasoningMode.AUTO:
                mode = ReasoningMode.LOW
            jobs.append(self._one(str(task.get("id") or f"worker_{idx+1}"), objective, mode, shared_context))
        if not jobs:
            return []
        return list(await asyncio.gather(*jobs))


class _NullAsyncContext:
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False
