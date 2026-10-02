from __future__ import annotations

import hashlib
import json

import pytest

from agent.cli import main as agent_cli_main
from agent.core.agent import CodingAgent
from agent.core.context import ContextBuilder
from agent.core.policy import ModelPolicy
from agent.models.registry import ModelDefinition, ModelRegistry
from agent.models.types import (
    CostEstimate,
    LLMError,
    LLMErrorKind,
    LLMResponse,
    ModelProfile,
    TokenUsage,
)
from agent.oj_client.client import (
    OJHTTPError,
    OJResultUnknownError,
    OJTransportError,
)
from agent.oj_client.types import (
    AgentProblem,
    ClientErrorKind,
    ProblemLimits,
    ProblemSample,
    SubmissionRecord,
)
from agent.tools.runtime import build_default_tools
from agent.workspace.task import TaskWorkspace
from experiments.summarize import summarize


CPP = """```cpp
#include <iostream>
int main() { long long a, b; std::cin >> a >> b; std::cout << a + b << '\\n'; }
```"""


class RecordingRouter:
    def __init__(self, response: LLMResponse):
        self.response = response
        self.calls = []

    def route(self, profile):
        return ModelDefinition(provider="fake", model="fake-model")

    def complete(self, profile, messages):
        self.calls.append((profile, messages))
        return self.response

    def estimate_cost(self, response):
        return CostEstimate(
            amount=None,
            currency=None,
            known=False,
            reason="missing_pricing" if response.usage is not None else "missing_usage",
        )


class RecordingOJ:
    def __init__(
        self,
        verdict: str = "AC",
        *,
        problem_error=None,
        submit_error=None,
        wait_error=None,
    ):
        self.verdict = verdict
        self.problem_error = problem_error
        self.submit_error = submit_error
        self.wait_error = wait_error
        self.submit_calls = 0
        self.wait_calls = 0
        self.run_calls = 0
        self.feedback_calls = 0

    def get_problem(self, problem_id):
        if self.problem_error is not None:
            raise self.problem_error
        return AgentProblem(
            problem_id="sum",
            title="Sum",
            statement="Add two integers. SANITIZED_MARKER",
            input_specification="Two integers.",
            output_specification="Their sum.",
            notes="",
            limits=ProblemLimits(1000, 128),
            samples=[ProblemSample("1 2\n", "3\n")],
            # ContextBuilder deliberately ignores all non-contract fields.
            extra_fields={"untrusted_metadata": "DO_NOT_SEND_TO_MODEL"},
        )

    def run_code(self, code, stdin):
        self.run_calls += 1
        raise AssertionError("code-only must not use Custom Run")

    def submit_solution(self, problem_id, code):
        self.submit_calls += 1
        if self.submit_error is not None:
            raise self.submit_error
        return SubmissionRecord("sub_phase3", "QUEUED")

    def wait_for_submission(
        self, submission_id, *, timeout_seconds, poll_interval_seconds
    ):
        self.wait_calls += 1
        if self.wait_error is not None:
            raise self.wait_error
        return SubmissionRecord(submission_id, "FINISHED", self.verdict)

    def get_submission(self, submission_id):
        raise AssertionError("Tool runtime should use the encapsulated wait helper")

    def get_feedback(self, submission_id):
        self.feedback_calls += 1
        raise AssertionError("code-only must not fetch judge feedback")


def successful_response(content=CPP, usage=TokenUsage(20, 10)):
    return LLMResponse(
        content=content,
        usage=usage,
        provider="fake",
        model="fake-model",
        profile=ModelProfile.STANDARD,
        request_id="req_phase3",
    )


def make_agent(tmp_path, response=None, oj=None, task_id="phase3-task"):
    workspace = TaskWorkspace.create(
        tmp_path,
        task_id,
        "sum",
        "code-only",
        trace_schema_version="phase3-v1",
    )
    router = RecordingRouter(response or successful_response())
    remote = oj or RecordingOJ()
    agent = CodingAgent(
        router,
        ModelPolicy(),
        ContextBuilder(),
        build_default_tools(remote, workspace),
        workspace,
        poll_seconds=0,
        judge_timeout_seconds=1,
    )
    return agent, workspace, router, remote


def events(workspace):
    return [
        json.loads(line)
        for line in (workspace.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_code_only_ac_has_exactly_one_code_call_one_submission_and_linked_evidence(
    tmp_path,
):
    agent, workspace, router, oj = make_agent(tmp_path)
    result = agent.run_code_only(ModelProfile.STANDARD)

    assert result.solved
    assert result.terminal_status == "accepted"
    assert result.termination_reason == "judge_accepted"
    assert result.llm_calls == 1
    assert result.submissions == 1
    assert workspace.state.submission_attempt_count == 1
    assert workspace.state.llm_success_count == 1
    assert oj.submit_calls == 1 and oj.wait_calls == 1
    assert oj.run_calls == 0 and oj.feedback_calls == 0
    assert len(router.calls) == 1
    prompt = "\n".join(message.content for message in router.calls[0][1])
    assert "SANITIZED_MARKER" in prompt
    assert "DO_NOT_SEND_TO_MODEL" not in prompt

    source = workspace.read_text("solution.cpp")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert result.solution_version == "solution-v1"
    assert result.solution_sha256 == digest
    assert workspace.state.solution_model_call_id == workspace.state.last_model_call_id

    records = events(workspace)
    llm_call = next(item for item in records if item["type"] == "LLM_CALL")
    llm_response = next(item for item in records if item["type"] == "LLM_RESPONSE")
    code_version = next(item for item in records if item["type"] == "CODE_VERSION")
    submission = next(item for item in records if item["type"] == "SUBMISSION")
    judged = next(item for item in records if item["type"] == "JUDGE_RESULT")
    call_id = llm_call["payload"]["correlation_id"]
    assert llm_call["payload"]["role"] == "CODE"
    assert llm_response["payload"]["correlation_id"] == call_id
    assert code_version["payload"]["model_call_id"] == call_id
    assert submission["payload"]["model_call_id"] == call_id
    assert judged["payload"]["model_call_id"] == call_id
    assert {
        code_version["payload"]["code_sha256"],
        submission["payload"]["code_sha256"],
        judged["payload"]["code_sha256"],
    } == {digest}
    assert all(item["schema_version"] == "phase3-v1" for item in records)
    called_tools = [
        item["payload"]["tool"] for item in records if item["type"] == "TOOL_CALL"
    ]
    assert "run_code" not in called_tools
    assert "get_feedback" not in called_tools
    assert workspace.read_json("artifacts/result.json")["model_call_id"] == call_id
    summary = summarize(tmp_path)["code-only:standard"]
    assert summary["average_submission_attempts"] == 1
    assert summary["average_submissions"] == 1
    assert summary["terminal_statuses"] == {"accepted": 1}
    assert summary["termination_reasons"] == {"judge_accepted": 1}
    assert summary["final_verdicts"] == {"AC": 1}


@pytest.mark.parametrize(
    "verdict,status,reason,error_kind",
    [
        ("WA", "user_program_failure", "judge_wa", "user_program"),
        ("IE", "remote_infrastructure_failure", "judge_ie", "remote_infrastructure"),
    ],
)
def test_code_only_judge_failures_are_terminal_without_feedback_or_retry(
    tmp_path, verdict, status, reason, error_kind
):
    oj = RecordingOJ(verdict)
    agent, workspace, router, _ = make_agent(
        tmp_path, oj=oj, task_id=f"phase3-{verdict.lower()}"
    )
    result = agent.run_code_only(ModelProfile.STANDARD)
    assert not result.solved
    assert result.terminal_status == status
    assert result.termination_reason == reason
    assert result.error_kind == error_kind
    assert len(router.calls) == 1
    assert oj.submit_calls == 1 and oj.feedback_calls == 0 and oj.run_calls == 0
    assert workspace.state.submission_count == 1
    assert workspace.state.submission_attempt_count == 1


def test_code_only_model_failure_is_persisted_and_never_submitted(tmp_path):
    failure = LLMResponse.failure(
        provider="fake",
        model="fake-model",
        profile=ModelProfile.STANDARD,
        error=LLMError(
            kind=LLMErrorKind.HTTP,
            message="fake model request returned HTTP 503",
            status_code=503,
            retryable=True,
        ),
    )
    agent, workspace, router, oj = make_agent(
        tmp_path, response=failure, task_id="phase3-model-failure"
    )
    result = agent.run_code_only(ModelProfile.STANDARD)
    assert result.terminal_status == "model_failure"
    assert result.termination_reason == "model_http"
    assert result.error_kind == "http"
    assert len(router.calls) == 1
    assert workspace.state.llm_call_count == 1
    assert workspace.state.llm_failure_count == 1
    assert workspace.state.submission_attempt_count == 0
    assert oj.submit_calls == 0


def test_code_only_invalid_model_output_is_saved_without_hidden_regeneration(tmp_path):
    agent, workspace, router, oj = make_agent(
        tmp_path,
        response=successful_response("This is not a complete program."),
        task_id="phase3-invalid-output",
    )
    result = agent.run_code_only(ModelProfile.STANDARD)
    assert result.terminal_status == "invalid_model_output"
    assert result.termination_reason == "code_extraction_failed"
    assert len(router.calls) == 1
    assert oj.submit_calls == 0
    assert workspace.state.attempt_count == 0
    assert workspace.state.submission_attempt_count == 0
    assert workspace.read_json("artifacts/model-response.json")["content"].startswith(
        "This is not"
    )


def oj_error(error_type, kind, message="remote failure"):
    return error_type(
        message,
        kind=kind,
        method="POST",
        path="/api/v1/submissions",
    )


def test_code_only_problem_http_failure_uses_no_model_or_submission(tmp_path):
    oj = RecordingOJ(
        problem_error=oj_error(OJHTTPError, ClientErrorKind.HTTP, "problem HTTP failure")
    )
    agent, workspace, router, _ = make_agent(
        tmp_path, oj=oj, task_id="phase3-problem-http"
    )
    result = agent.run_code_only(ModelProfile.STANDARD)
    assert result.terminal_status == "client_failure"
    assert result.termination_reason == "problem_fetch_http"
    assert len(router.calls) == 0
    assert workspace.state.llm_call_count == 0
    assert oj.submit_calls == 0


@pytest.mark.parametrize(
    "error,status,reason",
    [
        (
            oj_error(OJHTTPError, ClientErrorKind.HTTP),
            "client_failure",
            "submission_creation_http",
        ),
        (
            oj_error(OJTransportError, ClientErrorKind.TRANSPORT),
            "client_failure",
            "submission_creation_transport",
        ),
        (
            oj_error(OJResultUnknownError, ClientErrorKind.RESULT_UNKNOWN),
            "result_unknown",
            "submission_creation_result_unknown",
        ),
    ],
)
def test_code_only_submission_creation_failure_is_never_retried(
    tmp_path, error, status, reason
):
    oj = RecordingOJ(submit_error=error)
    agent, workspace, router, _ = make_agent(
        tmp_path, oj=oj, task_id=f"phase3-submit-{error.kind.value}"
    )
    result = agent.run_code_only(ModelProfile.STANDARD)
    assert result.terminal_status == status
    assert result.termination_reason == reason
    assert len(router.calls) == 1
    assert oj.submit_calls == 1 and oj.wait_calls == 0
    assert workspace.state.submission_attempt_count == 1
    assert workspace.state.submission_count == 0
    assert workspace.state.last_submission_id is None


def test_code_only_poll_timeout_keeps_confirmed_submission_and_unknown_result(tmp_path):
    error = OJResultUnknownError(
        "deadline reached",
        kind=ClientErrorKind.RESULT_UNKNOWN,
        method="GET",
        path="/api/v1/submissions/sub_phase3",
        submission_state_unknown=True,
    )
    oj = RecordingOJ(wait_error=error)
    agent, workspace, router, _ = make_agent(
        tmp_path, oj=oj, task_id="phase3-poll-timeout"
    )
    result = agent.run_code_only(ModelProfile.STANDARD)
    assert result.terminal_status == "result_unknown"
    assert result.termination_reason == "judge_poll_result_unknown"
    assert result.submission_id == "sub_phase3"
    assert len(router.calls) == 1
    assert oj.submit_calls == 1 and oj.wait_calls == 1
    assert workspace.state.submission_attempt_count == 1
    assert workspace.state.submission_count == 1
    assert workspace.state.last_verdict is None


def test_solve_cli_requires_confirmations_and_finite_limits_before_configuration(
    tmp_path, monkeypatch
):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("configuration must not load")

    monkeypatch.setattr(ModelRegistry, "from_yaml", forbidden)
    base = [
        "solve",
        "sum",
        "--mode",
        "code-only",
        "--profile",
        "standard",
        "--workspace-root",
        str(tmp_path),
        "--http-timeout",
        "1",
        "--poll-interval",
        "0",
        "--deadline",
        "1",
    ]
    with pytest.raises(SystemExit, match="requires --confirm-model-call"):
        agent_cli_main(base)
    with pytest.raises(SystemExit, match="finite and non-negative"):
        agent_cli_main(
            base[:-1]
            + ["nan", "--confirm-model-call", "--confirm-submit"]
        )
    assert not called
