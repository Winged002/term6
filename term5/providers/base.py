from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import ChatResult, ReasoningMode


class ProviderUnavailable(RuntimeError):
    pass


class ModelProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning: ReasoningMode = ReasoningMode.NONE,
        max_tokens: int | None = None,
    ) -> ChatResult:
        raise NotImplementedError

    @abstractmethod
    async def fim(
        self,
        prefix: str,
        suffix: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> tuple[str, dict[str, int]]:
        raise NotImplementedError
    async def vision(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
        *,
        max_tokens: int = 4096,
    ) -> ChatResult:
        """Optional multimodal inspection path. Providers without vision fail loudly."""
        raise ProviderUnavailable("Vision is not supported by this model provider")

