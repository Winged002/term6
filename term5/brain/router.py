from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ReasoningMode


@dataclass(slots=True)
class RoutingDecision:
    reasoning: ReasoningMode
    reason: str
    parallel_hint: int = 1


class AttentionRouter:
    """Cheap local heuristic router.

    Routing itself should not require a model call. The primary model can still
    escalate via `parallel_reason` when evidence gathering reveals complexity.
    """

    COMPLEX = re.compile(
        r"\b(why|debug|diagnos|architect|design|refactor|investigat|security|race|deadlock|"
        r"intermittent|migration|trade[- ]?off|root cause|plan|system design|dependency|"
        r"performance|optimi[sz]|review|threat|concurrency)\b", re.I
    )
    DEEP = re.compile(r"\b(max reasoning|deep reasoning|prove|critical|conflicting|high risk|cryptograph|security boundary)\b", re.I)
    SIMPLE = re.compile(r"^\s*(show|list|read|find|locate|what is|status|git status|print|display)\b", re.I)
    EDIT = re.compile(r"\b(fix|implement|change|modify|edit|add|remove|rename|patch|rewrite)\b", re.I)
    PRODUCT_BUILD = re.compile(r"\b(create|build|make|develop|generate|scaffold|design)\b.*\b(app|application|website|platform|portal|social|crm|e-?commerce|forum|saas)\b", re.I)
    PRODUCTION_OPS = re.compile(r"\b(deploy|deployment|production|nginx|certbot|let'?s encrypt|tls|https certificate|release rollback|rollback production|publish live)\b", re.I)
    EXISTING_IMPROVEMENT = re.compile(r"\b(improve|redesign|polish|revamp|overhaul|audit every|ui|ux|visual|moderni[sz]e|refactor)\b", re.I)

    def decide(self, text: str, configured: ReasoningMode = ReasoningMode.AUTO) -> RoutingDecision:
        if configured != ReasoningMode.AUTO:
            return RoutingDecision(configured, f"forced by configuration: {configured.value}")
        prompt = text.strip()
        if not prompt:
            return RoutingDecision(ReasoningMode.NONE, "empty input")
        if self.DEEP.search(prompt):
            return RoutingDecision(ReasoningMode.MAX, "high-risk/deep reasoning signal", 4)
        if self.PRODUCTION_OPS.search(prompt):
            return RoutingDecision(ReasoningMode.MAX, "production operation requires release/rollback planning", 4)
        if self.PRODUCT_BUILD.search(prompt):
            return RoutingDecision(ReasoningMode.HIGH, "product creation requires product planning and quality review", 4)
        if self.EXISTING_IMPROVEMENT.search(prompt):
            return RoutingDecision(ReasoningMode.HIGH, "existing-application improvement requires app mapping and rendered verification", 4)
        complex_hits = len(self.COMPLEX.findall(prompt))
        if complex_hits >= 2 or (self.COMPLEX.search(prompt) and len(prompt) > 120):
            return RoutingDecision(ReasoningMode.HIGH, "multi-signal analytical task", 4)
        if self.EDIT.search(prompt):
            return RoutingDecision(ReasoningMode.HIGH, "workspace modification requires planning/verification", 2)
        if self.COMPLEX.search(prompt):
            return RoutingDecision(ReasoningMode.HIGH, "analytical task", 2)
        if self.SIMPLE.search(prompt) and len(prompt) < 180:
            return RoutingDecision(ReasoningMode.NONE, "simple retrieval/tool-oriented request")
        if len(prompt) < 100:
            return RoutingDecision(ReasoningMode.LOW, "short semantic request")
        return RoutingDecision(ReasoningMode.LOW, "default lightweight reasoning")
