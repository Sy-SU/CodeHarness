"""Provider-independent model request, response, error, and cost types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


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


class LLMCallStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LLMErrorKind(str, enum.Enum):
    TRANSPORT = "transport"
    HTTP = "http"
    PROTOCOL = "protocol"
    PROVIDER = "provider"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Unsupported chat message role: {self.role}")
        if not isinstance(self.content, str):
            raise TypeError("Chat message content must be a string")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if isinstance(self.input_tokens, bool) or not isinstance(self.input_tokens, int):
            raise TypeError("input_tokens must be an integer")
        if isinstance(self.output_tokens, bool) or not isinstance(self.output_tokens, int):
            raise TypeError("output_tokens must be an integer")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("Token usage cannot be negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMToolCall:
    call_id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class LLMError:
    kind: LLMErrorKind
    message: str
    status_code: Optional[int] = None
    retryable: bool = False


@dataclass(frozen=True)
class LLMResponse:
    content: str
    usage: Optional[TokenUsage]
    provider: str
    model: str
    profile: ModelProfile
    request_id: Optional[str] = None
    status: LLMCallStatus = LLMCallStatus.SUCCEEDED
    finish_reason: Optional[str] = None
    tool_calls: Tuple[LLMToolCall, ...] = field(default_factory=tuple)
    error: Optional[LLMError] = None
    latency_ms: Optional[int] = None
    actual_response_model: Optional[str] = None
    usage_metadata: Dict[str, Any] = field(default_factory=dict)
    transport_diagnostics: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ModelProfile):
            raise TypeError("profile must be a ModelProfile")
        if not isinstance(self.status, LLMCallStatus):
            raise TypeError("status must be an LLMCallStatus")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if self.status is LLMCallStatus.SUCCEEDED and self.error is not None:
            raise ValueError("A successful model response cannot contain an error")
        if self.status is LLMCallStatus.FAILED and self.error is None:
            raise ValueError("A failed model response must contain an error")
        if self.status is LLMCallStatus.SUCCEEDED and not self.content and not self.tool_calls:
            raise ValueError("A successful model response needs content or tool calls")

    @property
    def succeeded(self) -> bool:
        return self.status is LLMCallStatus.SUCCEEDED

    @classmethod
    def failure(
        cls,
        *,
        provider: str,
        model: str,
        profile: ModelProfile,
        error: LLMError,
        request_id: Optional[str] = None,
        latency_ms: Optional[int] = None,
        usage: Optional[TokenUsage] = None,
        actual_response_model: Optional[str] = None,
        usage_metadata: Optional[Dict[str, Any]] = None,
        transport_diagnostics: Optional[Dict[str, Any]] = None,
    ) -> "LLMResponse":
        return cls(
            content="",
            usage=usage,
            provider=provider,
            model=model,
            profile=profile,
            request_id=request_id,
            status=LLMCallStatus.FAILED,
            error=error,
            latency_ms=latency_ms,
            actual_response_model=actual_response_model,
            usage_metadata=usage_metadata or {},
            transport_diagnostics=transport_diagnostics or {},
        )


@dataclass(frozen=True)
class CostEstimate:
    amount: Optional[float]
    currency: Optional[str]
    known: bool
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.known:
            if self.amount is None or self.currency is None or self.reason is not None:
                raise ValueError("Known costs require amount and currency only")
            if self.amount < 0:
                raise ValueError("Estimated cost cannot be negative")
        elif self.amount is not None or self.reason is None:
            raise ValueError("Unknown costs require a reason and no amount")
