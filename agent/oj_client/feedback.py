"""Versioned, fail-closed projection of formal judge data for verdict-only tasks."""
from __future__ import annotations

from .client import OJClientError, OJProtocolError
from .types import ClientErrorKind, JudgeFeedback, SubmissionRecord, SubmissionStatus


VERDICT_ONLY_POLICY = "formal_verdict_only_v1"
FORMAL_TOOLS = {"submit_solution", "get_submission", "wait_for_submission", "get_feedback"}


def compatible_feedback(expected, actual, policy=None):
    return expected is None or expected == actual or (
        policy == VERDICT_ONLY_POLICY and expected == "verdict_only" and actual == "full")


def restrict_formal_result(tool, result):
    """Drop all server prose/extra fields, not merely familiar hidden-test keys."""
    if tool == "get_feedback" and isinstance(result, JudgeFeedback):
        if result.known_verdict is not None:
            return JudgeFeedback(result.verdict, None)
    if tool in FORMAL_TOOLS - {"get_feedback"} and isinstance(result, SubmissionRecord):
        if (result.known_status is not None
                and (result.verdict is None or result.known_verdict is not None)
                and (result.known_status is not SubmissionStatus.FINISHED or result.verdict is not None)):
            return SubmissionRecord(result.submission_id, result.status, result.verdict)
    raise OJProtocolError("Unrecognized formal judge response", kind=ClientErrorKind.PROTOCOL,
        method="POST" if tool == "submit_solution" else "GET", path=tool)


def restricted_error(exc):
    # An HTTP error's free-text body can contain the same hidden data as feedback.
    message = "Formal judge request failed; remote details withheld by verdict-only policy"
    if isinstance(exc, OJClientError):
        return type(exc)(message, kind=exc.kind, method=exc.method, path=exc.path,
            http_status=exc.http_status, request_id=exc.request_id,
            submission_state_unknown=exc.submission_state_unknown)
    return RuntimeError(message)
