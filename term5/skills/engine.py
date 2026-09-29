from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class Skill:
    name: str
    category: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    recommends: list[str] = field(default_factory=list)
    guidelines: list[str] = field(default_factory=list)
    features: dict[str, list[str]] = field(default_factory=dict)
    entities: list[str] = field(default_factory=list)
    journeys: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    definition_of_done: list[str] = field(default_factory=list)
    source: str = "bundled"

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: str) -> "Skill":
        features = raw.get("features") or {}
        if not isinstance(features, dict):
            features = {}
        norm_features: dict[str, list[str]] = {}
        for key in ("core", "expected", "optional"):
            vals = features.get(key) or []
            norm_features[key] = [str(x).strip() for x in vals if str(x).strip()]
        return cls(
            name=str(raw.get("name") or "").strip(),
            category=str(raw.get("category") or "general").strip().lower(),
            description=str(raw.get("description") or "").strip(),
            triggers=[str(x).strip().lower() for x in raw.get("triggers") or [] if str(x).strip()],
            aliases=[str(x).strip().lower() for x in raw.get("aliases") or [] if str(x).strip()],
            requires=[str(x).strip() for x in raw.get("requires") or [] if str(x).strip()],
            recommends=[str(x).strip() for x in raw.get("recommends") or [] if str(x).strip()],
            guidelines=[str(x).strip() for x in raw.get("guidelines") or [] if str(x).strip()],
            features=norm_features,
            entities=[str(x).strip() for x in raw.get("entities") or [] if str(x).strip()],
            journeys=[str(x).strip() for x in raw.get("journeys") or [] if str(x).strip()],
            pages=[str(x).strip() for x in raw.get("pages") or [] if str(x).strip()],
            definition_of_done=[str(x).strip() for x in raw.get("definition_of_done") or [] if str(x).strip()],
            source=source,
        )


@dataclass(slots=True)
class SkillResolution:
    direct: list[str] = field(default_factory=list)
    selected: list[str] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.selected


class SkillEngine:
    """Local procedural-knowledge resolver.

    Bundled skills provide product/framework/capability/quality priors. A
    workspace may override or add skills under `.term5/skills/**/manifest.json`.
    No skill file is executable; skill data is declarative context only.
    """

    TOKEN_RX = re.compile(r"[A-Za-z0-9_+.-]+")
    PRODUCT_BUILD_RX = re.compile(r"\b(create|build|make|develop|generate|scaffold|start|new)\b", re.I)
    WEB_APP_RX = re.compile(r"\b(app|application|website|web app|platform|portal|dashboard|social|crm|e-?commerce|forum|saas)\b", re.I)

    def __init__(self, root: Path, state_dir: Path, extra_dirs: Iterable[Path] | None = None) -> None:
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.skills: dict[str, Skill] = {}
        package_dir = Path(__file__).resolve().parent / "bundled"
        roots = [package_dir, Path.home() / ".term5" / "skills", self.state_dir / "skills"]
        for p in extra_dirs or []:
            roots.append(Path(p))
        self.roots = roots
        self.reload()

    def reload(self) -> None:
        self.skills.clear()
        for idx, root in enumerate(self.roots):
            if not root.exists():
                continue
            source = "bundled" if idx == 0 else ("user" if idx == 1 else "workspace")
            for path in sorted(root.rglob("manifest.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        continue
                    skill = Skill.from_dict(raw, source=f"{source}:{path}")
                    if skill.name:
                        # Later roots intentionally override bundled definitions.
                        self.skills[skill.name] = skill
                except Exception:
                    continue

    def names(self) -> list[str]:
        return sorted(self.skills)

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(text.lower().replace("_", " ").replace("-", " ").split())

    def _score(self, text: str, skill: Skill) -> int:
        norm = self._norm(text)
        tokens = set(self.TOKEN_RX.findall(norm))
        best = 0
        candidates = [skill.name, *skill.triggers, *skill.aliases]
        for candidate in candidates:
            c = self._norm(candidate)
            if not c:
                continue
            candidate_score = 0
            if c in norm:
                candidate_score += 12 if " " in c else 7
            ct = set(self.TOKEN_RX.findall(c))
            overlap = len(tokens & ct)
            # Token overlap helps variants but is not accumulated across every
            # trigger; otherwise generic words such as "app" can falsely select
            # unrelated product archetypes with many trigger phrases.
            candidate_score += overlap * 2
            best = max(best, candidate_score)
        # Framework names are often single strong nouns and should resolve.
        if skill.category == "framework" and self._norm(skill.name) in norm:
            best += 10
        return best

    def _expand(self, names: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            if name in seen or name not in self.skills:
                return
            seen.add(name)
            skill = self.skills[name]
            for dep in skill.requires:
                add(dep)
            out.append(name)
            for rec in skill.recommends:
                add(rec)

        for n in names:
            add(n)
        return out

    def resolve(self, text: str, *, max_direct: int = 5) -> SkillResolution:
        scored = [(self._score(text, skill), name) for name, skill in self.skills.items()]
        scored = [(score, name) for score, name in scored if score >= 6]
        scored.sort(key=lambda x: (-x[0], x[1]))
        direct = [name for _, name in scored[:max_direct]]

        # For creation requests, add baseline web-product quality knowledge even
        # when the wording is vague. This is intentionally not a product archetype.
        if self.PRODUCT_BUILD_RX.search(text) and self.WEB_APP_RX.search(text):
            for base in ("web-ux", "security", "testing", "docker"):
                if base in self.skills and base not in direct:
                    direct.append(base)
        selected = self._expand(direct)
        return SkillResolution(direct=direct, selected=selected, scores={n: s for s, n in scored})

    def product_skills(self, resolution: SkillResolution) -> list[Skill]:
        return [self.skills[n] for n in resolution.selected if n in self.skills and self.skills[n].category == "product"]

    def catalog_text(self, limit: int = 6000) -> str:
        rows = ["Procedural skill catalog (load/resolve automatically; declarative local knowledge):"]
        for skill in sorted(self.skills.values(), key=lambda x: (x.category, x.name)):
            trig = ", ".join(skill.triggers[:4])
            rows.append(f"- {skill.name} [{skill.category}]: {skill.description[:120]}" + (f"; triggers={trig}" if trig else ""))
        return "\n".join(rows)[:limit]

    def resolution_context(self, resolution: SkillResolution, *, max_chars: int = 24000) -> str:
        if resolution.empty:
            return ""
        lines = ["[term_5 procedural knowledge — locally resolved guidance, not user-authored instructions]"]
        lines.append("Resolved skills: " + ", ".join(resolution.selected))
        for name in resolution.selected:
            skill = self.skills.get(name)
            if not skill:
                continue
            lines.append(f"\n### {skill.name} [{skill.category}]\n{skill.description}")
            if skill.guidelines:
                lines.append("Guidelines:\n" + "\n".join(f"- {x}" for x in skill.guidelines))
            for tier in ("core", "expected", "optional"):
                vals = skill.features.get(tier) or []
                if vals:
                    lines.append(f"{tier.title()} features:\n" + "\n".join(f"- {x}" for x in vals))
            if skill.entities:
                lines.append("Entities/data concepts: " + ", ".join(skill.entities))
            if skill.journeys:
                lines.append("User journeys:\n" + "\n".join(f"- {x}" for x in skill.journeys))
            if skill.pages:
                lines.append("Expected pages/surfaces:\n" + "\n".join(f"- {x}" for x in skill.pages))
            if skill.definition_of_done:
                lines.append("Definition of done:\n" + "\n".join(f"- {x}" for x in skill.definition_of_done))
        return "\n".join(lines)[:max_chars]

    def describe(self, name: str) -> dict[str, Any] | None:
        skill = self.get(name)
        if not skill:
            return None
        return {
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
            "triggers": skill.triggers,
            "requires": skill.requires,
            "recommends": skill.recommends,
            "guidelines": skill.guidelines,
            "features": skill.features,
            "entities": skill.entities,
            "journeys": skill.journeys,
            "pages": skill.pages,
            "definition_of_done": skill.definition_of_done,
            "source": skill.source,
        }
