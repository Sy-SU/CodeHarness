"""Model-call accounting and trace integration shared by agent workflows."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from typing import List

from agent.workspace.task import TaskWorkspace

from .router import ModelRouter
from .types import (
    AgentRole,
    ChatMessage,
    CostEstimate,
    LLMError,
    LLMErrorKind,
    LLMResponse,
    ModelProfile,
)


class ModelCallFailed(RuntimeError):
    def __init__(self, response: LLMResponse):
        self.response = response
        error = response.error
        message = error.message if error is not None else "Model call failed"
        super().__init__(message)


class ModelCallRuntime:
    """Run one routed call while preserving attempts, failures, usage, and cost state."""

    def __init__(self, router: ModelRouter, workspace: TaskWorkspace):
        self.router = router
        self.workspace = workspace

    def _record_cost(self, estimate: CostEstimate) -> None:
        state = self.workspace.state
        if not estimate.known:
            state.estimated_cost = None
            state.cost_estimate_status = estimate.reason or "unknown"
            if estimate.currency is not None:
                state.cost_currency = estimate.currency
            return
        if state.cost_estimate_status not in {"not_applicable", "known"}:
            return
        if state.cost_currency is not None and state.cost_currency != estimate.currency:
            state.estimated_cost = None
            state.cost_estimate_status = "currency_mismatch"
            state.cost_currency = None
            return
        state.estimated_cost = (state.estimated_cost or 0.0) + estimate.amount
        state.cost_estimate_status = "known"
        state.cost_currency = estimate.currency

    def complete(
        self,
        role: AgentRole,
        profile: ModelProfile,
        messages: List[ChatMessage],
        *, purpose=None,
    ) -> LLMResponse:
        route = self.router.route(profile)
        call_id = f"llm_{uuid.uuid4().hex}"
        state = self.workspace.state
        state.last_model_call_id = call_id
        state.current_model_profile = profile.value
        state.llm_call_count += 1
        state.calls_per_model_profile[profile.value] = (
            state.calls_per_model_profile.get(profile.value, 0) + 1
        )
        previous_cost = state.estimated_cost, state.cost_estimate_status
        # A durable attempted request is not free while its usage is unknown.
        # Restore the known subtotal only after a response is actually received.
        state.estimated_cost, state.cost_estimate_status = None, "pending_usage"
        self.workspace.trace.append(
            "LLM_CALL",
            {
                "role": role.value,
                "profile": profile.value,
                "provider": route.provider,
                "model": route.model,
                "parameters": route.parameters,
                "messages": [asdict(item) for item in messages],
                **({"purpose": purpose} if purpose else {}),
            },
            correlation_id=call_id,
        )
        self.workspace.save_state()

        started = time.monotonic()
        try:
            response = self.router.complete(profile, messages)
        except KeyboardInterrupt:
            registry = getattr(self.router, "registry", None)
            provider = registry.provider(route.provider) if registry is not None else None
            self.workspace.trace.append("LLM_TRANSPORT_DIAGNOSTICS", {
                "role": role.value, "profile": profile.value,
                "transport_diagnostics": getattr(provider, "last_transport_diagnostics", {}),
            }, correlation_id=call_id)
            raise
        except Exception:
            response = LLMResponse.failure(
                provider=route.provider,
                model=route.model,
                profile=profile,
                error=LLMError(
                    kind=LLMErrorKind.PROVIDER,
                    message="Model adapter raised an unexpected exception",
                ),
                latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            )

        if response.usage is None:
            state.llm_usage_missing_count += 1
        else:
            state.input_tokens += response.usage.input_tokens
            state.output_tokens += response.usage.output_tokens
        cost = self.router.estimate_cost(response)
        state.estimated_cost, state.cost_estimate_status = previous_cost
        self._record_cost(cost)

        if response.succeeded:
            state.llm_success_count += 1
        else:
            state.llm_failure_count += 1
            error_kind = (
                response.error.kind.value
                if response.error is not None
                else LLMErrorKind.PROVIDER.value
            )
            state.model_failures_by_kind[error_kind] = (
                state.model_failures_by_kind.get(error_kind, 0) + 1
            )

        self.workspace.trace.append(
            "LLM_RESPONSE",
            {
                "role": role.value,
                "status": response.status.value,
                "profile": profile.value,
                "provider": response.provider,
                "model": response.model,
                "usage": asdict(response.usage) if response.usage is not None else None,
                "usage_available": response.usage is not None,
                "request_id": response.request_id,
                "finish_reason": response.finish_reason,
                "tool_calls": [asdict(item) for item in response.tool_calls],
                "latency_ms": response.latency_ms,
                "actual_response_model": response.actual_response_model,
                "usage_metadata": response.usage_metadata,
                "transport_diagnostics": response.transport_diagnostics,
                "cost_estimate": asdict(cost),
                "error": asdict(response.error) if response.error is not None else None,
                "content": response.content,
                **({"purpose": purpose} if purpose else {}),
            },
            correlation_id=call_id,
        )
        self.workspace.save_state()
        if not response.succeeded:
            raise ModelCallFailed(response)
        return response
