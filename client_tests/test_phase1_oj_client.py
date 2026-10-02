from __future__ import annotations

import json

import httpx
import pytest

from agent.oj_client.client import (
    OJClient,
    OJHTTPError,
    OJProtocolError,
    OJResultUnknownError,
    OJTransportError,
)
from agent.oj_client.types import (
    ClientErrorKind,
    JudgeOutcomeKind,
    SubmissionRecord,
    Verdict,
)


PROBLEM = {
    "problem_id": "sum",
    "title": "Sum",
    "statement": "Add two integers.",
    "input_specification": "Two integers.",
    "output_specification": "Their sum.",
    "notes": "",
    "limits": {"time_ms": 1000, "memory_mb": 128},
    "samples": [{"input": "1 2\n", "output": "3\n"}],
}


def make_client(handler, **kwargs):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OJClient("https://oj.example.test", "oj_top_secret", client=http, **kwargs)


def test_five_protocol_methods_use_bearer_and_typed_responses():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/v1/agent/problems/sum":
            return httpx.Response(200, json=PROBLEM)
        if request.url.path == "/api/v1/runs":
            payload = json.loads(request.content)
            assert payload == {
                "language": "cpp20",
                "source_code": "int main(){}",
                "stdin": "",
            }
            return httpx.Response(200, json={"status": "OK", "stdout": ""})
        if request.method == "POST" and request.url.path == "/api/v1/submissions":
            return httpx.Response(
                202, json={"submission_id": "sub_1", "status": "QUEUED"}
            )
        if request.url.path == "/api/v1/submissions/sub_1":
            return httpx.Response(
                200,
                json={
                    "submission_id": "sub_1",
                    "status": "FINISHED",
                    "verdict": "AC",
                },
            )
        if request.url.path == "/api/v1/agent/submissions/sub_1/feedback":
            return httpx.Response(200, json={"verdict": "AC", "summary": "Accepted."})
        raise AssertionError(request.url)

    client = make_client(handler)
    assert client.get_problem("sum").problem_id == "sum"
    assert client.run_code("int main(){}", "").status == "OK"
    assert client.submit_solution("sum", "int main(){}").status == "QUEUED"
    assert client.get_submission("sub_1").known_verdict is Verdict.AC
    assert client.get_feedback("sub_1").known_verdict is Verdict.AC
    assert len(requests) == 5
    assert all(request.headers["authorization"] == "Bearer oj_top_secret" for request in requests)


def test_custom_run_alias_is_explicit_compatibility_mode():
    observed = []

    def handler(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "OK"})

    make_client(handler).run_code("int main(){}", "")
    make_client(handler, send_custom_run_code_alias=True).run_code("int main(){}", "")
    assert "code" not in observed[0]
    assert observed[1]["code"] == observed[1]["source_code"]


def test_polling_accepts_early_finished_machine_result_without_summary_parsing():
    responses = iter(
        [
            {"submission_id": "sub_1", "status": "COMPILING"},
            {
                "submission_id": "sub_1",
                "status": "FINISHED",
                "verdict": "CE",
                "summary": "This text is not parsed for status.",
            },
        ]
    )
    sleeps = []

    def handler(request):
        return httpx.Response(200, json=next(responses))

    client = make_client(handler, sleeper=sleeps.append, monotonic=lambda: 0.0)
    result = client.wait_for_submission(
        "sub_1", timeout_seconds=10, poll_interval_seconds=0.25
    )
    assert result.known_verdict is Verdict.CE
    assert sleeps == [0.25]


@pytest.mark.parametrize("verdict", [item.value for item in Verdict])
def test_all_canonical_verdicts_remain_machine_typed(verdict):
    record = SubmissionRecord.from_dict(
        {"submission_id": "sub_1", "status": "FINISHED", "verdict": verdict}
    )
    assert record.known_verdict is Verdict(verdict)
    if verdict == "AC":
        assert record.outcome_kind is JudgeOutcomeKind.ACCEPTED
    elif verdict == "IE":
        assert record.outcome_kind is JudgeOutcomeKind.REMOTE_INFRASTRUCTURE_FAILURE
    else:
        assert record.outcome_kind is JudgeOutcomeKind.USER_PROGRAM_FAILURE


def test_http_authentication_failure_is_classified_without_token_leak():
    def handler(request):
        return httpx.Response(401, json={"detail": "Invalid oj_top_secret"})

    with pytest.raises(OJHTTPError) as raised:
        make_client(handler).get_problem("sum")
    error = raised.value
    assert error.kind is ClientErrorKind.HTTP
    assert error.http_status == 401
    assert not error.automatic_retry_allowed
    assert "oj_top_secret" not in str(error)


@pytest.mark.parametrize(
    "body",
    (
        [],
        {"problem_id": 7, "title": "Bad"},
    ),
)
def test_invalid_json_shapes_are_protocol_errors(body):
    def handler(request):
        return httpx.Response(200, json=body)

    with pytest.raises(OJProtocolError) as raised:
        make_client(handler).get_problem("sum")
    assert raised.value.kind is ClientErrorKind.PROTOCOL


def test_sanitized_problem_rejects_known_information_leak_fields():
    def handler(request):
        return httpx.Response(200, json={**PROBLEM, "hidden_testcases": ["secret"]})

    with pytest.raises(OJProtocolError, match="excluded fields"):
        make_client(handler).get_problem("sum")


def test_invalid_accepted_submission_response_is_result_unknown_without_retry():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            202,
            json={"submission_id": "sub_1", "status": "FINISHED", "verdict": "AC"},
        )

    with pytest.raises(OJResultUnknownError, match="remote creation state is unknown") as raised:
        make_client(handler).submit_solution("sum", "int main(){}")
    assert calls == 1
    assert raised.value.http_status == 202
    assert raised.value.submission_state_unknown
    assert not raised.value.automatic_retry_allowed


def test_submission_identifier_accepts_confirmed_integer_wire_form():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"submission_id": 11, "status": "QUEUED"})
        assert request.url.path == "/api/v1/submissions/11"
        return httpx.Response(
            200,
            json={"submission_id": 11, "status": "FINISHED", "verdict": "AC"},
        )

    client = make_client(handler)
    created = client.submit_solution("sum", "int main(){}")
    assert created.submission_id == "11"
    final = client.get_submission(created.submission_id)
    assert final.submission_id == "11"
    assert final.verdict == "AC"


def test_feedback_rejects_unknown_verdict():
    def handler(request):
        return httpx.Response(200, json={"verdict": "FUTURE", "summary": "unknown"})

    with pytest.raises(OJProtocolError, match="Unknown MiniOJ feedback verdict"):
        make_client(handler).get_feedback("sub_1")


@pytest.mark.parametrize(
    "body",
    (
        {"submission_id": "sub_1", "status": "REMOTE_NEW_STATE"},
        {"submission_id": "sub_1", "status": "FINISHED"},
        {"submission_id": "sub_1", "status": "FINISHED", "verdict": "NEW_VERDICT"},
    ),
)
def test_unknown_or_incomplete_final_states_stop_polling_as_protocol_errors(body):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=body)

    with pytest.raises(OJProtocolError):
        make_client(handler).wait_for_submission(
            "sub_1", timeout_seconds=5, poll_interval_seconds=0
        )
    assert calls == 1


def test_unfinished_feedback_http_response_is_not_treated_as_feedback():
    def handler(request):
        return httpx.Response(409, json={"message": "Submission is not finished"})

    with pytest.raises(OJHTTPError) as raised:
        make_client(handler).get_feedback("sub_1")
    assert raised.value.http_status == 409


def test_poll_deadline_returns_result_unknown_without_resubmission():
    calls = []
    clock = iter((0.0, 2.0))

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200, json={"submission_id": "sub_1", "status": "RUNNING"}
        )

    client = make_client(handler, monotonic=lambda: next(clock), sleeper=lambda _: None)
    with pytest.raises(OJResultUnknownError) as raised:
        client.wait_for_submission(
            "sub_1", timeout_seconds=1, poll_interval_seconds=0.1
        )
    assert raised.value.kind is ClientErrorKind.RESULT_UNKNOWN
    assert raised.value.submission_state_unknown
    assert calls == [("GET", "/api/v1/submissions/sub_1")]


def test_submission_timeout_is_unknown_and_post_is_never_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(OJResultUnknownError) as raised:
        make_client(handler).submit_solution("sum", "int main(){}")
    assert calls == 1
    assert raised.value.submission_state_unknown
    assert not raised.value.automatic_retry_allowed


def test_connection_failure_is_transport_not_user_program_failure():
    def handler(request):
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(OJTransportError) as raised:
        make_client(handler).get_problem("sum")
    assert raised.value.kind is ClientErrorKind.TRANSPORT
    assert not raised.value.submission_state_unknown
