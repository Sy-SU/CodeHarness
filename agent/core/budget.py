"""Conservative paid-call guard shared by both execution modes."""
from __future__ import annotations

from decimal import Decimal


class BudgetStopped(RuntimeError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def reserve_model_cost(route, messages, state, max_cost_cny):
    output_limit = route.parameters.get("max_tokens")
    input_limit = route.input_token_limit
    if (not route.pricing_known or route.currency != "CNY"
            or isinstance(output_limit, bool) or not isinstance(output_limit, int) or output_limit <= 0
            or isinstance(input_limit, bool) or not isinstance(input_limit, int) or input_limit <= 0):
        raise BudgetStopped("budget_unverifiable", "cny_pricing_or_token_limits_missing")
    prompt_bound = sum(len(message.content.encode("utf-8")) + 1024 for message in messages)
    if prompt_bound > input_limit:
        raise BudgetStopped("context_limit", "prompt_exceeds_configured_input_limit")
    bound = (Decimal(str(route.input_cost_per_million)) * input_limit
             + Decimal(str(route.output_cost_per_million)) * output_limit) / Decimal(1_000_000)
    committed = Decimal(str(state.budget_committed_cny)) + bound
    if committed > Decimal(str(max_cost_cny)):
        raise BudgetStopped("budget_exhausted", "cost_reservation_limit")
    state.budget_committed_cny = float(committed)
    return input_limit, output_limit, float(bound)


def validate_usage(response, input_limit, output_limit):
    if response.usage is None:
        raise BudgetStopped("budget_unverifiable", "model_usage_missing")
    if response.usage.input_tokens > input_limit or response.usage.output_tokens > output_limit:
        raise BudgetStopped("budget_unverifiable", "provider_exceeded_configured_token_bound")
