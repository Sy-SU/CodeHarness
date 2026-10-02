"""Validated provider and logical-profile configuration."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import urlsplit

import yaml

from .provider import ModelProvider, OpenAICompatibleProvider
from .types import ModelProfile


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESERVED_PARAMETERS = {"model", "messages", "stream"}


class ModelConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ModelDefinition:
    provider: str
    model: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    input_cost_per_million: Optional[float] = None
    output_cost_per_million: Optional[float] = None
    currency: Optional[str] = None
    input_token_limit: Optional[int] = None

    @property
    def pricing_known(self) -> bool:
        return (
            self.input_cost_per_million is not None
            and self.output_cost_per_million is not None
            and self.currency is not None
        )


class ModelRegistry:
    """Resolve logical profiles without exposing provider payloads upstream."""

    def __init__(
        self,
        providers: Mapping[str, ModelProvider],
        models: Mapping[ModelProfile, ModelDefinition],
    ):
        self.providers = dict(providers)
        self.models = dict(models)

    @staticmethod
    def _mapping(value: Any, label: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ModelConfigurationError(f"{label} must be a mapping")
        return value

    @staticmethod
    def _positive_number(value: Any, label: str) -> float:
        if isinstance(value, bool):
            raise ModelConfigurationError(f"{label} must be a positive number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelConfigurationError(f"{label} must be a positive number") from exc
        if not math.isfinite(number) or number <= 0:
            raise ModelConfigurationError(f"{label} must be a positive number")
        return number

    @staticmethod
    def _price(value: Any, label: str) -> float:
        if isinstance(value, bool):
            raise ModelConfigurationError(f"{label} must be a non-negative number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelConfigurationError(f"{label} must be a non-negative number") from exc
        if not math.isfinite(number) or number < 0:
            raise ModelConfigurationError(f"{label} must be a non-negative number")
        return number

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        *,
        environ: Optional[Mapping[str, str]] = None,
        required_profiles: Optional[Iterable[ModelProfile]] = None,
    ) -> "ModelRegistry":
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ModelConfigurationError(f"Cannot read model configuration: {exc}") from exc
        raw = cls._mapping({} if loaded is None else loaded, "Model configuration root")
        provider_value = raw.get("providers")
        model_value = raw.get("models")
        provider_config = cls._mapping(
            {} if provider_value is None else provider_value,
            "providers",
        )
        model_config = cls._mapping(
            {} if model_value is None else model_value,
            "models",
        )
        required = set(ModelProfile) if required_profiles is None else {
            ModelProfile(profile) for profile in required_profiles}
        if required_profiles is not None:
            # Preflight resolves only routes actually used by the experiment.
            model_config = {key: value for key, value in model_config.items()
                            if key in {profile.value for profile in required}}
            used_providers = {value.get("provider") for value in model_config.values()
                              if isinstance(value, Mapping) and isinstance(value.get("provider"), str)}
            provider_config = {key: value for key, value in provider_config.items() if key in used_providers}
        values = os.environ if environ is None else environ

        providers: Dict[str, ModelProvider] = {}
        supported_by_provider: Dict[str, set] = {}
        for name, untyped_config in provider_config.items():
            if not isinstance(name, str) or not name:
                raise ModelConfigurationError("Provider names must be non-empty strings")
            config = cls._mapping(untyped_config, f"Provider {name}")
            if config.get("type") != "openai-compatible":
                raise ModelConfigurationError(f"Unsupported provider type for {name}")
            base_url_env = config.get("base_url_env")
            api_key_env = config.get("api_key_env")
            if not isinstance(base_url_env, str) or not ENV_NAME.fullmatch(base_url_env):
                raise ModelConfigurationError(f"Provider {name} has an invalid base_url_env")
            if not isinstance(api_key_env, str) or not ENV_NAME.fullmatch(api_key_env):
                raise ModelConfigurationError(f"Provider {name} has an invalid api_key_env")
            base_url = (values.get(base_url_env) or "").strip().rstrip("/")
            api_key = (values.get(api_key_env) or "").strip()
            if not base_url or not api_key:
                raise ModelConfigurationError(
                    f"Provider {name} is missing its base URL or API key environment variable"
                )
            parsed = urlsplit(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ModelConfigurationError(f"Provider {name} base URL must be absolute HTTP(S)")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ModelConfigurationError(
                    f"Provider {name} base URL cannot contain credentials, query, or fragment"
                )
            timeout = cls._positive_number(
                config.get("timeout_seconds"), f"Provider {name} timeout_seconds"
            )
            supported = config.get("supported_parameters", [])
            if not isinstance(supported, list) or any(
                not isinstance(item, str) or not item for item in supported
            ):
                raise ModelConfigurationError(
                    f"Provider {name} supported_parameters must be a list of names"
                )
            if len(supported) != len(set(supported)):
                raise ModelConfigurationError(
                    f"Provider {name} supported_parameters contains duplicates"
                )
            reserved = RESERVED_PARAMETERS.intersection(supported)
            if reserved:
                raise ModelConfigurationError(
                    f"Provider {name} cannot declare reserved parameters: {sorted(reserved)}"
                )
            supported_by_provider[name] = set(supported)
            providers[name] = OpenAICompatibleProvider(
                name,
                base_url,
                api_key,
                timeout_seconds=timeout,
            )

        models: Dict[ModelProfile, ModelDefinition] = {}
        for profile_name, untyped_config in model_config.items():
            try:
                profile = ModelProfile(profile_name)
            except (TypeError, ValueError) as exc:
                raise ModelConfigurationError(f"Unknown model profile: {profile_name}") from exc
            config = cls._mapping(untyped_config, f"Model profile {profile.value}")
            provider_name = config.get("provider")
            model_name = config.get("model")
            if not isinstance(provider_name, str) or provider_name not in providers:
                raise ModelConfigurationError(f"Invalid provider for {profile.value}")
            if not isinstance(model_name, str) or not model_name.strip():
                raise ModelConfigurationError(f"Invalid model ID for {profile.value}")
            parameter_value = config.get("parameters")
            parameters = cls._mapping(
                {} if parameter_value is None else parameter_value,
                f"Parameters for {profile.value}",
            )
            input_token_limit = config.get("input_token_limit")
            if input_token_limit is not None and (
                isinstance(input_token_limit, bool)
                or not isinstance(input_token_limit, int)
                or input_token_limit <= 0
            ):
                raise ModelConfigurationError("input_token_limit must be a positive integer")
            unsupported = set(parameters).difference(supported_by_provider[provider_name])
            if unsupported:
                raise ModelConfigurationError(
                    f"Unsupported parameters for {profile.value}: {sorted(unsupported)}"
                )
            try:
                json.dumps(parameters, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ModelConfigurationError(
                    f"Parameters for {profile.value} must be JSON serializable"
                ) from exc

            has_input_price = "input_cost_per_million" in config
            has_output_price = "output_cost_per_million" in config
            if has_input_price != has_output_price:
                raise ModelConfigurationError(
                    f"Pricing for {profile.value} requires both input and output prices"
                )
            if has_input_price:
                input_price = cls._price(
                    config["input_cost_per_million"],
                    f"{profile.value} input_cost_per_million",
                )
                output_price = cls._price(
                    config["output_cost_per_million"],
                    f"{profile.value} output_cost_per_million",
                )
                currency = config.get("currency")
                if not isinstance(currency, str) or not currency.strip():
                    raise ModelConfigurationError(
                        f"Pricing for {profile.value} requires a currency"
                    )
                currency = currency.strip().upper()
            else:
                if "currency" in config:
                    raise ModelConfigurationError(
                        f"Currency for {profile.value} requires explicit prices"
                    )
                input_price = None
                output_price = None
                currency = None
            models[profile] = ModelDefinition(
                provider=provider_name,
                model=model_name.strip(),
                parameters=dict(parameters),
                input_cost_per_million=input_price,
                output_cost_per_million=output_price,
                currency=currency,
                input_token_limit=input_token_limit,
            )

        missing = [profile.value for profile in sorted(required, key=lambda p: p.value) if profile not in models]
        if missing:
            raise ModelConfigurationError(f"Missing model profiles: {', '.join(missing)}")
        return cls(providers, models)

    def definition(self, profile: ModelProfile) -> ModelDefinition:
        try:
            return self.models[profile]
        except KeyError as exc:
            raise ModelConfigurationError(
                f"No model configured for profile {profile.value}"
            ) from exc

    def provider(self, name: str) -> ModelProvider:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise ModelConfigurationError(f"Unknown model provider: {name}") from exc
