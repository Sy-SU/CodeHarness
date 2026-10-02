"""Project the selected YAML without constructing Providers or resolving keys."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Optional

import yaml

from agent.core.policy import ModelPolicy
from dashboard.models import ModelsView, ProfileView
from dashboard.security import Sanitizer
from .workspace import number, string


class ConfigurationRepository:
    def __init__(self, path: Optional[Path], sanitizer: Sanitizer):
        self.path, self.sanitizer = path, sanitizer

    def read(self) -> ModelsView:
        if self.path is None:
            return ModelsView(None, "No configuration selected", [], {}, {},
                              "Select --model-config or MODEL_CONFIG to inspect a mapping.")
        try:
            if self.path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Configuration too large")
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("models"), dict):
                raise ValueError("Invalid configuration")
            policy = ModelPolicy.from_config(raw.get("policy"))
            profiles = []
            placeholder = False
            for profile, value in raw["models"].items():
                if not isinstance(value, dict):
                    raise ValueError("Invalid profile")
                model = string(value.get("model"))
                placeholder = placeholder or bool(model and model.startswith("MODEL_NAME_"))
                input_price, output_price = number(value.get("input_cost_per_million")), number(value.get("output_cost_per_million"))
                currency = string(value.get("currency"))
                known = input_price is not None and output_price is not None and currency is not None
                params = value.get("parameters", {})
                if not isinstance(params, dict):
                    raise ValueError("Invalid parameters")
                profiles.append(ProfileView(str(profile), string(value.get("provider")), model,
                    self.sanitizer.value(params), "known" if known else "missing_pricing", currency,
                    input_price if known else None, output_price if known else None))
            roles = {role.value: profile.value for role, profile in policy.mapping.items()}
            escalation = asdict(policy.debug_escalation)
            escalation["failure_kinds"] = sorted(escalation["failure_kinds"])
            escalation["from_profile"] = policy.debug_escalation.from_profile.value
            escalation["to_profile"] = policy.debug_escalation.to_profile.value
            safe = self.sanitizer.value(asdict(ModelsView(self.path.name, "Loaded configuration", profiles,
                    roles, escalation, "Placeholder model names; this is a structural example." if placeholder else
                    "Configuration view only; no claim of a finalized experiment policy.")))
            return ModelsView(safe["source"], safe["status"], [ProfileView(**value) for value in safe["profiles"]],
                              safe["roles"], safe["escalation"], safe["warning"])
        except (OSError, ValueError, TypeError, yaml.YAMLError, RecursionError):
            return ModelsView(self.sanitizer.text(self.path.name), "Configuration unavailable", [], {}, {},
                              "Unable to parse the selected configuration. No credentials are required or displayed.")
