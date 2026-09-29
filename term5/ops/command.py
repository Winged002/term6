from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class CommandResult:
    ok: bool
    code: int
    stdout: str
    argv: list[str]


class TypedCommandRunner:
    """Typed argv-only host command runner. Never evaluates a shell string."""

    def __init__(self, *, timeout_s: int = 120, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self.timeout_s = max(5, int(timeout_s))
        self._runner = runner or subprocess.run

    @staticmethod
    def which(binary: str) -> str | None:
        return shutil.which(binary)

    def run(self, argv: list[str], *, cwd: Path | None = None, timeout_s: int | None = None) -> CommandResult:
        if not argv or not isinstance(argv[0], str):
            raise ValueError("argv must contain a binary name")
        if not self.which(argv[0]):
            return CommandResult(False, 127, f"{argv[0]} is not installed or not on PATH", list(argv))
        proc = self._runner(
            list(argv), cwd=str(cwd) if cwd else None, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout_s or self.timeout_s, env=os.environ.copy(), shell=False,
        )
        return CommandResult(proc.returncode == 0, int(proc.returncode), (proc.stdout or "").strip(), list(argv))
