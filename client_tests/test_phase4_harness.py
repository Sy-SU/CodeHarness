from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
import httpx

from agent.cli import build_parser, main
from agent.core.agent import CodingAgent
from agent.core.context import ContextBuilder
from agent.core.harness import HarnessLoop, HarnessPolicy
from agent.core.policy import DebugEscalationPolicy, ModelPolicy
from agent.models.registry import ModelDefinition
from agent.models.registry import ModelRegistry
from agent.models.provider import OpenAICompatibleProvider
from agent.models.router import ModelRouter
from agent.models.types import (
    AgentRole, CostEstimate, LLMError, LLMErrorKind, LLMResponse, ModelProfile, TokenUsage,
)
from agent.oj_client.client import OJClient, OJHTTPError, OJProtocolError, OJResultUnknownError
from agent.oj_client.types import (
    AgentProblem, ClientErrorKind, CustomRunResult, JudgeFeedback,
    ProblemLimits, ProblemSample, SubmissionRecord,
)
from agent.tools.runtime import build_default_tools
from agent.workspace.task import TaskWorkspace


GOOD = "```cpp\n#include <iostream>\nint main(){std::cout << 3;}\n```"
BAD = "```cpp\n#include <iostream>\nint main(){std::cout << 0;}\n```"


class Router:
    def __init__(self, contents, *, priced=True, input_price=1, usage=TokenUsage(100, 50)):
        self.contents = iter(contents)
        self.calls = []
        self.priced = priced
        self.input_price = input_price
        self.usage = usage

    def route(self, profile):
        return ModelDefinition(
            provider="fake", model="fake-model", input_token_limit=32000,
            parameters={"max_tokens": 4096}, currency="CNY" if self.priced else None,
            input_cost_per_million=self.input_price if self.priced else None,
            output_cost_per_million=1 if self.priced else None,
        )

    def complete(self, profile, messages):
        self.calls.append((profile, messages))
        value = next(self.contents)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, LLMResponse):
            return value
        return LLMResponse(value, self.usage, "fake", "fake-model", profile)

    def estimate_cost(self, response):
        if response.usage is None:
            return CostEstimate(None, None, False, "missing_usage")
        return CostEstimate(0.00015, "CNY", True)


class OJ:
    def __init__(self, verdicts=("AC",), *, sample_results=None, interrupt=None):
        self.verdicts = list(verdicts)
        self.sample_results = iter(sample_results) if sample_results else None
        self.submissions = []
        self.run_calls = 0
        self.feedback_calls = 0
        self.problem_calls = 0
        self.wait_calls = 0
        self.interrupt = interrupt

    def get_problem(self, problem_id):
        self.problem_calls += 1
        return AgentProblem("sum", "Sum", "Add two integers.", "Two integers.",
                            "Their sum.", "Notes matter.", ProblemLimits(1000, 128),
                            [ProblemSample("1 2\n", "3\n")], extra_fields={"checker": "tokens"})

    def run_code(self, code, stdin):
        self.run_calls += 1
        if self.interrupt == "sample":
            self.interrupt = None
            raise KeyboardInterrupt()
        if self.sample_results:
            return next(self.sample_results)
        return CustomRunResult("OK", stdout="0\n" if "<< 0" in code else "3\n", exit_code=0)

    def submit_solution(self, problem_id, code):
        self.submissions.append(code)
        if self.interrupt == "submit":
            self.interrupt = None
            raise KeyboardInterrupt()
        return SubmissionRecord(str(len(self.submissions)), "QUEUED")

    def wait_for_submission(self, submission_id, *, timeout_seconds, poll_interval_seconds):
        self.wait_calls += 1
        if self.interrupt == "wait":
            self.interrupt = None
            raise KeyboardInterrupt()
        return SubmissionRecord(submission_id, "FINISHED", self.verdicts[int(submission_id) - 1])

    def get_submission(self, submission_id):
        return SubmissionRecord(submission_id, "FINISHED", self.verdicts[int(submission_id) - 1])

    def get_feedback(self, submission_id):
        self.feedback_calls += 1
        verdict = self.verdicts[int(submission_id) - 1]
        return JudgeFeedback(verdict, "judge summary", {"failure": {"test_index": 1}})


def make_agent(tmp_path, router, oj=None, *, workspace=None, policy=None):
    workspace = workspace or TaskWorkspace.create(
        tmp_path, "harness-test", "sum", "harness-loop", trace_schema_version="phase4-v1"
    )
    oj = oj or OJ()
    return CodingAgent(router, policy or ModelPolicy(), ContextBuilder(),
                       build_default_tools(oj, workspace), workspace,
                       poll_seconds=0, judge_timeout_seconds=1), workspace, oj


def events(workspace, event_type):
    return [json.loads(line) for line in workspace.read_text("events.jsonl").splitlines()
            if json.loads(line)["type"] == event_type]


def roles(router):
    return [next(m.content.split("\n", 1)[0] for m in messages if m.role == "user")
            for _, messages in router.calls]


def test_samples_fail_then_debug_passes_before_single_formal_submission(tmp_path):
    router = Router(["Add the integers.", BAD, GOOD])
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop()
    assert result.solved and result.submissions == 1
    assert roles(router) == ["Phase: PLAN", "Phase: CODE", "Phase: DEBUG"]
    assert oj.run_calls == 2 and len(oj.submissions) == 1
    assert oj.feedback_calls == 0
    assert workspace.state.debug_iterations == 1
    assert workspace.state.attempt_count == 2
    assert "public_sample" in router.calls[-1][1][-1].content
    assert workspace.read_text("artifacts/solutions/solution-v1.cpp") != workspace.read_text("solution.cpp")
    assert "Hash matches judged code: True" in workspace.read_text("artifacts/review.md")
    assert workspace.read_json("checkpoint.json")["phase"] == "DONE"
    assert not events(workspace, "MODEL_ESCALATION")
    trace = events(workspace, "SAMPLE_RESULT")
    assert [e["payload"]["passed"] for e in trace] == [False, True]


def test_every_public_sample_must_pass_before_formal_submission(tmp_path):
    fixed = "```cpp\n#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}\n```"

    class MultiSampleOJ(OJ):
        def get_problem(self, problem_id):
            return replace(super().get_problem(problem_id), samples=[
                ProblemSample("1 2\n", "3\n"), ProblemSample("4 5\n", "9\n")])

        def run_code(self, code, stdin):
            self.run_calls += 1
            stdout = "9" if stdin == "4 5\n" and "std::cin" in code else "3"
            return CustomRunResult("OK", stdout=stdout, exit_code=0)

    router = Router(["plan", GOOD, fixed])
    agent, workspace, oj = make_agent(tmp_path, router, MultiSampleOJ())
    assert agent.run_harness_loop().solved
    assert oj.run_calls == 4 and len(oj.submissions) == 1
    assert "std::cin" in oj.submissions[0]
    assert [e["payload"]["passed"] for e in events(workspace, "SAMPLE_RESULT")] == [
        True, False, True, True]


@pytest.mark.parametrize("result,terminal", [
    (CustomRunResult("OK", stdout="3", exit_code=1), "accepted"),
    (CustomRunResult("OK", stdout="3", exit_code=0, stdout_truncated=True), "sample_check_unverifiable"),
    (CustomRunResult("OK", stdout=None, exit_code=0), "client_failure"),
])
def test_ok_run_requires_complete_stdout_and_successful_exit(tmp_path, result, terminal):
    router = Router(["plan", GOOD, GOOD])
    oj = OJ(sample_results=[result, CustomRunResult("OK", stdout="3", exit_code=0)])
    agent, workspace, _ = make_agent(tmp_path, router, oj)
    assert agent.run_harness_loop().terminal_status == terminal
    if terminal == "accepted":
        assert oj.run_calls == 2 and len(oj.submissions) == 1
        assert workspace.state.debug_iterations == 1
    else:
        assert not oj.submissions and len(router.calls) == 2


def test_three_failed_debug_candidates_replan_then_code_without_escalation(tmp_path):
    router = Router(["initial plan", GOOD, GOOD, GOOD, GOOD, "new plan", GOOD])
    oj = OJ(["WA", "WA", "WA", "WA", "AC"])
    policy = ModelPolicy(debug_escalation=DebugEscalationPolicy(
        enabled=True, threshold=1, failure_kinds=frozenset({"WA"})))
    agent, workspace, _ = make_agent(tmp_path, router, oj, policy=policy)
    result = agent.run_harness_loop()
    assert result.solved and result.submissions == 5
    assert roles(router) == ["Phase: PLAN", "Phase: CODE", "Phase: DEBUG", "Phase: DEBUG",
                             "Phase: DEBUG", "Phase: PLAN", "Phase: CODE"]
    assert [profile for profile, _ in router.calls] == [
        ModelProfile.STRONG, ModelProfile.STANDARD, ModelProfile.STANDARD,
        ModelProfile.STANDARD, ModelProfile.STANDARD, ModelProfile.STRONG, ModelProfile.STANDARD]
    assert workspace.state.replan_count == 1
    assert workspace.state.debug_iterations == 3
    assert oj.feedback_calls == 4
    assert events(workspace, "REPLAN")[0]["payload"]["reason"] == "three_debug_candidates_failed"
    assert "judge summary" in router.calls[-2][1][-1].content
    assert not events(workspace, "MODEL_ESCALATION")
    assert len(list((workspace.root / "artifacts/solutions").glob("*.cpp"))) == 5
    for event in events(workspace, "JUDGE_RESULT"):
        payload = event["payload"]
        source = workspace.read_text(f"artifacts/solutions/{payload['solution_version']}.cpp")
        assert hashlib.sha256(source.encode()).hexdigest() == payload["code_sha256"]


def test_ten_submission_attempts_are_a_hard_cap_across_replans(tmp_path):
    router = Router([GOOD] * 30)
    agent, workspace, oj = make_agent(tmp_path, router, OJ(["WA"] * 20))
    result = agent.run_harness_loop()
    assert result.terminal_status == "budget_exhausted"
    assert result.termination_reason == "submission_limit"
    assert len(oj.submissions) == 10
    assert workspace.state.submission_attempt_count == workspace.state.submission_count == 10
    assert workspace.state.budget_committed_cny <= 1
    assert workspace.state.replan_count >= 2


def test_cost_is_reserved_before_call_and_never_exceeds_one_yuan(tmp_path):
    router = Router([GOOD] * 30, input_price=10)
    agent, workspace, oj = make_agent(tmp_path, router, OJ(["WA"] * 20))
    result = agent.run_harness_loop()
    assert result.terminal_status == "budget_exhausted"
    assert result.termination_reason == "cost_reservation_limit"
    assert len(router.calls) == 3
    assert workspace.state.budget_committed_cny <= 1
    assert len(oj.submissions) == 2


@pytest.mark.parametrize("cost", [0, -1, True, None, "2", float("nan"), float("inf"), 10 ** 400])
def test_task_cost_must_be_a_positive_finite_number(cost):
    with pytest.raises(ValueError, match="max_cost_cny"):
        HarnessPolicy(max_cost_cny=cost)


def test_custom_cost_can_exceed_default_and_is_enforced(tmp_path):
    assert HarnessPolicy().max_cost_cny == 1
    router = Router([GOOD] * 30, input_price=10)
    agent, workspace, oj = make_agent(tmp_path, router, OJ(["WA"] * 20))
    result = agent.run_harness_loop(harness_policy=HarnessPolicy(max_cost_cny=2))
    assert result.termination_reason == "cost_reservation_limit"
    assert len(router.calls) == 6 and workspace.state.budget_committed_cny <= 2
    assert workspace.read_json("checkpoint.json")["config"]["policy"]["max_cost_cny"] == 2
    assert len(oj.submissions) <= 10


@pytest.mark.parametrize("mode", ["price", "output_limit", "currency", "input_limit"])
def test_unknown_pricing_or_missing_bounds_stops_before_model(tmp_path, mode):
    router = Router([GOOD], priced=mode != "price")
    original = router.route
    changes = {"output_limit": {"parameters": {}}, "currency": {"currency": "USD"},
               "input_limit": {"input_token_limit": None}}
    if mode in changes:
        router.route = lambda p: replace(original(p), **changes[mode])
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop()
    assert result.terminal_status == "budget_unverifiable"
    assert len(router.calls) == 0 and len(oj.submissions) == 0
    assert workspace.state.budget_committed_cny == 0


def test_usage_missing_keeps_reservation_and_stops_safely(tmp_path):
    router = Router(["a plan"], usage=None)
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop()
    assert result.termination_reason == "model_usage_missing"
    assert result.llm_calls == 1 and result.submissions == 0
    assert workspace.state.budget_committed_cny > 0
    assert result.estimated_cost is None


def test_call_cap_stops_never_passing_samples_with_no_formal_submission(tmp_path):
    router = Router([BAD] * 30)
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=5))
    assert result.termination_reason == "llm_call_limit"
    assert result.llm_calls == 5 and len(oj.submissions) == 0
    assert workspace.state.replan_count == 1


def test_time_cap_prevents_another_action(tmp_path):
    router = Router([GOOD])
    agent, workspace, oj = make_agent(tmp_path, router)
    workspace.state.wall_clock_seconds = 2
    result = agent.run_harness_loop(harness_policy=HarnessPolicy(max_wall_clock_seconds=1))
    assert result.termination_reason == "wall_clock_limit"
    assert not router.calls and oj.problem_calls == 0


@pytest.mark.parametrize("status", ["IE", "FUTURE"])
def test_sample_infrastructure_or_protocol_failure_never_enters_debug(tmp_path, status):
    router = Router(["a plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(
        sample_results=[CustomRunResult(status, stdout="3\n")]))
    result = agent.run_harness_loop()
    assert result.terminal_status in {"remote_infrastructure_failure", "client_failure"}
    assert workspace.state.debug_iterations == 0 and not oj.submissions


def test_formal_ie_is_infrastructure_failure_without_feedback(tmp_path):
    router = Router(["a plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(["IE"]))
    result = agent.run_harness_loop()
    assert result.termination_reason == "judge_ie"
    assert workspace.state.debug_iterations == 0 and oj.feedback_calls == 0


def test_resume_during_wait_queries_existing_id_without_new_model_or_submit(tmp_path):
    router = Router(["a plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(interrupt="wait"))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    snapshot = workspace.read_json("checkpoint.json")
    assert snapshot["test_stage"] == "wait"
    assert snapshot["state"]["last_submission_id"] == "1"
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.solved and result.llm_calls == 2 and result.submissions == 1
    assert len(router.calls) == 2 and len(oj.submissions) == 1
    assert oj.problem_calls == 1 and loaded.state.resume_count == 1
    assert loaded.state.wall_clock_seconds >= snapshot["state"]["wall_clock_seconds"]


def test_resume_after_model_response_commit_reuses_cached_content(tmp_path, monkeypatch):
    router = Router(["cached plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router)
    original = workspace.write_json
    interrupted = False

    def interrupt_after_commit(relative, value):
        nonlocal interrupted
        original(relative, value)
        if relative == "checkpoint.json" and value.get("completed_model") and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    monkeypatch.setattr(workspace, "write_json", interrupt_after_commit)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.solved and len(router.calls) == 2
    assert loaded.read_text("artifacts/plan.md") == "cached plan"


@pytest.mark.parametrize("operation", ["model", "submit"])
def test_resume_never_reissues_uncertain_paid_call_or_post(tmp_path, operation):
    router = Router([KeyboardInterrupt()] if operation == "model" else ["plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(interrupt="submit" if operation == "submit" else None))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    calls, posts = len(router.calls), len(oj.submissions)
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.terminal_status == "result_unknown"
    assert len(router.calls) == calls and len(oj.submissions) == posts
    assert loaded.state.llm_call_count == calls
    assert loaded.state.submission_attempt_count == posts
    assert loaded.state.budget_committed_cny > 0


def test_completed_resume_is_idempotent_and_config_drift_is_rejected(tmp_path):
    router = Router(["plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router)
    assert agent.run_harness_loop().solved
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    assert resumed.run_harness_loop(resume=True).solved
    assert len(router.calls) == 2 and len(oj.submissions) == 1
    router.input_price = 2
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    with pytest.raises(ValueError, match="configuration differs"):
        resumed.run_harness_loop(resume=True)


def test_workspace_lock_and_managed_checkpoint_prevent_concurrent_or_tool_mutation(tmp_path):
    agent, workspace, _ = make_agent(tmp_path, Router([GOOD]))
    with workspace.exclusive_run():
        with pytest.raises(ValueError, match="already running"):
            with workspace.exclusive_run():
                pass
    with pytest.raises(ValueError, match="managed"):
        workspace.write_tool_text("checkpoint.json", "{}", overwrite=True)


def test_context_keeps_full_problem_code_and_all_samples_and_isolates_tasks():
    builder = ContextBuilder()
    problem = OJ().get_problem("sum").as_dict()
    problem["samples"] *= 4
    code = "CODE_START" + "x" * 24000 + "CODE_END"
    messages = builder.build(problem=problem, role=AgentRole.DEBUG, current_solution=code,
                             plan="PLAN_START" + "x" * 9000,
                             feedback={"actual": "z" * 10000}, recent_history=["old"] * 6 + ["new"])
    text = messages[-1].content
    assert "CODE_START" in text and "CODE_END" in text and "PLAN_START" in text
    assert "Sample 4 input:" in text and "Notes matter." in text
    assert "[feedback clipped; full artifact retained]" in text
    other = builder.build(problem={"title": "another"}, role=AgentRole.CODE)[-1].content
    assert "CODE_START" not in other and "PLAN_START" not in other


def test_oversize_context_stops_before_call_instead_of_silently_cutting_code(tmp_path):
    router = Router([GOOD])
    original = router.route
    router.route = lambda p: replace(original(p), input_token_limit=1)
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop()
    assert result.termination_reason == "prompt_exceeds_configured_input_limit"
    assert not router.calls


def test_cli_supports_harness_and_resume_and_guards_before_network():
    parser = build_parser()
    assert parser.parse_args(["solve", "sum", "--mode", "harness-loop", "--http-timeout", "1",
                              "--poll-interval", "0", "--deadline", "1"]).profile is None
    with pytest.raises(SystemExit, match="confirm-model-call"):
        main(["resume", "some-task", "--http-timeout", "1", "--poll-interval", "0", "--deadline", "1"])


@pytest.mark.parametrize("verdict", ["WA", "CE", "RE", "TLE", "MLE", "OLE"])
def test_all_user_program_verdicts_can_debug_and_preserve_machine_feedback(tmp_path, verdict):
    router = Router(["plan", GOOD, GOOD])
    oj = OJ([verdict, "AC"])
    agent, workspace, _ = make_agent(tmp_path, router, oj)
    result = agent.run_harness_loop()
    assert result.solved and result.submissions == 2
    assert workspace.state.debug_iterations == 1 and oj.feedback_calls == 1
    assert f'"verdict": "{verdict}"' in router.calls[-1][1][-1].content


def test_model_failure_and_invalid_code_stop_without_an_extra_call(tmp_path):
    failure = LLMResponse.failure(provider="fake", model="fake-model", profile=ModelProfile.STRONG,
                                  error=LLMError(LLMErrorKind.TRANSPORT, "network failure"))
    router = Router([failure])
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop()
    assert result.terminal_status == "model_failure"
    assert workspace.state.llm_failure_count == 1 and not oj.submissions


def test_invalid_code_and_wrong_feedback_are_terminal(tmp_path):
    agent, workspace, oj = make_agent(tmp_path, Router(["plan", "not code"]))
    result = agent.run_harness_loop()
    assert result.terminal_status == "invalid_model_output" and not oj.submissions


def test_wrong_feedback_never_triggers_debug(tmp_path):
    router = Router(["plan", GOOD])
    oj = OJ(["WA"])
    oj.get_feedback = lambda submission_id: JudgeFeedback("CE", "wrong feedback")
    agent, workspace, _ = make_agent(tmp_path, router, oj)
    result = agent.run_harness_loop()
    assert result.terminal_status == "client_failure" and result.error_kind == "protocol"
    assert workspace.state.debug_iterations == 0


def test_known_submission_timeout_can_resume_with_no_repost(tmp_path):
    router = Router(["plan", GOOD])
    oj = OJ()
    original = oj.wait_for_submission

    def timeout(*args, **kwargs):
        raise OJResultUnknownError("deadline", kind=ClientErrorKind.RESULT_UNKNOWN,
                                   method="GET", path="submissions/1", submission_state_unknown=True)

    oj.wait_for_submission = timeout
    agent, workspace, _ = make_agent(tmp_path, router, oj)
    assert agent.run_harness_loop().terminal_status == "result_unknown"
    oj.wait_for_submission = original
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    assert resumed.run_harness_loop(resume=True).solved
    assert len(router.calls) == 2 and len(oj.submissions) == 1


def test_resume_at_sample_stage_retains_code_plan_and_counters(tmp_path):
    router = Router(["saved plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(interrupt="sample"))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.solved and len(router.calls) == 2
    assert loaded.state.custom_run_count == 2
    assert loaded.read_json("checkpoint.json")["plan"] == "saved plan"


def test_resume_between_failed_sample_and_debug_cannot_submit_bad_code(tmp_path, monkeypatch):
    router = Router(["plan", BAD, GOOD])
    agent, workspace, oj = make_agent(tmp_path, router)
    loop = HarnessLoop(agent, HarnessPolicy())
    monkeypatch.setattr(loop, "_sample_failure", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        loop.run()
    assert workspace.read_json("checkpoint.json")["test_stage"] == "sample_failure"
    assert not oj.submissions
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    assert resumed.run_harness_loop(resume=True).solved
    assert roles(router) == ["Phase: PLAN", "Phase: CODE", "Phase: DEBUG"]
    assert oj.run_calls == 2 and len(oj.submissions) == 1
    assert "<< 0" not in oj.submissions[0]


def test_explicit_fixed_profile_is_not_labeled_as_mixed(tmp_path):
    router = Router(["plan", GOOD])
    policy = ModelPolicy(mapping={role: ModelProfile.STANDARD for role in AgentRole})
    agent, workspace, _ = make_agent(tmp_path, router, policy=policy)
    assert agent.run_harness_loop().solved
    assert workspace.state.experiment_variant == "fixed-standard-no-escalation"


def test_resume_reloads_state_after_lock_instead_of_using_stale_preload(tmp_path):
    router = Router(["plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(interrupt="sample"))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    stale = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    newer = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    oj.interrupt = "wait"
    runner, _, _ = make_agent(tmp_path, router, oj, workspace=newer)
    with pytest.raises(KeyboardInterrupt):
        runner.run_harness_loop(resume=True)
    assert stale.state.last_submission_id is None
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=stale)
    result = resumed.run_harness_loop(resume=True)
    assert result.solved and result.submissions == 1
    assert len(oj.submissions) == 1 and len(router.calls) == 2


def test_corrupted_candidate_on_resume_is_never_submitted(tmp_path):
    router = Router(["plan", GOOD])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(interrupt="sample"))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    workspace.write_text("artifacts/solutions/solution-v1.cpp", "changed source")
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.terminal_status == "internal_failure" and not oj.submissions


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{}"])
def test_malformed_202_is_unknown_creation_and_never_retried(body):
    calls = []
    client = OJClient("https://oj.test", "test-token", client=httpx.Client(
        transport=httpx.MockTransport(lambda r: (calls.append(r) or httpx.Response(202, content=body)))))
    with pytest.raises(OJResultUnknownError):
        client.submit_solution("sum", "int main(){}")
    assert len(calls) == 1


def test_cli_full_harness_task_and_completed_resume(tmp_path, monkeypatch, capsys):
    import agent.cli as cli

    router, oj = Router(["plan", GOOD]), OJ()
    monkeypatch.setenv("OJ_BASE_URL", "https://oj.test")
    monkeypatch.setenv("OJ_API_TOKEN", "test-token")
    monkeypatch.setattr(cli.ModelRegistry, "from_yaml", lambda path: object())
    monkeypatch.setattr(cli.ModelPolicy, "from_yaml", lambda path: ModelPolicy())
    monkeypatch.setattr(cli, "ModelRouter", lambda registry: router)
    oj.__class__.__enter__ = lambda self: self
    oj.__class__.__exit__ = lambda *args: None
    monkeypatch.setattr(cli, "OJClient", lambda *a, **kw: oj)
    limits = ["--workspace-root", str(tmp_path), "--http-timeout", "1", "--poll-interval", "0",
              "--deadline", "1", "--confirm-model-call", "--confirm-submit"]
    assert cli.main(["solve", "sum", "--mode", "harness-loop", "--task-id", "cli-harness",
                     "--max-cost-cny", "2"] + limits) == 0
    assert json.loads(capsys.readouterr().out)["solved"]
    assert json.loads((tmp_path / "cli-harness/checkpoint.json").read_text())["config"]["policy"]["max_cost_cny"] == 2
    assert cli.main(["resume", "cli-harness"] + limits) == 0
    assert json.loads(capsys.readouterr().out)["submissions"] == 1
    assert len(router.calls) == 2 and len(oj.submissions) == 1
    before = (tmp_path / "cli-harness/state.json").read_bytes()
    with pytest.raises(SystemExit, match="budget/policy differs"):
        cli.main(["resume", "cli-harness", "--max-cost-cny", "3"] + limits)
    assert (tmp_path / "cli-harness/state.json").read_bytes() == before
    assert len(router.calls) == 2 and len(oj.submissions) == 1


def test_full_http_adapters_samples_feedback_debug_and_correlated_versions(tmp_path):
    model_contents = iter(["add integers", BAD, GOOD, GOOD])
    model_requests, oj_requests = [], []
    posts = 0

    def model_handler(request):
        payload = json.loads(request.content)
        model_requests.append(payload)
        assert payload["max_tokens"] == 4096
        return httpx.Response(200, json={
            "id": f"req-{len(model_requests)}", "choices": [{"finish_reason": "stop",
            "message": {"content": next(model_contents)}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 100},
        })

    def oj_handler(request):
        nonlocal posts
        oj_requests.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path == "/api/v1/agent/problems/sum":
            return httpx.Response(200, json=OJ().get_problem("sum").as_dict())
        if request.url.path == "/api/v1/runs":
            body = json.loads(request.content)
            assert "source_code" in body and "code" not in body
            return httpx.Response(200, json={"status": "OK", "exit_code": 0,
                                            "stdout": "0\n" if "<< 0" in body["source_code"] else "3\n"})
        if request.method == "POST":
            posts += 1
            return httpx.Response(202, json={"submission_id": posts, "status": "QUEUED"})
        if request.url.path.endswith("/feedback"):
            return httpx.Response(200, json={"verdict": "WA", "failure": {"actual": "0"}})
        return httpx.Response(200, json={"submission_id": posts, "status": "FINISHED",
                                         "verdict": "WA" if posts == 1 else "AC"})

    provider = OpenAICompatibleProvider("fake", "https://model.test/v1", "fake-key",
        timeout_seconds=1, client=httpx.Client(transport=httpx.MockTransport(model_handler)))
    definition = Router([]).route(ModelProfile.STANDARD)
    router = ModelRouter(ModelRegistry({"fake": provider}, {p: definition for p in ModelProfile}))
    oj = OJClient("https://oj.test", "test-token", client=httpx.Client(
        transport=httpx.MockTransport(oj_handler)))
    agent, workspace, _ = make_agent(tmp_path, router, oj)
    result = agent.run_harness_loop()
    assert result.solved and result.llm_calls == 4 and result.submissions == 2
    assert workspace.state.debug_iterations == 2 and workspace.state.custom_run_count == 3
    assert workspace.state.estimated_cost == pytest.approx(0.0012)
    assert workspace.state.budget_committed_cny <= 1
    assert oj_requests.count(("POST", "/api/v1/submissions")) == 2
    assert oj_requests.count(("GET", "/api/v1/agent/problems/sum")) == 1
    assert not any(path == "/api/v1/problems/sum" for _, path in oj_requests)
    text = workspace.read_text("events.jsonl")
    assert "test-token" not in text and "fake-key" not in text
