from __future__ import annotations

import json

import httpx
import pytest

from agent.oj_client.client import OJClient, OJProtocolError
from agent.oj_client.workflow import run_fixed_solution
from agent.workspace.task import TaskWorkspace

from .test_phase1_oj_client import PROBLEM


FIXED_CPP = "#include <iostream>\nint main(){std::cout << 0 << '\\n';}\n"


def test_fixed_solution_http_workflow_persists_state_feedback_and_trace(tmp_path):
    requests = []
    submission_reads = 0

    def handler(request):
        nonlocal submission_reads
        requests.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/api/v1/agent/problems/sum":
            return httpx.Response(200, json=PROBLEM)
        if request.method == "POST" and request.url.path == "/api/v1/submissions":
            payload = json.loads(request.content)
            assert payload["source_code"] == FIXED_CPP
            return httpx.Response(
                202, json={"submission_id": "sub_fixed", "status": "QUEUED"}
            )
        if request.url.path == "/api/v1/submissions/sub_fixed":
            submission_reads += 1
            status = "RUNNING" if submission_reads == 1 else "FINISHED"
            body = {"submission_id": "sub_fixed", "status": status}
            if status == "FINISHED":
                body["verdict"] = "WA"
            return httpx.Response(200, json=body)
        if request.url.path == "/api/v1/agent/submissions/sub_fixed/feedback":
            return httpx.Response(
                200,
                json={
                    "verdict": "WA",
                    "summary": "Wrong answer.",
                    "failure": {"test_index": 1},
                },
            )
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OJClient(
        "https://oj.example.test",
        "oj_never_log_this",
        client=http,
        sleeper=lambda _: None,
        monotonic=lambda: 0.0,
    )
    workspace = TaskWorkspace.create(
        tmp_path, "phase1-fixed", "sum", "phase1-http"
    )
    result = run_fixed_solution(
        client,
        workspace,
        FIXED_CPP,
        timeout_seconds=5,
        poll_interval_seconds=0,
    )
    assert result.verdict == "WA"
    assert workspace.read_text("solution.cpp") == FIXED_CPP
    assert workspace.read_json("problem.json")["problem_id"] == "sum"
    assert workspace.read_json("state.json")["last_outcome_kind"] == "user_program_failure"
    assert workspace.read_json("artifacts/submission-final.json")["verdict"] == "WA"
    assert workspace.read_json("artifacts/feedback.json")["failure"]["test_index"] == 1
    assert workspace.read_json("artifacts/result.json")["feedback_recorded"] is True
    assert all(path != "/api/v1/runs" for _method, path, _auth in requests)
    assert all(auth == "Bearer oj_never_log_this" for _method, _path, auth in requests)

    trace_text = workspace.read_text("events.jsonl")
    assert "oj_never_log_this" not in trace_text
    records = [json.loads(line) for line in trace_text.splitlines()]
    event_types = {record["type"] for record in records}
    assert {"TOOL_CALL", "TOOL_RESULT", "SUBMISSION", "JUDGE_RESULT", "STATE_CHANGE"} <= event_types
    assert all(record["task_id"] == "phase1-fixed" for record in records)


def test_fixed_workflow_rejects_feedback_for_a_different_verdict(tmp_path):
    def handler(request):
        if request.url.path == "/api/v1/agent/problems/sum":
            return httpx.Response(200, json=PROBLEM)
        if request.method == "POST":
            return httpx.Response(
                202, json={"submission_id": "sub_fixed", "status": "QUEUED"}
            )
        if request.url.path == "/api/v1/submissions/sub_fixed":
            return httpx.Response(
                200,
                json={
                    "submission_id": "sub_fixed",
                    "status": "FINISHED",
                    "verdict": "AC",
                },
            )
        return httpx.Response(200, json={"verdict": "WA", "summary": "Mismatch"})

    client = OJClient(
        "https://oj.example.test",
        "oj_test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    workspace = TaskWorkspace.create(tmp_path, "phase1-mismatch", "sum", "phase1-http")
    with pytest.raises(OJProtocolError, match="does not match"):
        run_fixed_solution(
            client,
            workspace,
            FIXED_CPP,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
