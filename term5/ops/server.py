from __future__ import annotations

import os
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .command import TypedCommandRunner

_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")


class ServerOpsError(RuntimeError):
    pass


class ServerManager:
    """Bounded host observability and allowlisted systemd control.

    There is deliberately no generic shell. Read operations expose a compact
    production-health picture; write operations are restricted to explicit
    service names from configuration and to a tiny action set.
    """

    def __init__(self, *, root: Path, timeout_s: int = 120, allowed_services: list[str] | None = None,
                 runner=None, disk_warn_percent: int = 90) -> None:
        self.root = Path(root).resolve()
        self.cmd = TypedCommandRunner(timeout_s=timeout_s, runner=runner)
        self.allowed_services = tuple(dict.fromkeys(str(x).strip() for x in (allowed_services or []) if str(x).strip()))
        self.disk_warn_percent = max(1, min(int(disk_warn_percent), 99))

    @staticmethod
    def _meminfo() -> dict[str, int]:
        out: dict[str, int] = {}
        try:
            for line in Path('/proc/meminfo').read_text(encoding='utf-8', errors='replace').splitlines():
                if ':' not in line:
                    continue
                key, raw = line.split(':', 1)
                parts = raw.strip().split()
                if not parts:
                    continue
                try:
                    value = int(parts[0]) * (1024 if len(parts) > 1 and parts[1].lower() == 'kb' else 1)
                except ValueError:
                    continue
                out[key] = value
        except OSError:
            pass
        return out

    @staticmethod
    def _uptime_s() -> float | None:
        try:
            return float(Path('/proc/uptime').read_text().split()[0])
        except Exception:
            return None

    @staticmethod
    def _load() -> list[float]:
        try:
            return [round(float(x), 3) for x in os.getloadavg()]
        except Exception:
            return []

    @staticmethod
    def _disk(path: Path) -> dict[str, Any]:
        try:
            u = shutil.disk_usage(path)
            pct = round((u.used / u.total * 100.0), 2) if u.total else 0.0
            return {"path": str(path), "total": u.total, "used": u.used, "free": u.free, "used_percent": pct}
        except Exception as exc:
            return {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}

    def status(self) -> dict[str, Any]:
        mem = self._meminfo()
        total = int(mem.get('MemTotal', 0))
        available = int(mem.get('MemAvailable', 0))
        used = max(0, total - available) if total else 0
        memory = {
            "total": total,
            "available": available,
            "used": used,
            "used_percent": round(used / total * 100.0, 2) if total else None,
        }
        root_disk = self._disk(Path('/'))
        workspace_disk = self._disk(self.root)
        warnings: list[str] = []
        for row in (root_disk, workspace_disk):
            pct = row.get('used_percent')
            if isinstance(pct, (int, float)) and pct >= self.disk_warn_percent:
                warnings.append(f"disk usage high on {row.get('path')}: {pct}%")
        if isinstance(memory.get('used_percent'), (int, float)) and memory['used_percent'] >= 95:
            warnings.append(f"memory usage high: {memory['used_percent']}%")
        return {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "load_average": self._load(),
            "uptime_s": self._uptime_s(),
            "memory": memory,
            "disk": {"root": root_disk, "workspace": workspace_disk},
            "systemd": bool(self.cmd.which('systemctl')),
            "ss": bool(self.cmd.which('ss')),
            "allowed_services": list(self.allowed_services),
            "warnings": warnings,
        }

    def _validate_service(self, name: str, *, require_allowed: bool = True) -> str:
        name = str(name or '').strip()
        if not _SERVICE_RE.fullmatch(name):
            raise ServerOpsError('invalid service name')
        if require_allowed and name not in self.allowed_services:
            raise ServerOpsError(f"service is not allowlisted: {name}")
        return name

    def service_status(self, name: str) -> dict[str, Any]:
        name = self._validate_service(name)
        if not self.cmd.which('systemctl'):
            return {"ok": False, "service": name, "error": "systemctl is unavailable"}
        active = self.cmd.run(['systemctl', 'is-active', name], timeout_s=20)
        enabled = self.cmd.run(['systemctl', 'is-enabled', name], timeout_s=20)
        return {
            "ok": active.ok,
            "service": name,
            "active": (active.stdout or '').strip(),
            "enabled": (enabled.stdout or '').strip(),
            "active_code": active.code,
            "enabled_code": enabled.code,
        }

    def service_control(self, name: str, action: str) -> dict[str, Any]:
        name = self._validate_service(name)
        action = str(action or '').strip().lower()
        if action not in {'start', 'stop', 'restart', 'reload'}:
            raise ServerOpsError('action must be start, stop, restart, or reload')
        if not self.cmd.which('systemctl'):
            return {"ok": False, "service": name, "action": action, "error": "systemctl is unavailable"}
        result = self.cmd.run(['systemctl', action, name], timeout_s=120)
        data = {"ok": result.ok, "service": name, "action": action, "output": result.stdout, "code": result.code}
        if result.ok:
            data["status"] = self.service_status(name)
        return data

    def journal(self, name: str, lines: int = 200) -> dict[str, Any]:
        name = self._validate_service(name)
        lines = max(1, min(int(lines), 5000))
        if not self.cmd.which('journalctl'):
            return {"ok": False, "service": name, "error": "journalctl is unavailable"}
        result = self.cmd.run(['journalctl', '-u', name, '-n', str(lines), '--no-pager', '--output=short-iso'], timeout_s=60)
        return {"ok": result.ok, "service": name, "lines": lines, "output": result.stdout, "code": result.code}

    def ports(self, lines: int = 300) -> dict[str, Any]:
        lines = max(1, min(int(lines), 2000))
        if not self.cmd.which('ss'):
            return {"ok": False, "error": "ss is unavailable", "output": ""}
        result = self.cmd.run(['ss', '-lntupH'], timeout_s=30)
        rows = (result.stdout or '').splitlines()[:lines]
        return {"ok": result.ok, "count": len(rows), "output": '\n'.join(rows), "truncated": len((result.stdout or '').splitlines()) > len(rows)}

    def audit(self) -> dict[str, Any]:
        host = self.status()
        services: dict[str, Any] = {}
        for name in self.allowed_services:
            try:
                services[name] = self.service_status(name)
            except Exception as exc:
                services[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        ports = self.ports(lines=400)
        warnings = list(host.get('warnings') or [])
        for name, row in services.items():
            if not row.get('ok'):
                warnings.append(f"service not active: {name}")
        return {
            "ok": not warnings,
            "checked_at": time.time(),
            "host": host,
            "services": services,
            "ports": ports,
            "warnings": warnings,
        }
