"""MiniOJ wire types owned by CodeHarness.

The HTTP contract is still a draft.  Parsers therefore validate the small
agreed core while retaining additional fields and unrecognized status values
instead of silently treating them as supported protocol.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


class ProtocolValidationError(ValueError):
    """A response did not contain the minimally required wire shape."""


class SubmissionStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    COMPILING = "COMPILING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class Verdict(str, enum.Enum):
    AC = "AC"
    WA = "WA"
    CE = "CE"
    RE = "RE"
    TLE = "TLE"
    MLE = "MLE"
    OLE = "OLE"
    IE = "IE"


class CustomRunStatus(str, enum.Enum):
    OK = "OK"


class ClientErrorKind(str, enum.Enum):
    TRANSPORT = "transport"
    HTTP = "http"
    PROTOCOL = "protocol"
    REMOTE_INFRASTRUCTURE = "remote_infrastructure"
    USER_PROGRAM = "user_program"
    RESULT_UNKNOWN = "result_unknown"


class JudgeOutcomeKind(str, enum.Enum):
    ACCEPTED = "accepted"
    USER_PROGRAM_FAILURE = "user_program_failure"
    REMOTE_INFRASTRUCTURE_FAILURE = "remote_infrastructure_failure"


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolValidationError(f"{label} must be a JSON object")
    return value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ProtocolValidationError(f"{key} must be a non-empty string")
    return result


def _required_identifier(value: Mapping[str, Any], key: str) -> str:
    """Normalize confirmed string/integer resource IDs to the internal string form."""

    result = value.get(key)
    if isinstance(result, str) and result:
        return result
    if isinstance(result, int) and not isinstance(result, bool) and result >= 0:
        return str(result)
    raise ProtocolValidationError(f"{key} must be a non-empty string or non-negative integer")


def _optional_string(value: Mapping[str, Any], key: str) -> Optional[str]:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str):
        raise ProtocolValidationError(f"{key} must be a string or null")
    return result


def _required_int(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ProtocolValidationError(f"{key} must be an integer")
    return result


def _optional_int(value: Mapping[str, Any], key: str) -> Optional[int]:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, int) or isinstance(result, bool):
        raise ProtocolValidationError(f"{key} must be an integer or null")
    return result


def _optional_bool(value: Mapping[str, Any], key: str) -> Optional[bool]:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, bool):
        raise ProtocolValidationError(f"{key} must be a boolean or null")
    return result


def _extras(value: Mapping[str, Any], known: set[str]) -> Dict[str, Any]:
    return {key: item for key, item in value.items() if key not in known}


@dataclass(frozen=True)
class ProblemLimits:
    time_ms: int
    memory_mb: int
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProblemLimits":
        value = _object(raw, "limits")
        return cls(
            time_ms=_required_int(value, "time_ms"),
            memory_mb=_required_int(value, "memory_mb"),
            extra_fields=_extras(value, {"time_ms", "memory_mb"}),
        )


@dataclass(frozen=True)
class ProblemSample:
    input: str
    output: str
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProblemSample":
        value = _object(raw, "sample")
        sample_input = value.get("input")
        sample_output = value.get("output")
        if not isinstance(sample_input, str) or not isinstance(sample_output, str):
            raise ProtocolValidationError("sample input and output must be strings")
        return cls(
            input=sample_input,
            output=sample_output,
            extra_fields=_extras(value, {"input", "output"}),
        )


@dataclass(frozen=True)
class AgentProblem:
    problem_id: str
    title: str
    statement: str
    input_specification: str
    output_specification: str
    notes: str
    limits: ProblemLimits
    samples: List[ProblemSample]
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AgentProblem":
        value = _object(raw, "problem")
        excluded = {
            "rating",
            "tags",
            "editorial",
            "historical_solutions",
            "hidden_testcases",
            "hidden_tests",
        }
        leaked = sorted(excluded.intersection(value))
        if leaked:
            raise ProtocolValidationError(
                f"sanitized problem contains excluded fields: {', '.join(leaked)}"
            )
        raw_samples = value.get("samples", [])
        if not isinstance(raw_samples, list):
            raise ProtocolValidationError("samples must be a JSON array")
        return cls(
            problem_id=_required_string(value, "problem_id"),
            title=_required_string(value, "title"),
            statement=_required_string(value, "statement"),
            input_specification=_optional_string(value, "input_specification") or "",
            output_specification=_optional_string(value, "output_specification") or "",
            notes=_optional_string(value, "notes") or "",
            limits=ProblemLimits.from_dict(_object(value.get("limits"), "limits")),
            samples=[ProblemSample.from_dict(_object(item, "sample")) for item in raw_samples],
            extra_fields=_extras(
                value,
                {
                    "problem_id",
                    "title",
                    "statement",
                    "input_specification",
                    "output_specification",
                    "notes",
                    "limits",
                    "samples",
                },
            ),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "title": self.title,
            "statement": self.statement,
            "input_specification": self.input_specification,
            "output_specification": self.output_specification,
            "notes": self.notes,
            "limits": {
                "time_ms": self.limits.time_ms,
                "memory_mb": self.limits.memory_mb,
                **self.limits.extra_fields,
            },
            "samples": [
                {"input": sample.input, "output": sample.output, **sample.extra_fields}
                for sample in self.samples
            ],
            **self.extra_fields,
        }


@dataclass(frozen=True)
class SubmissionRecord:
    submission_id: str
    status: str
    verdict: Optional[str] = None
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SubmissionRecord":
        value = _object(raw, "submission")
        return cls(
            submission_id=_required_identifier(value, "submission_id"),
            status=_required_string(value, "status"),
            verdict=_optional_string(value, "verdict"),
            extra_fields=_extras(value, {"submission_id", "status", "verdict"}),
        )

    @property
    def known_status(self) -> Optional[SubmissionStatus]:
        try:
            return SubmissionStatus(self.status)
        except ValueError:
            return None

    @property
    def known_verdict(self) -> Optional[Verdict]:
        try:
            return Verdict(self.verdict) if self.verdict is not None else None
        except ValueError:
            return None

    @property
    def outcome_kind(self) -> Optional[JudgeOutcomeKind]:
        verdict = self.known_verdict
        if verdict is Verdict.AC:
            return JudgeOutcomeKind.ACCEPTED
        if verdict is Verdict.IE:
            return JudgeOutcomeKind.REMOTE_INFRASTRUCTURE_FAILURE
        if verdict is not None:
            return JudgeOutcomeKind.USER_PROGRAM_FAILURE
        return None

    def as_dict(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "submission_id": self.submission_id,
            "status": self.status,
            **self.extra_fields,
        }
        if self.verdict is not None:
            value["verdict"] = self.verdict
        return value


@dataclass(frozen=True)
class CustomRunResult:
    status: str
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    time_ms: Optional[int] = None
    memory_kb: Optional[int] = None
    stdout_truncated: Optional[bool] = None
    stderr_truncated: Optional[bool] = None
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CustomRunResult":
        value = _object(raw, "custom run result")
        return cls(
            status=_required_string(value, "status"),
            stdout=_optional_string(value, "stdout"),
            stderr=_optional_string(value, "stderr"),
            exit_code=_optional_int(value, "exit_code"),
            time_ms=_optional_int(value, "time_ms"),
            memory_kb=_optional_int(value, "memory_kb"),
            stdout_truncated=_optional_bool(value, "stdout_truncated"),
            stderr_truncated=_optional_bool(value, "stderr_truncated"),
            extra_fields=_extras(
                value,
                {
                    "status",
                    "stdout",
                    "stderr",
                    "exit_code",
                    "time_ms",
                    "memory_kb",
                    "stdout_truncated",
                    "stderr_truncated",
                },
            ),
        )

    @property
    def known_status(self) -> Optional[CustomRunStatus]:
        try:
            return CustomRunStatus(self.status)
        except ValueError:
            return None

    def as_dict(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {"status": self.status, **self.extra_fields}
        for key in (
            "stdout",
            "stderr",
            "exit_code",
            "time_ms",
            "memory_kb",
            "stdout_truncated",
            "stderr_truncated",
        ):
            item = getattr(self, key)
            if item is not None:
                value[key] = item
        return value


@dataclass(frozen=True)
class JudgeFeedback:
    verdict: str
    summary: Optional[str]
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "JudgeFeedback":
        value = _object(raw, "feedback")
        return cls(
            verdict=_required_string(value, "verdict"),
            summary=_optional_string(value, "summary"),
            # Detail keys are deliberately not promoted until U01/U04 is settled.
            details=_extras(value, {"verdict", "summary"}),
        )

    @property
    def known_verdict(self) -> Optional[Verdict]:
        try:
            return Verdict(self.verdict)
        except ValueError:
            return None

    def as_dict(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {"verdict": self.verdict, **self.details}
        if self.summary is not None:
            value["summary"] = self.summary
        return value


@dataclass(frozen=True)
class ErrorResponse:
    code: Optional[str]
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ErrorResponse":
        value = _object(raw, "error")
        return cls(
            code=_optional_string(value, "code"),
            message=_required_string(value, "message"),
            # The remote error schema is still an explicit draft (TODO U01).
            details=_extras(value, {"code", "message"}),
        )

    def as_dict(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {"message": self.message, **self.details}
        if self.code is not None:
            value["code"] = self.code
        return value
