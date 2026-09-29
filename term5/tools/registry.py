from __future__ import annotations

import asyncio
from typing import Any

from ..models import ToolDefinition, ToolResult
from ..security.policy import SecurityPolicy


class ToolRegistry:
    def __init__(self, security: SecurityPolicy, max_parallel_reads: int = 64) -> None:
        self.security = security
        self._tools: dict[str, ToolDefinition] = {}
        self._read_sem = asyncio.Semaphore(max(1, max_parallel_reads))

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, names: set[str] | None = None) -> list[dict[str, Any]]:
        tools = self._tools.values() if names is None else (self._tools[name] for name in self._tools if name in names)
        return [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        } for tool in tools]

    @staticmethod
    def _validate(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
        def check(spec: dict[str, Any], value: Any, label: str) -> str | None:
            typ = spec.get("type")
            ok = True
            if typ == "string": ok = isinstance(value, str)
            elif typ == "integer": ok = isinstance(value, int) and not isinstance(value, bool)
            elif typ == "number": ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif typ == "boolean": ok = isinstance(value, bool)
            elif typ == "array": ok = isinstance(value, list)
            elif typ == "object": ok = isinstance(value, dict)
            if not ok:
                return f"{label} must be {typ}"
            if "enum" in spec and value not in spec["enum"]:
                return f"{label} must be one of: {', '.join(map(str, spec['enum']))}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "minimum" in spec and value < spec["minimum"]:
                    return f"{label} is below minimum {spec['minimum']}"
                if "maximum" in spec and value > spec["maximum"]:
                    return f"{label} exceeds maximum {spec['maximum']}"
            if isinstance(value, str):
                if "minLength" in spec and len(value) < spec["minLength"]:
                    return f"{label} is shorter than minLength {spec['minLength']}"
                if "maxLength" in spec and len(value) > spec["maxLength"]:
                    return f"{label} exceeds maxLength {spec['maxLength']}"
            if isinstance(value, list):
                if "minItems" in spec and len(value) < spec["minItems"]:
                    return f"{label} has too few items"
                if "maxItems" in spec and len(value) > spec["maxItems"]:
                    return f"{label} has too many items"
                item_spec = spec.get("items")
                if isinstance(item_spec, dict):
                    for idx, item in enumerate(value):
                        err = check(item_spec, item, f"{label}[{idx}]")
                        if err:
                            return err
            if isinstance(value, dict):
                props = spec.get("properties") or {}
                missing = [name for name in (spec.get("required") or []) if name not in value]
                if missing:
                    return f"{label} missing required field(s): {', '.join(missing)}"
                if spec.get("additionalProperties") is False:
                    extra = sorted(set(value) - set(props))
                    if extra:
                        return f"{label} has unknown field(s): {', '.join(extra)}"
                for key, child in value.items():
                    if key in props:
                        err = check(props[key], child, f"{label}.{key}")
                        if err:
                            return err
            return None

        if not isinstance(args, dict):
            return "arguments must be an object"
        return check(schema, args, "arguments")

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"Unknown tool: {name}")
        invalid = self._validate(tool.parameters, args)
        if invalid:
            return ToolResult(False, f"Invalid arguments for {name}: {invalid}")
        try:
            self.security.authorize(tool.capabilities)
            if tool.read_only and not tool.stateful:
                async with self._read_sem:
                    return await tool.handler(**args)
            return await tool.handler(**args)
        except TypeError as exc:
            return ToolResult(False, f"Invalid arguments for {name}: {exc}")
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")

    async def execute_many(self, calls: list[tuple[str, dict[str, Any]]]) -> list[ToolResult]:
        """Run a batch concurrently only when every call is read-only.

        State tools and writes are serialized. Multi-file atomic work is exposed
        as one stateful tool so its transaction boundary stays deterministic.
        """
        defs = [self._tools.get(name) for name, _ in calls]
        parallel = bool(calls) and all(d is not None and d.read_only and not d.stateful for d in defs)
        if parallel:
            return await asyncio.gather(*(self.execute(name, args) for name, args in calls))
        out = []
        for name, args in calls:
            out.append(await self.execute(name, args))
        return out

    def describe(self) -> str:
        lines = []
        for name in self.names():
            t = self._tools[name]
            caps = ",".join(sorted(t.capabilities)) or "none"
            lines.append(f"{name:22} {'READ' if t.read_only else 'WRITE':5} risk={t.risk.value:8} caps={caps}")
        return "\n".join(lines)
