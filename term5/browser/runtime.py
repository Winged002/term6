from __future__ import annotations

import ipaddress
import json
import socket
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ..config import BrowserConfig
from ..state.artifacts import ArtifactStore


class BrowserUnavailable(RuntimeError):
    pass


class BrowserRuntime:
    """Guarded, headless browser for rendered application inspection.

    No arbitrary JavaScript execution is exposed as a model tool.  The runtime
    uses fixed internal probes for DOM metrics and preserves screenshots as
    local artifacts so binary data never inflates the normal model transcript.
    """

    def __init__(self, config: BrowserConfig, artifacts: ArtifactStore) -> None:
        self.config = config
        self.artifacts = artifacts
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.console: list[dict[str, str]] = []
        self.network_errors: list[dict[str, str]] = []
        self.last_url = ""

    @staticmethod
    def playwright_available() -> tuple[bool, str]:
        try:
            import playwright.async_api  # noqa: F401
            return True, "python package available"
        except Exception as exc:
            return False, f"playwright unavailable: {exc}"

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in {"http", "https"}:
            raise PermissionError("browser only permits http/https URLs")
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise PermissionError("browser URL has no hostname")
        allow = {str(x).lower().rstrip(".") for x in self.config.allowed_hosts}
        if host == "localhost" or host.endswith(".localhost"):
            if not self.config.allow_loopback:
                raise PermissionError("loopback browsing disabled")
            return host
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback:
                if not self.config.allow_loopback:
                    raise PermissionError("loopback browsing disabled")
                return host
            if ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
                if host not in allow:
                    raise PermissionError("private/internal browser target blocked unless explicitly allowlisted")
                return host
        except ValueError:
            pass
        if host in allow:
            return host
        if not self.config.allow_public:
            raise PermissionError(f"public browser target not allowed: {host}; add it to browser.allowed_hosts or enable browser.allow_public")
        # Guard against DNS rebinding into internal networks when public mode is enabled.
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise PermissionError(f"browser host did not resolve: {host}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
                raise PermissionError(f"unsafe browser DNS target blocked: {host} -> {ip}")
        return host

    async def _ensure(self) -> None:
        if not self.config.enabled:
            raise BrowserUnavailable("browser capability is disabled")
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise BrowserUnavailable("Playwright is not installed. Install term5-local[browser] and run 'playwright install chromium'.") from exc
        try:
            self._pw = await async_playwright().start()
            launch_kwargs = {"headless": bool(self.config.headless)}
            system_chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
            if system_chromium:
                launch_kwargs["executable_path"] = system_chromium
            self._browser = await self._pw.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context(
                viewport={"width": 1440, "height": 1000},
                ignore_https_errors=False,
            )
            self._page = await self._context.new_page()
            self._page.set_default_timeout(int(self.config.timeout_s * 1000))
            self._page.set_default_navigation_timeout(int(self.config.navigation_timeout_s * 1000))
            self._page.on("console", self._on_console)
            self._page.on("requestfailed", self._on_request_failed)
            self._page.on("pageerror", self._on_page_error)
        except Exception as exc:
            await self.close()
            raise BrowserUnavailable(
                "Chromium could not start. Install browser binaries with 'playwright install chromium'. "
                f"Underlying error: {exc}"
            ) from exc

    def _on_console(self, msg) -> None:
        try:
            self.console.append({"type": str(msg.type), "text": str(msg.text)[:4000]})
            self.console = self.console[-300:]
        except Exception:
            pass

    def _on_request_failed(self, req) -> None:
        try:
            self.network_errors.append({"url": str(req.url)[:2000], "failure": str(req.failure or "request failed")[:1000]})
            self.network_errors = self.network_errors[-300:]
        except Exception:
            pass

    def _on_page_error(self, exc) -> None:
        try:
            self.console.append({"type": "pageerror", "text": str(exc)[:4000]})
            self.console = self.console[-300:]
        except Exception:
            pass

    async def status(self) -> dict[str, Any]:
        available, detail = self.playwright_available()
        system_chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        return {
            "enabled": self.config.enabled,
            "package_available": available,
            "package_detail": detail,
            "system_chromium": system_chromium or "",
            "running": self._page is not None,
            "url": self.last_url,
            "headless": self.config.headless,
            "allow_loopback": self.config.allow_loopback,
            "allow_public": self.config.allow_public,
            "allowed_hosts": list(self.config.allowed_hosts),
        }

    async def open(self, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        self._validate_url(url)
        await self._ensure()
        allowed_wait = {"commit", "domcontentloaded", "load", "networkidle"}
        if wait_until not in allowed_wait:
            wait_until = "domcontentloaded"
        response = await self._page.goto(url, wait_until=wait_until)
        self.last_url = self._page.url
        return {
            "url": self._page.url,
            "title": await self._page.title(),
            "status": None if response is None else response.status,
            "ok": response is None or response.ok,
        }

    async def set_viewport(self, width: int, height: int) -> dict[str, int]:
        await self._ensure()
        width = max(320, min(int(width), 3840))
        height = max(480, min(int(height), 2160))
        await self._page.set_viewport_size({"width": width, "height": height})
        return {"width": width, "height": height}

    async def dom_snapshot(self, max_text_chars: int = 12000) -> dict[str, Any]:
        await self._ensure()
        data = await self._page.evaluate("""() => {
          const q = (s) => Array.from(document.querySelectorAll(s));
          const rect = document.documentElement.getBoundingClientRect();
          const over = Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0) - window.innerWidth;
          return {
            title: document.title,
            url: location.href,
            text: (document.body ? document.body.innerText : '').slice(0, 50000),
            headings: q('h1,h2,h3').slice(0,100).map(x => ({tag:x.tagName, text:(x.innerText||'').trim().slice(0,300)})),
            links: q('a').slice(0,200).map(x => ({text:(x.innerText||'').trim().slice(0,200), href:x.href})),
            buttons: q('button,[role="button"],input[type="submit"]').slice(0,120).map(x => (x.innerText||x.value||x.getAttribute('aria-label')||'').trim().slice(0,200)),
            forms: q('form').length,
            inputs: q('input,select,textarea').length,
            images: q('img,svg').length,
            dialogs: q('[role="dialog"],dialog').length,
            overflow_x_px: Math.max(0, over),
            viewport: {width: window.innerWidth, height: window.innerHeight},
            document: {width: Math.round(rect.width), height: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)}
          };
        }""")
        data["text"] = str(data.get("text") or "")[:max(1000, min(int(max_text_chars), 50000))]
        return data

    async def screenshot(self, name: str = "screenshot", full_page: bool | None = None) -> dict[str, Any]:
        await self._ensure()
        full = self.config.screenshot_full_page if full_page is None else bool(full_page)
        png = await self._page.screenshot(type="png", full_page=full)
        viewport = await self._page.evaluate("() => ({width:innerWidth,height:innerHeight})")
        ref = self.artifacts.put_bytes(
            png, kind="screenshot", name=name[:120] or "screenshot", extension=".png",
            metadata={"url": self._page.url, "viewport": viewport, "full_page": full},
        )
        return {"artifact_id": ref.id, "bytes": len(png), "url": self._page.url, "viewport": viewport, "full_page": full}

    async def click(self, selector: str) -> dict[str, Any]:
        if not self.config.allow_interaction:
            raise PermissionError("browser interaction is disabled")
        await self._ensure()
        await self._page.locator(selector).first.click()
        await self._page.wait_for_timeout(100)
        self.last_url = self._page.url
        return {"ok": True, "url": self._page.url, "selector": selector}

    async def fill(self, selector: str, value: str) -> dict[str, Any]:
        if not self.config.allow_interaction:
            raise PermissionError("browser interaction is disabled")
        await self._ensure()
        await self._page.locator(selector).first.fill(str(value))
        return {"ok": True, "selector": selector, "chars": len(str(value))}

    async def wait_for(self, selector: str, timeout_s: int = 10) -> dict[str, Any]:
        await self._ensure()
        timeout = max(1, min(int(timeout_s), 120)) * 1000
        await self._page.locator(selector).first.wait_for(timeout=timeout)
        return {"ok": True, "selector": selector}

    async def audit_pages(self, base_url: str, paths: list[str], viewports: list[dict[str, int]] | None = None) -> dict[str, Any]:
        self._validate_url(base_url)
        await self._ensure()
        clean_paths = [str(x or "/")[:1000] for x in paths[: self.config.max_batch_pages]] or ["/"]
        vps = viewports or [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}]
        vps = vps[:3]
        results: list[dict[str, Any]] = []
        for route in clean_paths:
            url = urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))
            self._validate_url(url)
            for vp in vps:
                width = max(320, min(int(vp.get("width", 1440)), 3840))
                height = max(480, min(int(vp.get("height", 1000)), 2160))
                await self.set_viewport(width, height)
                self.console.clear()
                self.network_errors.clear()
                started = time.monotonic()
                row: dict[str, Any] = {"route": route, "url": url, "viewport": {"width": width, "height": height}}
                try:
                    nav = await self.open(url)
                    dom = await self.dom_snapshot(max_text_chars=2500)
                    shot = await self.screenshot(f"audit-{route.strip('/').replace('/', '-') or 'home'}-{width}x{height}")
                    row.update({
                        "ok": bool(nav.get("ok")), "status": nav.get("status"), "title": nav.get("title"),
                        "artifact_id": shot["artifact_id"], "overflow_x_px": dom.get("overflow_x_px", 0),
                        "headings": dom.get("headings", [])[:20], "buttons": dom.get("buttons", [])[:30],
                        "forms": dom.get("forms", 0), "inputs": dom.get("inputs", 0), "images": dom.get("images", 0),
                        "console_errors": [x for x in self.console if x.get("type") in {"error", "pageerror"}][-20:],
                        "network_errors": self.network_errors[-20:],
                    })
                except Exception as exc:
                    row.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                row["elapsed_s"] = round(time.monotonic() - started, 3)
                results.append(row)
        return {"base_url": base_url, "count": len(results), "results": results}

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._page = self._context = self._browser = self._pw = None
        self.last_url = ""
