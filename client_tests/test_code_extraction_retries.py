"""Bounded output correction with fake models/OJ; no paid or remote actions."""
from dataclasses import replace

import pytest

from agent.core.harness import HarnessLoop, HarnessPolicy
from agent.workspace.task import TaskWorkspace
from agent.models.types import TokenUsage
from client_tests.test_phase4_harness import (
    GOOD, OJ, Router, events, good_variant, make_agent, roles,
)


INVALID = "<search_files><path>.</path><regex>.*</regex></search_files>"


@pytest.mark.parametrize("failures", [1, 3])
def test_format_retries_recover_before_any_oj_execution(tmp_path, failures):
    router = Router(["plan", *([INVALID] * failures), GOOD])
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop()
    assert result.solved and result.llm_calls == failures + 2
    assert oj.run_calls == len(oj.submissions) == workspace.state.attempt_count == 1
    assert roles(router) == ["Phase: PLAN"] + ["Phase: CODE"] * (failures + 1)
    assert "Format correction" not in router.calls[1][1][0].content
    for index, (_, messages) in enumerate(router.calls[2:], 1):
        assert f"Format correction retry {index}" in messages[0].content
        assert "No filesystem tools" in messages[0].content
    retries = events(workspace, "CODE_EXTRACTION_RETRY")
    assert [e["payload"]["retry"] for e in retries] == list(range(1, failures + 1))
    assert all(e["payload"]["max_retries"] == 3 for e in retries)
    assert len(events(workspace, "BUDGET_RESERVATION")) == result.llm_calls
    assert workspace.state.budget_committed_cny == pytest.approx(result.llm_calls * 0.00015)
    assert len(events(workspace, "BUDGET_SETTLEMENT")) == result.llm_calls
    checks = events(workspace, "MODEL_BUDGET_CHECK")
    assert checks[-1]["payload"]["component_breakdown"]["status"] == "complete_utf8_decomposition"
    assert workspace.read_json("checkpoint.json")["code_extraction_retries"] == 0


def test_three_additional_retries_then_terminal_and_done_resume_is_noop(tmp_path):
    router = Router(["plan", *([INVALID] * 4)])
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop()
    assert result.terminal_status == "invalid_model_output"
    assert result.termination_reason == "code_extraction_failed" and result.llm_calls == 5
    assert len(events(workspace, "CODE_EXTRACTION_RETRY")) == 3
    assert workspace.state.attempt_count == oj.run_calls == len(oj.submissions) == 0
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=loaded)
    assert resumed.run_harness_loop(resume=True) == result
    assert not resumed.router.calls


def test_debug_correction_keeps_role_feedback_and_candidate_lineage(tmp_path):
    router = Router(["plan", GOOD, INVALID, good_variant(1)])
    agent, workspace, oj = make_agent(tmp_path, router, OJ(["WA", "AC"]))
    result = agent.run_harness_loop()
    assert result.solved and result.submissions == 2
    assert roles(router) == ["Phase: PLAN", "Phase: CODE", "Phase: DEBUG", "Phase: DEBUG"]
    assert '"verdict": "WA"' in router.calls[-1][1][-1].content
    assert router.calls[-1][0] == router.calls[-2][0]
    assert events(workspace, "CODE_EXTRACTION_RETRY")[0]["payload"]["role"] == "DEBUG"
    assert workspace.state.debug_iterations == 2 and workspace.state.attempt_count == 2
    assert workspace.state.solution_model_call_id == workspace.state.last_model_call_id
    assert len(oj.submissions) == 2


def test_retry_allowance_resets_for_each_new_candidate(tmp_path):
    router = Router(["plan", INVALID, GOOD, INVALID, good_variant(1)])
    agent, workspace, _ = make_agent(tmp_path, router, OJ(["WA", "AC"]))
    assert agent.run_harness_loop().solved
    assert [e["payload"]["retry"] for e in events(workspace, "CODE_EXTRACTION_RETRY")] == [1, 1]


@pytest.mark.parametrize("policy,reason", [
    (HarnessPolicy(max_llm_calls=2), "llm_call_limit"),
    (HarnessPolicy(max_cost_cny=0.1), "cost_reservation_limit"),
])
def test_retries_obey_existing_budget_guards(tmp_path, policy, reason):
    router = Router(["plan", INVALID])
    if reason == "cost_reservation_limit":
        router.usage = TokenUsage(32000, 4096)
    agent, workspace, oj = make_agent(tmp_path, router)
    result = agent.run_harness_loop(harness_policy=policy)
    assert result.termination_reason == reason and result.llm_calls == 2
    assert len(router.calls) == 2 and not oj.submissions and oj.run_calls == 0


def test_resume_preserves_retry_count_after_scheduling_correction(tmp_path, monkeypatch):
    original = HarnessLoop._retry_code_extraction

    def interrupt(loop, role):
        result = original(loop, role)
        raise KeyboardInterrupt()

    monkeypatch.setattr(HarnessLoop, "_retry_code_extraction", interrupt)
    agent, workspace, oj = make_agent(tmp_path, Router(["plan", INVALID]))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    checkpoint = workspace.read_json("checkpoint.json")
    assert checkpoint["code_extraction_retries"] == 1 and checkpoint["completed_model"] is None
    monkeypatch.setattr(HarnessLoop, "_retry_code_extraction", original)
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    router = Router([INVALID] * 3)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.termination_reason == "code_extraction_failed" and result.llm_calls == 5
    assert len(router.calls) == 3
    assert loaded.read_json("checkpoint.json")["code_extraction_retries"] == 3


def test_resume_reuses_committed_correction_without_another_model_call(tmp_path, monkeypatch):
    original = HarnessLoop._generate

    def interrupt(loop, role):
        result = original(loop, role)
        if loop.checkpoint.get("code_extraction_retries") == 1:
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(HarnessLoop, "_generate", interrupt)
    agent, workspace, oj = make_agent(tmp_path, Router(["plan", INVALID, GOOD]))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    assert workspace.read_json("checkpoint.json")["completed_model"]["content"] == GOOD
    monkeypatch.setattr(HarnessLoop, "_generate", original)
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.solved and result.llm_calls == 3 and not resumed.router.calls
    assert len(oj.submissions) == 1


def test_unknown_interrupted_correction_is_not_reissued(tmp_path):
    agent, workspace, oj = make_agent(tmp_path, Router(["plan", INVALID, KeyboardInterrupt()]))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.terminal_status == "result_unknown"
    assert result.termination_reason == "interrupted_model_not_reissued"
    assert not resumed.router.calls and not oj.submissions


def test_legacy_checkpoint_keeps_fail_fast_policy(tmp_path, monkeypatch):
    original = HarnessLoop._transition

    def interrupt(loop, phase, reason=None):
        original(loop, phase, reason)
        if phase == "CODE":
            raise KeyboardInterrupt()

    monkeypatch.setattr(HarnessLoop, "_transition", interrupt)
    agent, workspace, oj = make_agent(tmp_path, Router(["plan"]))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    checkpoint = workspace.read_json("checkpoint.json")
    checkpoint["config"]["policy"].pop("max_code_extraction_retries")
    checkpoint.pop("code_extraction_retries")
    workspace.write_json("checkpoint.json", checkpoint)
    monkeypatch.setattr(HarnessLoop, "_transition", original)
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, Router([INVALID]), oj, workspace=loaded)
    result = resumed.run_harness_loop(resume=True)
    assert result.termination_reason == "code_extraction_failed" and result.llm_calls == 2
    assert not events(loaded, "CODE_EXTRACTION_RETRY")
    assert "max_code_extraction_retries" not in loaded.read_json("checkpoint.json")["config"]["policy"]


@pytest.mark.parametrize("value", [-1, True, 1.5, "3", None])
def test_invalid_retry_policy_is_rejected(value):
    with pytest.raises(ValueError, match="max_code_extraction_retries"):
        HarnessPolicy(max_code_extraction_retries=value)


def test_retry_policy_is_frozen_for_resume(tmp_path):
    agent, workspace, oj = make_agent(tmp_path, Router(["plan", GOOD]))
    agent.run_harness_loop()
    loaded = TaskWorkspace.load(tmp_path, workspace.state.task_id)
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=loaded)
    with pytest.raises(ValueError, match="Resume configuration"):
        resumed.run_harness_loop(resume=True, harness_policy=replace(HarnessPolicy(), max_code_extraction_retries=2))
