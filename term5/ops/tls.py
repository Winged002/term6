from __future__ import annotations

from pathlib import Path
from typing import Any

from .command import TypedCommandRunner
from .nginx import NginxManager


class TLSOpsError(RuntimeError):
    pass


class TLSManager:
    def __init__(self, *, timeout_s: int = 300, runner=None) -> None:
        self.command = TypedCommandRunner(timeout_s=timeout_s, runner=runner)

    def status(self, domain: str = "") -> dict[str, Any]:
        if not self.command.which("certbot"):
            return {"available": False, "reason": "certbot CLI not found"}
        args = ["certbot", "certificates"]
        r = self.command.run(args, timeout_s=120)
        out: dict[str, Any] = {"available": True, "ok": r.ok, "output": r.stdout}
        if domain:
            d = NginxManager.validate_domain(domain)
            live = Path("/etc/letsencrypt/live") / d
            out.update({"domain": d, "live_dir": str(live), "certificate_present": (live / "fullchain.pem").exists() and (live / "privkey.pem").exists()})
        return out

    def issue(self, *, domain: str, email: str, redirect: bool = True, staging: bool = False) -> dict[str, Any]:
        d = NginxManager.validate_domain(domain)
        email = email.strip()
        if "@" not in email or len(email) > 254:
            raise TLSOpsError("a valid contact email is required for Let's Encrypt")
        args = ["certbot", "--nginx", "-d", d, "--non-interactive", "--agree-tos", "--email", email]
        args.append("--redirect" if redirect else "--no-redirect")
        if staging:
            args.append("--staging")
        r = self.command.run(args, timeout_s=600)
        return {"ok": r.ok, "output": r.stdout, "domain": d, "staging": staging, "argv": r.argv}

    def renew_dry_run(self, domain: str = "") -> dict[str, Any]:
        args = ["certbot", "renew", "--dry-run", "--non-interactive"]
        if domain:
            args.extend(["--cert-name", NginxManager.validate_domain(domain)])
        r = self.command.run(args, timeout_s=900)
        return {"ok": r.ok, "output": r.stdout, "argv": r.argv}
