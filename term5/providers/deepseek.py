from __future__ import annotations

import base64
import json
from typing import Any

from ..config import Term5Config
from ..models import ChatResult, ReasoningMode, ToolCall, Usage
from .base import ModelProvider, ProviderUnavailable


class DeepSeekProvider(ModelProvider):
    """DeepSeek OpenAI-compatible adapter.

    Chat/reasoning uses the normal endpoint. FIM uses the beta completion endpoint.
    The adapter intentionally keeps provider objects out of the rest of the runtime.
    """

    def __init__(self, config: Term5Config) -> None:
        self.config = config
        self._client = None
        self._fim_client = None

    def _clients(self):
        if not self.config.api_key:
            raise ProviderUnavailable("DEEPSEEK_API_KEY is not configured")
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # pragma: no cover - depends on environment
            raise ProviderUnavailable("The 'openai' package is required: pip install openai") from exc
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self.config.api_key, base_url=self.config.model.base_url)
        if self._fim_client is None:
            self._fim_client = AsyncOpenAI(api_key=self.config.api_key, base_url=self.config.model.fim_base_url)
        return self._client, self._fim_client

    @staticmethod
    def _usage(obj: Any) -> Usage:
        if obj is None:
            return Usage()
        def get(name: str, default=None):
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)
        prompt = int(get("prompt_tokens", 0) or 0)
        completion = int(get("completion_tokens", 0) or 0)
        hit = get("prompt_cache_hit_tokens")
        miss = get("prompt_cache_miss_tokens")
        if hit is None:
            details = get("prompt_tokens_details")
            if details is not None:
                hit = details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", None)
        if miss is None and hit is not None:
            miss = max(0, prompt - int(hit))
        return Usage(prompt, completion,
                     None if hit is None else int(hit),
                     None if miss is None else int(miss))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning: ReasoningMode = ReasoningMode.NONE,
        max_tokens: int | None = None,
    ) -> ChatResult:
        client, _ = self._clients()
        kwargs: dict[str, Any] = {
            "model": self.config.model.model,
            "messages": messages,
            "stream": False,
            "max_tokens": int(max_tokens or self.config.model.max_output_tokens),
        }
        if tools:
            kwargs["tools"] = tools
        if reasoning == ReasoningMode.NONE:
            kwargs["temperature"] = self.config.model.temperature
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            if reasoning == ReasoningMode.AUTO:
                reasoning = ReasoningMode.HIGH
            kwargs["reasoning_effort"] = reasoning.value
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        response = await client.chat.completions.create(**kwargs)
        if not response.choices:
            return ChatResult(model=self.config.model.model, usage=self._usage(getattr(response, "usage", None)))
        choice = response.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        for tc in getattr(message, "tool_calls", None) or []:
            raw = getattr(tc.function, "arguments", "") or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    args = {"_invalid": raw}
            except Exception:
                args = {"_invalid": raw}
            calls.append(ToolCall(
                id=str(getattr(tc, "id", "")),
                name=str(getattr(tc.function, "name", "")),
                arguments=args,
            ))
        return ChatResult(
            content=getattr(message, "content", None) or "",
            reasoning_content=getattr(message, "reasoning_content", None) or "",
            tool_calls=calls,
            finish_reason=getattr(choice, "finish_reason", None),
            usage=self._usage(getattr(response, "usage", None)),
            model=getattr(response, "model", None) or self.config.model.model,
        )

    async def fim(
        self,
        prefix: str,
        suffix: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> tuple[str, dict[str, int]]:
        if not self.config.fim.enabled:
            raise ProviderUnavailable("FIM is disabled in term5 configuration")
        _, client = self._clients()
        max_tokens = max(1, min(int(max_tokens), self.config.fim.hard_max_output_tokens, 4096))
        response = await client.completions.create(
            model=self.config.model.model,
            prompt=prefix,
            suffix=suffix,
            max_tokens=max_tokens,
            temperature=float(temperature),
        )
        text = response.choices[0].text if response.choices else ""
        usage_obj = getattr(response, "usage", None)
        usage = self._usage(usage_obj)
        return text, {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        }


    async def vision(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
        *,
        max_tokens: int = 4096,
    ) -> ChatResult:
        if not self.config.vision.enabled:
            raise ProviderUnavailable("Vision is disabled in term5 configuration")
        if not images:
            raise ProviderUnavailable("Vision inspection requires at least one image")
        client, _ = self._clients()
        if len(images) > int(self.config.vision.max_images):
            raise ProviderUnavailable(f"Too many vision images; max is {self.config.vision.max_images}")
        content: list[dict[str, Any]] = [{"type": "text", "text": str(prompt or "Inspect these images.")[:24000]}]
        for raw, mime in images:
            if len(raw) > int(self.config.vision.max_image_bytes):
                raise ProviderUnavailable(f"Vision image exceeds configured byte cap ({self.config.vision.max_image_bytes})")
            mt = str(mime or "image/png")
            b64 = base64.b64encode(raw).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}", "detail": "high"}})
        model = self.config.vision.model or self.config.model.model
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            stream=False,
            max_tokens=max(512, min(int(max_tokens), int(self.config.vision.max_tokens))),
            temperature=0.1,
        )
        if not response.choices:
            return ChatResult(model=model, usage=self._usage(getattr(response, "usage", None)))
        choice = response.choices[0]
        message = choice.message
        return ChatResult(
            content=getattr(message, "content", None) or "",
            reasoning_content=getattr(message, "reasoning_content", None) or "",
            finish_reason=getattr(choice, "finish_reason", None),
            usage=self._usage(getattr(response, "usage", None)),
            model=getattr(response, "model", None) or model,
        )
