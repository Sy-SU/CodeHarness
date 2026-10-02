"""Configurable Role-to-Profile policy independent from the agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional

import yaml

from agent.models.registry import ModelConfigurationError
from agent.models.types import AgentRole, ModelProfile


DEFAULT_ROLE_MAPPING = {
    AgentRole.PLAN: ModelProfile.STRONG,
    AgentRole.CODE: ModelProfile.STANDARD,
    AgentRole.TEST_GENERATION: ModelProfile.STRONG,
    AgentRole.DEBUG: ModelProfile.STANDARD,
    AgentRole.REVIEW: ModelProfile.STRONG,
}


@dataclass(frozen=True)
class DebugEscalationPolicy:
    enabled: bool = False
    threshold: Optional[int] = None
    failure_kinds: FrozenSet[str] = field(default_factory=frozenset)
    from_profile: ModelProfile = ModelProfile.STANDARD
    to_profile: ModelProfile = ModelProfile.STRONG

    def __post_init__(self) -> None:
        if self.enabled:
            if (
                isinstance(self.threshold, bool)
                or not isinstance(self.threshold, int)
                or self.threshold < 1
            ):
                raise ValueError("Enabled DEBUG escalation requires a positive threshold")
            if not self.failure_kinds:
                raise ValueError("Enabled DEBUG escalation requires explicit failure_kinds")
            if self.from_profile is self.to_profile:
                raise ValueError("DEBUG escalation profiles must differ")
        elif self.threshold is not None or self.failure_kinds:
            raise ValueError("Disabled DEBUG escalation cannot define threshold or failure_kinds")


class ModelPolicy:
    def __init__(
        self,
        mapping: Optional[Mapping[AgentRole, ModelProfile]] = None,
        *,
        debug_escalation: Optional[DebugEscalationPolicy] = None,
    ):
        self.mapping = dict(DEFAULT_ROLE_MAPPING if mapping is None else mapping)
        missing = set(AgentRole).difference(self.mapping)
        extra = set(self.mapping).difference(AgentRole)
        if missing or extra:
            raise ValueError("Role mapping must define every known AgentRole exactly once")
        self.debug_escalation = debug_escalation or DebugEscalationPolicy()

    @classmethod
    def from_config(cls, raw: Optional[Mapping[str, Any]]) -> "ModelPolicy":
        config = {} if raw is None else raw
        if not isinstance(config, Mapping):
            raise ModelConfigurationError("policy must be a mapping")
        roles = config.get("roles")
        if roles is None:
            mapping = dict(DEFAULT_ROLE_MAPPING)
        else:
            if not isinstance(roles, Mapping):
                raise ModelConfigurationError("policy.roles must be a mapping")
            if set(roles) != {role.value for role in AgentRole}:
                raise ModelConfigurationError(
                    "policy.roles must define PLAN, CODE, TEST_GENERATION, DEBUG, and REVIEW"
                )
            mapping: Dict[AgentRole, ModelProfile] = {}
            for role_name, profile_name in roles.items():
                try:
                    mapping[AgentRole(role_name)] = ModelProfile(profile_name)
                except (TypeError, ValueError) as exc:
                    raise ModelConfigurationError(
                        f"Invalid policy mapping: {role_name} -> {profile_name}"
                    ) from exc

        escalation_value = config.get("debug_escalation")
        escalation_raw = (
            {"enabled": False} if escalation_value is None else escalation_value
        )
        if not isinstance(escalation_raw, Mapping):
            raise ModelConfigurationError("policy.debug_escalation must be a mapping")
        enabled = escalation_raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ModelConfigurationError("policy.debug_escalation.enabled must be boolean")
        try:
            if enabled:
                failure_kinds = escalation_raw.get("failure_kinds")
                if not isinstance(failure_kinds, list) or any(
                    not isinstance(item, str) or not item for item in failure_kinds
                ):
                    raise ModelConfigurationError(
                        "Enabled DEBUG escalation requires failure_kinds as a list"
                    )
                escalation = DebugEscalationPolicy(
                    enabled=True,
                    threshold=escalation_raw.get("threshold"),
                    failure_kinds=frozenset(failure_kinds),
                    from_profile=ModelProfile(
                        escalation_raw.get("from_profile", ModelProfile.STANDARD.value)
                    ),
                    to_profile=ModelProfile(
                        escalation_raw.get("to_profile", ModelProfile.STRONG.value)
                    ),
                )
            else:
                unexpected = set(escalation_raw).difference({"enabled"})
                if unexpected:
                    raise ModelConfigurationError(
                        "Disabled DEBUG escalation cannot define behavior"
                    )
                escalation = DebugEscalationPolicy()
        except ValueError as exc:
            raise ModelConfigurationError(f"Invalid DEBUG escalation policy: {exc}") from exc
        return cls(mapping, debug_escalation=escalation)

    @classmethod
    def from_yaml(cls, path: Path) -> "ModelPolicy":
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ModelConfigurationError(f"Cannot read model policy: {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, Mapping):
            raise ModelConfigurationError("Model configuration root must be a mapping")
        return cls.from_config(loaded.get("policy"))

    def choose(
        self,
        role: AgentRole,
        *,
        prior_failures: int = 0,
        failure_kind: Optional[str] = None,
        allow_escalation: bool = True,
    ) -> ModelProfile:
        if (
            isinstance(prior_failures, bool)
            or not isinstance(prior_failures, int)
            or prior_failures < 0
        ):
            raise ValueError("prior_failures must be a non-negative integer")
        profile = self.mapping[role]
        escalation = self.debug_escalation
        if (
            role is AgentRole.DEBUG
            and allow_escalation
            and escalation.enabled
            and profile is escalation.from_profile
            and prior_failures >= escalation.threshold
            and failure_kind in escalation.failure_kinds
        ):
            return escalation.to_profile
        return profile
