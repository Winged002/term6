from __future__ import annotations

from typing import Any

from ..models import ReasoningMode, TaskNode, WorkerResult
from .parallel import ParallelCortex
from .scheduler import DAGScheduler


class DAGCortex:
    """Execute bounded reasoning task graphs with dependency context.

    Each node is a temporary no-tools worker. Dependencies are exposed only as
    compact conclusions, preventing a large worker graph from recursively
    copying full private contexts into every call.
    """

    def __init__(self, parallel: ParallelCortex, max_parallel: int = 32) -> None:
        self.parallel = parallel
        self.scheduler = DAGScheduler(max_parallel=max_parallel)

    @staticmethod
    def parse_nodes(tasks: list[dict[str, Any]]) -> list[TaskNode]:
        if not tasks:
            raise ValueError("DAG requires at least one task")
        nodes: list[TaskNode] = []
        ids: set[str] = set()
        for idx, raw in enumerate(tasks):
            task_id = str(raw.get("id") or f"task_{idx+1}").strip()
            if not task_id or task_id in ids:
                raise ValueError(f"duplicate/empty task id: {task_id!r}")
            ids.add(task_id)
            objective = str(raw.get("objective") or "").strip()
            if not objective:
                raise ValueError(f"task {task_id} has no objective")
            try:
                reasoning = ReasoningMode(str(raw.get("reasoning") or "low").lower())
            except ValueError:
                reasoning = ReasoningMode.LOW
            if reasoning == ReasoningMode.AUTO:
                reasoning = ReasoningMode.LOW
            deps = {str(x).strip() for x in (raw.get("depends_on") or []) if str(x).strip()}
            nodes.append(TaskNode(task_id, objective, reasoning=reasoning, dependencies=deps))
        unknown = sorted({d for n in nodes for d in n.dependencies if d not in ids})
        if unknown:
            raise ValueError("DAG references unknown dependencies: " + ", ".join(unknown))
        return nodes

    async def run(self, tasks: list[dict[str, Any]], shared_context: str = "") -> dict[str, WorkerResult]:
        nodes = self.parse_nodes(tasks)

        async def execute(node: TaskNode, results: dict[str, WorkerResult]) -> WorkerResult:
            dependency_context = []
            for dep in sorted(node.dependencies):
                result = results[dep]
                dependency_context.append(
                    f"Dependency {dep} [{result.status}]:\n{result.conclusion[:8000]}"
                )
            context = shared_context[:12000]
            if dependency_context:
                context = (context + "\n\n" if context else "") + "\n\n".join(dependency_context)
            result = await self.parallel._one(node.id, node.objective, node.reasoning, context[:24000])
            return result

        return await self.scheduler.run(nodes, execute)
