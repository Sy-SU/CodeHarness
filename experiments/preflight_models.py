"""Configured routes and narrowly allowlisted GET /models evidence."""
from __future__ import annotations

import math
import json
from agent.execution import fingerprint
from agent.models.types import AgentRole

VERIFIED_FIELDS = ("model_available", "max_output_tokens", "context_window", "input_limit",
                   "input_price", "output_price", "currency")


def condition_profiles(config, policy):
    routes = {}
    for strategy in config.strategies:
        roles = (AgentRole.CODE,) if strategy.mode == "code-only" else (
            AgentRole.PLAN, AgentRole.CODE, AgentRole.DEBUG)
        routes[strategy.name] = {role.value: strategy.profile or strategy.roles.get(role.value)
                                or policy.mapping[role].value for role in roles}
    return routes


def registry_profiles(config, policy):
    """Only call routes, plus the existing local REVIEW checkpoint dependency.

    Harness snapshots currently serialize REVIEW's definition even though REVIEW
    is local and never calls a model. Do not change that checkpoint contract here.
    """
    from agent.models.types import ModelProfile
    routes = condition_profiles(config, policy)
    profiles = {ModelProfile(profile) for values in routes.values() for profile in values.values()}
    for strategy in config.strategies:
        if strategy.mode == "harness-loop":
            profiles.add(ModelProfile(strategy.profile or strategy.roles.get("REVIEW") or policy.mapping[AgentRole.REVIEW].value))
    return profiles


def configured_models(config, registry, policy):
    from agent.models.types import ModelProfile
    conditions = condition_profiles(config, policy)
    profiles = sorted({profile for routes in conditions.values() for profile in routes.values()})
    snapshots = []
    for profile in profiles:
        definition = registry.definition(ModelProfile(profile))
        provider = registry.provider(definition.provider)
        parameters = dict(definition.parameters)
        snapshots.append({"profile": profile, "provider": definition.provider, "model_id": definition.model,
            "endpoint_fingerprint": fingerprint(provider.base_url),
            "configured": {"max_output_tokens": parameters.get("max_tokens"),
                "input_reservation_boundary": definition.input_token_limit,
                "input_price": definition.input_cost_per_million, "output_price": definition.output_cost_per_million,
                "currency": definition.currency, "price_unit": "per_million_tokens",
                "temperature": parameters.get("temperature"), "top_p": parameters.get("top_p"),
                "generation_parameters": parameters, "provider_timeout_seconds": provider.timeout_seconds},
            "verified": {**{name: None for name in VERIFIED_FIELDS},
                         "verification_source": None, "verified_at": None, "field_sources": {}},
            "needs_live_probe": True, "warnings": []})
    return snapshots, conditions


def apply_model_metadata(snapshot, body, *, verified_at):
    """Each advertised capability is independent. Listing an ID proves only availability.

    Optional compatible metadata uses explicit max_output_tokens/context_window/input_limit
    fields and pricing.{input_price,output_price,currency,unit=per_million_tokens}.
    No aliases, deductions, configured fallbacks, or undocumented price conversions.
    """
    verified = snapshot["verified"]
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in data):
        snapshot["warnings"].append("provider_metadata_unavailable")
        return
    ids = [item["id"] for item in data]
    # A partial/paginated catalogue is insufficient evidence of absence.
    if len(ids) != len(set(ids)) or body.get("has_more") is True or body.get("next"):
        snapshot["warnings"].append("provider_metadata_incomplete")
        return
    source = "GET /models#data[].id"
    verified.update(model_available=snapshot["model_id"] in ids,
                    verification_source=source, verified_at=verified_at)
    verified["field_sources"]["model_available"] = source
    if verified["model_available"]:
        item = data[ids.index(snapshot["model_id"])]
        for key in ("max_output_tokens", "context_window", "input_limit"):
            value = item.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                verified[key] = value
                verified["field_sources"][key] = "GET /models#data[]." + key
        pricing = item.get("pricing")
        if (isinstance(pricing, dict) and pricing.get("unit") == "per_million_tokens"
                and isinstance(pricing.get("currency"), str) and pricing["currency"] in {"CNY", "USD", "EUR"}):
            for key in ("input_price", "output_price"):
                value = pricing.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                    verified[key] = value
                    verified["field_sources"][key] = "GET /models#data[].pricing." + key
            if verified["input_price"] is not None or verified["output_price"] is not None:
                verified["currency"] = pricing["currency"]
                verified["field_sources"]["currency"] = "GET /models#data[].pricing.currency"
    snapshot["needs_live_probe"] = any(verified[key] is None for key in ("max_output_tokens", "context_window", "input_limit"))


def model_checks(snapshots, config):
    blockers, warnings = [], []
    for snapshot in snapshots:
        profile, configured, verified = snapshot["profile"], snapshot["configured"], snapshot["verified"]
        snapshot["unknown"] = [key for key in VERIFIED_FIELDS if verified[key] is None]
        try:
            json.dumps(configured["generation_parameters"], allow_nan=False)
        except (ValueError, TypeError):
            blockers.append(f"generation_parameters_invalid:{profile}")
        for key in ("max_output_tokens", "input_reservation_boundary"):
            value = configured[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                blockers.append(f"budget_config_invalid:{profile}:{key}")
        if configured["currency"] != "CNY" or any(configured[key] is None for key in ("input_price", "output_price")):
            blockers.append(f"budget_config_invalid:{profile}:cny_pricing_missing")
        if snapshot["model_id"].startswith("MODEL_NAME_"):
            blockers.append(f"required_model_unresolved:{profile}")
        if verified["model_available"] is False:
            blockers.append(f"model_unavailable:{profile}")
        if (verified["max_output_tokens"] is not None and configured["max_output_tokens"] is not None
                and configured["max_output_tokens"] > verified["max_output_tokens"]):
            blockers.append(f"configured_output_exceeds_verified_limit:{profile}")
        for key in ("input_price", "output_price"):
            if verified[key] is None:
                warnings.append(f"verified_provider_price_unavailable:{profile}:{key}")
            elif verified[key] != configured[key] or verified["currency"] != configured["currency"]:
                warnings.append(f"configured_price_differs_from_metadata:{profile}:{key}")
        if snapshot["needs_live_probe"]:
            warnings.append(f"verified_context_or_output_limit_unavailable:{profile}")
        if configured["temperature"] is None or configured["top_p"] is None:
            warnings.append(f"provider_generation_defaults_unspecified:{profile}")
        if verified["model_available"] is None:
            warnings.append(f"model_availability_unverified:{profile}")
        warnings.extend(f"{warning}:{profile}" for warning in snapshot["warnings"])
        if (configured["currency"] == "CNY" and all(configured[key] is not None for key in ("input_price", "output_price"))
                and all(isinstance(configured[key], int) and not isinstance(configured[key], bool)
                        and configured[key] > 0 for key in ("input_reservation_boundary", "max_output_tokens"))):
            reservation = (configured["input_price"] * configured["input_reservation_boundary"]
                           + configured["output_price"] * configured["max_output_tokens"]) / 1_000_000
            snapshot["configured_call_reservation_cny"] = reservation
            if reservation > config.harness.max_cost_cny:
                blockers.append(f"budget_config_invalid:{profile}:one_call_exceeds_task_cap")
    return blockers, warnings
