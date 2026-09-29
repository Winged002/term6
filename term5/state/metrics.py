from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import Usage


@dataclass(slots=True)
class Pricing:
    cache_hit_per_million: float = 0.006
    cache_miss_per_million: float = 0.30
    output_per_million: float = 1.20


class UsageLedger:
    def __init__(self, pricing: Pricing) -> None:
        self.usage = Usage()
        self.calls = 0
        self.model_seconds = 0.0
        self.pricing = pricing
        self.unknown_cache_prompt_tokens = 0

    def add(self, usage: Usage, elapsed_s: float = 0.0) -> None:
        self.usage.add(usage)
        if usage.prompt_tokens and usage.cache_hit_tokens is None and usage.cache_miss_tokens is None:
            self.unknown_cache_prompt_tokens += usage.prompt_tokens
        self.calls += 1
        self.model_seconds += max(0.0, float(elapsed_s))

    def estimated_cost(self) -> float | None:
        u = self.usage
        if self.unknown_cache_prompt_tokens or (u.cache_hit_tokens is None and u.cache_miss_tokens is None):
            # At least one prompt call lacks a cache split; do not understate cost.
            return None
        hit = int(u.cache_hit_tokens or 0)
        miss = int(u.cache_miss_tokens if u.cache_miss_tokens is not None else max(0, u.prompt_tokens - hit))
        return (
            hit * self.pricing.cache_hit_per_million
            + miss * self.pricing.cache_miss_per_million
            + u.completion_tokens * self.pricing.output_per_million
        ) / 1_000_000.0

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "model_seconds": round(self.model_seconds, 3),
            "usage": asdict(self.usage),
            "estimated_cost_usd": None if self.estimated_cost() is None else round(self.estimated_cost() or 0.0, 8),
            "cache_split_unknown_prompt_tokens": self.unknown_cache_prompt_tokens,
            "pricing_per_million": {
                "cache_hit": self.pricing.cache_hit_per_million,
                "cache_miss": self.pricing.cache_miss_per_million,
                "output": self.pricing.output_per_million,
            },
        }
