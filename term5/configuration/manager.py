from __future__ import annotations

import json
import os
import re
import smtplib
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .discover import KEY_RX, discover_environment, is_secret_key, parse_env_text, service_for
from .vault import SecretVault

FORMAT = 1
MASK = "••••••••"
_ALLOWED_ENV_FILE = re.compile(r"^\.env(?:\.[A-Za-z0-9_.-]+)?$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_env(name: str) -> str:
    value = str(name or "production").strip().lower().replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value):
        raise ValueError("environment must use letters, numbers, '.', '_' or '-'")
    return value


class ConfigurationManager:
    """Project/environment configuration coordinator with a non-model secret boundary."""

    def __init__(self, root: Path, state_dir: Path, projects, *, config, redactor=None) -> None:
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.projects = projects
        self.config = config
        self.path = self.state_dir / str(config.values_file)
        self._data: dict[str, Any] = {"format": FORMAT, "projects": {}}
        self.redactor = redactor
        self.vault = SecretVault(self.state_dir / str(config.secret_file), on_secret=(redactor.add if redactor is not None else None))
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"configuration registry is unreadable: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("format") != FORMAT or not isinstance(raw.get("projects"), dict):
            raise RuntimeError("unsupported or corrupt configuration registry format")
        self._data = raw

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _project(self, project: str = ""):
        if self.projects is None:
            raise RuntimeError("projects are disabled")
        return self.projects._item(project)

    def _profile(self, project: str = "", environment: str = "", *, create: bool = True) -> tuple[Any, str, dict[str, Any]]:
        item = self._project(project)
        env = _clean_env(environment or self.config.default_environment)
        projects = self._data.setdefault("projects", {})
        p = projects.get(item.name)
        if p is None:
            if not create:
                return item, env, {}
            p = {"environments": {}}
            projects[item.name] = p
        envs = p.setdefault("environments", {})
        profile = envs.get(env)
        if profile is None:
            if not create:
                return item, env, {}
            profile = {"values": {}, "secret_refs": {}, "requirements": {}, "pending": None, "updated_at": _now()}
            envs[env] = profile
        return item, env, profile

    def _secret_ref(self, project: str, environment: str, key: str) -> str:
        return f"project:{project}:environment:{environment}:{key}"

    def _project_root(self, project: str = "") -> Path:
        item = self._project(project)
        return self.projects.path(item.name)

    @staticmethod
    def env_filename(environment: str) -> str:
        env = _clean_env(environment)
        return ".env" if env == "production" else f".env.{env}"

    def discover(self, project: str = "", environment: str = "", *, persist: bool = True) -> dict[str, Any]:
        item, env, profile = self._profile(project, environment)
        rows = discover_environment(self.projects.path(item.name), max_files=int(self.config.scan_max_files))
        reqs = profile.setdefault("requirements", {})
        changed = False
        for row in rows[: int(self.config.max_variables)]:
            key = row["name"]
            existing = reqs.get(key) or {}
            merged = {
                "name": key,
                "secret": bool(existing.get("secret") or row.get("secret")),
                "required": bool(existing.get("required") or row.get("required")),
                "default": existing.get("default") or row.get("default") or "",
                "description": existing.get("description") or "",
                "service": existing.get("service") or row.get("service") or service_for(key),
                "sources": sorted(set((existing.get("sources") or []) + (row.get("sources") or [])))[:20],
                "updated_at": _now(),
            }
            if reqs.get(key) != merged:
                reqs[key] = merged
                changed = True
        if changed and persist:
            profile["updated_at"] = _now()
            self.save()
        return self.status(item.name, env, discover=False)

    def require(self, keys: list[str], *, project: str = "", environment: str = "", secret_keys: list[str] | None = None,
                reason: str = "", service: str = "application", descriptions: dict[str, str] | None = None) -> dict[str, Any]:
        item, env, profile = self._profile(project, environment)
        secret_set = {str(x).strip().upper() for x in (secret_keys or [])}
        reqs = profile.setdefault("requirements", {})
        clean_keys: list[str] = []
        for raw in keys:
            key = str(raw or "").strip().upper()
            if not KEY_RX.fullmatch(key) or key in clean_keys:
                continue
            clean_keys.append(key)
            old = reqs.get(key) or {}
            reqs[key] = {
                "name": key,
                "secret": bool(key in secret_set or old.get("secret") or is_secret_key(key)),
                "required": True,
                "default": old.get("default") or "",
                "description": (descriptions or {}).get(key) or old.get("description") or "",
                "service": service or old.get("service") or service_for(key),
                "sources": sorted(set((old.get("sources") or []) + ["agent requirement"])),
                "updated_at": _now(),
            }
        pending = {
            "id": "input_" + uuid.uuid4().hex[:12], "status": "waiting", "reason": str(reason or "Configuration required")[:4000],
            "keys": clean_keys, "service": str(service or "application")[:100], "created_at": _now(), "updated_at": _now(),
        }
        profile["pending"] = pending
        profile["updated_at"] = _now()
        self.save()
        return self.status(item.name, env, discover=False)

    def _configured(self, item, env: str, profile: dict[str, Any], key: str) -> tuple[bool, str]:
        if key in (profile.get("values") or {}) and str(profile["values"].get(key) or "") != "":
            return True, "managed"
        ref = (profile.get("secret_refs") or {}).get(key)
        if ref and self.vault.has(ref):
            return True, "vault"
        target = self.projects.path(item.name) / self.env_filename(env)
        if target.is_file():
            try:
                for row in parse_env_text(target.read_text(encoding="utf-8", errors="replace"), target.name):
                    if row["name"] == key:
                        # parse_env_text never returns secret values, but a nonempty source entry means the key exists.
                        raw = self._read_env_map(target).get(key, "")
                        if raw:
                            return True, "file"
            except OSError:
                pass
        if os.getenv(key):
            return True, "host"
        return False, ""

    def status(self, project: str = "", environment: str = "", *, discover: bool = True) -> dict[str, Any]:
        item, env, profile = self._profile(project, environment)
        if discover and self.config.auto_discover:
            return self.discover(item.name, env, persist=True)
        reqs = profile.get("requirements") or {}
        variables: list[dict[str, Any]] = []
        missing: list[str] = []
        services: dict[str, dict[str, int]] = {}
        for key in sorted(reqs):
            req = reqs[key]
            configured, source = self._configured(item, env, profile, key)
            if req.get("required") and not configured:
                missing.append(key)
            svc = str(req.get("service") or service_for(key))
            stats = services.setdefault(svc, {"total": 0, "configured": 0, "missing": 0})
            stats["total"] += 1
            if configured:
                stats["configured"] += 1
            elif req.get("required"):
                stats["missing"] += 1
            managed_value = "" if req.get("secret") else str((profile.get("values") or {}).get(key) or "")
            variables.append({
                "name": key, "secret": bool(req.get("secret")), "required": bool(req.get("required")),
                "configured": configured, "source": source, "service": svc,
                "description": str(req.get("description") or ""), "default": "" if req.get("secret") else str(req.get("default") or ""),
                "value": managed_value,
                "sources": list(req.get("sources") or [])[:20],
            })
        pending = dict(profile.get("pending") or {}) if profile.get("pending") else None
        if pending:
            pending_missing = [k for k in pending.get("keys", []) if k in missing]
            pending["missing"] = pending_missing
            if not pending_missing and pending.get("status") == "waiting":
                pending["status"] = "ready"
        return {
            "project": item.name, "display_name": item.display_name, "environment": env,
            "configured": len(variables) - len(missing), "required": sum(1 for x in variables if x["required"]),
            "total": len(variables), "missing": missing, "ready": not missing,
            "variables": variables, "services": services, "pending": pending,
            "secret_count": sum(1 for x in variables if x["secret"] and x["configured"]),
            "env_file": self.env_filename(env), "files": self.list_env_files(item.name),
        }

    def set_values(self, entries: list[dict[str, Any]], *, project: str = "", environment: str = "", materialize: bool = True) -> dict[str, Any]:
        item, env, profile = self._profile(project, environment)
        values = profile.setdefault("values", {})
        refs = profile.setdefault("secret_refs", {})
        reqs = profile.setdefault("requirements", {})
        for entry in entries[: int(self.config.max_variables)]:
            key = str(entry.get("name") or "").strip().upper()
            if not KEY_RX.fullmatch(key):
                raise ValueError(f"invalid environment variable name: {key!r}")
            secret = bool(entry.get("secret") or (reqs.get(key) or {}).get("secret") or is_secret_key(key))
            value = str(entry.get("value") or "")
            old = reqs.get(key) or {}
            reqs[key] = {
                "name": key, "secret": secret, "required": bool(entry.get("required", old.get("required", True))),
                "default": old.get("default") or "", "description": str(entry.get("description") or old.get("description") or "")[:1000],
                "service": str(entry.get("service") or old.get("service") or service_for(key))[:100],
                "sources": sorted(set((old.get("sources") or []) + ["configuration UI"])), "updated_at": _now(),
            }
            if value == "" and bool(entry.get("remove")):
                values.pop(key, None)
                ref = refs.pop(key, None)
                if ref:
                    self.vault.remove(ref)
                continue
            if secret:
                if value and value != MASK:
                    ref = refs.get(key) or self._secret_ref(item.name, env, key)
                    self.vault.set(ref, value)
                    refs[key] = ref
                values.pop(key, None)
            else:
                if value != MASK:
                    values[key] = value
                ref = refs.pop(key, None)
                if ref:
                    self.vault.remove(ref)
        profile["updated_at"] = _now()
        self.save()
        if materialize and self.config.write_dotenv:
            self.materialize(item.name, env)
        return self.status(item.name, env, discover=False)

    def unset(self, key: str, *, project: str = "", environment: str = "") -> dict[str, Any]:
        item, env, profile = self._profile(project, environment)
        key = str(key or "").strip().upper()
        profile.setdefault("values", {}).pop(key, None)
        ref = profile.setdefault("secret_refs", {}).pop(key, None)
        if ref:
            self.vault.remove(ref)
        profile["updated_at"] = _now()
        self.save()
        if self.config.write_dotenv:
            self.materialize(item.name, env)
        return self.status(item.name, env, discover=False)

    def _resolved_map(self, item, env: str, profile: dict[str, Any]) -> dict[str, str]:
        result = {str(k): str(v) for k, v in (profile.get("values") or {}).items()}
        for key, ref in (profile.get("secret_refs") or {}).items():
            if self.vault.has(ref):
                result[str(key)] = self.vault.resolve(ref)
        return result

    @staticmethod
    def _read_env_map(path: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return out
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if KEY_RX.fullmatch(key):
                out[key] = value.strip().strip('"').strip("'")
        return out


    def _ensure_gitignore(self, project: str = "") -> None:
        root = self._project_root(project)
        path = root / ".gitignore"
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        except OSError:
            text = ""
        required = [".env", ".env.*", "!.env.example"]
        existing = {line.strip() for line in text.splitlines()}
        missing = [line for line in required if line not in existing]
        if not missing:
            return
        block = "# term_5 local environment / secrets\n" + "\n".join(missing) + "\n"
        if text and not text.endswith("\n"):
            text += "\n"
        if text and text.strip():
            text += "\n"
        path.write_text(text + block, encoding="utf-8")

    def materialize(self, project: str = "", environment: str = "") -> dict[str, Any]:
        item, env, profile = self._profile(project, environment)
        target = self.projects.path(item.name) / self.env_filename(env)
        self._ensure_gitignore(item.name)
        managed = self._resolved_map(item, env, profile)
        old_lines = target.read_text(encoding="utf-8", errors="replace").splitlines() if target.is_file() else []
        seen: set[str] = set()
        new_lines: list[str] = []
        for raw in old_lines:
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in managed:
                    new_lines.append(f"{key}={managed[key]}")
                    seen.add(key)
                    continue
            new_lines.append(raw)
        if new_lines and managed and new_lines[-1].strip():
            new_lines.append("")
        for key in sorted(managed):
            if key not in seen:
                new_lines.append(f"{key}={managed[key]}")
        target.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return {"ok": True, "project": item.name, "environment": env, "file": target.name, "variables": len(managed)}

    def list_env_files(self, project: str = "") -> list[dict[str, Any]]:
        root = self._project_root(project)
        rows = []
        try:
            candidates = sorted(p for p in root.iterdir() if p.is_file() and _ALLOWED_ENV_FILE.fullmatch(p.name))
        except OSError:
            candidates = []
        for p in candidates:
            rows.append({"name": p.name, "bytes": p.stat().st_size, "example": "example" in p.name.lower() or "sample" in p.name.lower() or "template" in p.name.lower()})
        return rows

    def read_env_file(self, name: str, *, project: str = "") -> dict[str, Any]:
        if not _ALLOWED_ENV_FILE.fullmatch(str(name or "")):
            raise ValueError("only .env and .env.* files are editable here")
        root = self._project_root(project)
        path = root / name
        if not path.exists():
            return {"project": self._project(project).name, "name": name, "exists": False, "content": ""}
        text = path.read_text(encoding="utf-8", errors="replace")
        masked: list[str] = []
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if KEY_RX.fullmatch(key) and is_secret_key(key):
                    prefix = raw[: len(raw) - len(raw.lstrip())]
                    masked.append(f"{prefix}{key}={MASK}")
                    continue
            masked.append(raw)
        return {"project": self._project(project).name, "name": name, "exists": True, "content": "\n".join(masked) + ("\n" if text.endswith("\n") else "")}

    def write_env_file(self, name: str, content: str, *, project: str = "") -> dict[str, Any]:
        if not _ALLOWED_ENV_FILE.fullmatch(str(name or "")):
            raise ValueError("only .env and .env.* files are editable here")
        item = self._project(project)
        root = self.projects.path(item.name)
        self._ensure_gitignore(item.name)
        path = root / name
        old = self._read_env_map(path)
        out: list[str] = []
        environment = "production" if name == ".env" else name.removeprefix(".env.") or "production"
        entries: list[dict[str, Any]] = []
        for raw in str(content or "").splitlines():
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, val = stripped.split("=", 1)
                key = key.strip()
                if KEY_RX.fullmatch(key):
                    val = val.strip().strip('"').strip("'")
                    secret = is_secret_key(key)
                    if secret and val == MASK:
                        val = old.get(key, "")
                    out.append(f"{key}={val}")
                    if val:
                        entries.append({"name": key, "value": val, "secret": secret, "required": True, "service": service_for(key)})
                    continue
            out.append(raw)
        path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600 if name != ".env.example" else 0o644)
        except OSError:
            pass
        if entries and name != ".env.example":
            self.set_values(entries, project=item.name, environment=environment, materialize=False)
        return self.read_env_file(name, project=item.name)

    def pending(self, project: str = "", environment: str = "") -> dict[str, Any] | None:
        _item, _env, profile = self._profile(project, environment, create=False)
        p = profile.get("pending") if profile else None
        return dict(p) if isinstance(p, dict) else None

    def has_pending(self) -> bool:
        if self.projects is None or not self.projects.registry.active_name:
            return False
        project = self.projects.registry.active_name
        projects = self._data.get("projects", {}) if isinstance(self._data, dict) else {}
        pdata = projects.get(project, {}) if isinstance(projects, dict) else {}
        envs = pdata.get("environments", {}) if isinstance(pdata, dict) else {}
        if isinstance(envs, dict):
            for profile in envs.values():
                if not isinstance(profile, dict):
                    continue
                pending = profile.get("pending")
                if isinstance(pending, dict) and pending.get("status") in {"waiting", "ready"}:
                    return True
        return False

    def resume_pending(self, project: str = "", environment: str = "") -> dict[str, Any] | None:
        item, env, profile = self._profile(project, environment)
        pending = profile.get("pending")
        if not isinstance(pending, dict):
            return None
        status = self.status(item.name, env, discover=False)
        still = [k for k in pending.get("keys", []) if k in status.get("missing", [])]
        if still:
            raise ValueError("required configuration is still missing: " + ", ".join(still))
        result = dict(pending)
        profile["pending"] = None
        profile["updated_at"] = _now()
        self.save()
        return result

    def context_text(self) -> str:
        if self.projects is None or not self.projects.registry.active_name:
            return ""
        try:
            st = self.status(self.projects.registry.active_name, self.config.default_environment, discover=self.config.auto_discover)
        except Exception as exc:
            return f"[term_5 configuration]\nConfiguration status unavailable: {type(exc).__name__}: {exc}"
        lines = [
            "[term_5 project configuration — secret values are intentionally unavailable to the model]",
            f"Environment: {st['environment']}; configured={st['configured']}/{st['total']}; required={st['required']}; ready={st['ready']}",
        ]
        if st["missing"]:
            lines.append("Missing required variables: " + ", ".join(st["missing"][:40]))
            lines.append("Use configuration_require for missing human-supplied credentials. Never ask the user to paste secrets into chat.")
        if st.get("pending"):
            lines.append("Human input state: " + str(st["pending"].get("status") or "waiting") + " — " + str(st["pending"].get("reason") or ""))
        configured_secrets = [v["name"] for v in st["variables"] if v["secret"] and v["configured"]]
        if configured_secrets:
            lines.append("Configured secrets (values inaccessible): " + ", ".join(configured_secrets[:40]))
        return "\n".join(lines)[:10000]

    def _get(self, status: dict[str, Any], key: str, *, required: bool = False) -> str:
        item, env, profile = self._profile(status["project"], status["environment"])
        key = key.upper()
        if key in (profile.get("values") or {}):
            return str(profile["values"][key])
        ref = (profile.get("secret_refs") or {}).get(key)
        if ref and self.vault.has(ref):
            return self.vault.resolve(ref)
        file_map = self._read_env_map(self.projects.path(item.name) / self.env_filename(env))
        if key in file_map:
            return file_map[key]
        val = os.getenv(key, "")
        if required and not val:
            raise ValueError(f"{key} is not configured")
        return val

    def test_connection(self, service: str, *, project: str = "", environment: str = "") -> dict[str, Any]:
        st = self.status(project, environment, discover=False)
        svc = str(service or "").strip().lower()
        timeout = max(2, min(int(self.config.connection_timeout_s), 60))
        started = _now()
        try:
            if svc == "smtp":
                host = self._get(st, "SMTP_HOST", required=True)
                port = int(self._get(st, "SMTP_PORT") or 587)
                username = self._get(st, "SMTP_USERNAME") or self._get(st, "SMTP_USER")
                password = self._get(st, "SMTP_PASSWORD")
                use_ssl = (self._get(st, "SMTP_USE_SSL") or "").lower() in {"1", "true", "yes", "on"} or port == 465
                starttls = (self._get(st, "SMTP_STARTTLS") or "true").lower() not in {"0", "false", "no", "off"}
                ctx = ssl.create_default_context()
                if use_ssl:
                    client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
                else:
                    client = smtplib.SMTP(host, port, timeout=timeout)
                try:
                    client.ehlo()
                    if not use_ssl and starttls:
                        client.starttls(context=ctx); client.ehlo()
                    if username and password:
                        client.login(username, password)
                finally:
                    try: client.quit()
                    except Exception: pass
                return {"ok": True, "service": "smtp", "started_at": started, "host": host, "port": port, "tls": "ssl" if use_ssl else ("starttls" if starttls else "plain"), "authenticated": bool(username and password)}
            if svc == "deepseek":
                base = self._get(st, "DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
                key = self._get(st, "DEEPSEEK_API_KEY", required=True)
                url = base.rstrip("/") + "/models"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    code = int(getattr(resp, "status", 200))
                return {"ok": 200 <= code < 300, "service": "deepseek", "started_at": started, "base_url": base, "http_status": code}
            if svc in {"mongodb", "redis", "postgres", "database"}:
                candidates = {
                    "mongodb": ["MONGO_URI", "MONGODB_URI", "MONGO_URL"],
                    "redis": ["REDIS_URL", "REDIS_URI"],
                    "postgres": ["DATABASE_URL", "POSTGRES_URL"],
                    "database": ["DATABASE_URL"],
                }[svc]
                uri = ""
                for key in candidates:
                    uri = self._get(st, key)
                    if uri: break
                if not uri:
                    raise ValueError(f"no {svc} connection URL is configured")
                parsed = urllib.parse.urlparse(uri)
                host = parsed.hostname or ""
                defaults = {"mongodb": 27017, "redis": 6379, "postgres": 5432, "database": 5432}
                port = int(parsed.port or defaults[svc])
                with socket.create_connection((host, port), timeout=timeout):
                    pass
                return {"ok": True, "service": svc, "started_at": started, "host": host, "port": port, "note": "TCP connectivity verified; protocol authentication was not exercised."}
            raise ValueError("supported connection tests: smtp, deepseek, mongodb, redis, postgres, database")
        except Exception as exc:
            return {"ok": False, "service": svc, "started_at": started, "error": f"{type(exc).__name__}: {exc}"}
