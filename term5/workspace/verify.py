from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None


def verify_content(path: Path, content: str) -> tuple[bool, str]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            ast.parse(content, filename=str(path))
            return True, "python syntax: PASS"
        if suffix == ".json":
            json.loads(content)
            return True, "json parse: PASS"
        if suffix == ".toml" and tomllib is not None:
            tomllib.loads(content)
            return True, "toml parse: PASS"
        if suffix in {".yaml", ".yml"}:
            try:
                import yaml
            except Exception:
                return True, "yaml verification: UNKNOWN (PyYAML not installed)"
            yaml.safe_load(content)
            return True, "yaml parse: PASS"
        return True, "verification: no syntax verifier for this file type"
    except Exception as exc:
        return False, f"verification: FAIL — {exc}"


@dataclass(slots=True)
class VerificationReport:
    ok: bool
    checks: list[str] = field(default_factory=list)
    test_output: str = ""

    def text(self) -> str:
        lines = [*self.checks]
        if self.test_output:
            lines.append(self.test_output)
        return "\n".join(lines)


class VerificationPipeline:
    """Deterministic post-change verification.

    Syntax/parse checks are always local. Tests are opt-in because they execute
    project code. Beta1 deliberately exposes only pytest/unittest, never a
    generic shell command.
    """

    def __init__(self, root: Path, *, allow_tests: bool = False, timeout_s: int = 300) -> None:
        self.root = root
        self.allow_tests = allow_tests
        self.timeout_s = max(5, min(int(timeout_s), 1800))

    def files(self, paths: list[Path]) -> VerificationReport:
        checks: list[str] = []
        ok = True
        for path in paths:
            if not path.exists() or not path.is_file():
                checks.append(f"{path.name}: FAIL — file missing")
                ok = False
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                passed, detail = verify_content(path, content)
            except Exception as exc:
                passed, detail = False, f"verification: FAIL — {exc}"
            checks.append(f"{path.relative_to(self.root).as_posix()}: {detail}")
            ok = ok and passed
        return VerificationReport(ok, checks)

    def tests(self, targets: list[str] | None = None) -> VerificationReport:
        if not self.allow_tests:
            return VerificationReport(False, ["tests: BLOCKED — enable security.allow_process_tests / TERM5_ALLOW_TESTS"])
        targets = [str(x) for x in (targets or []) if str(x).strip()][:32]
        if (self.root / "pytest.ini").exists() or (self.root / "pyproject.toml").exists() or (self.root / "tests").exists():
            cmd = ["python", "-m", "pytest", "-q", *targets]
        else:
            cmd = ["python", "-m", "unittest", *targets]
        try:
            proc = subprocess.run(cmd, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout_s)
            out = proc.stdout[-30000:] if proc.stdout else "(no output)"
            return VerificationReport(proc.returncode == 0, [f"tests: {'PASS' if proc.returncode == 0 else 'FAIL'} ({' '.join(cmd)})"], out)
        except subprocess.TimeoutExpired:
            return VerificationReport(False, [f"tests: FAIL — timeout after {self.timeout_s}s"])
        except Exception as exc:
            return VerificationReport(False, [f"tests: FAIL — {type(exc).__name__}: {exc}"])
