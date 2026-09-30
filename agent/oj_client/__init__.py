"""Typed HTTP client for the remote CodeHarness OJ."""

from .client import (
    OJClient,
    OJClientError,
    OJHTTPError,
    OJProtocolError,
    OJResultUnknownError,
    OJTransportError,
)

__all__ = ["OJClient", "OJClientError"]
"""Public CodeHarness-owned MiniOJ client types."""

from .client import OJClient, OJClientError
from .types import (
    AgentProblem,
    ClientErrorKind,
    CustomRunResult,
    CustomRunStatus,
    ErrorResponse,
    JudgeOutcomeKind,
    JudgeFeedback,
    ProblemLimits,
    ProblemSample,
    ProtocolValidationError,
    SubmissionRecord,
    SubmissionStatus,
    Verdict,
)

__all__ = [
    "AgentProblem",
    "ClientErrorKind",
    "CustomRunResult",
    "CustomRunStatus",
    "ErrorResponse",
    "JudgeFeedback",
    "JudgeOutcomeKind",
    "OJClient",
    "OJClientError",
    "OJHTTPError",
    "OJProtocolError",
    "OJResultUnknownError",
    "OJTransportError",
    "ProblemLimits",
    "ProblemSample",
    "ProtocolValidationError",
    "SubmissionRecord",
    "SubmissionStatus",
    "Verdict",
]
