from __future__ import annotations

import json
import os

import pytest

from agent.oj_client.types import AgentProblem, ProblemLimits
from agent.tools.runtime import ToolValidationError, build_default_tools
from agent.workspace.task import TaskWorkspace


class NoNetworkClient:
    def get_problem(self, problem_id):
        return AgentProblem(
            problem_id,
            "Title",
            "Statement",
            "",
            "",
            "",
            ProblemLimits(1000, 128),
            [],
        )

    def run_code(self, code, stdin):
        raise AssertionError("not used")

    def submit_solution(self, problem_id, code):
        raise AssertionError("not used")

    def get_submission(self, submission_id):
        raise AssertionError("not used")

    def get_feedback(self, submission_id):
        raise AssertionError("not used")

    def wait_for_submission(self, submission_id, *, timeout_seconds, poll_interval_seconds):
        raise AssertionError("not used")


def make_runtime(tmp_path):
    workspace = TaskWorkspace.create(tmp_path, "task-tools", "p1", "phase1-test")
    return workspace, build_default_tools(NoNetworkClient(), workspace)


def test_workspace_has_required_files_and_atomic_state_replace(tmp_path, monkeypatch):
    replacements = []
    original_replace = os.replace

    def observed_replace(source, target):
        replacements.append((source, target))
        return original_replace(source, target)

    monkeypatch.setattr(os, "replace", observed_replace)
    workspace = TaskWorkspace.create(tmp_path, "task-state", "p1", "phase1-test")
    workspace.state.submission_count = 2
    workspace.save_state()
    assert {"task.json", "state.json", "events.jsonl", "artifacts"}.issubset(
        {path.name for path in workspace.root.iterdir()}
    )
    assert workspace.read_json("state.json")["submission_count"] == 2
    assert replacements
    assert not list(workspace.root.rglob("*.tmp"))


def test_file_tool_requires_explicit_overwrite_and_protects_managed_files(tmp_path):
    workspace, runtime = make_runtime(tmp_path)
    runtime.call("write_file", relative="solution.cpp", content="first")
    with pytest.raises(FileExistsError):
        runtime.call("write_file", relative="solution.cpp", content="second")
    runtime.call(
        "write_file", relative="solution.cpp", content="second", overwrite=True
    )
    assert runtime.call("read_file", relative="solution.cpp") == "second"
    for managed_path in ("state.json", "artifacts/../state.json"):
        with pytest.raises(ValueError):
            runtime.call(
                "write_file", relative=managed_path, content="{}", overwrite=True
            )
    assert "solution.cpp" in runtime.call("list_files")
    assert workspace.read_json("state.json")["task_id"] == "task-tools"


@pytest.mark.parametrize("relative", ("../escape.txt", "/tmp/escape.txt"))
def test_workspace_rejects_paths_outside_task(tmp_path, relative):
    _workspace, runtime = make_runtime(tmp_path)
    with pytest.raises(ValueError):
        runtime.call("write_file", relative=relative, content="no")


def test_workspace_rejects_symbolic_links_even_when_target_is_inside(tmp_path):
    workspace, runtime = make_runtime(tmp_path)
    target = workspace.root / "artifacts" / "target.txt"
    target.write_text("target", encoding="utf-8")
    (workspace.root / "link.txt").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic links"):
        runtime.call("read_file", relative="link.txt")
    assert "link.txt" not in runtime.call("list_files")


def test_tool_schema_rejects_missing_extra_and_wrong_arguments_with_trace(tmp_path):
    workspace, runtime = make_runtime(tmp_path)
    descriptions = runtime.describe()
    assert {
        "get_problem",
        "run_code",
        "submit_solution",
        "get_submission",
        "get_feedback",
        "wait_for_submission",
        "read_file",
        "write_file",
        "list_files",
    } == set(descriptions)
    with pytest.raises(ToolValidationError):
        runtime.call("get_problem")
    with pytest.raises(ToolValidationError):
        runtime.call("get_problem", problem_id="p1", unexpected=True)
    with pytest.raises(ToolValidationError):
        runtime.call("write_file", relative="x", content="x", overwrite="yes")
    records = [json.loads(line) for line in workspace.read_text("events.jsonl").splitlines()]
    assert len(records) == 6
    for call, result in zip(records[::2], records[1::2]):
        assert call["task_id"] == "task-tools"
        assert call["phase"] == "PLAN"
        assert call["payload"]["correlation_id"] == result["payload"]["correlation_id"]
        assert result["payload"]["ok"] is False


def test_trace_redacts_authorization_and_token_fields(tmp_path):
    workspace, _runtime = make_runtime(tmp_path)
    workspace.trace.append(
        "TOOL_RESULT",
        {
            "authorization": "Bearer super-secret",
            "api_token": "oj_super_secret",
            "message": "failed with Bearer another-secret",
        },
    )
    text = workspace.read_text("events.jsonl")
    assert "super-secret" not in text
    assert "another-secret" not in text
    assert "oj_super_secret" not in text
    record = json.loads(text.splitlines()[-1])
    assert record["event_id"].startswith("evt_")
    assert record["schema_version"] == "phase1-v1"
