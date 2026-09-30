"""HTTP-only MiniOJ client with typed responses and conservative failures."""

from __future__ import annotations

import re
import time
import math
from typing import Any, Callable, Mapping, Optional, Sequence, Type, TypeVar
from urllib.parse import quote, urlsplit

import httpx

from .types import (
    AgentProblem,
    ClientErrorKind,
    CustomRunResult,
    ErrorResponse,
    JudgeFeedback,
    ProblemLimits,
    ProblemSample,
    ProtocolValidationError,
    SubmissionRecord,
    SubmissionStatus,
)


T = TypeVar("T")
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[^\s,;]+")


class OJClientError(RuntimeError):
    """Safe, classified client failure that never contains the API token."""

    def __init__(
        self,
        message: str,
        *,
        kind: ClientErrorKind,
        method: str,
        path: str,
        http_status: Optional[int] = None,
        remote_code: Optional[str] = None,
        request_id: Optional[str] = None,
        submission_state_unknown: bool = False,
    ):
        super().__init__(BEARER_PATTERN.sub("Bearer <redacted>", message))
        self.kind = kind
        self.method = method
        self.path = path
        self.http_status = http_status
        self.remote_code = remote_code
        self.request_id = request_id
        self.submission_state_unknown = submission_state_unknown
        # U03 has not approved automatic retries for any transport operation.
        self.automatic_retry_allowed = False


class OJTransportError(OJClientError):
    pass


class OJHTTPError(OJClientError):
    pass


class OJProtocolError(OJClientError):
    pass


class OJResultUnknownError(OJClientError):
    pass


class OJClient:
    """Synchronous client; it never retries or executes submitted code locally."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout_seconds: float = 120,
        send_custom_run_code_alias: bool = False,
        client: Optional[httpx.Client] = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        normalized_url = base_url.strip().rstrip("/")
        parsed = urlsplit(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http:// or https:// URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "base_url must not contain credentials, a query, or a fragment"
            )
        if not api_token.strip():
            raise ValueError("api_token must not be empty")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        self.base_url = normalized_url
        self._api_token = api_token.strip()
        self.send_custom_run_code_alias = send_custom_run_code_alias
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None
        self._sleep = sleeper
        self._monotonic = monotonic

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "OJClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @staticmethod
    def _resource_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        return quote(value.strip(), safe="")

    def _remote_error(self, response: httpx.Response) -> tuple[Optional[str], str]:
        message = f"MiniOJ returned HTTP {response.status_code}"
        remote_code: Optional[str] = None
        try:
            body = response.json()
        except ValueError:
            return remote_code, message
        if not isinstance(body, Mapping):
            return remote_code, message
        try:
            parsed = ErrorResponse.from_dict(body)
        except ProtocolValidationError:
            detail = body.get("detail")
            if isinstance(detail, str) and detail:
                message = f"{message}: {detail[:500]}"
            return remote_code, message.replace(self._api_token, "<redacted>")
        message = f"{message}: {parsed.message[:500]}"
        return parsed.code, message.replace(self._api_token, "<redacted>")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: Sequence[int],
        submission_state_unknown_on_timeout: bool = False,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._api_token}"
        try:
            response = self.client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            if submission_state_unknown_on_timeout:
                raise OJResultUnknownError(
                    "Formal submission timed out; the remote creation state is unknown",
                    kind=ClientErrorKind.RESULT_UNKNOWN,
                    method=method,
                    path=path,
                    submission_state_unknown=True,
                ) from exc
            raise OJTransportError(
                f"MiniOJ request timed out: {method} {path}",
                kind=ClientErrorKind.TRANSPORT,
                method=method,
                path=path,
            ) from exc
        except httpx.RequestError as exc:
            raise OJTransportError(
                f"MiniOJ transport failed: {method} {path}: {type(exc).__name__}",
                kind=ClientErrorKind.TRANSPORT,
                method=method,
                path=path,
            ) from exc

        request_id = response.headers.get("x-request-id")
        if response.status_code not in expected_statuses:
            remote_code, message = self._remote_error(response)
            raise OJHTTPError(
                message,
                kind=ClientErrorKind.HTTP,
                method=method,
                path=path,
                http_status=response.status_code,
                remote_code=remote_code,
                request_id=request_id,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise OJProtocolError(
                f"MiniOJ returned invalid JSON for {method} {path}",
                kind=ClientErrorKind.PROTOCOL,
                method=method,
                path=path,
                http_status=response.status_code,
                request_id=request_id,
            ) from exc
        if not isinstance(body, Mapping):
            raise OJProtocolError(
                f"MiniOJ returned a non-object JSON response for {method} {path}",
                kind=ClientErrorKind.PROTOCOL,
                method=method,
                path=path,
                http_status=response.status_code,
                request_id=request_id,
            )
        return body

    @staticmethod
    def _parse(
        parser_type: Type[T],
        body: Mapping[str, Any],
        *,
        method: str,
        path: str,
    ) -> T:
        try:
            return parser_type.from_dict(body)  # type: ignore[attr-defined]
        except ProtocolValidationError as exc:
            raise OJProtocolError(
                f"Invalid MiniOJ response for {method} {path}: {exc}",
                kind=ClientErrorKind.PROTOCOL,
                method=method,
                path=path,
            ) from exc

    def get_problem(self, problem_id: str) -> AgentProblem:
        path = f"/api/v1/agent/problems/{self._resource_id(problem_id, 'problem_id')}"
        body = self._request_json("GET", path, expected_statuses=(200,))
        return self._parse(AgentProblem, body, method="GET", path=path)

    def run_code(self, code: str, stdin: str) -> CustomRunResult:
        if not isinstance(code, str) or not code:
            raise ValueError("code must be a non-empty string")
        if not isinstance(stdin, str):
            raise ValueError("stdin must be a string")
        payload = {"language": "cpp20", "source_code": code, "stdin": stdin}
        if self.send_custom_run_code_alias:
            payload["code"] = code
        path = "/api/v1/runs"
        body = self._request_json("POST", path, expected_statuses=(200,), json=payload)
        return self._parse(CustomRunResult, body, method="POST", path=path)

    def submit_solution(self, problem_id: str, code: str) -> SubmissionRecord:
        if not isinstance(code, str) or not code:
            raise ValueError("code must be a non-empty string")
        raw_problem_id = problem_id.strip() if isinstance(problem_id, str) else ""
        if not raw_problem_id:
            raise ValueError("problem_id must be a non-empty string")
        path = "/api/v1/submissions"
        body = self._request_json(
            "POST",
            path,
            expected_statuses=(202,),
            submission_state_unknown_on_timeout=True,
            json={
                "problem_id": raw_problem_id,
                "language": "cpp20",
                "source_code": code,
            },
        )
        record = self._parse(SubmissionRecord, body, method="POST", path=path)
        if record.known_status is not SubmissionStatus.QUEUED or record.verdict is not None:
            raise OJProtocolError(
                "Formal submission creation must return QUEUED without a verdict",
                kind=ClientErrorKind.PROTOCOL,
                method="POST",
                path=path,
            )
        return record

    def get_submission(self, submission_id: str) -> SubmissionRecord:
        path = f"/api/v1/submissions/{self._resource_id(submission_id, 'submission_id')}"
        body = self._request_json("GET", path, expected_statuses=(200,))
        return self._parse(SubmissionRecord, body, method="GET", path=path)

    def get_feedback(self, submission_id: str) -> JudgeFeedback:
        path = (
            "/api/v1/agent/submissions/"
            f"{self._resource_id(submission_id, 'submission_id')}/feedback"
        )
        body = self._request_json("GET", path, expected_statuses=(200,))
        feedback = self._parse(JudgeFeedback, body, method="GET", path=path)
        if feedback.known_verdict is None:
            raise OJProtocolError(
                f"Unknown MiniOJ feedback verdict: {feedback.verdict}",
                kind=ClientErrorKind.PROTOCOL,
                method="GET",
                path=path,
            )
        return feedback

    def wait_for_submission(
        self,
        submission_id: str,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> SubmissionRecord:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds < 0
        ):
            raise ValueError("timeout_seconds must be non-negative")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds < 0
        ):
            raise ValueError("poll_interval_seconds must be non-negative")
        deadline = self._monotonic() + timeout_seconds
        while True:
            record = self.get_submission(submission_id)
            status = record.known_status
            if status is None:
                path = f"/api/v1/submissions/{quote(submission_id, safe='')}"
                raise OJProtocolError(
                    f"Unknown MiniOJ submission status: {record.status}",
                    kind=ClientErrorKind.PROTOCOL,
                    method="GET",
                    path=path,
                )
            if status is SubmissionStatus.FINISHED:
                if record.known_verdict is None:
                    path = f"/api/v1/submissions/{quote(submission_id, safe='')}"
                    raise OJProtocolError(
                        "FINISHED submission is missing a recognized verdict",
                        kind=ClientErrorKind.PROTOCOL,
                        method="GET",
                        path=path,
                    )
                return record
            now = self._monotonic()
            if now >= deadline:
                raise OJResultUnknownError(
                    f"Submission {submission_id} did not reach a final state before the deadline",
                    kind=ClientErrorKind.RESULT_UNKNOWN,
                    method="GET",
                    path=f"/api/v1/submissions/{quote(submission_id, safe='')}",
                    submission_state_unknown=True,
                )
            self._sleep(min(poll_interval_seconds, max(0.0, deadline - now)))
