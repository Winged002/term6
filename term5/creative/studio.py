from __future__ import annotations

import base64
import html
import os
import urllib.request
from typing import Any


class CreativeStudio:
    """Local creative rendering adapter.

    DeepSeek remains the coordinator. This class only renders visual concepts.
    OpenAI credentials are resolved locally from the host environment or the
    trusted project Configuration vault and are never returned in tool results.
    """

    def __init__(self, config, artifacts, *, configuration=None, projects=None, redactor=None) -> None:
        self.config = config
        self.artifacts = artifacts
        self.configuration = configuration
        self.projects = projects
        self.redactor = redactor
        self._client = None

    def _project_name(self, project: str = "") -> str:
        if project:
            return project
        if self.projects is not None and self.projects.registry.active_name:
            return self.projects.registry.active_name
        return ""

    def _openai_key(self, project: str = "", environment: str = "production") -> str:
        env_name = str(getattr(self.config, "api_key_env", "OPENAI_API_KEY") or "OPENAI_API_KEY")
        direct = os.getenv(env_name, "")
        if direct:
            if self.redactor is not None:
                self.redactor.add(direct)
            return direct
        if self.configuration is None:
            return ""
        name = self._project_name(project)
        if not name:
            return ""
        try:
            item, env, profile = self.configuration._profile(name, environment)
            ref = (profile.get("secret_refs") or {}).get(env_name)
            if ref and self.configuration.vault.has(ref):
                value = self.configuration.vault.resolve(ref)
                if value and self.redactor is not None:
                    self.redactor.add(value)
                return value
            target = self.configuration.projects.path(item.name) / self.configuration.env_filename(env)
            from_file = self.configuration._read_env_map(target).get(env_name, "") if target.exists() else ""
            if from_file and self.redactor is not None:
                self.redactor.add(from_file)
            return from_file
        except Exception:
            return ""

    def status(self, project: str = "", environment: str = "production") -> dict[str, Any]:
        return {
            "enabled": bool(getattr(self.config, "enabled", True)),
            "openai_enabled": bool(getattr(self.config, "openai_enabled", True)),
            "openai_configured": bool(self._openai_key(project, environment)),
            "model": str(getattr(self.config, "model", "gpt-image-2.5-flare")),
            "quality": str(getattr(self.config, "quality", "medium")),
            "size": str(getattr(self.config, "size", "1536x1024")),
            "max_options": int(getattr(self.config, "max_options", 4)),
            "api_key_name": str(getattr(self.config, "api_key_env", "OPENAI_API_KEY")),
        }

    async def generate_images(self, *, brief: str, directions: list[dict[str, Any]], project: str = "", environment: str = "production") -> dict[str, Any]:
        if not getattr(self.config, "enabled", True) or not getattr(self.config, "openai_enabled", True):
            raise RuntimeError("OpenAI creative image generation is disabled")
        key = self._openai_key(project, environment)
        if not key:
            raise RuntimeError(f"{getattr(self.config, 'api_key_env', 'OPENAI_API_KEY')} is not configured")
        try:
            from openai import AsyncOpenAI
        except Exception as exc:
            raise RuntimeError("The openai Python package is required for Creative Studio") from exc
        client = AsyncOpenAI(api_key=key)
        max_options = max(1, min(int(getattr(self.config, "max_options", 4)), 6))
        output: list[dict[str, Any]] = []
        for idx, direction in enumerate((directions or [])[:max_options], start=1):
            label = str(direction.get("label") or f"Option {idx}")[:120]
            prompt = str(direction.get("prompt") or direction.get("description") or "")[:24000]
            merged = (
                "Create a polished product/UI design concept mockup. This is a visual direction for a software design decision, not a final implementation. "
                "Make the visual differences from other concepts clear. Do not add explanatory borders or watermarks.\n\n"
                f"PRODUCT BRIEF:\n{brief[:12000]}\n\nDIRECTION {idx} — {label}:\n{prompt}"
            )
            merged = merged[:32000]
            kwargs = {
                "model": str(getattr(self.config, "model", "gpt-image-2.5-flare")),
                "prompt": merged,
                "n": 1,
                "size": str(getattr(self.config, "size", "1536x1024")),
                "quality": str(getattr(self.config, "quality", "medium")),
                "output_format": "png",
            }
            response = await client.images.generate(**kwargs)
            data = list(getattr(response, "data", None) or [])
            if not data:
                raise RuntimeError(f"image provider returned no image for {label}")
            item = data[0]
            b64 = getattr(item, "b64_json", None)
            url = getattr(item, "url", None)
            if isinstance(item, dict):
                b64 = b64 or item.get("b64_json")
                url = url or item.get("url")
            if b64:
                raw = base64.b64decode(b64)
            elif url:
                with urllib.request.urlopen(str(url), timeout=60) as resp:
                    raw = resp.read(25 * 1024 * 1024 + 1)
                if len(raw) > 25 * 1024 * 1024:
                    raise RuntimeError("creative image exceeded 25MiB safety limit")
            else:
                raise RuntimeError("image provider response contained neither base64 image data nor a downloadable image URL")
            ref = self.artifacts.put_bytes(
                raw, kind="creative_mockup", name=f"creative-{idx}-{label}", extension=".png",
                metadata={"project": self._project_name(project), "label": label, "provider": "openai", "model": kwargs["model"], "brief": brief[:2000]},
            )
            output.append({"id": f"option_{idx}", "label": label, "artifact_id": ref.id, "description": str(direction.get("description") or "")[:2000]})
        return {"provider": "openai", "model": str(getattr(self.config, "model", "")), "options": output, "count": len(output)}

    def generate_svg(self, *, brief: str, directions: list[dict[str, Any]], project: str = "") -> dict[str, Any]:
        max_options = max(1, min(int(getattr(self.config, "max_options", 4)), 6))
        output = []
        for idx, direction in enumerate((directions or [])[:max_options], start=1):
            label = str(direction.get("label") or f"Option {idx}")[:120]
            description = str(direction.get("description") or direction.get("prompt") or "")[:600]
            palette = direction.get("palette") if isinstance(direction.get("palette"), list) else []
            palette = [str(x) for x in palette[:5] if str(x).startswith("#") and len(str(x)) in {4, 7}]
            if not palette:
                palette = ["#111827", "#F8FAFC", "#64748B", "#CBD5E1"]
            swatches = "".join(f'<rect x="{60+i*82}" y="420" width="64" height="64" rx="14" fill="{html.escape(c)}"/>' for i, c in enumerate(palette))
            svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">
<rect width="1200" height="760" fill="{html.escape(palette[1] if len(palette)>1 else '#F8FAFC')}"/>
<rect x="44" y="44" width="1112" height="672" rx="38" fill="{html.escape(palette[0])}" opacity="0.98"/>
<text x="72" y="112" font-family="Inter,Arial,sans-serif" font-size="24" fill="white" opacity="0.65">TERM_6 CREATIVE CONCEPT {idx}</text>
<text x="72" y="174" font-family="Inter,Arial,sans-serif" font-size="54" font-weight="700" fill="white">{html.escape(label)}</text>
<foreignObject x="72" y="215" width="840" height="180"><div xmlns="http://www.w3.org/1999/xhtml" style="font:26px/1.5 Inter,Arial,sans-serif;color:#fff;opacity:.78">{html.escape(description)}</div></foreignObject>
{swatches}
<rect x="72" y="535" width="430" height="92" rx="24" fill="white" opacity="0.12"/>
<rect x="528" y="535" width="270" height="92" rx="24" fill="white" opacity="0.07"/>
<rect x="824" y="535" width="260" height="92" rx="24" fill="white" opacity="0.07"/>
<text x="72" y="688" font-family="Inter,Arial,sans-serif" font-size="18" fill="white" opacity="0.45">{html.escape(brief[:110])}</text>
</svg>'''
            ref = self.artifacts.put_bytes(svg.encode("utf-8"), kind="creative_mockup", name=f"creative-svg-{idx}-{label}", extension=".svg", metadata={"project": self._project_name(project), "label": label, "provider": "svg"})
            output.append({"id": f"option_{idx}", "label": label, "artifact_id": ref.id, "description": description})
        return {"provider": "svg", "options": output, "count": len(output)}
