"""Provider-independent model request and response types."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class ModelProfile(str, enum.Enum):
    FAST = "fast"
    STANDARD = "standard"
    STRONG = "strong"
    MAX = "max"


class AgentRole(str, enum.Enum):
    PLAN = "PLAN"
    CODE = "CODE"
    TEST_GENERATION = "TEST_GENERATION"
    DEBUG = "DEBUG"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    content: str
    usage: TokenUsage
    provider: str
    model: str
    profile: ModelProfile
    request_id: Optional[str] = None
