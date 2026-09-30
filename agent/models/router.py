"""Logical-profile model router."""

from __future__ import annotations

from typing import List

from .registry import ModelRegistry
from .types import ChatMessage, LLMResponse, ModelProfile


class ModelRouter:
    def __init__(self, registry: ModelRegistry):
        self.registry = registry

    def complete(
        self, profile: ModelProfile, messages: List[ChatMessage]
    ) -> LLMResponse:
        definition = self.registry.definition(profile)
        provider = self.registry.provider(definition.provider)
        return provider.complete(messages, model=definition.model, profile=profile)

    def estimate_cost(self, response: LLMResponse) -> float:
        definition = self.registry.definition(response.profile)
        return (
            response.usage.input_tokens * definition.input_cost_per_million
            + response.usage.output_tokens * definition.output_cost_per_million
        ) / 1_000_000
