"""Synthetic Workspace only; no model, OJ, or solution execution."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from dashboard.repository.trace import TraceReader
from dashboard.repository.workspace import UnsafePath, WorkspaceIndex, WorkspaceRepository
from dashboard.security import Sanitizer
from dashboard.services.metrics import aggregate, experiments
from dashboard.services.tasks import TaskService


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def event(kind, payload=None, phase="CODE", second=0):
    return {"timestamp": f"2026-10-01T12:00:{second:02d}+00:00", "type": kind,
            "phase": phase, "payload": payload or {}}


def make_task(root, name="ac", *, verdict="AC", status="accepted", versions=1):
    task = root / name
    write_json(task / "task.json", {"task_id": name, "problem_id": "t1001", "mode": "code-only"})
    write_json(task / "state.json", {
        "task_id": name, "problem_id": "t1001", "mode": "code-only", "experiment_variant": "standard",
        "current_model_profile": "standard", "current_phase": "DONE", "terminal_status": status,
        "termination_reason": "judge_accepted" if verdict == "AC" else status,
        "last_verdict": verdict, "solved": verdict == "AC", "llm_call_count": versions,
        "llm_usage_missing_count": 0, "input_tokens": 230 * versions, "output_tokens": 222 * versions,
        "submission_attempt_count": versions, "submission_count": versions,
        "debug_iterations": versions - 1, "wall_clock_seconds": 11.302,
        "estimated_cost": None, "cost_estimate_status": "missing_pricing", "cost_currency": None})
    write_json(task / "problem.json", {"title": "<script>alert(1)</script>", "statement": "unsafe <b>statement</b>"})
    (task / "solution.cpp").write_text('// <script>alert(1)</script>\nint main(){}', encoding="utf-8")
    events = [event("TASK_CREATED"), event("STATE_CHANGE", {"from": "PLAN", "to": "CODE"})]
    for version in range(1, versions + 1):
        call = f"llm_{version}"
        sid = str(version)
        if version > 1:
            events.append(event("STATE_CHANGE", {"from": "TEST", "to": "DEBUG"}, "DEBUG"))
        events += [event("LLM_CALL", {"correlation_id": call, "role": "CODE", "profile": "standard", "provider": "fixture", "model": "fixture-only"}),
            event("LLM_RESPONSE", {"correlation_id": call, "status": "succeeded", "usage": {"input_tokens": 230, "output_tokens": 222},
                  "cost_estimate": {"amount": None, "known": False, "reason": "missing_pricing"}, "latency_ms": 3931}),
            event("CODE_VERSION", {"solution_version": f"solution-v{version}", "code_sha256": f"hash-{version}", "model_call_id": call}),
            event("TOOL_CALL", {"correlation_id": f"tool_{version}", "tool": "submit_solution"}, second=2),
            event("TOOL_RESULT", {"correlation_id": f"tool_{version}", "tool": "submit_solution", "ok": True, "result": {"submission_id": sid, "status": "QUEUED"}}, second=3),
            event("SUBMISSION", {"submission_id": sid, "solution_version": f"solution-v{version}", "code_sha256": f"hash-{version}", "model_call_id": call}),
            event("JUDGE_RESULT", {"submission_id": sid, "solution_version": f"solution-v{version}", "code_sha256": f"hash-{version}", "verdict": verdict if version == versions else "WA"})]
    (task / "events.jsonl").write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
    return task


@pytest.fixture
def readers(tmp_path):
    root = tmp_path / "workspace"
    make_task(root)
    make_task(root, "wa", verdict="WA", status="user_program_failure")
    failed = make_task(root, "model-failure", verdict=None, status="model_failure")
    state = json.loads((failed / "state.json").read_text())
    state.update(submission_attempt_count=0, submission_count=0, llm_usage_missing_count=1)
    write_json(failed / "state.json", state)
    (failed / "events.jsonl").write_text(json.dumps(event("LLM_RESPONSE", {"correlation_id": "llm_failed", "status": "failed", "usage": None})) + "\n")
    uncertain = make_task(root, "uncertain", verdict=None, status="result_unknown")
    state = json.loads((uncertain / "state.json").read_text())
    state.update(submission_attempt_count=1, submission_count=0)
    write_json(uncertain / "state.json", state)
    (uncertain / "events.jsonl").write_text("")
    write_json(root / "old" / "state.json", {"mode": "legacy", "current_phase": "FUTURE_PHASE", "last_verdict": "FUTURE_VERDICT"})
    (root / "old" / "events.jsonl").write_text(json.dumps(event("FUTURE_EVENT", {"safe": 42})) + "\n")
    loop = make_task(root, "future-loop-fixture", versions=2)
    state = json.loads((loop / "state.json").read_text())
    state.update(mode="harness-loop", experiment_variant="mixed")
    write_json(loop / "state.json", state)
    write_json(root / "broken" / "task.json", {"task_id": "broken"})
    (root / "broken" / "state.json").write_text("{incomplete")
    sanitizer = Sanitizer(environ={"OJ_API_TOKEN": "test-secret-value"})
    repo = WorkspaceRepository(root, sanitizer)
    index, traces = WorkspaceIndex(repo, refresh_seconds=0), TraceReader(repo)
    return root, repo, index, traces, TaskService(repo, index, traces)


def test_discovery_and_summary_cache_never_read_trace(readers, monkeypatch):
    _, repo, index, traces, service = readers
    monkeypatch.setattr(traces, "read", lambda *_: pytest.fail("list must not parse Trace"))
    assert len(index.tasks()) == 7
    original = repo.read_json
    reads = []
    monkeypatch.setattr(repo, "read_json", lambda *args: (reads.append(args), original(*args))[1])
    assert len(index.tasks(force=True)) == 7
    assert reads == []
    assert [task.task_id for task in service.list(q="wa", verdict="WA")] == ["wa"]
    assert len(service.list(problem_id="t1001", mode="code-only", profile="standard")) == 4
    assert service.list(status="model_failure")[0].task_id == "model-failure"


def test_detail_actual_schema_and_associations(readers):
    _, _, _, _, service = readers
    detail = service.detail("ac")
    assert detail.summary.solved
    assert detail.model_calls[0].call_id == detail.code_versions[0].model_call_id
    assert detail.code_versions[0].submission_ids == ["1"]
    assert detail.submissions[0].code_sha256 == detail.code_versions[0].sha256
    assert detail.submissions[0].verdict == "AC"
    assert detail.model_calls[0].input_tokens == 230
    assert detail.model_calls[0].estimated_cost is None
    assert detail.tool_calls[0].duration_ms == 1000


def test_multi_version_multi_submission_fixture_is_display_only(readers):
    detail = readers[-1].detail("future-loop-fixture")
    assert len(detail.code_versions) == len(detail.submissions) == len(detail.model_calls) == 2
    assert [version.verdicts for version in detail.code_versions] == [["WA"], ["AC"]]
    assert [version.submission_ids for version in detail.code_versions] == [["1"], ["2"]]


def test_partial_unknown_failure_and_cost_semantics(readers):
    service = readers[-1]
    old = service.detail("old")
    assert old.summary.input_tokens is None and old.summary.submission_attempt_count is None
    assert old.summary.final_verdict == "FUTURE_VERDICT"
    assert old.timeline[0].event_type == "UNKNOWN EVENT" and old.timeline[0].metadata["safe"] == 42
    assert service.detail("broken").summary.data_status == "partial"
    failed = service.detail("model-failure")
    assert failed.summary.final_verdict is None and failed.summary.status == "model_failure"
    assert failed.summary.input_tokens is None and failed.model_calls[0].input_tokens is None
    uncertain = service.detail("uncertain").summary
    assert uncertain.submission_attempt_count == 1 and uncertain.submission_count == 0
    metrics = aggregate(service.list())
    assert metrics.known_costs == {} and metrics.unknown_cost_tasks == 7
    assert metrics.submission_attempts == metrics.submissions + 1
    groups = {group.name: group for group in experiments(service.list())}
    assert groups["harness-loop:strong"].metrics.total_tasks == 0
    assert groups["harness-loop:mixed"].metrics.total_tasks == 1


def test_currency_totals_never_mix_and_explicit_zero_is_known(readers):
    tasks = readers[-1].list()[:2]
    tasks[0].estimated_cost, tasks[0].cost_currency, tasks[0].cost_status = 0.0, "USD", "known"
    tasks[1].estimated_cost, tasks[1].cost_currency, tasks[1].cost_status = 2.0, "CNY", "known"
    metrics = aggregate(tasks)
    assert metrics.known_costs == {"USD": 0, "CNY": 2} and metrics.unknown_cost_tasks == 0


def test_json_write_race_keeps_last_valid_and_recovers(readers):
    root, _, index, _, _ = readers
    first = index.task("ac")
    (root / "ac" / "state.json").write_text("{")
    cached = index.task("ac")
    assert cached.llm_call_count == first.llm_call_count and cached.data_status == "partial"
    replacement = root / "ac" / "replacement.json"
    write_json(replacement, {"task_id": "ac", "current_phase": "DEBUG", "llm_call_count": 2})
    replacement.replace(root / "ac" / "state.json")
    assert index.task("ac").llm_call_count == 2 and not index.task("ac").terminal


def test_trace_append_partial_tail_invalid_line_and_replacement(readers):
    root, _, _, traces, _ = readers
    path = root / "ac" / "events.jsonl"
    first = traces.read("ac")
    with path.open("a") as handle:
        handle.write('{"type":"FUTURE_EVENT"')
    partial = traces.read("ac")
    assert len(partial.events) == len(first.events) and partial.warnings
    with path.open("a") as handle:
        handle.write(',"payload":{"n":1}}\ninvalid\n')
    final = traces.read("ac")
    assert len(final.events) == len(first.events) + 1
    assert final.events[-1].event_type == "UNKNOWN EVENT"
    replacement = root / "ac" / "new-events"
    replacement.write_text(json.dumps(event("TASK_CREATED")) + "\n")
    replacement.replace(path)
    reset = traces.read("ac")
    assert reset.generation > first.generation and len(reset.events) == 1


@pytest.mark.parametrize("task,name", [("..", "state.json"), ("ac", "../state.json"), ("ac", "/etc/passwd"), ("ac", "artifacts/../../secret"), ("ac", ".env")])
def test_artifact_path_rejected(readers, task, name):
    with pytest.raises((UnsafePath, OSError)):
        readers[1].artifact(task, name)


def test_symlink_escape_rejected_in_discovery_and_file_reads(readers, tmp_path):
    root, repo, _, _, _ = readers
    (tmp_path / "secret").write_text("outside-secret")
    (root / "ac" / "solution.cpp").unlink()
    (root / "ac" / "solution.cpp").symlink_to(tmp_path / "secret")
    with pytest.raises(UnsafePath):
        repo.artifact("ac", "solution.cpp")
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    assert "escape" not in repo.discover()
    (root / "ac" / "artifacts").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(UnsafePath):
        repo.artifact("ac", "artifacts/result.json")


def test_secret_filter_applies_to_nested_json_trace_envelope_and_plain_text(readers):
    root, repo, _, traces, service = readers
    data = {"Authorization": "Bearer abc", "BAILIAN_API_KEY": "key-value", "safe": "test-secret-value", "extra": {"client_secret": "nested"}}
    write_json(root / "ac" / "artifacts" / "result.json", data)
    text = repo.artifact("ac", "artifacts/result.json")
    assert all(secret not in text for secret in ["abc", "key-value", "test-secret-value", "nested"])
    (root / "ac" / "solution.cpp").write_text('// api_key="key-value"\n// Bearer another-secret\n// test-secret-value')
    (root / "ac" / "events.jsonl").write_text(json.dumps(event("FUTURE_EVENT", data, phase="test-secret-value")) + "\n")
    serialized = json.dumps(asdict(service.detail("ac")))
    assert all(secret not in serialized for secret in ["abc", "key-value", "test-secret-value", "another-secret"])


def test_current_code_and_submission_hash_mismatches_are_reported(readers):
    root, _, _, _, service = readers
    state = json.loads((root / "ac" / "state.json").read_text())
    state["solution_sha256"] = "0" * 64
    write_json(root / "ac" / "state.json", state)
    with (root / "ac" / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(event("SUBMISSION", {"submission_id": "other", "solution_version": "solution-v1", "code_sha256": "different"})) + "\n")
    detail = service.detail("ac")
    assert detail.solution_sha256 is not None
    assert any("solution.cpp differs" in warning for warning in detail.warnings)
    assert any("hash mismatch" in warning for warning in detail.warnings)
    assert detail.code_versions[0].submission_ids == ["1"]
