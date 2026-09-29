from __future__ import annotations

import re
from pathlib import Path
from typing import Any

KEY_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SECRET_WORDS = ("PASSWORD", "PASSWD", "PASS", "SECRET", "TOKEN", "API_KEY", "APIKEY", "PRIVATE_KEY", "CREDENTIAL", "AUTH_KEY")


def is_secret_key(key: str) -> bool:
    upper = str(key or "").upper()
    return any(word in upper for word in SECRET_WORDS)


def service_for(key: str) -> str:
    k = str(key or "").upper()
    for prefix, service in (
        ("SMTP_", "smtp"), ("MAIL_", "smtp"), ("DEEPSEEK_", "deepseek"),
        ("OPENAI_", "openai"), ("ANTHROPIC_", "anthropic"),
        ("MONGO", "mongodb"), ("DATABASE_", "database"), ("POSTGRES", "postgres"), ("PG_", "postgres"),
        ("REDIS", "redis"), ("AWS_", "aws"), ("S3_", "s3"), ("STRIPE_", "stripe"),
        ("TWILIO_", "twilio"), ("CLOUDFLARE_", "cloudflare"), ("GITHUB_", "github"),
    ):
        if k.startswith(prefix):
            return service
    return "application"


def parse_env_text(text: str, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not KEY_RX.fullmatch(key):
            continue
        value = value.strip().strip('"').strip("'")
        placeholder = (not value or value.lower() in {"changeme", "change-me", "todo", "required"} or
                       (value.startswith("<") and value.endswith(">")))
        rows.append({
            "name": key,
            "secret": is_secret_key(key),
            "required": placeholder,
            "default": "" if placeholder or is_secret_key(key) else value[:1000],
            "service": service_for(key),
            "sources": [source],
        })
    return rows


def discover_environment(root: Path, *, max_files: int = 1200) -> list[dict[str, Any]]:
    """Deterministically discover environment variable names without returning secret values."""
    root = Path(root)
    found: dict[str, dict[str, Any]] = {}

    def add(key: str, *, source: str, required: bool = True, default: str = "") -> None:
        key = str(key or "").strip()
        if not KEY_RX.fullmatch(key):
            return
        item = found.setdefault(key, {
            "name": key, "secret": is_secret_key(key), "required": bool(required),
            "default": "" if is_secret_key(key) else str(default or "")[:1000],
            "service": service_for(key), "sources": [],
        })
        item["secret"] = bool(item["secret"] or is_secret_key(key))
        item["required"] = bool(item["required"] or required)
        if not item.get("default") and default and not item["secret"]:
            item["default"] = str(default)[:1000]
        if source not in item["sources"]:
            item["sources"].append(source)

    # Explicit example/schema files have the highest-quality intent.
    for name in (".env.example", ".env.sample", ".env.template", "env.example", "example.env"):
        p = root / name
        if p.is_file():
            try:
                for row in parse_env_text(p.read_text(encoding="utf-8", errors="replace"), name):
                    add(row["name"], source=name, required=bool(row["required"]), default=str(row.get("default") or ""))
            except OSError:
                pass

    py_patterns = [
        re.compile(r"os\.getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"](?:\s*,\s*([^\)]+))?\)"),
        re.compile(r"os\.environ\.get\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"](?:\s*,\s*([^\)]+))?\)"),
        re.compile(r"os\.environ\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),
    ]
    js_patterns = [
        re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
        re.compile(r"process\.env\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]"),
    ]
    compose_rx = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-)([^}]*))?\}")
    count = 0
    skip_dirs = {".git", ".term5", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
    for p in root.rglob("*"):
        if count >= max_files:
            break
        if not p.is_file() or any(part in skip_dirs for part in p.relative_to(root).parts):
            continue
        if p.name.startswith(".env"):
            continue
        if p.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".jsx", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh"} and p.name not in {"Dockerfile", "docker-compose.yml", "compose.yml", "compose.yaml"}:
            continue
        try:
            if p.stat().st_size > 1_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count += 1
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() == ".py":
            for rx in py_patterns:
                for m in rx.finditer(text):
                    default_expr = (m.group(2) if m.lastindex and m.lastindex >= 2 else "") or ""
                    optional = bool(default_expr.strip() and default_expr.strip() not in {"None", "''", '\"\"'})
                    add(m.group(1), source=rel, required=not optional)
        if p.suffix.lower() in {".js", ".ts", ".tsx", ".jsx"}:
            for rx in js_patterns:
                for m in rx.finditer(text):
                    add(m.group(1), source=rel, required=True)
        if p.suffix.lower() in {".yml", ".yaml"} or "compose" in p.name.lower():
            for m in compose_rx.finditer(text):
                default = (m.group(3) or "").strip()
                add(m.group(1), source=rel, required=not bool(default), default=default)
    return [found[k] for k in sorted(found)]
