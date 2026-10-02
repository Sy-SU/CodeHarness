from dataclasses import replace
import json
from pathlib import Path

import pytest

from agent.execution import RunRequest,fingerprint
from agent.core.checker import SampleGatePolicy
from agent.workspace.task import TaskWorkspace
from experiments.config import ExperimentConfig,Strategy
from experiments.prepare import prepare_experiment
from experiments.runner import ExperimentRunner
from experiments.results import task_row,summarize_rows
from experiments.cli import main
from client_tests.test_phase5_experiments import config,service,ExperimentOJ,Provider

ROOT=Path(__file__).parents[1]


def test_smallset_has_twelve_verified_ratings_and_exactly_five_conditions():
    cfg=ExperimentConfig.from_yaml(ROOT/'config/experiment.small.yaml')
    plan=prepare_experiment(cfg)
    assert plan['task_count']==60 and len(plan['tasks'])==60
    assert plan['rating_distribution']=={'1200':3,'1400':3,'1600':3,'1800':3}
    assert len({t['condition'] for t in plan['tasks']})==5
    assert not plan['execution_authorized'] and plan['model_snapshot_status']=='unresolved'
    assert plan['llm_calls']==plan['formal_submissions']==plan['custom_runs']==0
    assert all(m['rating_source'].startswith('GET /api/v1/problems/') for m in cfg.problem_metadata.values())


def test_prepare_can_freeze_configured_model_data_without_invoking_provider(tmp_path):
    executor,provider,oj=service(tmp_path)
    plan=prepare_experiment(config(),service=executor)
    evidence=plan['frozen']['conditions']['standard-code']['model_configuration_evidence']['CODE']
    assert evidence['configured_max_output_tokens']==4096 and evidence['configured_input_price']==1
    assert evidence['configured_input_token_limit']==32000 and evidence['configured_context_limit'] is None
    assert evidence['verified_input_price'] is None and evidence['verified_max_output_tokens'] is None
    assert evidence['verified_context_limit'] is None and evidence['verification_status']=='unverified'
    assert 'endpoint_fingerprint' in evidence and not provider.calls and not oj.submissions and oj.run_calls==0
    assert not list(tmp_path.iterdir())
    provider.base_url='https://changed.test'
    assert prepare_experiment(config(),service=executor)['configuration_fingerprint']!=plan['configuration_fingerprint']


@pytest.mark.parametrize('change',[{'repetitions':2},{'strategies':[Strategy('only','code-only','standard')]},
    {'problem_metadata':{}},{'problems':['CF2119B']},{'preparation':{'problem_count':True}},
    {'problem_metadata':{'other':{'rating':1200,'rating_source':'fiction'}}}])
def test_invalid_smallset_requirements_fail_closed(change):
    cfg=ExperimentConfig.from_yaml(ROOT/'config/experiment.small.yaml')
    with pytest.raises(ValueError):replace(cfg,**change)


def test_unknown_rating_is_null_and_never_added_to_model_context(tmp_path):
    executor,provider,oj=service(tmp_path)
    cfg=config(problem_metadata={'sum':{'rating':None,'rating_source':None}},
               strategies=[Strategy('only','harness-loop','standard')])
    row=ExperimentRunner(executor).run(cfg,'unknown-rating')['tasks'][0]
    assert row['rating'] is None and row['official_performance'] is None
    assert row['official_performance_status']=='not_applicable_task_level'
    assert 'rating' not in provider.calls[0][1][-1].content.lower()
    assert row['first_try_ac'] and row['candidate_version_count']==1
    assert row['recovery_metrics']['metrics_status']=='derived' and row['actual_models']['CODE']['model']=='fake-model'


def test_native_projected_unknown_and_rating_are_separate_groups(tmp_path):
    rows=[]
    for name,mode,rating in [('native','verdict_only',1200),('projected','full',1200),('unknown',None,1200),('hard','verdict_only',1800)]:
        executor,provider,oj=service(tmp_path/name,oj=ExperimentOJ(feedback_mode=mode))
        cfg=config(strategies=[Strategy('same','code-only','standard')],
                   expected_feedback_mode=None if mode is None else 'verdict_only',require_feedback_mode=mode is not None,
                   problem_metadata={'sum':{'rating':rating,'rating_source':'fixture'}})
        rows+=ExperimentRunner(executor).run(cfg,name)['tasks']
    groups=summarize_rows(rows)
    assert len(groups)==4 and {g['actual_feedback_mode'] for g in groups}=={'full','verdict_only',None}
    assert all(g['official_performance'] is None for g in groups)
    assert all(g['first_try_ac_count']==1 for g in groups)


def test_unknown_usage_cost_and_recovery_remain_independent(tmp_path):
    executor,provider,oj=service(tmp_path,provider=Provider(usage=None))
    row=ExperimentRunner(executor).run(config(strategies=[Strategy('only','code-only','standard')]),'no-usage')['tasks'][0]
    assert row['input_tokens'] is None and row['estimated_cost'] is None and row['estimated_cost_cny'] is None
    summary=summarize_rows([row])[0]
    assert summary['input_tokens'] is None and summary['estimated_cost'] is None
    assert summary['unknown_cost_tasks']==1 and summary['first_try_ac_count']==0


def test_prepare_cli_needs_no_confirmation_or_network_and_never_overwrites(tmp_path,capsys,monkeypatch):
    import experiments.cli as module
    monkeypatch.setattr(module,'ExecutionService',lambda *a,**kw:pytest.fail('offline CLI must not initialize execution'))
    output=tmp_path/'plan.json'
    args=['prepare',str(ROOT/'config/experiment.small.yaml'),'--experiment-id','prepared','--output',str(output)]
    assert main(args)==0
    assert json.loads(output.read_text())['task_count']==60
    assert not (tmp_path/'.experiments').exists()
    with pytest.raises(SystemExit):main(args)


def test_resume_of_legacy_frozen_task_preserves_original_sample_policy(tmp_path):
    executor,provider,oj=service(tmp_path,oj=ExperimentOJ(interrupt='wait'))
    request=RunRequest('sum',profile='standard',sample_checking=None)
    with pytest.raises(KeyboardInterrupt):executor.run(request,'legacy')
    checkpoint=json.loads((tmp_path/'legacy/checkpoint.json').read_text())
    assert 'sample_checking' not in checkpoint['config']
    assert checkpoint['config']['sample_comparison']=='whitespace_tokens'
    calls=len(provider.calls);posts=len(oj.submissions)
    fresh=TaskWorkspace.load(tmp_path,'legacy')
    assert executor.run(request,'legacy',workspace=fresh,resume=True).solved
    assert len(provider.calls)==calls and len(oj.submissions)==posts
    with pytest.raises(ValueError,match='configuration'):
        executor.run(replace(request,sample_checking=SampleGatePolicy().as_dict()),'legacy',workspace=fresh,resume=True)


@pytest.mark.parametrize('option',[['--llm-checker','disabled'],['--sample-unverifiable','submit'],
                                  ['--max-cost-cny','2'],['--profile','strong']])
def test_preparation_rejects_ignored_execution_options_before_initialization(monkeypatch,option):
    import experiments.cli as module
    monkeypatch.setattr(module,'load_dotenv',lambda:pytest.fail('Reject ignored options before config/environment loads'))
    with pytest.raises(SystemExit):
        main(['prepare',str(ROOT/'config/experiment.small.yaml'),'--experiment-id','plan',*option])
