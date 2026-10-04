"""Checker-only classifications and paid-intent recovery; all runs are Fakes."""
from dataclasses import asdict, replace
from pathlib import Path
import json
import subprocess

import pytest

from agent.core.checker import SAMPLE_POLICY, CheckerSpec, SampleGatePolicy as GatePolicy, checker_dimensions
from agent.core.checker_session import GeneratedCheckerSession
from agent.core.context import ContextBuilder
from agent.core.generated_checker import checker_messages
from agent.core.harness import HarnessLoop
from agent.oj_client.types import CustomRunResult
from agent.workspace.task import TaskWorkspace
from client_tests.test_phase4_harness import BAD, GOOD, Router, OJ, events, make_agent as base_make_agent
from client_tests.test_sample_checkers import CHECKER, ConstructionOJ, construction_outputs


def SampleGatePolicy(**kwargs):
    """These regressions exercise the explicitly frozen v2 contract."""
    return GatePolicy(**{"version": "sample_check_v2", **kwargs})


def make_agent(*args, **kwargs):
    agent, workspace, oj = base_make_agent(*args, **kwargs)
    if kwargs.get("workspace") is None:
        agent.sample_checking = SampleGatePolicy()
    return agent, workspace, oj


def test_v2_rejection_is_checker_stop_with_no_algorithm_credit(tmp_path):
    agent, w, oj = make_agent(tmp_path, Router(['plan', GOOD, CHECKER]), ConstructionOJ(checker_replies=[True, False]))
    result = agent.run_harness_loop()
    assert result.terminal_status == 'checker_rejected_unverified'
    assert not oj.submissions and w.state.debug_iterations == 0
    assert w.state.checker_observation['sample_check_status'] == 'rejected_unverified'
    assert w.state.sample_gate_reject_count == 0
    m = w.state.recovery_metrics
    assert m['llm_checker_generation_count'] == m['llm_checker_candidate_reject_count'] == m['unverified_checker_stop_count'] == 1
    assert not any(m[k] for k in ('recovered_to_ac', 'recovered_after_sample_failure', 'formal_recovery_to_ac'))
    assert m['sample_gate_reject_count'] == m['debug_count'] == 0


def test_v2_pass_links_generation_execution_and_candidate_without_trust_upgrade(tmp_path):
    agent, w, oj = make_agent(tmp_path, Router(['plan', GOOD, CHECKER]), ConstructionOJ(checker_replies=[True, True]))
    assert agent.run_harness_loop().solved and len(oj.submissions) == 1
    observation = w.state.checker_observation
    assert observation['checker_kind'] == 'special' and observation['checker_source'] == 'llm_generated'
    assert observation['checker_verification'] == 'llm_generated_unverified'
    assert observation['sample_check_status'] == 'pass_unverified'
    generated = w.read_json('checkpoint.json')['generated_checker']
    assert observation['checker_id'] == generated['checker_id']
    assert observation['generation_model_call_id'] == generated['model_call_id']
    assert observation['candidate_version'] == w.state.solution_version
    assert observation['candidate_source_sha256'] == w.state.solution_sha256
    assert observation['checker_source_sha256'] == generated['code_sha256']
    run = events(w, 'CHECKER_RUN')[-1]
    assert observation['checker_run_event_id'] == run['event_id']
    assert observation['execution_id'] == run['payload']['execution_id']
    assert w.state.recovery_metrics['llm_checker_candidate_pass_count'] == 1
    assert events(w, 'SAMPLE_RESULT')[0]['payload']['passed'] is None


def test_v2_trusted_token_rejection_still_debugs(tmp_path):
    agent, w, oj = make_agent(tmp_path, Router(['plan', BAD, GOOD]), OJ())
    assert agent.run_harness_loop().solved and w.state.debug_iterations == 1
    assert w.state.recovery_metrics['sample_gate_reject_count'] == 1
    assert w.state.recovery_metrics['recovered_after_sample_failure']
    assert w.state.recovery_metrics['llm_checker_generation_count'] == 0


def test_v2_reference_rejection_never_judges_candidate(tmp_path):
    agent, w, oj = make_agent(tmp_path, Router(['plan', GOOD, CHECKER]), ConstructionOJ(checker_replies=[False]))
    assert agent.run_harness_loop().terminal_status == 'checker_sanity_failed'
    assert oj.run_calls == 2 and not oj.submissions and w.state.debug_iterations == 0
    assert not [e for e in events(w, 'CHECKER_EXECUTION_RESULT') if e['payload']['stage'] == 'candidate']
    assert w.state.recovery_metrics['llm_checker_sanity_failure_count'] == 1
    assert w.state.recovery_metrics['llm_checker_candidate_reject_count'] == 0


@pytest.mark.parametrize('run,status', [
    (CustomRunResult('CE', stdout=''), 'checker_execution_failed'),
    (CustomRunResult('RE', stdout='', exit_code=1), 'checker_execution_failed'),
    (CustomRunResult('TLE', stdout=''), 'checker_execution_failed'),
    (CustomRunResult('IE', stdout=''), 'checker_execution_failed'),
    (CustomRunResult('OK', stdout='explanation'), 'checker_result_unknown'),
    (CustomRunResult('OK', stdout='{"valid":1}'), 'checker_result_unknown'),
    (CustomRunResult('OK', stdout=None), 'checker_result_unknown'),
    (CustomRunResult('OK', stdout='{"valid":true}', stdout_truncated=True), 'checker_result_unknown')])
def test_checker_execution_failures_are_not_candidate_verdicts(tmp_path, run, status):
    class FailedChecker(ConstructionOJ):
        def run_code(self, code, stdin):
            if 'CHECKER_MARKER' in code:
                self.run_calls += 1
                return run
            return super().run_code(code, stdin)
    agent, w, oj = make_agent(tmp_path, Router(['plan', GOOD, CHECKER]), FailedChecker())
    assert agent.run_harness_loop().terminal_status == status
    assert w.state.debug_iterations == 0 and not oj.submissions and oj.run_calls == 2
    m = w.state.recovery_metrics
    assert m['llm_checker_execution_failure_count'] == 1 and m['sample_gate_reject_count'] == 0
    assert not m['recovered_to_ac'] and not m['formal_recovery_to_ac']


def test_invalid_checker_source_is_generation_failure(tmp_path):
    agent, w, oj = make_agent(tmp_path, Router(['plan', GOOD, 'Here is an explanation']), ConstructionOJ())
    assert agent.run_harness_loop().terminal_status == 'checker_generation_failed'
    assert oj.run_calls == 1 and not oj.submissions and w.state.debug_iterations == 0
    assert w.state.recovery_metrics['llm_checker_generation_failure_count'] == 1


def test_completed_checker_and_known_runs_are_reused_across_resume(tmp_path):
    router = Router(['plan', GOOD, CHECKER])
    agent, w, oj = make_agent(tmp_path, router, ConstructionOJ(checker_replies=[True, True], interrupt='wait'))
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    generated = w.read_json('checkpoint.json')['generated_checker']
    assert generated['generation_status'] == 'success'
    restored = TaskWorkspace.load(tmp_path, w.state.task_id)
    resumed, w2, _ = make_agent(tmp_path, router, oj, workspace=restored)
    assert resumed.run_harness_loop(resume=True).solved
    assert len(router.calls) == 3 and oj.run_calls == 3 and len(oj.submissions) == 1
    assert w2.read_json('checkpoint.json')['generated_checker'] == generated


@pytest.mark.parametrize('interrupt_event', ['CHECKER_GENERATION_RESULT', 'CHECKER_EXECUTION_RESULT', 'CHECKER_SANITY_RESULT'])
def test_known_checker_commit_before_trace_interruption_reuses_without_remote_repeat(tmp_path, interrupt_event):
    router = Router(['plan', GOOD, CHECKER])
    agent, w, oj = make_agent(tmp_path, router, ConstructionOJ(checker_replies=[True, True]))
    append = w.trace.append
    interrupted = []
    def interrupt(kind, payload, **kwargs):
        if kind == interrupt_event and not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt()
        return append(kind, payload, **kwargs)
    w.trace.append = interrupt
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    w.trace.append = append
    assert agent.run_harness_loop(resume=True).solved
    assert len(router.calls) == 3 and oj.run_calls == 3 and len(oj.submissions) == 1
    assert len(events(w, 'CHECKER_GENERATION_RESULT')) == 1
    assert len(events(w, 'CHECKER_EXECUTION_RESULT')) == 2
    assert len(events(w, 'CHECKER_SANITY_RESULT')) == 1


def test_unknown_generation_and_checker_run_never_resend(tmp_path):
    router = Router(['plan', GOOD, KeyboardInterrupt()])
    agent, w, oj = make_agent(tmp_path, router, ConstructionOJ())
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    assert agent.run_harness_loop(resume=True).terminal_status == 'checker_result_unknown'
    assert len(router.calls) == 3 and oj.run_calls == 1 and not oj.submissions
    assert events(w, 'UNVERIFIED_CHECKER_STOP')[-1]['payload']['checker_decision'] == 'generation_failed'


def test_legacy_policy_resume_keeps_original_prompt_and_status(tmp_path):
    router = Router(['plan', GOOD, CHECKER])
    agent, w, oj = make_agent(tmp_path, router, ConstructionOJ(checker_replies=[True, True], interrupt='wait'))
    agent.sample_checking = SampleGatePolicy(version='sample_check_v1')
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    assert (w.root/'artifacts/checkers/checker-v1.cpp').is_file()
    restored = TaskWorkspace.load(tmp_path, w.state.task_id)
    resumed, _, _ = make_agent(tmp_path, router, oj, workspace=restored)
    assert resumed.run_harness_loop(resume=True).solved
    assert resumed.sample_checking.version == 'sample_check_v1' and len(router.calls) == 3


def test_checker_prompt_supports_multiple_valid_outputs_without_answer_oracle():
    problem = {'problem_id': 'construction', 'title': 'Derangement', 'statement': 'Return any derangement.',
               'input_specification': 'An integer n.', 'output_specification': 'A permutation with no fixed point.',
               'samples': [{'input': '4', 'output': construction_outputs()[0]}]}
    messages = checker_messages(ContextBuilder(), problem)
    text = '\n'.join(m.content for m in messages)
    assert 'Never compare' in text and 'hardcode sample answers' in text and 'every valid construction' in text
    assert 'No Markdown fences' in text and 'Only stdin/stdout' in text
    assert 'Current C++20 solution' not in text and 'Implement the complete solution now.' not in text


def test_no_local_generated_source_execution_and_cache_drift_fails(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('local generated checker execution')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    agent, w, oj = make_agent(tmp_path, Router(['plan', GOOD, CHECKER]), ConstructionOJ(checker_replies=[True, True]))
    assert agent.run_harness_loop().solved
    loop = HarnessLoop(agent, __import__('agent.core.harness', fromlist=['HarnessPolicy']).HarnessPolicy())
    loop.checkpoint = w.read_json('checkpoint.json')
    loop.checkpoint['generated_checker']['identity']['model_id'] = 'different-model'
    with pytest.raises(ValueError, match='identity'):
        GeneratedCheckerSession(loop).check(loop.checkpoint['problem']['samples'][0], 'alternative', 0)
    assert len(events(w, 'LLM_CALL')) == 3 and oj.run_calls == 3


def test_checker_kind_is_not_verification_class():
    assert checker_dimensions(CheckerSpec('special'), generated=True) == {
        'checker_kind': 'special', 'checker_source': 'llm_generated', 'checker_verification': 'llm_generated_unverified'}
    assert checker_dimensions(CheckerSpec('float'))['checker_verification'] == 'unverifiable'
    assert checker_dimensions(CheckerSpec('float', absolute_tolerance=0, relative_tolerance=0))['checker_verification'] == 'client_verified'
