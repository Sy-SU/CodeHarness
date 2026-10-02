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
from .contests import (ContestProblem, ContestSnapshot, contest_identifier,
                       public_contest, public_problem_identifier)
from .standings import OfficialPerformance, account_identity, parse_performance


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
        contest_id: Optional[str] = None,
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
        self.contest_id = contest_identifier(contest_id) if contest_id is not None else None

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

    def _request_response(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: Sequence[int],
        submission_state_unknown_on_timeout: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
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
        return response

    def _request_json(self, method, path, *, expected_statuses,
                      submission_state_unknown_on_timeout=False, **kwargs):
        response = self._request_response(method, path, expected_statuses=expected_statuses,
            submission_state_unknown_on_timeout=submission_state_unknown_on_timeout, **kwargs)
        request_id = response.headers.get("x-request-id")
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

    def _public_html(self, path):
        response = self._request_response("GET", path, expected_statuses=(200,), follow_redirects=False)
        if (not response.headers.get("content-type", "").lower().startswith("text/html")
                or len(response.content) > 2_000_000):
            raise OJProtocolError("Invalid public contest page", kind=ClientErrorKind.PROTOCOL,
                                  method="GET", path=path)
        return response.text

    def get_contest(self, contest_id: str) -> ContestSnapshot:
        contest_id = contest_identifier(contest_id)
        path = f"/contests/{contest_id}"
        try:
            title, status, entries = public_contest(self._public_html(path), contest_id, self.base_url)
            problems = []
            for label, problem_title in entries:
                problem_path = f"{path}/problems/{label}"
                problem_id = public_problem_identifier(self._public_html(problem_path))
                if problem_id in {problem.problem_id for problem in problems}:
                    raise ProtocolValidationError("Duplicate contest problem ID")
                problems.append(ContestProblem(label, problem_id, problem_title))
            return ContestSnapshot(contest_id, title, status, problems)
        except ProtocolValidationError as exc:
            raise OJProtocolError("Unrecognized public contest data", kind=ClientErrorKind.PROTOCOL,
                                  method="GET", path=path) from exc

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

    def get_checker_metadata(self, problem_id: str):
        """Allowlist only checker; ordinary problem content never reaches tools."""
        path = f"/api/v1/problems/{self._resource_id(problem_id, 'problem_id')}"
        try:
            body = self._request_json("GET", path, expected_statuses=(200,))
        except OJClientError as exc:
            # Ordinary metadata error prose may contain fields excluded from the Agent.
            exc.args = ("Public checker metadata unavailable",)
            exc.remote_code = None
            raise
        if body.get("problem_id") != problem_id:
            raise OJProtocolError("Checker metadata identity mismatch", kind=ClientErrorKind.PROTOCOL,
                                  method="GET", path=path)
        value = body.get("checker")
        return {"checker": value if isinstance(value, str) and len(value) <= 50 else None,
                "source": f"GET {path}#checker"}

    def get_account_identity(self):
        return account_identity(self._request_json("GET", "/api/v1/me", expected_statuses=(200,)))

    def get_official_performance(self, contest_id, identity):
        """GET only. Identity is supplied by the frozen run, never a row index."""
        from dataclasses import replace
        from datetime import datetime, timezone
        contest_id = contest_identifier(contest_id)
        source = f"GET /api/v1/contests/{contest_id}/standings#rows[].performance"
        if (not isinstance(identity, dict) or isinstance(identity.get("user_id"), bool)
                or not isinstance(identity.get("user_id"), int) or identity["user_id"] < 1):
            return OfficialPerformance(official_performance_status="identity_unresolved",
                                       official_performance_source=source)
        current = self.get_account_identity()
        if current is None or current["user_id"] != identity.get("user_id"):
            return OfficialPerformance(official_performance_status="identity_unresolved",
                official_performance_source=source, official_performance_identity=identity)
        response = self._request_response("GET", f"/api/v1/contests/{contest_id}/standings",
                                          expected_statuses=(200,), follow_redirects=False)
        try:
            body = response.json() if len(response.content) <= 2_000_000 else None
        except ValueError:
            body = None
        observation = parse_performance(body, contest_id, identity)
        return replace(observation, official_performance_fetched_at=datetime.now(timezone.utc).isoformat())

    def get_feedback_mode(self):
        """Record an explicit server declaration; never infer it from role/diagnostics.

        Current MiniOJ may omit this optional capability. Unknown stays unknown;
        experiment conditions can require an explicit declaration before paid calls.
        """
        path = "/api/v1/me"
        body = self._request_json("GET", path, expected_statuses=(200,))
        mode = body.get("feedback_mode")
        if mode in ("full", "verdict_only"):
            return {"mode": mode, "source": "GET /api/v1/me#feedback_mode", "status": "confirmed"}
        return {"mode": None, "source": "GET /api/v1/me",
                "status": "not_advertised" if mode is None else "unrecognized"}

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
        path = (f"/api/v1/contests/{self.contest_id}/submissions" if self.contest_id
                else "/api/v1/submissions")
        try:
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
        except OJProtocolError as exc:
            # HTTP 202 means the server may already have created a submission.
            # Without a valid queued record, its ID/result cannot be recovered
            # safely and repeating the POST could create a duplicate.
            raise OJResultUnknownError(
                f"Formal submission returned HTTP 202 with an invalid response; "
                f"the remote creation state is unknown: {exc}",
                kind=ClientErrorKind.RESULT_UNKNOWN,
                method="POST",
                path=path,
                http_status=202,
                submission_state_unknown=True,
            ) from exc
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
