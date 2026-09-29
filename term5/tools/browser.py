from __future__ import annotations

import json

from ..models import RiskLevel, ToolDefinition, ToolResult
from ..browser import BrowserRuntime


def register_browser_tools(registry, browser: BrowserRuntime, events=None) -> None:
    def obj(props=None, required=None):
        return {"type":"object","properties":props or {},"required":required or [],"additionalProperties":False}
    def s(): return {"type":"string"}
    def i(minimum=None, maximum=None):
        d={"type":"integer"}
        if minimum is not None: d["minimum"]=minimum
        if maximum is not None: d["maximum"]=maximum
        return d
    def b(): return {"type":"boolean"}
    async def emit(kind: str, **data):
        if events is not None:
            await events.emit(kind, **data)

    async def status() -> ToolResult:
        data = await browser.status()
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "browser_status", "Inspect the guarded Playwright/Chromium capability without launching a page.",
        obj(), status, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    async def open_url(url: str, wait_until: str = "domcontentloaded") -> ToolResult:
        await emit("browser.open.started", url=url, wait_until=wait_until)
        try:
            data = await browser.open(url, wait_until=wait_until)
            await emit("browser.open.completed", **data)
            return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
        except Exception as exc:
            await emit("browser.open.completed", url=url, ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
    registry.register(ToolDefinition(
        "browser_open", "Open an allowlisted/loopback web application in the headless browser and return navigation status/title.",
        obj({"url":s(),"wait_until":{"type":"string","enum":["commit","domcontentloaded","load","networkidle"]}},["url"]),
        open_url, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    async def viewport(width: int, height: int) -> ToolResult:
        data = await browser.set_viewport(width, height)
        return ToolResult(True, json.dumps(data), data=data)
    registry.register(ToolDefinition(
        "browser_set_viewport", "Set browser viewport for responsive verification.",
        obj({"width":i(320,3840),"height":i(480,2160)},["width","height"]),
        viewport, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    async def dom(max_text_chars: int = 12000) -> ToolResult:
        data = await browser.dom_snapshot(max_text_chars=max_text_chars)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "browser_dom", "Return a bounded rendered DOM/UX snapshot: headings, links, buttons, forms, input/image counts, viewport and horizontal overflow. No arbitrary page JavaScript is exposed.",
        obj({"max_text_chars":i(1000,50000)}), dom, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    async def screenshot(name: str = "screenshot", full_page: bool = True) -> ToolResult:
        await emit("browser.screenshot.started", name=name, url=browser.last_url, full_page=full_page)
        data = await browser.screenshot(name=name, full_page=full_page)
        await emit("browser.screenshot.completed", name=name, **data)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "browser_screenshot", "Capture the rendered page as a PNG artifact. Binary image bytes stay in the local artifact store; use vision_inspect/vision_compare to analyze them.",
        obj({"name":s(),"full_page":b()}), screenshot, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    async def click(selector: str) -> ToolResult:
        data = await browser.click(selector)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "browser_click", "Click the first matching CSS selector in the current app page. Use for UI-flow verification, not destructive external workflows.",
        obj({"selector":s()},["selector"]), click, {"browser.interact"}, RiskLevel.MEDIUM, False, True,
    ))

    async def fill(selector: str, value: str) -> ToolResult:
        data = await browser.fill(selector, value)
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "browser_fill", "Fill the first matching form control. Never place secrets in model-visible arguments.",
        obj({"selector":s(),"value":s()},["selector","value"]), fill, {"browser.interact"}, RiskLevel.MEDIUM, False, True,
    ))

    async def wait_for(selector: str, timeout_s: int = 10) -> ToolResult:
        data = await browser.wait_for(selector, timeout_s)
        return ToolResult(True, json.dumps(data), data=data)
    registry.register(ToolDefinition(
        "browser_wait_for", "Wait for an element before inspecting a dynamic UI.",
        obj({"selector":s(),"timeout_s":i(1,120)},["selector"]), wait_for, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    async def diagnostics() -> ToolResult:
        data={
            "console": browser.console[-100:],
            "network_errors": browser.network_errors[-100:],
            "url": browser.last_url,
        }
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "browser_diagnostics", "Return recent browser console/page errors and failed network requests for the current rendered app.",
        obj(), diagnostics, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    vp_schema={"type":"array","maxItems":3,"items":{"type":"object","properties":{"width":i(320,3840),"height":i(480,2160)},"required":["width","height"],"additionalProperties":False}}
    async def audit_pages(base_url: str, paths: list[str], viewports: list[dict] | None = None) -> ToolResult:
        await emit("browser.audit.started", base_url=base_url, paths=paths[:64], viewports=viewports or [])
        data=await browser.audit_pages(base_url, paths, viewports)
        screenshots=[]
        for row in data.get("results", []):
            if row.get("artifact_id"):
                screenshots.append({"artifact_id":row.get("artifact_id"),"url":row.get("url"),"viewport":row.get("viewport"),"route":row.get("route")})
        await emit("browser.audit.completed", base_url=base_url, count=data.get("count",0), screenshots=screenshots[-24:])
        # Compact model result: screenshot artifact ids + rendered diagnostics, no binary.
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)
    registry.register(ToolDefinition(
        "browser_audit_pages",
        "Batch-render existing application routes across desktop/mobile/tablet viewports. Captures screenshot artifacts and returns overflow, headings, controls, console errors and failed requests. Prefer this before and after broad UI improvements instead of manually inspecting one page at a time.",
        obj({"base_url":s(),"paths":{"type":"array","minItems":1,"maxItems":64,"items":s()},"viewports":vp_schema},["base_url","paths"]),
        audit_pages, {"browser.inspect"}, RiskLevel.LOW, True, True,
    ))

    async def close() -> ToolResult:
        await browser.close()
        return ToolResult(True, "Browser closed.")
    registry.register(ToolDefinition(
        "browser_close", "Close the local Chromium inspection process and release its resources.", obj(), close,
        {"browser.inspect"}, RiskLevel.LOW, False, True,
    ))
