"""Regression tests for client candidates that predated the Phase 0 split."""

import json

import httpx

from agent.core.agent import CodingAgent
from agent.core.context import ContextBuilder
from agent.core.policy import DebugEscalationPolicy, ModelPolicy
from agent.models.registry import ModelDefinition
from agent.models.types import CostEstimate, LLMResponse, ModelProfile, TokenUsage
from agent.oj_client.client import AgentProblem, OJClient, ProblemLimits, ProblemSample
from agent.oj_client.types import CustomRunResult, JudgeFeedback, SubmissionRecord
from agent.tools.runtime import build_default_tools
from agent.workspace.task import TaskWorkspace
from experiments.summarize import summarize


CPP_BAD = "```cpp\n#include <iostream>\nint main(){std::cout << 0;}\n```"
CPP_GOOD = "```cpp\n#include <iostream>\nint main(){std::cout << 3 << '\\n';}\n```"


class FakeRouter:
    def __init__(self, contents):
        self.contents = iter(contents)
        self.profiles = []

    def complete(self, profile, messages):
        self.profiles.append(profile)
        return LLMResponse(
            next(self.contents), TokenUsage(10, 5), "fake", "fake-model", profile
        )

    def route(self, profile):
        return ModelDefinition(provider="fake", model="fake-model",
                               input_token_limit=32000, parameters={"max_tokens": 4096},
                               input_cost_per_million=1, output_cost_per_million=1,
                               currency="CNY")

    def estimate_cost(self, response):
        return CostEstimate(amount=0.001, currency="CNY", known=True)


class FakeOJ:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.submissions = []
        self.feedback_calls = 0
        self.run_calls = 0
        self.problem = AgentProblem(
            "sum",
            "Sum",
            "Add two integers.",
            "Two integers.",
            "Their sum.",
            "",
            ProblemLimits(1000, 128),
            [ProblemSample("1 2\n", "3\n")],
            extra_fields={"checker": "tokens"},
        )

    def get_problem(self, problem_id):
        return self.problem

    def run_code(self, code, stdin):
        self.run_calls += 1
        return CustomRunResult(
            "OK", stdout="3\n", stderr="", exit_code=0, time_ms=1, memory_kb=0
        )

    def submit_solution(self, problem_id, code):
        index = len(self.submissions)
        self.submissions.append(code)
        return SubmissionRecord(f"sub_{index}", "QUEUED")

    def get_submission(self, submission_id):
        index = int(submission_id.split("_")[1])
        return SubmissionRecord(submission_id, "FINISHED", self.verdicts[index])

    def wait_for_submission(
        self, submission_id, *, timeout_seconds, poll_interval_seconds
    ):
        return self.get_submission(submission_id)

    def get_feedback(self, submission_id):
        self.feedback_calls += 1
        return JudgeFeedback(
            "WA", "Wrong answer.", {"failure": {"test_index": 1}}
        )


def make_agent(tmp_path, router, oj, mode, policy=None):
    workspace = TaskWorkspace.create(tmp_path, f"task-{mode}", "sum", mode)
    tools = build_default_tools(oj, workspace)
    return (
        CodingAgent(
            router,
            policy or ModelPolicy(),
            ContextBuilder(),
            tools,
            workspace,
            poll_seconds=0,
            judge_timeout_seconds=1,
        ),
        workspace,
    )


def test_code_only_has_one_model_call_submission_and_no_feedback(tmp_path):
    oj = FakeOJ(["WA"])
    agent, workspace = make_agent(tmp_path, FakeRouter([CPP_BAD]), oj, "code-only")
    result = agent.run_code_only(ModelProfile.STANDARD)
    assert not result.solved
    assert result.llm_calls == 1 and result.submissions == 1
    assert oj.feedback_calls == 0 and oj.run_calls == 0
    assert workspace.state.current_phase == "DONE"


def test_harness_retries_without_escalation_and_records_required_trace_events(tmp_path):
    oj = FakeOJ(["WA", "WA", "AC"])
    router = FakeRouter(["Use addition.", CPP_BAD, CPP_BAD, CPP_GOOD, "Looks correct."])
    policy = ModelPolicy(
        debug_escalation=DebugEscalationPolicy(
            enabled=True,
            threshold=2,
            failure_kinds=frozenset({"WA"}),
        )
    )
    agent, workspace = make_agent(tmp_path, router, oj, "harness-loop", policy)
    result = agent.run_harness_loop(max_attempts=3)
    assert result.solved and result.submissions == 3
    assert router.profiles == [
        ModelProfile.STRONG,
        ModelProfile.STANDARD,
        ModelProfile.STANDARD,
        ModelProfile.STANDARD,
    ]
    events = [
        json.loads(line)["type"]
        for line in (workspace.root / "events.jsonl").read_text().splitlines()
    ]
    for required in (
        "LLM_CALL",
        "LLM_RESPONSE",
        "TOOL_CALL",
        "TOOL_RESULT",
        "SUBMISSION",
        "JUDGE_RESULT",
        "STATE_CHANGE",
        "CODE_VERSION",
        "SAMPLE_RESULT",
    ):
        assert required in events


def test_oj_client_custom_run_preserves_current_compatibility_alias():
    observed = {}

    def handler(request):
        observed.update(json.loads(request.content))
        return httpx.Response(200, json={"status": "OK"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    result = OJClient(
        "http://oj.test",
        "oj_test",
        client=http,
        send_custom_run_code_alias=True,
    ).run_code(
        "int main(){return 0;}", ""
    )
    assert result.status == "OK"
    assert observed["source_code"] == observed["code"]


def test_experiment_summary_groups_comparable_metrics(tmp_path):
    for index, solved in enumerate((True, False)):
        task = tmp_path / f"task-{index}"
        task.mkdir()
        (task / "state.json").write_text(
            json.dumps(
                {
                    "mode": "code-only",
                    "experiment_variant": "standard",
                    "solved": solved,
                    "llm_call_count": 1,
                    "submission_count": 1,
                    "debug_iterations": 0,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "estimated_cost": 0.01,
                    "wall_clock_seconds": 2,
                    "calls_per_model_profile": {"standard": 1},
                }
            )
        )
    result = summarize(tmp_path)["code-only:standard"]
    assert result["tasks"] == 2
    assert result["solved"] == 1
    assert result["solve_rate"] == 0.5
    assert result["calls_per_model_profile"] == {"standard": 2}
