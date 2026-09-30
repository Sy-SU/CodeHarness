"""Role-to-profile policy kept independent from the agent loop."""

from __future__ import annotations

from typing import Dict, Optional

from agent.models.types import AgentRole, ModelProfile


class ModelPolicy:
    def __init__(self, mapping: Optional[Dict[AgentRole, ModelProfile]] = None):
        self.mapping = mapping or {
            AgentRole.PLAN: ModelProfile.STRONG,
            AgentRole.CODE: ModelProfile.STANDARD,
            AgentRole.TEST_GENERATION: ModelProfile.STRONG,
            AgentRole.DEBUG: ModelProfile.STANDARD,
            AgentRole.REVIEW: ModelProfile.STRONG,
        }

    def choose(self, role: AgentRole, *, prior_failures: int = 0) -> ModelProfile:
        profile = self.mapping[role]
        if role is AgentRole.DEBUG and prior_failures >= 2 and profile is ModelProfile.STANDARD:
            return ModelProfile.STRONG
        return profile
