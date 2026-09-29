from __future__ import annotations

from dataclasses import dataclass, field


class CapabilityError(PermissionError):
    pass


@dataclass(slots=True)
class CapabilitySet:
    allowed: set[str] = field(default_factory=set)

    def has(self, capability: str) -> bool:
        if capability in self.allowed:
            return True
        # `workspace.*` grants workspace.read, workspace.write, etc.
        namespace = capability.split(".", 1)[0] + ".*"
        return namespace in self.allowed or "*" in self.allowed

    def require(self, capabilities: set[str]) -> None:
        missing = sorted(c for c in capabilities if not self.has(c))
        if missing:
            raise CapabilityError("Missing capabilities: " + ", ".join(missing))


class SecurityPolicy:
    def __init__(self, capabilities: set[str] | None = None) -> None:
        self.capabilities = CapabilitySet(capabilities or {
            "workspace.read",
            "workspace.write",
            "memory.read",
            "memory.write",
            "git.read",
            "model.reason",
            "model.fim",
        })

    def authorize(self, required: set[str]) -> None:
        self.capabilities.require(required)
