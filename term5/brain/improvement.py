from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from ..models import ReasoningMode, Usage
from ..providers.base import ModelProvider
from ..workspace.application import ApplicationMap


@dataclass(slots=True)
class ImprovementPlan:
    objective: str
    surfaces: list[str] = field(default_factory=list)
    shared_changes: list[str] = field(default_factory=list)
    page_changes: list[str] = field(default_factory=list)
    code_quality_changes: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    visible_impact: list[str] = field(default_factory=list)
    batches: list[list[str]] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
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

    def context_text(self, max_chars: int = 30000) -> str:
        lines = [
            "[term_5 improvement plan — existing application; treat as implementation acceptance criteria]",
            f"Objective: {self.objective}",
        ]
        for title, vals in (
            ("Surfaces that must be inspected/covered", self.surfaces),
            ("Shared/system changes", self.shared_changes),
            ("Page/surface changes", self.page_changes),
            ("Code quality/refactor changes", self.code_quality_changes),
            ("Expected visible impact", self.visible_impact),
            ("Verification", self.verification),
            ("Stop conditions before declaring success", self.stop_conditions),
        ):
            if vals:
                lines.append("\n### " + title)
                lines.extend("- " + x for x in vals)
        if self.batches:
            lines.append("\n### Coherent implementation batches")
            for i, batch in enumerate(self.batches, 1):
                lines.append(f"- Batch {i}: " + ", ".join(batch))
        lines.append(
            "\nExecution rule: do not declare a broad improvement request complete after a tiny local diff. "
            "Use the application map, cover the requested surfaces, prefer coherent shared-component/multi-file edits, "
            "and when browser/vision capabilities are available verify rendered before/after impact."
        )
        return "\n".join(lines)[:max_chars]


class ExistingAppImprover:
    IMPROVE_RX = re.compile(
        r"\b(improve|improving|upgrade|redesign|polish|moderni[sz]e|revamp|audit|overhaul|"
        r"make .*better|take .*next level|visual|ui|ux|existing app|existing application|clean up|refactor)\b",
        re.I,
    )
    NARROW_RX = re.compile(r"\b(one line|single typo|rename only|change only|just this text|exactly this one)\b", re.I)
    NEW_APP_RX = re.compile(r"\b(create|build|make|develop|generate|scaffold)\b.*\b(new )?(app|application|website|platform|portal|saas)\b", re.I)

    def __init__(self, provider: ModelProvider, *, max_tokens: int = 6000, max_surfaces: int = 64,
                 max_batch_files: int = 24, require_coverage: bool = True,
                 prefer_before_after: bool = True, visible_impact_min_surfaces: int = 3) -> None:
        self.provider = provider
        self.max_tokens = max(1000, min(int(max_tokens), 12000))
        self.max_surfaces = max(1, int(max_surfaces))
        self.max_batch_files = max(1, int(max_batch_files))
        self.require_coverage = bool(require_coverage)
        self.prefer_before_after = bool(prefer_before_after)
        self.visible_impact_min_surfaces = max(1, int(visible_impact_min_surfaces))
        self.last_plan: ImprovementPlan | None = None

    def should_plan(self, text: str, enabled: bool = True) -> bool:
        raw = text or ""
        if not enabled or self.NARROW_RX.search(raw):
            return False
        if self.NEW_APP_RX.search(raw) and "existing" not in raw.lower():
            return False
        return bool(self.IMPROVE_RX.search(raw))

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
        for candidate in (raw, raw[raw.find("{"):raw.rfind("}") + 1] if "{" in raw and "}" in raw else ""):
            if not candidate:
                continue
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
        return None

    @staticmethod
    def _list(v: Any, limit: int = 96) -> list[str]:
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for item in v[:limit]:
            s = str(item).strip()
            if s and s not in out:
                out.append(s[:600])
        return out

    def _fallback(self, text: str, amap: ApplicationMap) -> ImprovementPlan:
        routes = [r.route for r in amap.routes[:self.max_surfaces]]
        surface_count = len(routes)
        shared = [
            "Inspect shared shell/navigation/layout before page-local styling so improvements remain consistent.",
            "Prefer reusable components/tokens over repeated page-specific CSS.",
            "Keep existing behavior and authorization boundaries intact while improving presentation and maintainability.",
        ]
        page_changes = [f"Inspect and improve {r}" for r in routes]
        verification = [
            "Run deterministic syntax/parse verification for changed files.",
            "Run the relevant automated test suite and add regression coverage for behavior changed.",
            "Check browser console/network errors on representative routes when browser capability is available.",
            "Verify desktop and mobile layouts for the changed surface family.",
        ]
        if self.prefer_before_after:
            verification.append("Capture representative before/after screenshots and compare visible impact when browser/vision are available.")
        stop = [
            "Do not stop at infrastructure health; verify the user-facing result.",
            "Do not declare a broad redesign complete if only one surface or a tiny local style patch changed without an explicit narrow request.",
        ]
        if self.require_coverage and surface_count:
            stop.append(f"Account for all {surface_count} discovered route surfaces: modified, verified acceptable, or explicitly deferred with a reason.")
        return ImprovementPlan(
            objective=text[:1000], surfaces=routes,
            shared_changes=shared,
            page_changes=page_changes,
            code_quality_changes=[
                "Use the application graph to update route/template/style/script relationships coherently.",
                "Batch related edits before expensive rebuild/deploy cycles.",
            ],
            visible_impact=[
                "Changes should be clearly perceptible in hierarchy, density, navigation, states, or workflow—not merely code motion.",
                f"For broad requests, aim to materially improve at least {min(max(self.visible_impact_min_surfaces, 1), max(surface_count, self.visible_impact_min_surfaces))} representative surfaces unless the audit shows shared-shell changes cover more.",
            ],
            verification=verification,
            batches=[amap.shared_templates[:self.max_batch_files], routes[:self.max_batch_files]],
            stop_conditions=stop,
            generated_by_model=False,
        )

    async def plan(self, user_text: str, amap: ApplicationMap, app_context: str) -> ImprovementPlan:
        fallback = self._fallback(user_text, amap)
        system = (
            "You are term_5's existing-application improvement planner. Your job is to turn a broad request into a high-impact, "
            "repository-scale improvement plan before any edits. Prevent tiny cosmetic diffs from being mislabeled as app-wide improvement. "
            "Use discovered routes/templates/styles/tests. Prefer shared-component changes plus page-specific work, coherent batches, and explicit coverage. "
            "When browser/vision are available, require rendered verification and before/after comparison for UI work. Return ONLY JSON."
        )
        prompt = f"""User request:\n{user_text}\n\nApplication map:\n{app_context[:26000]}\n\nReturn JSON with:\n{{\n  \"objective\": string,\n  \"surfaces\": [string],\n  \"shared_changes\": [string],\n  \"page_changes\": [string],\n  \"code_quality_changes\": [string],\n  \"visible_impact\": [string],\n  \"verification\": [string],\n  \"batches\": [[string]],\n  \"stop_conditions\": [string]\n}}\nKeep it implementation-oriented and bounded."""
        try:
            res = await self.provider.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                tools=None, reasoning=ReasoningMode.HIGH, max_tokens=self.max_tokens,
            )
            obj = self._extract_json(res.content)
        except Exception:
            self.last_plan = fallback
            return fallback
        if not obj:
            fallback.usage = res.usage
            self.last_plan = fallback
            return fallback
        surfaces = self._list(obj.get("surfaces"), self.max_surfaces) or fallback.surfaces
        batches_raw = obj.get("batches") if isinstance(obj.get("batches"), list) else []
        batches: list[list[str]] = []
        for batch in batches_raw[:12]:
            vals = self._list(batch, self.max_batch_files)
            if vals:
                batches.append(vals)
        plan = ImprovementPlan(
            objective=str(obj.get("objective") or user_text)[:1000],
            surfaces=surfaces,
            shared_changes=self._list(obj.get("shared_changes")) or fallback.shared_changes,
            page_changes=self._list(obj.get("page_changes")) or fallback.page_changes,
            code_quality_changes=self._list(obj.get("code_quality_changes")) or fallback.code_quality_changes,
            verification=self._list(obj.get("verification")) or fallback.verification,
            visible_impact=self._list(obj.get("visible_impact")) or fallback.visible_impact,
            batches=batches or fallback.batches,
            stop_conditions=self._list(obj.get("stop_conditions")) or fallback.stop_conditions,
            generated_by_model=True,
            usage=res.usage,
        )
        # Deterministic safety/completeness requirements cannot be erased by the model.
        for item in fallback.stop_conditions:
            if item not in plan.stop_conditions:
                plan.stop_conditions.append(item)
        for item in fallback.verification:
            if item not in plan.verification:
                plan.verification.append(item)
        self.last_plan = plan
        return plan
