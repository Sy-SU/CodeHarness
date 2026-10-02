from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from agent.execution import RunRequest
from agent.oj_client.client import OJHTTPError, OJResultUnknownError
from agent.oj_client.client import OJClient
from agent.oj_client.feedback import FORMAL_TOOLS, VERDICT_ONLY_POLICY
from agent.oj_client.types import ClientErrorKind, CustomRunResult, JudgeFeedback, SubmissionRecord
from agent.tools.runtime import ToolRuntime
from agent.workspace.task import TaskWorkspace
from client_tests.test_phase5_experiments import ExperimentOJ, config, service
from experiments.results import summarize_rows
from experiments.runner import ExperimentRunner


HIDDEN = "PRIVATE_TEST_PAYLOAD_MUST_NOT_REACH_AGENT"
DETAILS = {"failure": {"input": HIDDEN, "expected": HIDDEN, "actual": HIDDEN,
    "stderr": HIDDEN}, "future_extension": {"nested": [HIDDEN]}, "summary": HIDDEN}


def restricted_runtime(tmp_path):
    workspace = TaskWorkspace.create(tmp_path, "filtered", "sum", "harness-loop")
    workspace.state.feedback_policy = VERDICT_ONLY_POLICY
    workspace.state.actual_feedback_mode = "full"
    workspace.state.effective_feedback_mode = "verdict_only"
    return ToolRuntime(workspace), workspace


@pytest.mark.parametrize("tool", sorted(FORMAL_TOOLS))
def test_all_formal_tool_results_are_projected_before_trace_or_caller(tmp_path, tool):
    runtime, workspace = restricted_runtime(tmp_path)
    raw = (JudgeFeedback("WA", HIDDEN, DETAILS) if tool == "get_feedback"
        else SubmissionRecord("known-id", "FINISHED", "WA", DETAILS))
    runtime.register(tool, lambda: raw)
    result = runtime.call(tool)
    assert result.as_dict() == ({"verdict": "WA"} if tool == "get_feedback"
        else {"submission_id": "known-id", "status": "FINISHED", "verdict": "WA"})
    assert HIDDEN in json.dumps(raw.as_dict())  # The source object is not mutated.
    assert HIDDEN not in workspace.read_text("events.jsonl")


@pytest.mark.parametrize("tool", sorted(FORMAL_TOOLS))
@pytest.mark.parametrize("uncertain", [False, True])
def test_formal_error_prose_is_withheld_without_losing_classification(tmp_path, tool, uncertain):
    runtime, workspace = restricted_runtime(tmp_path)
    error_type = OJResultUnknownError if uncertain else OJHTTPError
    error = error_type(HIDDEN, kind=ClientErrorKind.RESULT_UNKNOWN if uncertain else ClientErrorKind.HTTP,
        method="POST" if tool == "submit_solution" else "GET", path="/formal",
        http_status=202 if uncertain else 403, remote_code=HIDDEN,
        request_id="request-id", submission_state_unknown=uncertain)

    def fail():
        raise error

    runtime.register(tool, fail)
    with pytest.raises(error_type) as caught:
        runtime.call(tool)
    assert HIDDEN not in str(caught.value) and caught.value.remote_code is None
    assert caught.value.kind == error.kind and caught.value.http_status == error.http_status
    assert caught.value.submission_state_unknown is uncertain
    assert HIDDEN not in workspace.read_text("events.jsonl")


def test_public_sample_run_is_not_formal_feedback_and_keeps_stdout_stderr(tmp_path):
    runtime, workspace = restricted_runtime(tmp_path)
    sample = CustomRunResult("OK", stdout="public sample output", stderr="public sample diagnostic", exit_code=0)
    runtime.register("run_code", lambda: sample)
    assert runtime.call("run_code") == sample
    assert "public sample output" in workspace.read_text("events.jsonl")


def test_effective_metadata_cannot_disable_the_frozen_projection(tmp_path):
    runtime, workspace = restricted_runtime(tmp_path)
    workspace.state.effective_feedback_mode = None
    runtime.register("get_feedback", lambda: JudgeFeedback("WA", HIDDEN, DETAILS))
    assert runtime.call("get_feedback").as_dict() == {"verdict": "WA"}
    assert HIDDEN not in workspace.read_text("events.jsonl")


class RichOJ(ExperimentOJ):
    def submit_solution(self, problem_id, code):
        return replace(super().submit_solution(problem_id, code), extra_fields=DETAILS)

    def get_submission(self, submission_id):
        return replace(super().get_submission(submission_id), extra_fields=DETAILS)

    def wait_for_submission(self, submission_id, **kwargs):
        return replace(super().wait_for_submission(submission_id, **kwargs), extra_fields=DETAILS)

    def get_feedback(self, submission_id):
        self.feedback_calls += 1
        return JudgeFeedback(self.verdicts[int(submission_id) - 1], HIDDEN, DETAILS)


@pytest.mark.parametrize("actual", ["full", "verdict_only"])
def test_rich_judge_data_never_enters_prompts_trace_artifacts_or_checkpoint(tmp_path, actual):
    executor, provider, oj = service(tmp_path, oj=RichOJ(["WA", "AC"], feedback_mode=actual))
    cfg = config(strategies=[config().strategies[1]])
    result = ExperimentRunner(executor).run(cfg, "filtered-loop")
    row = result["tasks"][0]
    assert row["solved"] and row["conditions_verified"]
    assert row["actual_feedback_mode"] == actual and row["effective_feedback_mode"] == "verdict_only"
    assert row["feedback_policy"] == VERDICT_ONLY_POLICY
    assert len(provider.calls) == 3 and len(oj.submissions) == 2 and oj.feedback_calls == 1
    assert all(HIDDEN not in message.content for _, messages in provider.calls for message in messages)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert HIDDEN.encode() not in path.read_bytes(), str(path)
    summary = summarize_rows(result["tasks"])[0]
    assert summary["actual_feedback_mode"] == actual and summary["effective_feedback_mode"] == "verdict_only"
    from pathlib import Path
    root = Path(row["workspace"])
    artifact = json.loads((root / "artifacts/result.json").read_text())
    checkpoint = json.loads((root / "checkpoint.json").read_text())
    assert artifact["effective_feedback_mode"] == "verdict_only"
    assert checkpoint["config"]["feedback_policy"] == VERDICT_ONLY_POLICY
    assert checkpoint["feedback"] == {"verdict": "WA"}
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    effective = next(e["payload"] for e in events if e["type"] == "FEEDBACK_MODE_EFFECTIVE")
    assert effective["downgraded"] is (actual == "full")


def test_real_http_client_full_feedback_is_filtered_before_any_task_persistence(tmp_path):
    executor, provider, _ = service(tmp_path)
    created = []
    reference = ExperimentOJ(["WA", "AC"])

    def respond(request):
        path = request.url.path
        assert request.headers["Authorization"] == "Bearer test-token"
        if path == "/api/v1/me":
            body = {"feedback_mode": "full"}
        elif path == "/api/v1/agent/problems/sum":
            body = reference.get_problem("sum").as_dict()
        elif path == "/api/v1/runs":
            body = CustomRunResult("OK", stdout="3\n", exit_code=0).as_dict()
        elif request.method == "POST" and path == "/api/v1/submissions":
            created.append(json.loads(request.content))
            return httpx.Response(202, json={"submission_id": str(len(created)), "status": "QUEUED", **DETAILS})
        elif path.endswith("/feedback"):
            body = {"verdict": "WA", "summary": HIDDEN, **DETAILS}
        elif path.startswith("/api/v1/submissions/"):
            sid = path.rsplit("/", 1)[-1]
            body = {"submission_id": sid, "status": "FINISHED",
                "verdict": "WA" if sid == "1" else "AC", **DETAILS}
        else:
            pytest.fail(f"Unexpected request: {request.method} {path}")
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        executor.client_factory = lambda *args, **kwargs: OJClient(*args, client=http, **kwargs)
        result = executor.run(RunRequest("sum", profile="standard"), "http-filtered")
    assert result.solved and len(created) == 2 and len(provider.calls) == 3
    state = json.loads((tmp_path / "http-filtered/state.json").read_text())
    assert state["actual_feedback_mode"] == "full" and state["effective_feedback_mode"] == "verdict_only"
    assert state["feedback_mode_status"] == "confirmed"
    assert all(HIDDEN not in m.content for _, messages in provider.calls for m in messages)
    assert all(HIDDEN.encode() not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


def test_unknown_formal_response_fails_closed_instead_of_dumping_body(tmp_path):
    runtime, workspace = restricted_runtime(tmp_path)
    runtime.register("get_feedback", lambda: {"verdict": "WA", "arbitrary": HIDDEN})
    with pytest.raises(RuntimeError, match="withheld"):
        runtime.call("get_feedback")
    assert HIDDEN not in workspace.read_text("events.jsonl")


@pytest.mark.parametrize("tool,result", [
    ("get_feedback", JudgeFeedback(HIDDEN, HIDDEN, DETAILS)),
    ("get_submission", SubmissionRecord("known-id", HIDDEN, "WA", DETAILS)),
    ("wait_for_submission", SubmissionRecord("known-id", "FINISHED", HIDDEN, DETAILS)),
    ("wait_for_submission", SubmissionRecord("known-id", "FINISHED", None, DETAILS)),
])
def test_unknown_machine_values_fail_closed_without_leaking_prose(tmp_path, tool, result):
    runtime, workspace = restricted_runtime(tmp_path)
    runtime.register(tool, lambda: result)
    with pytest.raises(RuntimeError) as caught:
        runtime.call(tool)
    assert caught.value.kind is ClientErrorKind.PROTOCOL
    assert HIDDEN not in str(caught.value) and HIDDEN not in workspace.read_text("events.jsonl")


def test_explicit_full_is_not_filtered_or_grouped_as_downgraded_full(tmp_path):
    executor, provider, oj = service(tmp_path, oj=RichOJ(["WA", "AC"], feedback_mode="full"))
    cfg = config(expected_feedback_mode="full", strategies=[config().strategies[1]])
    result = ExperimentRunner(executor).run(cfg, "explicit-full")
    row = result["tasks"][0]
    assert row["solved"] and row["conditions_verified"]
    assert row["actual_feedback_mode"] == row["effective_feedback_mode"] == "full"
    assert row["feedback_policy"] is None
    assert any(HIDDEN in m.content for _, messages in provider.calls for m in messages)
    filtered = {**row, "effective_feedback_mode": "verdict_only", "feedback_policy": VERDICT_ONLY_POLICY,
        "comparison_key": "different-policy"}
    assert len(summarize_rows([row, filtered])) == 2


def test_full_to_verdict_only_resume_keeps_filter_and_known_submission(tmp_path):
    executor, provider, oj = service(tmp_path, oj=RichOJ(["WA", "AC"], feedback_mode="full", interrupt="wait"))
    cfg = config(strategies=[config().strategies[1]])
    runner = ExperimentRunner(executor)
    with pytest.raises(KeyboardInterrupt):
        runner.run(cfg, "filtered-resume")
    assert len(provider.calls) == 2 and len(oj.submissions) == 1
    result = runner.run(cfg, "filtered-resume", resume=True)
    row = result["tasks"][0]
    assert row["solved"] and row["effective_feedback_mode"] == "verdict_only"
    assert len(provider.calls) == 3 and len(oj.submissions) == 2
    assert all(HIDDEN not in m.content for _, messages in provider.calls for m in messages)
    assert all(HIDDEN.encode() not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


def test_old_exact_verdict_only_checkpoint_cannot_silently_gain_new_policy(tmp_path, monkeypatch):
    executor, provider, oj = service(tmp_path, oj=ExperimentOJ(["AC"], interrupt="wait"))
    request = RunRequest("sum", "harness-loop", "standard")
    current_snapshot = executor.snapshot
    monkeypatch.setattr(executor, "snapshot", lambda r: {k: v for k, v in current_snapshot(r).items()
        if k != "feedback_policy"})
    workspace = executor.create_workspace(request, "legacy-exact")
    with pytest.raises(KeyboardInterrupt):
        executor.run(request, workspace.state.task_id, workspace=workspace)
    saved = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    monkeypatch.setattr(executor, "snapshot", current_snapshot)
    with pytest.raises(ValueError, match="differs"):
        executor.run(request, workspace.state.task_id, resume=True)
    assert len(provider.calls) == 2 and len(oj.submissions) == 1
    assert all(p.read_bytes() == data for p, data in saved.items())


def test_removed_policy_cannot_disable_filter_without_configuration_failure(tmp_path):
    executor, provider, oj = service(tmp_path, oj=RichOJ(["AC"], feedback_mode="full"))
    request = RunRequest("sum", profile="standard")
    workspace = executor.create_workspace(request, "policy-drift")
    workspace.state.feedback_policy = None
    with pytest.raises(ValueError, match="differs"):
        executor.run(request, workspace.state.task_id, workspace=workspace)
    assert not provider.calls and not oj.problem_calls and not oj.submissions


def test_resume_rejects_effective_metadata_drift_before_any_more_calls(tmp_path):
    executor, provider, oj = service(tmp_path, oj=RichOJ(["AC"], feedback_mode="full", interrupt="wait"))
    request = RunRequest("sum", profile="standard")
    workspace = executor.create_workspace(request, "effective-drift")
    with pytest.raises(KeyboardInterrupt):
        executor.run(request, workspace.state.task_id, workspace=workspace)
    checkpoint = workspace.read_json("checkpoint.json")
    checkpoint["state"]["effective_feedback_mode"] = "full"
    workspace.write_json("checkpoint.json", checkpoint)
    saved = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="effective feedback mode"):
        executor.run(request, workspace.state.task_id, resume=True)
    assert len(provider.calls) == 2 and len(oj.submissions) == 1
    assert all(p.read_bytes() == data for p, data in saved.items())
