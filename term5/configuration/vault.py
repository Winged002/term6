from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecretVault:
    """Local file-permission protected secret store.

    The vault deliberately does not expose a value-list/read API to model-facing
    tools. Trusted configuration adapters can resolve a named reference locally
    for materialization or connection tests. Values are never included in status
    payloads. This is local-at-rest protection via Unix permissions, not claimed
    cryptographic encryption.
    """

    def __init__(self, path: Path, *, on_secret=None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, dict[str, Any]] = {}
        self._on_secret = on_secret
        self.load()

    def load(self) -> None:
        self._items = {}
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"secret vault is unreadable: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("format") != FORMAT or not isinstance(raw.get("secrets"), dict):
            raise RuntimeError("unsupported or corrupt secret vault format")
        for key, item in raw["secrets"].items():
            if isinstance(item, dict) and isinstance(item.get("value"), str):
                self._items[str(key)] = item
                if self._on_secret and item.get("value"):
                    self._on_secret(item["value"])

    def save(self) -> None:
        payload = {"format": FORMAT, "secrets": self._items}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def set(self, ref: str, value: str) -> None:
        ref = str(ref or "").strip()
        value = str(value or "")
        if not ref:
            raise ValueError("secret reference is required")
        if not value:
            raise ValueError("secret value cannot be empty")
        old = self._items.get(ref) or {}
        self._items[ref] = {
            "value": value,
            "created_at": old.get("created_at") or _now(),
            "updated_at": _now(),
        }
        if self._on_secret:
            self._on_secret(value)
        self.save()

    def has(self, ref: str) -> bool:
        item = self._items.get(str(ref or ""))
        return bool(item and item.get("value"))

    def resolve(self, ref: str) -> str:
        item = self._items.get(str(ref or ""))
        if not item or not item.get("value"):
            raise KeyError(f"secret is not configured: {ref}")
        return str(item["value"])

    def remove(self, ref: str) -> bool:
        key = str(ref or "")
        if key not in self._items:
            return False
        del self._items[key]
        self.save()
        return True

    def metadata(self, ref: str) -> dict[str, Any]:
        item = self._items.get(str(ref or "")) or {}
        return {
            "configured": bool(item.get("value")),
            "created_at": item.get("created_at") or "",
            "updated_at": item.get("updated_at") or "",
        }

    def count(self) -> int:
        return sum(1 for item in self._items.values() if item.get("value"))
