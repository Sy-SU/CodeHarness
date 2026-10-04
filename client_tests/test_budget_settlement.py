"""Actual-usage budget regression tests; all providers and OJ calls are fake."""
from dataclasses import asdict, replace

import pytest

from agent.core.budget import BudgetStopped, MODEL_BUDGET_ACCOUNTING, reserve_model_cost
from agent.core.harness import HarnessLoop, HarnessPolicy
from agent.models.registry import ModelDefinition, ModelRegistry
from agent.models.router import ModelRouter
from agent.models.runtime import ModelCallRuntime
from agent.models.types import AgentRole, ChatMessage, LLMError, LLMErrorKind, LLMResponse, ModelProfile, TokenUsage
from agent.workspace.task import TaskWorkspace
from experiments.results import audit_trace
from experiments.runner import ExperimentRunner
from experiments.contest import ContestRunner
from client_tests.test_phase4_harness import GOOD, Router, events, make_agent
from client_tests.test_phase5_experiments import config, service
from client_tests.test_contests import ContestOJ, request


MESSAGES = [ChatMessage("user", "Solve the problem")]


def test_twenty_yuan_budget_uses_usage_instead_of_accumulated_ceilings(tmp_path):
    # Reproduce the reported 56-call totals without invoking a real provider.
    calls = ([(ModelProfile.STRONG, TokenUsage(4494, 5522))] * 14
             + [(ModelProfile.STRONG, TokenUsage(4490, 5526))]
             + [(ModelProfile.STANDARD, TokenUsage(8455, 3491))] * 40
             + [(ModelProfile.STANDARD, TokenUsage(8492, 3501))])

    class Provider:
        def complete(self, messages, *, model, profile, parameters):
            return LLMResponse("response", calls.pop(0)[1], "fake", model, profile)

    definitions = {
        ModelProfile.STRONG: ModelDefinition("fake", "plus", {"max_tokens": 65536}, 2, 8, "CNY", 262144),
        ModelProfile.STANDARD: ModelDefinition("fake", "flash", {"max_tokens": 16384}, .8, 2.7, "CNY", 65536),
    }
    router = ModelRouter(ModelRegistry({"fake": Provider()}, definitions))
    workspace = TaskWorkspace.create(tmp_path, "budget-replay", "sum", "code-only")
    runtime = ModelCallRuntime(router, workspace)
    for profile, _ in list(calls):
        _, _, ceiling = reserve_model_cost(router.route(profile), MESSAGES, workspace.state, 20)
        workspace.trace.append("BUDGET_RESERVATION", {"reserved_cny": ceiling})
        runtime.complete(AgentRole.CODE, profile, MESSAGES)
        assert workspace.state.pending_model_reservation is None
    assert workspace.state.llm_call_count == 56
    assert workspace.state.input_tokens == 414098 and workspace.state.output_tokens == 225975
    assert workspace.state.budget_committed_cny == pytest.approx(1.4613183)
    assert workspace.state.budget_committed_cny == pytest.approx(workspace.state.estimated_cost)
    workspace.state.terminal_status = "completed"
    assert audit_trace(workspace, asdict(workspace.state))["status"] == "consistent"
    # The previous implementation had only 0.308 CNY left and rejected this call.
    reserve_model_cost(router.route(ModelProfile.STRONG), MESSAGES, workspace.state, 20)


def test_global_batch_can_use_released_budget(tmp_path):
    executor, provider, oj = service(tmp_path)
    manifest = ExperimentRunner(executor).run(config(total_cost_cny=.04), "released-batch")
    assert len(provider.calls) == 3 and len(oj.submissions) == 2
    assert all(row["solved"] and row["audit"]["status"] == "consistent" for row in manifest["tasks"])
    assert manifest["budget_committed_cny"] == pytest.approx(.00045)


def test_contest_can_use_released_budget_across_problems(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    report = ContestRunner(executor).run(replace(request(), total_cost_cny=.08), "released-contest")
    assert report["accepted"] == 2 and len(provider.calls) == 4 and len(oj.submissions) == 2
    assert report["budget_committed_cny"] == pytest.approx(.0006)
    assert report["estimated_cost_cny"] == pytest.approx(.0006)


@pytest.mark.parametrize("usage,reason", [
    (None, "model_usage_missing"),
    (TokenUsage(32001, 50), "provider_exceeded_configured_token_bound"),
])
def test_unverifiable_usage_keeps_ceiling_and_stops(tmp_path, usage, reason):
    agent, workspace, _ = make_agent(tmp_path, Router(["plan"], usage=usage))
    result = agent.run_harness_loop()
    assert result.termination_reason == reason and result.llm_calls == 1
    assert workspace.state.budget_committed_cny == pytest.approx(.036096)
    settlement = events(workspace, "BUDGET_SETTLEMENT")[0]["payload"]
    assert settlement["status"] == "retained" and settlement["released_cny"] == 0
    with pytest.raises(BudgetStopped, match="previous_model_usage_unresolved"):
        reserve_model_cost(agent.router.route(ModelProfile.STANDARD), MESSAGES, workspace.state, 1)


def test_failed_call_with_known_usage_is_settled_once(tmp_path):
    response = LLMResponse.failure(provider="fake", model="fake-model", profile=ModelProfile.STRONG,
        error=LLMError(LLMErrorKind.PROVIDER, "failed after token generation"), usage=TokenUsage(100, 50))
    agent, workspace, _ = make_agent(tmp_path, Router([response]))
    assert agent.run_harness_loop().terminal_status == "model_failure"
    assert workspace.state.budget_committed_cny == pytest.approx(.00015)
    assert len(events(workspace, "BUDGET_SETTLEMENT")) == 1
    assert workspace.state.pending_model_reservation is None


def test_failed_call_with_excessive_usage_keeps_reservation_and_reports_violation(tmp_path):
    response = LLMResponse.failure(provider="fake", model="fake-model", profile=ModelProfile.STRONG,
        error=LLMError(LLMErrorKind.PROVIDER, "failed"), usage=TokenUsage(32001, 50))
    agent, workspace, _ = make_agent(tmp_path, Router([response]))
    result = agent.run_harness_loop()
    assert result.termination_reason == "provider_exceeded_configured_token_bound"
    assert workspace.state.budget_committed_cny == pytest.approx(.036096)
    assert workspace.state.pending_model_reservation is not None


def test_full_usage_with_decimal_prices_does_not_falsely_exceed_reservation(tmp_path):
    agent, workspace, _ = make_agent(tmp_path, Router(["plan", GOOD], input_price=1.1,
                                                    usage=TokenUsage(32000, 4096)))
    assert agent.run_harness_loop().solved
    assert workspace.state.pending_model_reservation is None
    assert workspace.state.budget_committed_cny == pytest.approx(.078592)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupt_after_durable_response_preserves_settlement_without_reissue(tmp_path, monkeypatch, interruption):
    agent, workspace, oj = make_agent(tmp_path, Router(["plan"]))
    original = workspace.write_json

    def interrupt(path, value, **kwargs):
        if path.startswith("artifacts/models/"):
            raise interruption()
        return original(path, value, **kwargs)

    monkeypatch.setattr(workspace, "write_json", interrupt)
    with pytest.raises(interruption):
        agent.run_harness_loop()
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=loaded)
    assert resumed.run_harness_loop(resume=True).terminal_status == "result_unknown"
    assert not resumed.router.calls and loaded.state.llm_call_count == 1
    assert loaded.state.budget_committed_cny == pytest.approx(.00015)
    assert loaded.state.pending_model_reservation is None
    assert len(events(loaded, "BUDGET_SETTLEMENT")) == 1


def test_legacy_checkpoint_keeps_frozen_cumulative_reservations(tmp_path, monkeypatch):
    agent, workspace, oj = make_agent(tmp_path, Router(["plan"]))
    loop = HarnessLoop(agent, HarnessPolicy())
    loop.budget_settlement_enabled = False
    original = HarnessLoop._transition

    def interrupt(self, phase, reason=None):
        original(self, phase, reason)
        if phase == "CODE":
            raise KeyboardInterrupt()

    monkeypatch.setattr(HarnessLoop, "_transition", interrupt)
    with pytest.raises(KeyboardInterrupt):
        loop.run()
    assert "model_budget_accounting" not in workspace.read_json("checkpoint.json")["config"]
    monkeypatch.setattr(HarnessLoop, "_transition", original)
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, Router([GOOD]), oj, workspace=loaded)
    assert resumed.run_harness_loop(resume=True).solved
    assert loaded.state.budget_committed_cny == pytest.approx(.072192)
    assert not events(loaded, "BUDGET_SETTLEMENT")


def test_new_execution_snapshot_freezes_budget_accounting_version(tmp_path):
    executor, _, _ = service(tmp_path)
    snapshot = executor.snapshot(config().request("sum", config().strategies[0]))
    assert snapshot["model_budget_accounting"] == MODEL_BUDGET_ACCOUNTING
