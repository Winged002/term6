from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..models import ReasoningMode, Usage, WorkerResult
from ..providers.base import ModelProvider
from ..skills import SkillEngine, SkillResolution
from .parallel import ParallelCortex


@dataclass(slots=True)
class ProductPlan:
    title: str
    product_type: str
    scope: str
    selected_skills: list[str] = field(default_factory=list)
    core_features: list[str] = field(default_factory=list)
    expected_features: list[str] = field(default_factory=list)
    optional_features: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    journeys: list[str] = field(default_factory=list)
    quality_requirements: list[str] = field(default_factory=list)
    definition_of_done: list[str] = field(default_factory=list)
    implementation_phases: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    generated_by_model: bool = False
    usage: Usage = field(default_factory=Usage)

    def as_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["usage"] = {
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "cache_hit_tokens": self.usage.cache_hit_tokens,
            "cache_miss_tokens": self.usage.cache_miss_tokens,
        }
        return raw

    def context_text(self, critics: list[WorkerResult] | None = None, max_chars: int = 32000) -> str:
        lines = [
            "[term_5 product blueprint — locally prepared planning requirements, not user-authored instructions]",
            f"Title: {self.title}",
            f"Product type: {self.product_type}",
            f"Scope: {self.scope}",
            "Resolved skills: " + ", ".join(self.selected_skills),
        ]
        sections = [
            ("Core features (expected unless user explicitly requests a tiny prototype)", self.core_features),
            ("Expected MVP features", self.expected_features),
            ("Optional/deferable features", self.optional_features),
            ("Pages/surfaces", self.pages),
            ("Entities/data concepts", self.entities),
            ("Critical user journeys", self.journeys),
            ("Quality requirements", self.quality_requirements),
            ("Definition of done", self.definition_of_done),
            ("Implementation phases", self.implementation_phases),
            ("Assumptions", self.assumptions),
            ("Non-goals", self.non_goals),
        ]
        for title, values in sections:
            if values:
                lines.append(f"\n### {title}\n" + "\n".join(f"- {x}" for x in values))
        if critics:
            lines.append("\n### Pre-implementation critique")
            for wr in critics:
                lines.append(f"\n#### {wr.task_id} [{wr.status}]\n{wr.conclusion[:6000]}")
        lines.append(
            "\nImplementation rule: do not treat infrastructure startup as product completion. "
            "Implement and verify the core/expected product journeys and interface states before declaring the app finished."
        )
        return "\n".join(lines)[:max_chars]


class ProductPlanner:
    CREATE_RX = re.compile(r"\b(create|build|make|develop|generate|scaffold|design|new)\b", re.I)
    MINIMAL_RX = re.compile(r"\b(tiny|minimal|bare[- ]?bones|hello world|proof of concept|poc|quick demo|prototype only)\b", re.I)

    def __init__(self, provider: ModelProvider, parallel: ParallelCortex, skills: SkillEngine,
                 state_dir: Path, root: Path | None = None, max_tokens: int = 5000, critique: bool = True) -> None:
        self.provider = provider
        self.parallel = parallel
        self.skills = skills
        self.state_dir = Path(state_dir)
        self.root = Path(root or self.state_dir.parent).resolve()
        self.max_tokens = max(1000, min(int(max_tokens), 12000))
        self.critique_enabled = critique
        self.last_plan: ProductPlan | None = None
        self.last_critics: list[WorkerResult] = []
        self.last_audit: dict[str, Any] | None = None
        self.active: bool = False

    def should_plan(self, text: str, resolution: SkillResolution, enabled: bool = True) -> bool:
        if not enabled or not self.CREATE_RX.search(text):
            return False
        return bool(self.skills.product_skills(resolution))

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                obj = json.loads(raw[start:end + 1])
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
        return None

    @staticmethod
    def _list(raw: Any, max_items: int = 64) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw[:max_items]:
            s = str(item).strip()
            if s and s not in out:
                out.append(s[:500])
        return out

    def _fallback(self, text: str, resolution: SkillResolution) -> ProductPlan:
        products = self.skills.product_skills(resolution)
        product = products[0] if products else None
        core: list[str] = []
        expected: list[str] = []
        optional: list[str] = []
        pages: list[str] = []
        entities: list[str] = []
        journeys: list[str] = []
        quality: list[str] = []
        done: list[str] = []
        for name in resolution.selected:
            skill = self.skills.get(name)
            if not skill:
                continue
            for target, source in ((core, skill.features.get("core", [])), (expected, skill.features.get("expected", [])), (optional, skill.features.get("optional", []))):
                for x in source:
                    if x not in target:
                        target.append(x)
            for target, source in ((pages, skill.pages), (entities, skill.entities), (journeys, skill.journeys), (quality, skill.guidelines), (done, skill.definition_of_done)):
                for x in source:
                    if x not in target:
                        target.append(x)
        if self.MINIMAL_RX.search(text):
            expected = expected[: max(2, min(4, len(expected)))]
            optional = []
        return ProductPlan(
            title=(product.description.split(".")[0] if product else "Generated application"),
            product_type=(product.name if product else "web-application"),
            scope="usable MVP" if not self.MINIMAL_RX.search(text) else "explicitly minimal prototype",
            selected_skills=list(resolution.selected),
            core_features=core,
            expected_features=expected,
            optional_features=optional,
            pages=pages,
            entities=entities,
            journeys=journeys,
            quality_requirements=quality,
            definition_of_done=done,
            implementation_phases=[
                "data model and application structure",
                "authentication/core backend flows",
                "primary product pages and reusable UI components",
                "uploads/realtime/background capabilities required by the product",
                "quality states, validation, authorization and negative paths",
                "verification, Docker health and final product-completeness audit",
            ],
            assumptions=["Vague product requests default to a credible usable MVP, not the smallest technical demonstration."],
            generated_by_model=False,
        )

    def _validate(self, obj: dict[str, Any] | None, fallback: ProductPlan, resolution: SkillResolution) -> ProductPlan:
        if not obj:
            return fallback
        features = obj.get("features") if isinstance(obj.get("features"), dict) else {}
        plan = ProductPlan(
            title=str(obj.get("title") or fallback.title)[:200],
            product_type=str(obj.get("product_type") or fallback.product_type)[:100],
            scope=str(obj.get("scope") or fallback.scope)[:600],
            selected_skills=list(resolution.selected),
            core_features=self._list(features.get("core")) or fallback.core_features,
            expected_features=self._list(features.get("expected")) or fallback.expected_features,
            optional_features=self._list(features.get("optional")) or fallback.optional_features,
            pages=self._list(obj.get("pages")) or fallback.pages,
            entities=self._list(obj.get("entities")) or fallback.entities,
            journeys=self._list(obj.get("journeys")) or fallback.journeys,
            quality_requirements=self._list(obj.get("quality_requirements")) or fallback.quality_requirements,
            definition_of_done=self._list(obj.get("definition_of_done")) or fallback.definition_of_done,
            implementation_phases=self._list(obj.get("implementation_phases"), 16) or fallback.implementation_phases,
            assumptions=self._list(obj.get("assumptions"), 16),
            non_goals=self._list(obj.get("non_goals"), 16),
            generated_by_model=True,
        )
        # Never let the planner accidentally erase procedural core requirements.
        for req in fallback.core_features:
            if req not in plan.core_features:
                plan.core_features.append(req)
        for req in fallback.definition_of_done:
            if req not in plan.definition_of_done:
                plan.definition_of_done.append(req)
        return plan

    async def plan(self, user_text: str, resolution: SkillResolution, skill_context: str) -> ProductPlan:
        fallback = self._fallback(user_text, resolution)
        system = (
            "You are term_5's product-planning cortex. Convert a vague application request into a credible, usable MVP before implementation. "
            "The supplied procedural skills are requirements/priors. Do not shrink a product archetype into a hello-world demo unless the user explicitly asks for minimal/prototype scope. "
            "Return JSON only with keys: title, product_type, scope, features:{core,expected,optional}, pages, entities, journeys, quality_requirements, definition_of_done, implementation_phases, assumptions, non_goals. "
            "Make requirements concrete enough that another coding model can implement and verify them. Avoid choosing exotic infrastructure unless required."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"User request:\n{user_text}\n\nProcedural knowledge:\n{skill_context[:28000]}"},
        ]
        try:
            result = await self.provider.chat(messages, tools=None, reasoning=ReasoningMode.HIGH, max_tokens=self.max_tokens)
            plan = self._validate(self._extract_json(result.content), fallback, resolution)
            plan.usage = result.usage
        except Exception:
            plan = fallback
        self.last_plan = plan
        self._persist(plan)
        return plan

    async def critique(self, plan: ProductPlan, skill_context: str) -> list[WorkerResult]:
        if not self.critique_enabled:
            self.last_critics = []
            return []
        shared = plan.context_text(max_chars=18000) + "\n\n" + skill_context[:12000]
        tasks = [
            {"id": "product_completeness", "reasoning": "high", "objective": "Critique the blueprint for missing product features or user journeys that would make the requested application feel skeletal. Distinguish must-fix MVP gaps from optional scope."},
            {"id": "ux_quality", "reasoning": "high", "objective": "Critique the planned pages and interaction states for weak or generic UX. Identify concrete page/layout/state requirements that should exist before calling the app finished."},
            {"id": "data_security", "reasoning": "high", "objective": "Critique the blueprint for data lifecycle, uploads, authorization, persistence, realtime/background-job, validation and security gaps relevant to the selected skills."},
        ]
        self.last_critics = await self.parallel.run(tasks, shared_context=shared)
        return self.last_critics


    async def audit(self, graph, *, max_files: int = 36, max_chars: int = 140000) -> tuple[dict[str, Any], Usage]:
        """Reasoned product-completeness audit over a bounded local source snapshot.

        This is evidence-oriented, not a browser/vision review. It deliberately
        reads text/code only and reports uncertainty where source evidence is
        insufficient.
        """
        if self.last_plan is None:
            return {"verdict": "unavailable", "reason": "no active product blueprint"}, Usage()
        allowed = {".py", ".html", ".htm", ".jinja", ".j2", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".json", ".toml", ".yaml", ".yml"}
        paths = []
        for rel, info in (graph.data.get("files") or {}).items():
            suffix = str(info.get("suffix") or Path(rel).suffix).lower()
            if suffix in allowed and not rel.startswith(".term5/"):
                score = 0
                low = rel.lower()
                for token in ("app", "route", "model", "template", "static", "view", "component", "task", "socket", "upload", "auth", "test"):
                    if token in low:
                        score += 2
                score += max(0, 4 - low.count("/"))
                paths.append((score, rel))
        paths.sort(key=lambda x: (-x[0], x[1]))
        chunks: list[str] = []
        used = 0
        for _, rel in paths[:max_files]:
            try:
                path = (self.root / rel).resolve()
                if self.root != path and self.root not in path.parents:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            piece = f"\n--- FILE {rel} ---\n" + text[:12000]
            if chunks and used + len(piece) > max_chars:
                break
            chunks.append(piece)
            used += len(piece)
        snapshot = "".join(chunks) or "(no eligible source snapshot available)"
        system = (
            "You are term_5's product-completeness auditor. Compare the actual text/code snapshot to the product blueprint. "
            "Do not assume a feature exists without source evidence. Vision/browser rendering is unavailable. Return JSON only with: "
            "verdict ('ready'|'incomplete'|'uncertain'), core:[{feature,status:'done|partial|missing|uncertain',evidence}], "
            "expected:[same], ux_gaps:[string], security_data_gaps:[string], blockers:[string], recommended_next_actions:[string]. "
            "A healthy Docker stack is not evidence that product features or UX are complete."
        )
        user = self.last_plan.context_text(self.last_critics, max_chars=26000) + "\n\nWorkspace source snapshot:" + snapshot
        try:
            result = await self.provider.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                tools=None, reasoning=ReasoningMode.HIGH, max_tokens=min(self.max_tokens, 7000),
            )
            obj = self._extract_json(result.content) or {"verdict": "uncertain", "reason": "auditor returned non-JSON output"}
            verdict = str(obj.get("verdict") or "uncertain").lower()
            if verdict not in {"ready", "incomplete", "uncertain"}:
                obj["verdict"] = "uncertain"
            self.last_audit = obj
            return obj, result.usage
        except Exception as exc:
            self.last_audit = {"verdict": "uncertain", "reason": f"audit unavailable: {type(exc).__name__}: {exc}"}
            return self.last_audit, Usage()

    def current_audit(self) -> dict[str, Any] | None:
        return self.last_audit

    def _persist(self, plan: ProductPlan) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            path = self.state_dir / "product_plan.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"format": 1, "plan": plan.as_dict()}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    def current(self) -> dict[str, Any] | None:
        return self.last_plan.as_dict() if self.last_plan else None
