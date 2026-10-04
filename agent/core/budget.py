"""Conservative paid-call guard shared by both execution modes."""
from __future__ import annotations

from decimal import Decimal
from dataclasses import asdict
from hashlib import sha256
import json
import math
import uuid


MODEL_BUDGET_ACCOUNTING = "actual_usage_settlement_v1"


class BudgetStopped(RuntimeError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def reserve_model_cost(route, messages, state, max_cost_cny, *, audit=None, components=None,
                       settle_actual=True):
    output_limit = route.parameters.get("max_tokens")
    input_limit = route.input_token_limit
    prompt_bound = sum(len(message.content.encode("utf-8")) + 1024 for message in messages)
    metadata = {"schema_version": "model_budget_check_v1", "provider": route.provider, "model": route.model,
        "configured_context_limit": None, "configured_provider_max_input_tokens": None,
        "local_input_reservation_boundary": input_limit, "requested_max_output_tokens": output_limit,
        "reserved_output_tokens": output_limit, "prompt_upper_bound_tokens": prompt_bound,
        "prompt_bound_method": "sum_utf8_bytes_plus_1024_per_message", "actual_prompt_tokens": None,
        "prompt_hash": sha256(json.dumps([asdict(m) for m in messages],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        "safety_padding_tokens": 1024*len(messages), "component_breakdown": components,
        "max_cost_cny": max_cost_cny, "committed_before_cny": state.budget_committed_cny,
        "remaining_before_cny": float(Decimal(str(max_cost_cny))-Decimal(str(state.budget_committed_cny)))}
    def decision(status, reason=None, **extra):
        if audit:
            audit({**metadata, "decision": status, "reason": reason, **extra})
    if settle_actual and state.pending_model_reservation is not None:
        decision("rejected", "previous_model_usage_unresolved")
        raise BudgetStopped("budget_unverifiable", "previous_model_usage_unresolved")
    if (not route.pricing_known or route.currency != "CNY"
            or isinstance(output_limit, bool) or not isinstance(output_limit, int) or output_limit <= 0
            or isinstance(input_limit, bool) or not isinstance(input_limit, int) or input_limit <= 0):
        decision("rejected", "cny_pricing_or_token_limits_missing")
        raise BudgetStopped("budget_unverifiable", "cny_pricing_or_token_limits_missing")
    if prompt_bound > input_limit:
        decision("rejected", "prompt_exceeds_configured_input_limit")
        raise BudgetStopped("context_limit", "prompt_exceeds_configured_input_limit")
    bound = (Decimal(str(route.input_cost_per_million)) * input_limit
             + Decimal(str(route.output_cost_per_million)) * output_limit) / Decimal(1_000_000)
    committed = Decimal(str(state.budget_committed_cny)) + bound
    if committed > Decimal(str(max_cost_cny)):
        decision("rejected", "cost_reservation_limit", proposed_reservation_cny=float(bound))
        raise BudgetStopped("budget_exhausted", "cost_reservation_limit")
    decision("allowed", proposed_reservation_cny=float(bound), committed_after_cny=float(committed))
    state.budget_committed_cny = float(committed)
    if settle_actual:
        state.pending_model_reservation = {
            "reservation_id": "budget_" + uuid.uuid4().hex,
            "provider": route.provider, "model": route.model,
            "input_limit": input_limit, "output_limit": output_limit,
            "reserved_cny": float(bound), "model_call_id": None,
        }
    return input_limit, output_limit, float(bound)


def settle_model_cost(state, response, cost, call_id):
    """Replace this call's ceiling with known usage cost; uncertainty stays reserved."""
    reservation = state.pending_model_reservation
    if reservation is None:
        return None  # Historical tasks keep their frozen cumulative-ceiling policy.
    bound = Decimal(str(reservation["reserved_cny"]))
    reason = None
    if (reservation["model_call_id"] != call_id
            or response.provider != reservation["provider"]
            or response.model != reservation["model"]):
        reason = "reservation_route_mismatch"
    elif response.usage is None:
        reason = "model_usage_missing"
    elif (response.usage.input_tokens > reservation["input_limit"]
          or response.usage.output_tokens > reservation["output_limit"]):
        reason = "provider_exceeded_configured_token_bound"
    elif (not cost.known or cost.currency != "CNY" or cost.amount is None
          or not math.isfinite(cost.amount) or cost.amount < 0):
        reason = "actual_cost_unverifiable"
    elif Decimal(str(cost.amount)) > bound:
        reason = "actual_cost_exceeds_reservation"
    released = Decimal(0)
    if reason is None:
        released = bound - Decimal(str(cost.amount))
        state.budget_committed_cny = float(Decimal(str(state.budget_committed_cny)) - released)
        state.pending_model_reservation = None
    return {
        "schema_version": MODEL_BUDGET_ACCOUNTING,
        "reservation_id": reservation["reservation_id"], "model_call_id": call_id,
        "reserved_cny": float(bound), "actual_cost_cny": cost.amount if reason is None else None,
        "released_cny": float(released), "committed_cny": state.budget_committed_cny,
        "status": "settled" if reason is None else "retained", "reason": reason,
    }


def validate_usage(response, input_limit, output_limit):
    if response.usage is None:
        raise BudgetStopped("budget_unverifiable", "model_usage_missing")
    if response.usage.input_tokens > input_limit or response.usage.output_tokens > output_limit:
        raise BudgetStopped("budget_unverifiable", "provider_exceeded_configured_token_bound")
