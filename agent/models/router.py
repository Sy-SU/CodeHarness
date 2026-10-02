"""Logical-profile routing and explicit cost-estimate semantics."""

from __future__ import annotations

from typing import List

from .registry import ModelDefinition, ModelRegistry
from .types import (
    ChatMessage,
    CostEstimate,
    LLMError,
    LLMErrorKind,
    LLMResponse,
    ModelProfile,
)


class ModelRouter:
    def __init__(self, registry: ModelRegistry):
        self.registry = registry

    def route(self, profile: ModelProfile) -> ModelDefinition:
        return self.registry.definition(profile)

    def complete(
        self, profile: ModelProfile, messages: List[ChatMessage]
    ) -> LLMResponse:
        definition = self.route(profile)
        provider = self.registry.provider(definition.provider)
        response = provider.complete(
            messages,
            model=definition.model,
            profile=profile,
            parameters=definition.parameters,
        )
        if (
            response.provider != definition.provider
            or response.model != definition.model
            or response.profile is not profile
        ):
            return LLMResponse.failure(
                provider=definition.provider,
                model=definition.model,
                profile=profile,
                error=LLMError(
                    kind=LLMErrorKind.PROTOCOL,
                    message="Model adapter returned mismatched route metadata",
                ),
                request_id=response.request_id,
                latency_ms=response.latency_ms,
                usage=response.usage,
            )
        return response

    def estimate_cost(self, response: LLMResponse) -> CostEstimate:
        if response.usage is None:
            return CostEstimate(
                amount=None,
                currency=None,
                known=False,
                reason="missing_usage",
            )
        definition = self.route(response.profile)
        if not definition.pricing_known:
            return CostEstimate(
                amount=None,
                currency=definition.currency,
                known=False,
                reason="missing_pricing",
            )
        amount = (
            response.usage.input_tokens * definition.input_cost_per_million
            + response.usage.output_tokens * definition.output_cost_per_million
        ) / 1_000_000
        return CostEstimate(
            amount=amount,
            currency=definition.currency,
            known=True,
        )
