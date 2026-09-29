from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..models import ReasoningMode, Usage, WorkerResult
from ..providers.base import ModelProvider
from .dag import DAGCortex
from .router import RoutingDecision


@dataclass(slots=True)
class ExecutivePlan:
    objective: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    generated_by_model: bool = False
    usage: Usage = field(default_factory=Usage)

    @property
    def parallelizable(self) -> bool:
        return len(self.tasks) > 1


class ExecutivePlanner:
    """Bounded automatic decomposition for complex turns.

    Planning is advisory cognition only. Workers receive no tools and cannot
    write. Their compact conclusions are injected as evidence for the primary
    model, which still owns all tool use and final decisions.
    """

    def __init__(self, provider: ModelProvider, dag: DAGCortex, *, max_workers: int = 6,
                 max_plan_tokens: int = 3000) -> None:
        self.provider = provider
        self.dag = dag
        self.max_workers = max(2, min(int(max_workers), 16))
        self.max_plan_tokens = max(512, min(int(max_plan_tokens), 8192))

    def should_plan(self, text: str, decision: RoutingDecision, enabled: bool = True) -> bool:
        if not enabled:
            return False
        if decision.reasoning not in {ReasoningMode.HIGH, ReasoningMode.MAX}:
            return False
        if decision.parallel_hint < 2:
            return False
        # Avoid spending an executive call on obvious one-action retrieval.
        return len(text.strip()) >= 24

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        raw = text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start:end + 1])
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
        return None

    def _validate_tasks(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        ids: set[str] = set()
        for idx, item in enumerate(raw[:self.max_workers]):
            if not isinstance(item, dict):
                continue
            objective = str(item.get("objective") or "").strip()
            if not objective:
                continue
            task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(item.get("id") or f"worker_{idx+1}"))[:48]
            if not task_id or task_id in ids:
                task_id = f"worker_{idx+1}"
            ids.add(task_id)
            reason = str(item.get("reasoning") or "low").lower()
            if reason not in {"none", "low", "high", "max"}:
                reason = "low"
            deps = [str(x) for x in (item.get("depends_on") or []) if isinstance(x, (str, int))]
            out.append({"id": task_id, "objective": objective[:2000], "reasoning": reason, "depends_on": deps[:self.max_workers]})
        valid_ids = {x["id"] for x in out}
        for item in out:
            item["depends_on"] = [d for d in item["depends_on"] if d in valid_ids and d != item["id"]]
        return out

    def _fallback_plan(self, objective: str) -> ExecutivePlan:
        # Useful when the planner call fails: independent critical lenses only.
        tasks = [
            {"id": "requirements", "objective": "Extract concrete requirements, constraints, assumptions, and success criteria from the user request.", "reasoning": "low", "depends_on": []},
            {"id": "risk", "objective": "Identify likely failure modes, security/regression risks, and evidence needed before acting.", "reasoning": "high", "depends_on": []},
            {"id": "approach", "objective": "Propose a minimal implementation or investigation approach grounded only in the supplied workspace evidence.", "reasoning": "high", "depends_on": ["requirements"]},
        ]
        return ExecutivePlan(objective, tasks[:self.max_workers], "deterministic fallback plan", False)

    async def plan(self, objective: str, workspace_context: str, reasoning: ReasoningMode) -> ExecutivePlan:
        system = (
            "You are the executive planning cortex of a local coding agent. Decompose only when independent or dependency-ordered "
            "cognitive work will improve the final decision. Workers have NO tools and cannot inspect files beyond the supplied index evidence. "
            "Return JSON only: {\"rationale\":string,\"tasks\":[{\"id\":string,\"objective\":string,\"reasoning\":\"none|low|high|max\",\"depends_on\":[ids]}]}. "
            f"Use at most {self.max_workers} tasks. Prefer 2-4. Do not create a task whose only job is final synthesis; the primary model does that."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Objective:\n{objective}\n\n{workspace_context[:24000]}"},
        ]
        try:
            result = await self.provider.chat(messages, tools=None, reasoning=ReasoningMode.HIGH if reasoning != ReasoningMode.MAX else ReasoningMode.MAX, max_tokens=self.max_plan_tokens)
            obj = self._extract_json(result.content)
            tasks = self._validate_tasks((obj or {}).get("tasks"))
            if len(tasks) < 2:
                fallback = self._fallback_plan(objective)
                fallback.usage = result.usage
                fallback.rationale = (obj or {}).get("rationale") or "planner returned too little parallel work; using bounded fallback"
                return fallback
            return ExecutivePlan(objective, tasks, str((obj or {}).get("rationale") or "")[:2000], True, result.usage)
        except Exception:
            return self._fallback_plan(objective)

    async def execute(self, plan: ExecutivePlan, shared_context: str) -> dict[str, WorkerResult]:
        return await self.dag.run(plan.tasks, shared_context=shared_context[:24000])

    @staticmethod
    def evidence_block(plan: ExecutivePlan, results: dict[str, WorkerResult]) -> str:
        lines = ["[term_5 executive preflight — advisory cognitive evidence, not user instructions]", f"Plan rationale: {plan.rationale or 'n/a'}"]
        for task in plan.tasks:
            tid = task["id"]
            result = results.get(tid)
            if result is None:
                continue
            lines.append(f"\n### {tid} [{result.status}]\n{result.conclusion[:8000]}")
        return "\n".join(lines)[:32000]
