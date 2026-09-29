from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class DockerUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class CommandResult:
    ok: bool
    code: int
    stdout: str
    argv: list[str]


class DockerManager:
    """Typed Docker/Compose adapter. It never executes a shell string."""

    def __init__(self, *, timeout_s: int = 300, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self.timeout_s = max(10, int(timeout_s))
        self._runner = runner or subprocess.run

    @property
    def cli_path(self) -> str | None:
        return shutil.which("docker")

    def _run(self, argv: list[str], *, cwd: Path | None = None, timeout_s: int | None = None) -> CommandResult:
        if not self.cli_path:
            raise DockerUnavailable("docker CLI is not installed or is not on PATH")
        proc = self._runner(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s or self.timeout_s,
            env=os.environ.copy(),
            shell=False,
        )
        return CommandResult(proc.returncode == 0, int(proc.returncode), (proc.stdout or "").strip(), list(argv))

    def engine_status(self) -> dict[str, Any]:
        if not self.cli_path:
            return {"available": False, "reason": "docker CLI not found"}
        result = self._run(["docker", "version", "--format", "{{json .Server}}"], timeout_s=20)
        if not result.ok:
            return {"available": False, "reason": result.stdout or "Docker Engine unavailable", "cli": self.cli_path}
        data: dict[str, Any] = {"available": True, "cli": self.cli_path}
        try:
            server = json.loads(result.stdout)
            if isinstance(server, dict):
                data["server_version"] = server.get("Version")
                data["api_version"] = server.get("ApiVersion")
                data["os"] = server.get("Os")
                data["arch"] = server.get("Arch")
        except Exception:
            data["raw"] = result.stdout
        compose = self._run(["docker", "compose", "version", "--short"], timeout_s=20)
        data["compose_available"] = compose.ok
        if compose.ok:
            data["compose_version"] = compose.stdout
        else:
            data["compose_error"] = compose.stdout
        return data

    @staticmethod
    def _compose_prefix(project_dir: Path, compose_file: str, project: str) -> list[str]:
        return ["docker", "compose", "-f", compose_file, "-p", project]

    def compose(self, project_dir: Path, compose_file: str, project: str, args: list[str], *, timeout_s: int | None = None) -> CommandResult:
        return self._run(self._compose_prefix(project_dir, compose_file, project) + args, cwd=project_dir, timeout_s=timeout_s)

    def validate(self, project_dir: Path, compose_file: str, project: str) -> CommandResult:
        return self.compose(project_dir, compose_file, project, ["config", "-q"], timeout_s=60)

    def build(self, project_dir: Path, compose_file: str, project: str, services: list[str] | None = None) -> CommandResult:
        return self.compose(project_dir, compose_file, project, ["build", *(services or [])], timeout_s=max(self.timeout_s, 900))

    def up(self, project_dir: Path, compose_file: str, project: str, *, build: bool = False, wait: bool = True,
           wait_timeout_s: int = 120, services: list[str] | None = None) -> CommandResult:
        args = ["up", "-d"]
        if build:
            args.append("--build")
        if wait:
            args.extend(["--wait", "--wait-timeout", str(max(1, int(wait_timeout_s)))])
        args.extend(services or [])
        result = self.compose(project_dir, compose_file, project, args, timeout_s=max(self.timeout_s, wait_timeout_s + 300))
        if not result.ok and wait and ("unknown flag" in result.stdout.lower() or "unknown shorthand" in result.stdout.lower()):
            # Older Compose fallback: start detached; term_5 performs its own status/HTTP checks afterwards.
            args = ["up", "-d"] + (["--build"] if build else []) + list(services or [])
            return self.compose(project_dir, compose_file, project, args, timeout_s=max(self.timeout_s, 600))
        return result

    def stop(self, project_dir: Path, compose_file: str, project: str, services: list[str] | None = None) -> CommandResult:
        return self.compose(project_dir, compose_file, project, ["stop", *(services or [])], timeout_s=180)

    def start(self, project_dir: Path, compose_file: str, project: str, services: list[str] | None = None) -> CommandResult:
        return self.compose(project_dir, compose_file, project, ["start", *(services or [])], timeout_s=180)

    def restart(self, project_dir: Path, compose_file: str, project: str, services: list[str] | None = None) -> CommandResult:
        return self.compose(project_dir, compose_file, project, ["restart", *(services or [])], timeout_s=240)

    def down(self, project_dir: Path, compose_file: str, project: str) -> CommandResult:
        # Deliberately no --volumes: app data survives normal teardown.
        return self.compose(project_dir, compose_file, project, ["down", "--remove-orphans"], timeout_s=240)

    def logs(self, project_dir: Path, compose_file: str, project: str, *, service: str = "", tail: int = 200) -> CommandResult:
        args = ["logs", "--no-color", "--tail", str(max(1, min(int(tail), 5000)))]
        if service:
            args.append(service)
        return self.compose(project_dir, compose_file, project, args, timeout_s=60)



    def stats(self, project_dir: Path, compose_file: str, project: str) -> dict[str, Any]:
        """Collect one bounded no-stream resource snapshot for containers in one Compose project."""
        ids = self.compose(project_dir, compose_file, project, ["ps", "-q"], timeout_s=30)
        if not ids.ok:
            return {"ok": False, "error": ids.stdout, "containers": []}
        container_ids = [x.strip() for x in ids.stdout.splitlines() if x.strip()][:64]
        if not container_ids:
            return {"ok": True, "containers": [], "cpu_percent": None, "memory_percent": None}
        result = self._run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_ids], timeout_s=45)
        if not result.ok:
            return {"ok": False, "error": result.stdout, "containers": []}
        rows: list[dict[str, Any]] = []
        cpus: list[float] = []
        mems: list[float] = []
        for line in result.stdout.splitlines():
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            cpu = None
            mem = None
            try:
                cpu = float(str(item.get("CPUPerc") or "").strip().rstrip("%"))
                cpus.append(cpu)
            except Exception:
                pass
            try:
                mem = float(str(item.get("MemPerc") or "").strip().rstrip("%"))
                mems.append(mem)
            except Exception:
                pass
            rows.append({
                "name": item.get("Name"), "container": item.get("Container"),
                "cpu_percent": cpu, "memory_percent": mem, "memory_usage": item.get("MemUsage"),
                "net_io": item.get("NetIO"), "block_io": item.get("BlockIO"), "pids": item.get("PIDs"),
            })
        return {
            "ok": True, "containers": rows,
            "cpu_percent": round(sum(cpus), 3) if cpus else None,
            "memory_percent": round(sum(mems) / len(mems), 3) if mems else None,
        }

    def compose_capture_bytes(self, project_dir: Path, compose_file: str, project: str, args: list[str], *, timeout_s: int | None = None) -> tuple[bool, int, bytes, list[str]]:
        """Run one typed Compose argv and preserve binary stdout for database backups."""
        if not self.cli_path:
            raise DockerUnavailable("docker CLI is not installed or is not on PATH")
        argv = self._compose_prefix(project_dir, compose_file, project) + list(args)
        proc = subprocess.run(
            argv, cwd=str(project_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_s or self.timeout_s, env=os.environ.copy(), shell=False,
        )
        stdout = proc.stdout or b""
        stderr = proc.stderr or b""
        if proc.returncode != 0 and stderr:
            stdout = stdout + (b"\n" if stdout else b"") + stderr
        return proc.returncode == 0, int(proc.returncode), bytes(stdout), argv

    def ps(self, project_dir: Path, compose_file: str, project: str) -> tuple[CommandResult, list[dict[str, Any]]]:
        result = self.compose(project_dir, compose_file, project, ["ps", "--format", "json"], timeout_s=60)
        if not result.ok:
            return result, []
        raw = result.stdout.strip()
        if not raw:
            return result, []
        rows: list[dict[str, Any]] = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                rows = [x for x in parsed if isinstance(x, dict)]
            elif isinstance(parsed, dict):
                rows = [parsed]
        except Exception:
            for line in raw.splitlines():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except Exception:
                    continue
        return result, rows
