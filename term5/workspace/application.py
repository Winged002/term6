from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..security.paths import PathGuard

_ROUTE_RX = re.compile(r"@(?:[A-Za-z_][\w]*\.)?(?:route|get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']")
_TEMPLATE_RX = re.compile(r"render_template\(\s*[\"']([^\"']+)[\"']")
_JINJA_REL_RX = re.compile(r"\{[%{]\s*(?:extends|include|import|from)\s+[\"']([^\"']+)[\"']")
_CLASS_ATTR_RX = re.compile(r"\bclass\s*=\s*[\"']([^\"']+)[\"']", re.I)
_ID_ATTR_RX = re.compile(r"\bid\s*=\s*[\"']([^\"']+)[\"']", re.I)
_CSS_CLASS_RX = re.compile(r"(?<![A-Za-z0-9_-])\.([A-Za-z_-][A-Za-z0-9_-]*)")
_CSS_ID_RX = re.compile(r"(?<![A-Za-z0-9_-])#([A-Za-z_-][A-Za-z0-9_-]*)")
_JS_SELECTOR_RX = re.compile(r"(?:querySelector(?:All)?|getElementById)\(\s*[\"']([^\"']+)[\"']")
_LINK_RX = re.compile(r"\bhref\s*=\s*[\"']([^\"'#][^\"']*)[\"']", re.I)
_FORM_RX = re.compile(r"<form\b", re.I)
_BUTTON_RX = re.compile(r"<(?:button|input)\b", re.I)


@dataclass(slots=True)
class ApplicationSurface:
    route: str
    source: str = ""
    template: str = ""
    methods: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ApplicationMap:
    framework: str = "unknown"
    routes: list[ApplicationSurface] = field(default_factory=list)
    templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    stylesheets: dict[str, dict[str, Any]] = field(default_factory=dict)
    scripts: dict[str, dict[str, Any]] = field(default_factory=dict)
    tests: list[str] = field(default_factory=list)
    shared_templates: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["routes"] = [asdict(x) for x in self.routes]
        return raw

    def context_text(self, max_chars: int = 24000) -> str:
        lines = [
            "[term_5 existing-application map — deterministic local source analysis]",
            f"Framework: {self.framework}",
            f"Routes: {len(self.routes)}; templates: {len(self.templates)}; stylesheets: {len(self.stylesheets)}; scripts: {len(self.scripts)}; tests: {len(self.tests)}",
        ]
        if self.routes:
            lines.append("\nSurfaces/routes:")
            for r in self.routes[:80]:
                extra = f" -> {r.template}" if r.template else ""
                lines.append(f"- {r.route}{extra} [{r.source}]")
        if self.shared_templates:
            lines.append("\nShared template/shell candidates:\n" + "\n".join(f"- {x}" for x in self.shared_templates[:30]))
        if self.stylesheets:
            lines.append("\nStylesheets:\n" + "\n".join(f"- {x}" for x in list(self.stylesheets)[:30]))
        if self.tests:
            lines.append("\nTests:\n" + "\n".join(f"- {x}" for x in self.tests[:30]))
        return "\n".join(lines)[:max_chars]


class ExistingApplicationGraph:
    """Cheap repository-scale web-application structure map.

    This is deliberately deterministic and model-free.  It complements the
    import graph with web-specific relations: routes -> templates, Jinja
    extends/includes, CSS selectors used by templates, JS selectors, links,
    forms and tests.  The goal is to stop large UI requests from degenerating
    into one-file edits.
    """

    def __init__(self, guard: PathGuard, max_files: int = 4000) -> None:
        self.guard = guard
        self.max_files = max_files

    @staticmethod
    def _read(path: Path, max_bytes: int = 2_000_000) -> str:
        try:
            if path.stat().st_size > max_bytes:
                return ""
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def build(self, prefix: str = "") -> ApplicationMap:
        files: list[tuple[Path, str]] = []
        normalized_prefix = str(prefix or "").strip("/")
        for path, rel in self.guard.iter_files():
            rel_s = rel.as_posix()
            if normalized_prefix and normalized_prefix != "." and not (rel_s == normalized_prefix or rel_s.startswith(normalized_prefix + "/")):
                continue
            files.append((path, rel_s))
            if len(files) >= self.max_files:
                break

        amap = ApplicationMap()
        py_files = [(p, r) for p, r in files if p.suffix.lower() == ".py"]
        html_files = [(p, r) for p, r in files if p.suffix.lower() in {".html", ".htm", ".jinja", ".jinja2"}]
        css_files = [(p, r) for p, r in files if p.suffix.lower() == ".css"]
        js_files = [(p, r) for p, r in files if p.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}]

        if py_files and any("flask" in self._read(p, 300_000).lower() for p, _ in py_files[:100]):
            amap.framework = "flask"
        elif any((Path(r).name == "package.json") for _, r in files):
            amap.framework = "javascript-web"

        template_name_to_rel: dict[str, str] = {}
        for p, rel in html_files:
            name = Path(rel).name
            template_name_to_rel[name] = rel
            text = self._read(p)
            classes = sorted({c for group in _CLASS_ATTR_RX.findall(text) for c in group.split() if c})
            ids = sorted(set(_ID_ATTR_RX.findall(text)))
            relations = sorted(set(_JINJA_REL_RX.findall(text)))
            links = sorted(set(_LINK_RX.findall(text)))[:100]
            amap.templates[rel] = {
                "classes": classes[:300], "ids": ids[:100], "relations": relations[:100],
                "links": links, "forms": len(_FORM_RX.findall(text)), "buttons": len(_BUTTON_RX.findall(text)),
                "chars": len(text),
            }

        for p, rel in py_files:
            text = self._read(p)
            if not text:
                continue
            lines = text.splitlines()
            for m in _ROUTE_RX.finditer(text):
                route = m.group(1)
                line_no = text.count("\n", 0, m.start())
                window = "\n".join(lines[line_no:line_no + 80])
                tm = _TEMPLATE_RX.search(window)
                template = tm.group(1) if tm else ""
                if template and template in template_name_to_rel:
                    template = template_name_to_rel[template]
                amap.routes.append(ApplicationSurface(route=route, source=rel, template=template))

        for p, rel in css_files:
            text = self._read(p)
            amap.stylesheets[rel] = {
                "classes": sorted(set(_CSS_CLASS_RX.findall(text)))[:1000],
                "ids": sorted(set(_CSS_ID_RX.findall(text)))[:300],
                "chars": len(text),
            }

        for p, rel in js_files:
            text = self._read(p)
            amap.scripts[rel] = {
                "selectors": sorted(set(_JS_SELECTOR_RX.findall(text)))[:300],
                "chars": len(text),
            }

        amap.tests = sorted(r for _, r in files if self._is_test(r))[:500]

        relation_count: dict[str, int] = {}
        for rel, info in amap.templates.items():
            relation_count.setdefault(rel, 0)
            for target in info.get("relations", []):
                target_rel = template_name_to_rel.get(Path(target).name, target)
                relation_count[target_rel] = relation_count.get(target_rel, 0) + 1
        # Templates referenced by several pages are likely shells/components.
        amap.shared_templates = [k for k, v in sorted(relation_count.items(), key=lambda kv: (-kv[1], kv[0])) if v >= 2][:50]
        amap.routes = sorted(amap.routes, key=lambda r: (r.route, r.source))[:256]
        amap.summary = {
            "routes": len(amap.routes), "templates": len(amap.templates),
            "stylesheets": len(amap.stylesheets), "scripts": len(amap.scripts),
            "tests": len(amap.tests), "shared_templates": len(amap.shared_templates),
        }
        return amap

    @staticmethod
    def _is_test(rel: str) -> bool:
        p = Path(rel)
        n = p.name.lower()
        return n.startswith("test_") or n.endswith("_test.py") or ".test." in n or ".spec." in n or "tests" in p.parts or "__tests__" in p.parts

    def json(self) -> str:
        return json.dumps(self.build().as_dict(), ensure_ascii=False, indent=2)
