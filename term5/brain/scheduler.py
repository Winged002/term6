from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..models import TaskNode, WorkerResult


class DAGScheduler:
    """Dependency-aware bounded scheduler preview.

    Alpha2 uses this primitive through parallel_dag. The main model can submit a
    bounded dependency graph while the scheduler remains independent of planning.
    """

    def __init__(self, max_parallel: int = 32) -> None:
        self.sem = asyncio.Semaphore(max(1, max_parallel))

    async def run(
        self,
        nodes: list[TaskNode],
        execute: Callable[[TaskNode, dict[str, WorkerResult]], Awaitable[WorkerResult]],
    ) -> dict[str, WorkerResult]:
        pending = {n.id: n for n in nodes}
        results: dict[str, WorkerResult] = {}
        while pending:
            ready = [n for n in pending.values() if n.dependencies <= results.keys()]
            if not ready:
                unresolved = {k: sorted(v.dependencies - results.keys()) for k, v in pending.items()}
                raise ValueError(f"Task graph has a cycle or missing dependency: {unresolved}")

            async def one(node: TaskNode):
                async with self.sem:
                    try:
                        if node.timeout_s:
                            return await asyncio.wait_for(execute(node, results), timeout=node.timeout_s)
                        return await execute(node, results)
                    except asyncio.TimeoutError:
                        return WorkerResult(node.id, "failed", "task timed out")
                    except Exception as exc:
                        return WorkerResult(node.id, "failed", f"{type(exc).__name__}: {exc}")

            batch = await asyncio.gather(*(one(n) for n in ready))
            for node, result in zip(ready, batch):
                results[node.id] = result
                pending.pop(node.id, None)
        return results
