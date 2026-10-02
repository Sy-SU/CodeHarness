"""Task-local dedup with deterministic providers and no external execution."""
from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import httpx
import pytest

from agent.core.agent import extract_cpp
from agent.core.formal_dedup import FORMAL_DEDUP_POLICY, evaluation_identity, stable_hash
from agent.core.harness import HarnessLoop, HarnessPolicy
from agent.core.metrics import recovery_metrics, trace_events
from agent.oj_client.client import OJClient
from agent.oj_client.feedback import VERDICT_ONLY_POLICY
from agent.workspace.task import TaskWorkspace
from client_tests.test_phase4_harness import GOOD, OJ, Router, events, good_variant, make_agent, roles
from experiments.results import summarize_rows, task_row


class NoSamplesOJ(OJ):
    def get_problem(self, problem_id):
        return replace(super().get_problem(problem_id), samples=[],
                       extra_fields={"checker": "tokens", "revision": "fixture-v1"})


def setup(tmp_path, contents, *, oj=None, task_id="dedup", feedback_policy=VERDICT_ONLY_POLICY):
    workspace = TaskWorkspace.create(tmp_path, task_id, "sum", "harness-loop",
                                     trace_schema_version="phase4-v1")
    workspace.state.feedback_policy = feedback_policy
    workspace.state.actual_feedback_mode = "full"
    workspace.state.effective_feedback_mode = "verdict_only" if feedback_policy else "full"
    workspace.state.feedback_mode_status = "confirmed"
    return make_agent(tmp_path, Router(contents), oj or NoSamplesOJ(["WA"] * 10), workspace=workspace)


def stop_after_first_formal(monkeypatch):
    original = HarnessLoop._failure
    stopped = []

    def interrupt(loop, feedback):
        original(loop, feedback)  # Commit final result, feedback and next phase.
        if not stopped:
            stopped.append(True)
            raise KeyboardInterrupt()

    monkeypatch.setattr(HarnessLoop, "_failure", interrupt)


def resume_agent(tmp_path, workspace, oj, contents):
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    return make_agent(tmp_path, Router(contents), oj, workspace=loaded)[0:2]


def test_same_source_two_candidates_one_formal_post_and_same_observation(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD, GOOD])
    result = agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=3))
    assert result.termination_reason == "llm_call_limit"
    assert result.llm_calls == 3 and workspace.state.attempt_count == 2
    assert workspace.state.debug_iterations == 1 and workspace.state.duplicate_candidate_count == 1
    assert result.submissions == workspace.state.submission_attempt_count == workspace.state.submission_count == 1
    assert len(oj.submissions) == oj.wait_calls == oj.feedback_calls == 1
    assert oj.problem_calls == 1 and oj.run_calls == 0
    reused = events(workspace, "FORMAL_RESULT_REUSED")[0]["payload"]
    assert reused["candidate_duplicate"] and reused["formal_submission_reused"]
    assert reused["observation_source"] == "cached_formal_result"
    assert reused["source_submission_id"] == workspace.state.last_submission_id == "1"
    assert reused["source_solution_version"] == "solution-v1" and reused["solution_version"] == "solution-v2"
    assert reused["source_model_call_id"] != reused["model_call_id"]
    assert reused["source_sha256"] == hashlib.sha256(oj.submissions[0].encode("utf-8")).hexdigest()
    assert reused["observation"] == {"final": {"submission_id": "1", "status": "FINISHED", "verdict": "WA"},
                                      "feedback": {"verdict": "WA"}}
    assert workspace.read_json("checkpoint.json")["feedback"] == {"verdict": "WA"}
    assert len(events(workspace, "SUBMISSION")) == len(events(workspace, "JUDGE_RESULT")) == 1
    assert sum(e["payload"].get("tool") == "submit_solution" for e in events(workspace, "TOOL_CALL")) == 1


def test_a_b_a_reuses_original_submission_and_changed_source_gets_new_post(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD, good_variant(1), GOOD])
    agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=4))
    assert workspace.state.attempt_count == 3 and workspace.state.debug_iterations == 2
    assert len(oj.submissions) == workspace.state.submission_count == workspace.state.submission_attempt_count == 2
    assert oj.submissions[0] != oj.submissions[1] and oj.wait_calls == oj.feedback_calls == 2
    reuse = events(workspace, "FORMAL_RESULT_REUSED")[0]["payload"]
    assert reuse["source_submission_id"] == "1" and reuse["source_solution_version"] == "solution-v1"
    assert reuse["solution_version"] == "solution-v3" and workspace.state.duplicate_candidate_count == 1
    checkpoint = workspace.read_json("checkpoint.json")
    assert len(checkpoint["formal_evaluations"]) == 2
    assert not workspace.state.recovery_metrics["recovered_after_formal_failure"]


def test_same_bytes_are_evaluated_independently_in_different_tasks(tmp_path):
    oj = NoSamplesOJ(["WA", "WA"])
    identities = []
    for task_id in ("condition-standard", "condition-strong"):
        agent, workspace, _ = setup(tmp_path, ["plan", GOOD], oj=oj, task_id=task_id)
        agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=2))
        assert workspace.state.submission_count == 1 and workspace.state.duplicate_candidate_count == 0
        identities.append(next(iter(workspace.read_json("checkpoint.json")["formal_evaluations"].values()))["identity"])
    assert oj.submissions[0] == oj.submissions[1] and len(oj.submissions) == 2
    assert identities[0]["source_sha256"] == identities[1]["source_sha256"]
    assert identities[0]["task_id"] != identities[1]["task_id"]
    assert stable_hash(identities[0]) != stable_hash(identities[1])


def test_custom_run_does_not_fill_formal_cache_or_suppress_first_post(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD, GOOD], oj=OJ(["WA"]))
    agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=3))
    assert oj.run_calls == workspace.state.custom_run_count == 2
    assert len(oj.submissions) == workspace.state.submission_count == 1
    assert oj.wait_calls == oj.feedback_calls == 1
    trace, _ = trace_events(workspace.read_text("events.jsonl"))
    custom_index = next(i for i, e in enumerate(trace) if e["type"] == "SAMPLE_RESULT")
    formal_index = next(i for i, e in enumerate(trace) if e["type"] == "SUBMISSION")
    assert custom_index < formal_index
    cached = next(iter(workspace.read_json("checkpoint.json")["formal_evaluations"].values()))
    assert cached["submission_id"] == "1" and cached["final"]["verdict"] == "WA"


def test_resume_reloads_formal_cache_and_duplicate_uses_zero_formal_requests(tmp_path, monkeypatch):
    stop_after_first_formal(monkeypatch)
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD])
    policy = HarnessPolicy(max_llm_calls=3)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    assert workspace.read_json("checkpoint.json")["phase"] == "DEBUG"
    before = (len(oj.submissions), oj.wait_calls, oj.feedback_calls, oj.problem_calls)
    agent, loaded = resume_agent(tmp_path, workspace, oj, [GOOD])
    result = agent.run_harness_loop(resume=True, harness_policy=policy)
    assert result.termination_reason == "llm_call_limit" and result.llm_calls == 3
    assert before == (len(oj.submissions), oj.wait_calls, oj.feedback_calls, oj.problem_calls)
    assert loaded.state.attempt_count == 2 and loaded.state.duplicate_candidate_count == 1
    assert loaded.state.resume_count == 1 and loaded.state.last_submission_id == "1"
    assert events(loaded, "FORMAL_RESULT_REUSED")[0]["payload"]["observation"]["feedback"] == {"verdict": "WA"}
    assert loaded.read_json("checkpoint.json")["state"]["duplicate_candidate_count"] == 1


def test_richer_disk_cache_is_projected_again_before_observation_or_next_prompt(tmp_path, monkeypatch):
    stop_after_first_formal(monkeypatch)
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD])
    policy = HarnessPolicy(max_llm_calls=4)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    checkpoint = workspace.read_json("checkpoint.json")
    cached = next(iter(checkpoint["formal_evaluations"].values()))
    hidden = "HIDDEN_TEST_FROM_RICH_DISK_CACHE"
    cached["final"]["arbitrary"] = {"input": hidden}
    cached["feedback"] = {"verdict": "WA", "summary": hidden, "failure": {"expected": hidden}}
    workspace.write_json("checkpoint.json", checkpoint)
    agent, loaded = resume_agent(tmp_path, workspace, oj, [GOOD, good_variant(1)])
    agent.run_harness_loop(resume=True, harness_policy=policy)
    assert len(oj.submissions) == 2 and oj.feedback_calls == oj.wait_calls == 2
    assert all(hidden not in m.content for _, messages in agent.router.calls for m in messages)
    for path in loaded.root.rglob("*"):
        if path.is_file():
            assert hidden.encode() not in path.read_bytes(), str(path)
    receipt = loaded.read_json("artifacts/formal-reuses/solution-v2.json")
    assert receipt["observation"]["feedback"] == {"verdict": "WA"}
    assert receipt["observation"]["final"] == {"submission_id": "1", "status": "FINISHED", "verdict": "WA"}


def test_trace_metrics_and_report_count_candidates_without_inventing_formal_actions(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD, GOOD])
    agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=3))
    row = task_row(workspace, {"strategy": "standard-harness", "mode": "harness-loop", "profile": "standard"})
    assert row["audit"]["status"] == "consistent"
    assert row["metrics_status"] == "derived" and not row["metrics_warnings"]
    assert row["llm_calls"] == 3 and row["candidate_version_count"] == 2 and row["debug_iterations"] == 1
    assert row["duplicate_candidate_count"] == row["formal_result_reuse_count"] == 1
    assert row["formal_submissions"] == row["oj_submissions"] == row["submission_attempts"] == len(oj.submissions) == 1
    summary = summarize_rows([row])[0]
    assert summary["candidate_version_count"] == 2 and summary["duplicate_candidate_count"] == 1
    assert summary["formal_submission_count"] == summary["oj_submissions"] == 1


@pytest.mark.parametrize("change", ["task", "problem", "revision", "language", "space", "comment", "unicode"])
def test_identity_uses_exact_utf8_bytes_and_all_evaluation_fields(change):
    problem = {"problem_id": "sum", "revision": "1"}
    code = extract_cpp(GOOD)
    first = evaluation_identity("task", "sum", problem, code)
    other_problem, other_code = copy.deepcopy(problem), code
    task_id, problem_id, language = "task", "sum", "cpp20"
    if change == "task": task_id = "another-task"
    if change == "problem": problem_id = other_problem["problem_id"] = "other"
    if change == "revision": other_problem["revision"] = "2"
    if change == "language": language = "cpp17"
    if change == "space": other_code = code.replace("int main", "int  main")
    if change == "comment": other_code += "// comment\n"
    if change == "unicode": other_code += "// 字节\n"
    other = evaluation_identity(task_id, problem_id, other_problem, other_code, language=language)
    assert other["source_sha256"] == hashlib.sha256(other_code.encode("utf-8")).hexdigest()
    assert stable_hash(first) != stable_hash(other)
    assert first == evaluation_identity("task", "sum", {"revision": "1", "problem_id": "sum"}, code)


def test_no_repetition_early_stop_and_original_three_debug_failures_replan(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD, GOOD, GOOD, GOOD, "new plan", GOOD],
                                 oj=NoSamplesOJ(["WA"]))
    router = agent.router
    result = agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=7))
    assert result.termination_reason == "llm_call_limit" and result.llm_calls == 7
    assert roles(router) == ["Phase: PLAN", "Phase: CODE", "Phase: DEBUG", "Phase: DEBUG",
                             "Phase: DEBUG", "Phase: PLAN", "Phase: CODE"]
    assert workspace.state.replan_count == 1 and workspace.state.debug_iterations == 3
    assert workspace.state.attempt_count == 5 and workspace.state.duplicate_candidate_count == 4
    assert len(oj.submissions) == 1 and not events(workspace, "MODEL_ESCALATION")


def test_submit_reentry_with_known_pending_id_waits_without_second_post(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD], oj=NoSamplesOJ(["WA"], interrupt="wait"))
    policy = HarnessPolicy(max_llm_calls=2)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    # Model a stale/reentered submit stage; checkpoint already owns the known ID.
    checkpoint = workspace.read_json("checkpoint.json")
    checkpoint["test_stage"] = "submit"
    workspace.write_json("checkpoint.json", checkpoint)
    agent, loaded = resume_agent(tmp_path, workspace, oj, [])
    agent.run_harness_loop(resume=True, harness_policy=policy)
    assert len(oj.submissions) == loaded.state.submission_count == loaded.state.submission_attempt_count == 1
    assert loaded.state.last_submission_id == "1" and loaded.state.last_verdict == "WA"
    assert loaded.state.duplicate_candidate_count == 0


def test_unknown_post_intent_survives_reload_and_never_reposts(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD], oj=NoSamplesOJ(["WA"], interrupt="submit"))
    policy = HarnessPolicy(max_llm_calls=2)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    checkpoint = workspace.read_json("checkpoint.json")
    assert checkpoint["pending_operation"]["kind"] == "submission"
    cached = next(iter(checkpoint["formal_evaluations"].values()))
    assert cached["submission_id"] is None and cached["final"] is None
    agent, loaded = resume_agent(tmp_path, workspace, oj, [])
    result = agent.run_harness_loop(resume=True, harness_policy=policy)
    assert result.termination_reason == "interrupted_submission_not_reissued" and result.terminal_status == "result_unknown"
    assert len(oj.submissions) == loaded.state.submission_attempt_count == 1
    assert loaded.state.submission_count == 0 and oj.wait_calls == 0


def test_persisted_claim_alone_blocks_post_even_without_pending_marker(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD], oj=NoSamplesOJ(["WA"], interrupt="submit"))
    policy = HarnessPolicy(max_llm_calls=2)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    checkpoint = workspace.read_json("checkpoint.json")
    checkpoint["pending_operation"] = None
    workspace.write_json("checkpoint.json", checkpoint)
    agent, loaded = resume_agent(tmp_path, workspace, oj, [])
    result = agent.run_harness_loop(resume=True, harness_policy=policy)
    assert result.termination_reason == "previous_formal_submission_unknown_not_reissued"
    assert len(oj.submissions) == loaded.state.submission_attempt_count == 1


def test_two_task_executors_cannot_both_observe_cache_miss_and_post(tmp_path):
    entered, release = Event(), Event()

    class BlockingOJ(NoSamplesOJ):
        def submit_solution(self, problem_id, code):
            entered.set()
            assert release.wait(10), "test did not release the first POST"
            return super().submit_solution(problem_id, code)

    agent, workspace, oj = setup(tmp_path, ["plan", GOOD], oj=BlockingOJ(["WA"]))
    policy = HarnessPolicy(max_llm_calls=2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(agent.run_harness_loop, harness_policy=policy)
        try:
            assert entered.wait(10), "first executor did not reach POST"
            other, _ = resume_agent(tmp_path, workspace, oj, [])
            with pytest.raises(ValueError, match="already running"):
                other.run_harness_loop(resume=True, harness_policy=policy)
        finally:
            release.set()
        assert first.result(timeout=10).termination_reason == "llm_call_limit"
    assert len(oj.submissions) == workspace.state.submission_attempt_count == 1


@pytest.mark.parametrize("crash_after_append", [False, True])
def test_crash_around_reuse_trace_does_not_double_counter_event_or_formal_post(tmp_path, monkeypatch, crash_after_append):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD, GOOD])
    original = workspace.trace.append
    interrupted = []

    def append(kind, payload, **kwargs):
        if kind == "FORMAL_RESULT_REUSED" and not interrupted:
            interrupted.append(True)
            if crash_after_append:
                original(kind, payload, **kwargs)
            raise KeyboardInterrupt()
        return original(kind, payload, **kwargs)

    monkeypatch.setattr(workspace.trace, "append", append)
    policy = HarnessPolicy(max_llm_calls=3)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    assert workspace.read_json("checkpoint.json")["state"]["duplicate_candidate_count"] == 1
    agent, loaded = resume_agent(tmp_path, workspace, oj, [])
    agent.run_harness_loop(resume=True, harness_policy=policy)
    assert loaded.state.duplicate_candidate_count == 1 and loaded.state.attempt_count == 2
    assert len(events(loaded, "FORMAL_RESULT_REUSED")) == 1
    assert len(oj.submissions) == oj.wait_calls == oj.feedback_calls == 1
    assert loaded.state.recovery_metrics["metrics_status"] == "derived"


@pytest.mark.parametrize("corrupt", ["source", "policy", "id", "verdict", "revision"])
def test_corrupt_cache_identity_or_observation_fails_closed_without_new_post(tmp_path, monkeypatch, corrupt):
    stop_after_first_formal(monkeypatch)
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD])
    policy = HarnessPolicy(max_llm_calls=3)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    checkpoint = workspace.read_json("checkpoint.json")
    cached = next(iter(checkpoint["formal_evaluations"].values()))
    if corrupt == "source": cached["submission_links"]["code_sha256"] = "0" * 64
    if corrupt == "policy": cached["feedback_policy"] = None
    if corrupt == "id": cached["final"]["submission_id"] = "unrelated"
    if corrupt == "verdict": cached["feedback"]["verdict"] = "CE"
    if corrupt == "revision": checkpoint["problem"]["revision"] = "changed"
    workspace.write_json("checkpoint.json", checkpoint)
    agent, loaded = resume_agent(tmp_path, workspace, oj, [GOOD])
    result = agent.run_harness_loop(resume=True, harness_policy=policy)
    assert result.terminal_status == "internal_failure"
    assert len(oj.submissions) == oj.wait_calls == oj.feedback_calls == 1
    assert not events(loaded, "FORMAL_RESULT_REUSED")


def test_explicit_full_policy_reuses_original_structured_feedback_without_extra_get(tmp_path):
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD, GOOD], feedback_policy=None)
    agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=3))
    observation = events(workspace, "FORMAL_RESULT_REUSED")[0]["payload"]["observation"]
    assert observation["feedback"] == {"verdict": "WA", "summary": "judge summary", "failure": {"test_index": 1}}
    assert oj.feedback_calls == 1


def test_formal_post_uses_the_exact_source_bytes_in_cache_identity(tmp_path):
    posts = []
    reference = NoSamplesOJ()

    def respond(request):
        path = request.url.path
        if path == "/api/v1/agent/problems/sum":
            body = reference.get_problem("sum").as_dict()
        elif request.method == "POST" and path == "/api/v1/submissions":
            posts.append(json.loads(request.content))
            return httpx.Response(202, json={"submission_id": "wire-id", "status": "QUEUED"})
        elif path.endswith("/feedback"):
            body = {"verdict": "WA", "summary": "withheld"}
        elif path == "/api/v1/submissions/wire-id":
            body = {"submission_id": "wire-id", "status": "FINISHED", "verdict": "WA"}
        else:
            pytest.fail(f"Unexpected fake HTTP request: {request.method} {path}")
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = OJClient("https://oj.test", "test-token", client=http)
        agent, workspace, _ = setup(tmp_path, ["plan", GOOD, GOOD], oj=client)
        agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=3))
    assert len(posts) == 1
    identity = next(iter(workspace.read_json("checkpoint.json")["formal_evaluations"].values()))["identity"]
    assert identity["source_sha256"] == hashlib.sha256(posts[0]["source_code"].encode("utf-8")).hexdigest()
    assert identity["language"] == posts[0]["language"] == "cpp20"


def test_dedup_semantics_are_frozen_in_task_and_experiment_fingerprint(tmp_path, monkeypatch):
    from client_tests.test_experiment_preflight import fake_preflight
    from experiments.freeze import frozen_fingerprint
    monkeypatch.setattr("experiments.freeze.git_snapshot", lambda root: {
        "git_commit_sha": "a" * 40, "git_dirty": False, "git_diff_hash": "b" * 64})
    cfg, executor, report = fake_preflight(tmp_path)
    assert report["fingerprint"]["components"]["formal_submission_dedup_policy"] == FORMAL_DEDUP_POLICY
    agent, workspace, _ = setup(tmp_path, ["plan", GOOD])
    agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=2))
    assert workspace.read_json("checkpoint.json")["config"]["formal_submission_dedup"] == FORMAL_DEDUP_POLICY
    before = report["fingerprint"]["sha256"]
    monkeypatch.setitem(FORMAL_DEDUP_POLICY, "version", "future-different-policy")
    changed = frozen_fingerprint(cfg, report["models"], report["condition_routes"],
        executor.settings.oj_base_url, git_root=tmp_path)
    assert changed["sha256"] != before


@pytest.mark.parametrize("verdict", ["WA", "AC"])
def test_reentry_after_confirmed_final_does_not_repoll_or_create_a_submission(tmp_path, monkeypatch, verdict):
    original = HarnessLoop._wait
    interrupted = []

    def interrupt(loop):
        original(loop)
        if not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt()

    monkeypatch.setattr(HarnessLoop, "_wait", interrupt)
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD], oj=NoSamplesOJ([verdict]))
    policy = HarnessPolicy(max_llm_calls=2)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    checkpoint = workspace.read_json("checkpoint.json")
    checkpoint["phase"] = "TEST"
    checkpoint["test_stage"] = "submit"
    workspace.write_json("checkpoint.json", checkpoint)
    agent, loaded = resume_agent(tmp_path, workspace, oj, [])
    result = agent.run_harness_loop(resume=True, harness_policy=policy)
    assert len(oj.submissions) == oj.wait_calls == loaded.state.submission_count == 1
    assert oj.feedback_calls == 0 and loaded.state.duplicate_candidate_count == 0
    receipt = events(loaded, "FORMAL_RESULT_REUSED")[0]["payload"]
    assert receipt["candidate_duplicate"] is False and receipt["solution_version"] == "solution-v1"
    assert receipt["source_submission_id"] == "1" and loaded.state.recovery_metrics["metrics_status"] == "derived"
    assert result.solved is (verdict == "AC")
    assert not loaded.state.recovery_metrics["formal_recovery_to_ac"]
    if verdict == "AC":
        assert events(loaded, "REVIEW_RESULT")[0]["payload"]["formal_submission_reused"]
    else:
        assert loaded.read_json("checkpoint.json")["feedback"] == {"verdict": "WA"}


def test_resume_cannot_change_frozen_dedup_version(tmp_path, monkeypatch):
    stop_after_first_formal(monkeypatch)
    agent, workspace, oj = setup(tmp_path, ["plan", GOOD])
    policy = HarnessPolicy(max_llm_calls=3)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop(harness_policy=policy)
    checkpoint = workspace.read_json("checkpoint.json")
    checkpoint["config"]["formal_submission_dedup"]["version"] = "another-policy"
    workspace.write_json("checkpoint.json", checkpoint)
    agent, _ = resume_agent(tmp_path, workspace, oj, [GOOD])
    before = {path: path.read_bytes() for path in workspace.root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="Resume configuration differs"):
        agent.run_harness_loop(resume=True, harness_policy=policy)
    assert len(oj.submissions) == 1 and not agent.router.calls
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize("field", ["source_sha256", "source_submission_id", "source_model_call_id", "model_call_id"])
def test_bad_reuse_links_are_not_credited_as_verified_metrics(tmp_path, field):
    agent, workspace, _ = setup(tmp_path, ["plan", GOOD, GOOD])
    agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=3))
    trace, invalid = trace_events(workspace.read_text("events.jsonl"))
    assert not invalid
    for event in trace:
        if event["type"] == "FORMAL_RESULT_REUSED":
            event["payload"][field] = "unrelated"
    metrics = recovery_metrics(trace, workspace.read_json("state.json"))
    assert metrics["metrics_status"] == "incomplete_trace"
    assert "cached_formal_candidate_association_invalid" in metrics["metrics_warnings"]
    assert metrics["formal_recovery_to_ac"] is None
