"""Output verification only. Execution and special judging belong to MiniOJ."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

SAMPLE_POLICY = "sample_check_v1"
KINDS = {"exact", "token", "float", "special", "unknown"}
PROGRAM_FAILURES = {"WA", "CE", "RE", "TLE", "MLE", "OLE"}


@dataclass(frozen=True)
class CheckerSpec:
    kind: str = "unknown"
    absolute_tolerance: Optional[float] = None
    relative_tolerance: Optional[float] = None
    normalization: str = "none"
    source: str = "unadvertised"

    def __post_init__(self):
        if self.kind not in KINDS or self.normalization not in {"none", "line_endings"}:
            raise ValueError("Unsupported sample checker")
        for value in (self.absolute_tolerance, self.relative_tolerance):
            try:
                valid = value is None or (not isinstance(value, bool) and isinstance(value, (int, float))
                                         and math.isfinite(value) and value >= 0)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("Float tolerances must be explicit finite nonnegative numbers")
        if self.kind != "float" and any(v is not None for v in (
                self.absolute_tolerance, self.relative_tolerance)):
            raise ValueError("Tolerances apply only to float checking")
        if not isinstance(self.source, str) or not self.source or len(self.source) > 500:
            raise ValueError("Invalid checker source")

    @classmethod
    def from_metadata(cls, value, *, source="sanitized_problem#checker"):
        """Unknown wire values do not acquire token semantics."""
        if isinstance(value, str):
            kind = {"tokens": "token", "testlib": "special"}.get(value, value)
            return cls(kind=kind if kind in KINDS else "unknown", source=source)
        # The remote object schema is not confirmed. Explicit config uses from_dict.
        return cls(source=source)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Checker configuration must be an object")
        return cls(**value)


@dataclass(frozen=True)
class SampleGatePolicy:
    version: str = SAMPLE_POLICY
    on_unverifiable: str = "stop"
    overrides: dict = field(default_factory=dict)
    llm_checker: str = "submit_on_pass"

    def __post_init__(self):
        if self.version != SAMPLE_POLICY or self.on_unverifiable not in {"stop", "submit"}:
            raise ValueError("Unsupported sample gate policy")
        if self.llm_checker not in {"disabled", "advisory", "submit_on_pass"}:
            raise ValueError("Invalid LLM checker policy")
        if not isinstance(self.overrides, dict) or len(self.overrides) > 1000:
            raise ValueError("Invalid sample checker overrides")
        for problem_id, spec in self.overrides.items():
            if not isinstance(problem_id, str) or not problem_id.strip() or problem_id != problem_id.strip():
                raise ValueError("Invalid checker problem ID")
            CheckerSpec.from_dict(spec)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Sample gate policy must be an object")
        return cls(**value)

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SampleCheck:
    status: str
    passed: Optional[bool]
    reason: str
    verdict: Optional[str] = None


def unverifiable(reason):
    return SampleCheck("sample_check_unverifiable", None, reason)


def compared(passed, reason):
    return SampleCheck("sample_pass" if passed else "sample_wrong_answer", passed,
                       reason, None if passed else "WA")


class SampleChecker:
    def check(self, expected, actual, *, stdin="", judge=None):
        raise NotImplementedError


class ExactChecker(SampleChecker):
    def __init__(self, normalization="none"):
        self.normalization = normalization

    def check(self, expected, actual, **kwargs):
        if self.normalization == "line_endings":
            expected = expected.replace("\r\n", "\n").replace("\r", "\n")
            actual = actual.replace("\r\n", "\n").replace("\r", "\n")
        return compared(actual == expected, "exact_comparison")


class TokenChecker(SampleChecker):
    def check(self, expected, actual, **kwargs):
        return compared(actual.split() == expected.split(), "token_comparison")


class FloatChecker(SampleChecker):
    def __init__(self, absolute_tolerance, relative_tolerance):
        self.absolute, self.relative = absolute_tolerance, relative_tolerance

    def check(self, expected, actual, **kwargs):
        if self.absolute is None or self.relative is None:
            return unverifiable("float_tolerances_missing")
        want, got = expected.split(), actual.split()
        if len(want) != len(got):
            return compared(False, "float_token_count")
        for left, right in zip(want, got):
            try:
                reference = float(left)
            except ValueError:
                if left != right:
                    return compared(False, "nonnumeric_token_mismatch")
                continue
            try:
                observed = float(right)
            except ValueError:
                return compared(False, "numeric_token_required")
            if not math.isfinite(reference):
                return unverifiable("nonfinite_reference")
            if not math.isfinite(observed):
                return compared(False, "nonfinite_output")
            # Explicit contest-style bound, relative to the reference value.
            bound = max(self.absolute, self.relative * abs(reference))
            if abs(observed - reference) > bound:
                return compared(False, "float_outside_tolerance")
        return compared(True, "float_comparison")


class RemoteChecker(SampleChecker):
    def check(self, expected, actual, *, stdin="", judge=None):
        if judge is None:
            return unverifiable("remote_sample_checker_unavailable")
        # A future verified adapter must attest that a checker was evaluated.
        # Execution OK or an unknown response can never be promoted to AC.
        result = judge(stdin=stdin, stdout=actual)
        if not isinstance(result, dict) or result.get("checker_evaluated") is not True:
            return unverifiable("remote_checker_result_unknown")
        verdict = result.get("verdict")
        if verdict in {"AC", "WA"}:
            return compared(verdict == "AC", "remote_checker_verdict")
        return unverifiable("remote_checker_result_unknown")


class UnknownChecker(SampleChecker):
    def check(self, expected, actual, **kwargs):
        return unverifiable("checker_type_unknown")


def checker(spec):
    return {"exact": lambda: ExactChecker(spec.normalization), "token": TokenChecker,
            "float": lambda: FloatChecker(spec.absolute_tolerance, spec.relative_tolerance),
            "special": RemoteChecker, "unknown": UnknownChecker}[spec.kind]()


def check_run(spec, sample, result, *, judge=None):
    if result.status in PROGRAM_FAILURES:
        return SampleCheck("sample_program_failure", False, "custom_run_program_verdict", result.status)
    if result.exit_code not in {None, 0}:
        return SampleCheck("sample_program_failure", False, "nonzero_exit_code", "RE")
    if result.stdout_truncated:
        return unverifiable("stdout_truncated")
    return checker(spec).check(sample["output"], result.stdout, stdin=sample["input"], judge=judge)
