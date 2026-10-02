"""Configuration is an experiment condition, not supplier verification."""

def model_evidence(definition):
    return {
        "provider": definition.provider, "model_id": definition.model,
        "configured_context_limit": None,
        "configured_input_token_limit": definition.input_token_limit,
        "configured_max_output_tokens": definition.parameters.get("max_tokens"),
        "configured_input_price": definition.input_cost_per_million,
        "configured_output_price": definition.output_cost_per_million,
        "configured_currency": definition.currency, "price_unit": "per_million_tokens",
        "configured_temperature": definition.parameters.get("temperature"),
        "configured_top_p": definition.parameters.get("top_p"),
        "configured_generation_parameters": dict(definition.parameters),
        "verified_context_limit": None, "verified_input_token_limit": None,
        "verified_max_output_tokens": None, "verified_input_price": None,
        "verified_output_price": None, "verified_currency": None,
        "verification_status": "unverified", "verification_source": None,
    }
