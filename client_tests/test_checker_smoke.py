"""Bounded checker smoke Fakes: zero network or generated local execution."""
from dataclasses import replace
from pathlib import Path
import json

import pytest

from agent.core.checker import SAMPLE_POLICY, SampleGatePolicy
from agent.models.types import LLMResponse
from agent.oj_client.types import CustomRunResult
from experiments.checker_smoke import (SMOKE_PROBLEMS, SmokeLimits, load_checker_smoke, main, run_checker_smoke)
from experiments.checker_scope import checker_change_scope
from experiments.main_preflight import extend_main_preflight, checker_matrix
from client_tests.test_phase5_experiments import Provider, ExperimentOJ, service
from client_tests.test_sample_checkers import CHECKER
from client_tests.test_main_final_preflight import main_fixture
from client_tests.test_experiment_preflight import git_state

ROOT = Path(__file__).parents[1]


class CheckerProvider(Provider):
    def __init__(self, *, interrupt=False, invalid=False):
        super().__init__(); self.interrupt = interrupt; self.invalid = invalid
    def complete(self, messages, *, model, profile, parameters):
        self.calls.append(messages)
        if self.interrupt:
            self.interrupt = False
            raise KeyboardInterrupt()
        return LLMResponse('explanation' if self.invalid else CHECKER, self.usage, 'fake', model, profile)


class CheckerOJ(ExperimentOJ):
    def __init__(self, *, reject=None):
        super().__init__(); self.reject = reject; self.metadata = []
    def get_checker_metadata(self, problem_id):
        self.metadata.append(problem_id)
        return {'checker': 'testlib', 'source': 'GET /api/v1/problems/' + problem_id + '#checker'}
    def run_code(self, code, stdin):
        assert 'CHECKER_MARKER' in code
        self.run_calls += 1
        return CustomRunResult('OK', stdout=json.dumps({'valid': self.metadata[-1] != self.reject}), exit_code=0)
    def submit_solution(self, *args, **kwargs):
        raise AssertionError('formal submission in smoke')


def smoke(tmp_path, *, provider=None, oj=None, resume=False):
    provider, oj = provider or CheckerProvider(), oj or CheckerOJ()
    executor, _, _ = service(tmp_path, provider=provider, oj=oj)
    result = run_checker_smoke(executor.settings, ROOT/'config/models.infrastructure.yaml', tmp_path, 'checker-fixture',
                               resume=resume, registry=executor.registry, client=oj)
    return result, provider, oj


def test_eight_once_smoke_and_completed_resume_zero_new_actions(tmp_path):
    summary, provider, oj = smoke(tmp_path)
    assert summary['gate'] == 'CHECKER_LIVE_SMOKE_PASSED_WITH_WARNINGS'
    assert summary['accounting']['llm_calls'] == summary['accounting']['custom_runs'] == 8
    assert len(provider.calls) == oj.run_calls == 8
    assert summary['formal_submissions'] == summary['main_tasks_started'] == summary['local_generated_checker_executions'] == 0
    assert all(row['verification_class'] == 'llm_generated_unverified' and row['candidate_judgments'] == 0 for row in summary['problems'])
    assert load_checker_smoke(tmp_path, 'checker-fixture')['summary'] == summary
    resumed, _, _ = smoke(tmp_path, provider=provider, oj=oj, resume=True)
    assert resumed['accounting'] == summary['accounting'] and len(provider.calls) == oj.run_calls == 8


def test_reference_rejection_stops_problem_without_regeneration(tmp_path):
    summary, provider, oj = smoke(tmp_path, oj=CheckerOJ(reject='CF2118B'))
    row = next(row for row in summary['problems'] if row['problem_id'] == 'CF2118B')
    assert row['live_path_status'] == 'FAIL' and row['reference_sanity_status'] == 'failed'
    assert row['llm_calls'] == row['custom_runs'] == 1
    assert summary['gate'] == 'CHECKER_LIVE_SMOKE_FAILED'
    assert len(provider.calls) == oj.run_calls == 8


def test_smoke_unknown_paid_call_resume_does_not_retry(tmp_path):
    provider, oj = CheckerProvider(interrupt=True), CheckerOJ()
    with pytest.raises(KeyboardInterrupt):
        smoke(tmp_path, provider=provider, oj=oj)
    summary, _, _ = smoke(tmp_path, provider=provider, oj=oj, resume=True)
    assert len(provider.calls) == 8 and oj.run_calls == 7
    assert summary['accounting']['llm_calls'] == 8
    assert summary['problems'][0]['live_path_status'] == 'UNKNOWN'
    assert summary['accounting']['estimated_cost_cny'] is None


@pytest.mark.parametrize('limits', [{'calls_per_problem': 2}, {'total_calls': 9}, {'task_cost_cap_cny': 2},
    {'total_cost_cap_cny': 9}, {'custom_runs_per_problem': 4}, {'task_cost_cap_cny': float('nan')}])
def test_smoke_fixed_authorization_bounds(limits):
    with pytest.raises(ValueError):
        SmokeLimits(**limits)


def test_smoke_cli_requires_both_confirmation_flags_before_http():
    with pytest.raises(SystemExit):
        main(['--smoke-id', 'not-authorized'])


def test_smoke_artifact_drift_and_no_cross_experiment_injection(tmp_path):
    summary, _, _ = smoke(tmp_path)
    path = tmp_path/'.checker-smoke/checker-fixture'/('checker-fixture-'+SMOKE_PROBLEMS[0])/'artifacts/checkers/checker-v2.cpp'
    path.write_text(path.read_text()+'// tampered\n')
    with pytest.raises(ValueError, match='drift'):
        load_checker_smoke(tmp_path, 'checker-fixture')
    assert summary['frozen']['cross_experiment_checker_reuse'] is False


def test_checker_scope_rejects_later_code_format_retry_changes():
    scope = checker_change_scope(ROOT, '60d6ad1ef45ab54c65d6d9c8fd8cb8d3a5f00cc9', version=SAMPLE_POLICY)
    # Format correction is a separately authorized algorithm change, outside the
    # historical checker-only receipt. Do not broaden that receipt's approval.
    assert 'algorithm_unit_changed:agent/core/harness.py#HarnessLoop._generate' in scope['blockers']
    assert 'algorithm_unit_changed:agent/core/harness.py#HarnessLoop.run' in scope['blockers']
    assert scope['protected_algorithm_hash_before'] != scope['protected_algorithm_hash_after']


def test_v2_main_requires_smoke_and_never_treats_smoke_as_trust(tmp_path, git_state, monkeypatch):
    # Isolate the retained v2 smoke requirement from the separately audited v3 source change.
    monkeypatch.setattr("experiments.checker_scope.checker_change_scope", lambda *args, **kw: {"blockers": [], "policy_version": "authorized_checker_fix_v2"})
    cfg, executor, provider, oj, base, pilot = main_fixture(tmp_path)
    cfg = replace(cfg, sample_checking={**cfg.sample_checking, 'version': 'sample_check_v2'})
    pilot['receipt']['git_sha'] = '60d6ad1ef45ab54c65d6d9c8fd8cb8d3a5f00cc9'
    without = extend_main_preflight(base, cfg, main_experiment_id='new-main', workspace_root=tmp_path, pilot=pilot)
    assert 'checker_v2_live_smoke_unavailable' in without['blockers']
    rows = [{**row, 'live_path_status': 'PASS', 'generation_status': 'success', 'execution_status': 'success',
             'reference_sanity_status': 'pass'} for row in base['checker_summary']['problems'] if row['problem_id'] in SMOKE_PROBLEMS]
    evidence = {'summary': {'smoke_id': 'fake-smoke', 'gate': 'CHECKER_LIVE_SMOKE_PASSED_WITH_WARNINGS', 'problems': rows}, 'file_hashes': {}}
    for row in rows:
        row['checker_spec'] = base['checker_summary']['problems'][cfg.problems.index(row['problem_id'])]['checker_spec']
    report = extend_main_preflight(base, cfg, main_experiment_id='new-main', workspace_root=tmp_path, pilot=pilot, smoke=evidence)
    assert not report['blockers']
    matrix = report['main_final_preflight']['checker_matrix']
    assert len([row for row in matrix['problems'] if row['checker_smoke_live_path_status'] == 'PASS']) == 8
    assert len(matrix['strata']['llm_generated_unverified']) == 8 and len(matrix['strata']['remote_verified']) == 0
    assert not provider.calls and not oj.run_calls and not oj.submissions
    evidence['summary']['gate'] = 'CHECKER_LIVE_SMOKE_FAILED'
    evidence['summary']['problems'][0]['live_path_status'] = 'FAIL'
    failed = extend_main_preflight(base, cfg, main_experiment_id='new-main', workspace_root=tmp_path, pilot=pilot, smoke=evidence)
    assert failed['main_final_preflight']['gate'] == 'MAIN_V1_BLOCKED'
    assert 'checker_live_smoke_failed' in failed['blockers']
