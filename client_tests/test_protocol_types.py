from __future__ import annotations

import json

import pytest

from agent.models.types import LLMResponse, ModelProfile, TokenUsage
from agent.oj_client.types import (
    AgentProblem,
    CustomRunResult,
    ErrorResponse,
    JudgeFeedback,
    ProtocolValidationError,
    SubmissionRecord,
    SubmissionStatus,
    Verdict,
)
from agent.workspace.task import EventType, TaskState, TraceEvent


def test_fixture_manifest_marks_unconfirmed_shapes(protocol_fixture):
    manifest = protocol_fixture("manifest.json")
    assert manifest["schema_version"].startswith("phase1-client")
    assert manifest["fixtures"]["problem.json"] == "agreed_example"
    assert manifest["fixtures"]["error.json"] == "unconfirmed_schema"
    assert manifest["fixtures"]["feedback_ce.json"] == "draft_detail_schema"
    assert "failure_schema_unconfirmed" in manifest["fixtures"]["custom_run_ok.json"]


def test_problem_fixture_parses_as_client_owned_type(protocol_fixture):
    problem = AgentProblem.from_dict(protocol_fixture("problem.json"))
    assert problem.problem_id == "example-problem"
    assert problem.limits.time_ms == 2000
    assert problem.samples[0].output == "3\n"
    assert problem.as_dict() == protocol_fixture("problem.json")


def test_submission_known_values_remain_typed(protocol_fixture):
    queued = SubmissionRecord.from_dict(protocol_fixture("submission_queued.json"))
    finished = SubmissionRecord.from_dict(protocol_fixture("submission_finished.json"))
    assert queued.known_status is SubmissionStatus.QUEUED
    assert queued.known_verdict is None
    assert finished.known_status is SubmissionStatus.FINISHED
    assert finished.known_verdict is Verdict.AC


def test_custom_run_success_fixture_parses_without_freezing_failure_schema(protocol_fixture):
    result = CustomRunResult.from_dict(protocol_fixture("custom_run_ok.json"))
    assert result.status == "OK"
    assert result.stdout == "3\n"
    assert result.time_ms == 15


def test_unknown_status_verdict_and_fields_are_not_promoted(protocol_fixture):
    submission = SubmissionRecord.from_dict(protocol_fixture("submission_unknown.json"))
    assert submission.known_status is None
    assert submission.known_verdict is None
    assert submission.status == "PAUSED_BY_REMOTE"
    assert submission.extra_fields == {"remote_extension": {"opaque": True}}


def test_draft_feedback_and_error_details_remain_opaque(protocol_fixture):
    feedback = JudgeFeedback.from_dict(protocol_fixture("feedback_ce.json"))
    error = ErrorResponse.from_dict(protocol_fixture("error.json"))
    assert feedback.known_verdict is Verdict.CE
    assert feedback.details == {
        "compile": {"success": False, "stderr": "example diagnostic"}
    }
    assert error.details == {"request_id": "req_example"}


def test_invalid_protocol_shape_fails_instead_of_coercing_values():
    with pytest.raises(ProtocolValidationError):
        SubmissionRecord.from_dict({"submission_id": 123, "status": "QUEUED"})


def test_model_state_and_event_types_are_client_owned():
    response = LLMResponse(
        content="example",
        usage=TokenUsage(1, 2),
        provider="fake",
        model="fake-model",
        profile=ModelProfile.STANDARD,
    )
    state = TaskState(task_id="task-1", problem_id="p-1")
    event = TraceEvent("2026-10-01T00:00:00+00:00", "LLM_RESPONSE", {"ok": True})
    future_event = TraceEvent("2026-10-01T00:00:00+00:00", "FUTURE_EVENT", {})
    assert response.usage.output_tokens == 2
    assert state.current_phase == "PLAN"
    assert event.known_type is EventType.LLM_RESPONSE
    assert future_event.known_type is None
    assert json.loads(json.dumps(event.as_dict()))["type"] == "LLM_RESPONSE"
