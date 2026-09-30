"""Configuration-driven providers and logical model profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

import yaml

from .provider import ModelProvider, OpenAICompatibleProvider
from .types import ModelProfile


class ModelConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ModelDefinition:
    provider: str
    model: str
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0


class ModelRegistry:
    """Resolve logical profiles without exposing vendor objects to the agent."""

    def __init__(
        self,
        providers: Mapping[str, ModelProvider],
        models: Mapping[ModelProfile, ModelDefinition],
    ):
        self.providers = dict(providers)
        self.models = dict(models)

    @classmethod
    def from_yaml(cls, path: Path) -> "ModelRegistry":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ModelConfigurationError(f"Cannot read model configuration: {exc}") from exc
        providers: Dict[str, ModelProvider] = {}
        for name, config in (raw.get("providers") or {}).items():
            if config.get("type") != "openai-compatible":
                raise ModelConfigurationError(f"Unsupported provider type for {name}")
            base_url = os.getenv(str(config.get("base_url_env", "")))
            api_key = os.getenv(str(config.get("api_key_env", "")))
            if not base_url or not api_key:
                raise ModelConfigurationError(
                    f"Provider {name} is missing its base URL or API key environment variable"
                )
            providers[name] = OpenAICompatibleProvider(name, base_url, api_key)
        models: Dict[ModelProfile, ModelDefinition] = {}
        for profile_name, config in (raw.get("models") or {}).items():
            try:
                profile = ModelProfile(profile_name)
            except ValueError as exc:
                raise ModelConfigurationError(f"Unknown model profile: {profile_name}") from exc
            provider_name = str(config.get("provider", ""))
            model_name = str(config.get("model", ""))
            if provider_name not in providers or not model_name:
                raise ModelConfigurationError(f"Invalid model mapping for {profile.value}")
            models[profile] = ModelDefinition(
                provider=provider_name,
                model=model_name,
                input_cost_per_million=float(config.get("input_cost_per_million") or 0),
                output_cost_per_million=float(config.get("output_cost_per_million") or 0),
            )
        missing = [profile.value for profile in ModelProfile if profile not in models]
        if missing:
            raise ModelConfigurationError(f"Missing model profiles: {', '.join(missing)}")
        return cls(providers, models)

    def definition(self, profile: ModelProfile) -> ModelDefinition:
        return self.models[profile]

    def provider(self, name: str) -> ModelProvider:
        return self.providers[name]
