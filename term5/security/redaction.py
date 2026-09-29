from __future__ import annotations

import re


class SecretRedactor:
    TOKEN_RX = re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{12,}|bearer\s+[A-Za-z0-9._\-]{12,})")

    def __init__(self, secrets: list[str] | None = None) -> None:
        self.secrets = [s for s in (secrets or []) if s]

    def add(self, secret: str) -> None:
        value = str(secret or "")
        if value and value not in self.secrets:
            self.secrets.append(value)

    def extend(self, secrets: list[str]) -> None:
        for secret in secrets:
            self.add(secret)

    def redact(self, text: str) -> str:
        result = str(text)
        for secret in sorted(self.secrets, key=len, reverse=True):
            result = result.replace(secret, "[REDACTED]")
        return self.TOKEN_RX.sub("[REDACTED]", result)
