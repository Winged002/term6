from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .command import TypedCommandRunner

_DOMAIN_RX = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
MANAGED_MARKER = "# managed-by: term_5 v5.2"


class NginxOpsError(RuntimeError):
    pass


@dataclass(slots=True)
class SiteInstallResult:
    ok: bool
    output: str
    site_path: str
    enabled_path: str
    rolled_back: bool = False


class NginxManager:
    def __init__(self, *, sites_available: str = "/etc/nginx/sites-available", sites_enabled: str = "/etc/nginx/sites-enabled",
                 timeout_s: int = 120, runner=None) -> None:
        self.available_dir = Path(sites_available)
        self.enabled_dir = Path(sites_enabled)
        self.command = TypedCommandRunner(timeout_s=timeout_s, runner=runner)

    @staticmethod
    def validate_domain(domain: str) -> str:
        d = domain.strip().lower().rstrip(".")
        if not _DOMAIN_RX.match(d):
            raise NginxOpsError("invalid public DNS hostname")
        return d

    @staticmethod
    def validate_name(name: str) -> str:
        if not _NAME_RX.match(name):
            raise NginxOpsError("invalid site name")
        return name

    def status(self) -> dict[str, Any]:
        if not self.command.which("nginx"):
            return {"available": False, "reason": "nginx CLI not found"}
        v = self.command.run(["nginx", "-v"])
        test = self.command.run(["nginx", "-t"])
        return {"available": True, "version": v.stdout, "config_ok": test.ok, "config_test": test.stdout,
                "sites_available": str(self.available_dir), "sites_enabled": str(self.enabled_dir)}

    def service_status(self) -> dict[str, Any]:
        if self.command.which("systemctl"):
            r = self.command.run(["systemctl", "is-active", "nginx"], timeout_s=30)
            return {"ok": r.ok, "active": r.ok and r.stdout.strip() == "active", "output": r.stdout, "manager": "systemd"}
        if self.command.which("pgrep"):
            r = self.command.run(["pgrep", "-x", "nginx"], timeout_s=20)
            return {"ok": True, "active": r.ok, "output": r.stdout, "manager": "process"}
        return {"ok": False, "active": False, "output": "no service-status backend available", "manager": "none"}

    def service_control(self, action: str) -> dict[str, Any]:
        if action not in {"start", "reload", "restart"}:
            raise NginxOpsError("action must be start, reload, or restart")
        if self.command.which("systemctl"):
            r = self.command.run(["systemctl", action, "nginx"], timeout_s=120)
            return {"ok": r.ok, "output": r.stdout, "argv": r.argv, "manager": "systemd"}
        if action == "start":
            r = self.command.run(["nginx"], timeout_s=120)
            return {"ok": r.ok, "output": r.stdout, "argv": r.argv, "manager": "nginx"}
        if action == "reload":
            r = self.command.run(["nginx", "-s", "reload"], timeout_s=120)
            return {"ok": r.ok, "output": r.stdout, "argv": r.argv, "manager": "nginx"}
        return {"ok": False, "output": "restart requires systemctl on this host", "argv": [], "manager": "none"}

    def render(self, *, domain: str, upstream_port: int, upstream_host: str = "127.0.0.1", websocket: bool = True,
               max_body_mb: int = 64) -> str:
        domain = self.validate_domain(domain)
        if upstream_host not in {"127.0.0.1", "localhost", "::1"}:
            raise NginxOpsError("v5.2 alpha only permits loopback Nginx upstreams")
        port = int(upstream_port)
        if not 1 <= port <= 65535:
            raise NginxOpsError("upstream port out of range")
        body = max(1, min(int(max_body_mb), 4096))
        ws = """
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection \"upgrade\";""" if websocket else ""
        return f"""{MANAGED_MARKER}
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};

    client_max_body_size {body}m;

    # Site-scoped logs let term_5 correlate requests/errors to one deployment.
    access_log /var/log/nginx/{domain}.access.log;
    error_log /var/log/nginx/{domain}.error.log;

    location / {{
        proxy_pass http://{upstream_host}:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;{ws}
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }}
}}
"""

    def plan(self, **kwargs: Any) -> dict[str, Any]:
        domain = self.validate_domain(str(kwargs["domain"]))
        name = self.validate_name(str(kwargs.get("name") or domain.replace(".", "-")))
        content = self.render(domain=domain, upstream_port=int(kwargs["upstream_port"]),
                              upstream_host=str(kwargs.get("upstream_host") or "127.0.0.1"),
                              websocket=bool(kwargs.get("websocket", True)), max_body_mb=int(kwargs.get("max_body_mb", 64)))
        return {"name": name, "domain": domain, "site_path": str(self.available_dir / f"{name}.conf"),
                "enabled_path": str(self.enabled_dir / f"{name}.conf"), "config": content}

    def install(self, *, name: str, domain: str, upstream_port: int, upstream_host: str = "127.0.0.1",
                websocket: bool = True, max_body_mb: int = 64, reload: bool = True) -> SiteInstallResult:
        name = self.validate_name(name)
        content = self.render(domain=domain, upstream_port=upstream_port, upstream_host=upstream_host,
                              websocket=websocket, max_body_mb=max_body_mb)
        self.available_dir.mkdir(parents=True, exist_ok=True)
        self.enabled_dir.mkdir(parents=True, exist_ok=True)
        site = self.available_dir / f"{name}.conf"
        enabled = self.enabled_dir / f"{name}.conf"
        old_content = site.read_bytes() if site.exists() else None
        old_link = os.readlink(enabled) if enabled.is_symlink() else None
        old_regular = enabled.read_bytes() if enabled.exists() and not enabled.is_symlink() else None
        tmp = site.with_suffix(site.suffix + ".term5.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, site)
            if enabled.exists() or enabled.is_symlink():
                enabled.unlink()
            enabled.symlink_to(site)
            test = self.command.run(["nginx", "-t"])
            if not test.ok:
                raise NginxOpsError(test.stdout or "nginx -t failed")
            if reload:
                rr = self.command.run(["nginx", "-s", "reload"])
                if not rr.ok:
                    state = self.service_status()
                    if not state.get("active"):
                        started = self.service_control("start")
                        if not started.get("ok"):
                            raise NginxOpsError(started.get("output") or rr.stdout or "nginx start/reload failed")
                    else:
                        raise NginxOpsError(rr.stdout or "nginx reload failed")
            return SiteInstallResult(True, test.stdout or "nginx configuration valid", str(site), str(enabled), False)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if old_content is None:
                site.unlink(missing_ok=True)
            else:
                site.write_bytes(old_content)
            if enabled.exists() or enabled.is_symlink():
                enabled.unlink()
            if old_link is not None:
                enabled.symlink_to(old_link)
            elif old_regular is not None:
                enabled.write_bytes(old_regular)
            # Best-effort revalidation/reload of restored config.
            self.command.run(["nginx", "-t"])
            if reload:
                self.command.run(["nginx", "-s", "reload"])
            return SiteInstallResult(False, str(exc), str(site), str(enabled), True)

    def remove(self, name: str, *, reload: bool = True) -> dict[str, Any]:
        name = self.validate_name(name)
        site = self.available_dir / f"{name}.conf"
        enabled = self.enabled_dir / f"{name}.conf"
        if site.exists():
            text = site.read_text(encoding="utf-8", errors="replace")
            if MANAGED_MARKER not in text:
                raise NginxOpsError("refusing to remove an Nginx site not marked as term_5-managed")
        enabled.unlink(missing_ok=True)
        site.unlink(missing_ok=True)
        test = self.command.run(["nginx", "-t"])
        if not test.ok:
            return {"ok": False, "output": test.stdout, "removed": True, "reload": False}
        rr = self.command.run(["nginx", "-s", "reload"]) if reload else None
        return {"ok": rr.ok if rr else True, "output": rr.stdout if rr else test.stdout, "removed": True, "reload": bool(reload)}

    def managed_sites(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if not self.available_dir.exists():
            return out
        for p in sorted(self.available_dir.glob("*.conf")):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if MANAGED_MARKER in text:
                out.append({"name": p.stem, "path": str(p), "enabled": str((self.enabled_dir / p.name).exists())})
        return out

    def logs(self, *, kind: str = "error", lines: int = 200) -> str:
        p = Path("/var/log/nginx/access.log" if kind == "access" else "/var/log/nginx/error.log")
        if not p.is_file():
            return f"log not found: {p}"
        data = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(data[-max(1, min(int(lines), 5000)):])
