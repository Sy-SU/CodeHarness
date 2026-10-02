from dataclasses import replace
import json

import pytest

from agent.core.metrics import workspace_metrics,recovery_metrics,trace_events
from agent.core.harness import HarnessPolicy
from agent.oj_client.client import OJHTTPError,OJResultUnknownError
from agent.oj_client.types import ClientErrorKind
from client_tests.test_phase4_harness import GOOD,BAD,Router,OJ,make_agent,good_variant


def test_first_formal_ac_is_distinct_from_first_candidate_sample_pass(tmp_path):
    agent,w,_=make_agent(tmp_path,Router(['plan',GOOD]))
    assert agent.run_harness_loop().solved
    m=w.state.recovery_metrics
    assert m['first_try_ac'] is True and m['first_candidate_sample_pass'] is True
    assert not m['recovered_to_ac'] and m['candidate_version_count']==1 and m['formal_submission_count']==1


def test_verified_sample_failure_debug_then_ac(tmp_path):
    agent,w,_=make_agent(tmp_path,Router(['plan',BAD,GOOD]))
    assert agent.run_harness_loop().solved
    m=w.state.recovery_metrics
    assert m['first_try_ac'] is True and m['first_candidate_sample_pass'] is False
    assert m['recovered_to_ac'] and m['recovered_after_sample_failure']
    assert not m['recovered_after_formal_failure'] and m['recovery_type']==['sample_failure']
    assert m['successful_debug_count']==1 and m['sample_gate_reject_count']==1
    assert m['custom_run_count']==2 and m['candidate_version_count']==2


@pytest.mark.parametrize('verdict',['WA','CE','RE','TLE','MLE','OLE'])
def test_formal_program_failure_debug_ac_requires_linked_evidence(tmp_path,verdict):
    agent,w,oj=make_agent(tmp_path,Router(['plan',GOOD,good_variant(1)]),OJ([verdict,'AC']))
    assert agent.run_harness_loop().solved
    m=w.state.recovery_metrics
    assert not m['first_try_ac'] and m['recovered_to_ac'] and m['formal_recovery_to_ac']
    assert m['recovered_after_formal_failure'] and m['recovery_type']==['formal_'+verdict]
    evidence=m['recovery_evidence'][0]
    assert evidence['failure']['submission_id']=='1'
    assert evidence['accepted_submission']['submission_id']=='2'
    assert evidence['debug_model_call_id']==evidence['accepted_candidate']['model_call_id']
    assert evidence['review_completed']
    checkpoint=w.read_json('checkpoint.json')
    assert checkpoint['state']['recovery_metrics']==m


@pytest.mark.parametrize('failure',['provider','http','result_unknown','ie','protocol'])
def test_infrastructure_failures_do_not_earn_algorithm_recovery(tmp_path,failure):
    class BrokenOJ(OJ):
        def wait_for_submission(self,*a,**kw):
            if failure=='ie':return replace(super().wait_for_submission(*a,**kw),verdict='IE')
            if failure=='http':raise OJHTTPError('offline',kind=ClientErrorKind.HTTP,method='GET',path='query')
            if failure=='result_unknown':raise OJResultUnknownError('unknown',kind=ClientErrorKind.RESULT_UNKNOWN,method='POST',path='submit')
            if failure=='protocol':return replace(super().wait_for_submission(*a,**kw),verdict='FUTURE')
            return super().wait_for_submission(*a,**kw)
    contents=['plan',RuntimeError('provider')] if failure=='provider' else ['plan',GOOD]
    agent,w,oj=make_agent(tmp_path,Router(contents),BrokenOJ())
    result=agent.run_harness_loop()
    assert not result.solved and not w.state.recovery_metrics['recovered_to_ac']
    assert not w.state.recovery_metrics['formal_recovery_to_ac']
    assert w.state.recovery_metrics['successful_debug_count']==0 and w.state.debug_iterations==0


def test_no_submission_never_is_first_try_ac_and_invalid_output_is_counted(tmp_path):
    agent,w,_=make_agent(tmp_path,Router(['plan','not complete code']))
    assert agent.run_harness_loop().terminal_status=='invalid_model_output'
    m=w.state.recovery_metrics
    assert m['first_try_ac'] is False and m['invalid_model_output_count']==1 and m['candidate_version_count']==0


def test_replan_count_and_more_than_one_failed_candidate(tmp_path):
    agent,w,_=make_agent(tmp_path,Router(['plan',*[good_variant(i) for i in range(4)],
        'new plan',good_variant(4)]),OJ(['WA']*4+['AC']))
    assert agent.run_harness_loop().solved
    m=w.state.recovery_metrics
    assert m['replan_count']==1 and m['debug_count']==3 and m['recovered_to_ac']
    assert not m['formal_recovery_to_ac'] # final CODE came from PLAN, not DEBUG
    assert m['successful_debug_count']==0 and m['candidate_version_count']==5


def test_corrupted_association_and_incomplete_trace_cannot_create_recovery_credit(tmp_path):
    agent,w,_=make_agent(tmp_path,Router(['plan',GOOD,good_variant(1)]),OJ(['WA','AC']))
    agent.run_harness_loop()
    events,invalid=trace_events(w.read_text('events.jsonl'))
    state=w.read_json('state.json')
    for event in events:
        if event['type']=='JUDGE_RESULT' and event['payload']['verdict']=='AC':event['payload']['code_sha256']='0'*64
    assert not recovery_metrics(events,state)['recovered_to_ac']
    events,_=trace_events(w.read_text('events.jsonl')+'{broken\n')
    m=recovery_metrics(events,state,invalid_lines=1)
    assert m['recovered_to_ac'] is None and m['first_try_ac'] is None and m['metrics_status']=='incomplete_trace'


@pytest.mark.parametrize('corrupt',['debug_correlation','debug_response','candidate_call','review_hash','missing_submission'])
def test_recovery_cannot_earn_credit_from_broken_causal_links(tmp_path,corrupt):
    agent,w,_=make_agent(tmp_path,Router(['plan',GOOD,good_variant(1)]),OJ(['WA','AC']))
    agent.run_harness_loop()
    events,_=trace_events(w.read_text('events.jsonl'));state=w.read_json('state.json')
    for event in events:
        p=event['payload']
        if corrupt=='debug_correlation' and event['type']=='LLM_CALL' and p.get('role')=='DEBUG':
            p['correlation_id']='other'
        if corrupt=='debug_response' and event['type']=='LLM_RESPONSE' and p.get('role')=='DEBUG':
            p['status']='failed'
        if corrupt=='candidate_call' and event['type'] in {'CODE_VERSION','SUBMISSION','JUDGE_RESULT'} and p.get('solution_version')=='solution-v2':
            p['model_call_id']='unrelated'
        if corrupt=='review_hash' and event['type']=='REVIEW_RESULT':p['code_sha256']='0'*64
    if corrupt=='missing_submission':
        events=[e for e in events if not(e['type']=='SUBMISSION' and e['payload']['submission_id']=='1')]
    m=recovery_metrics(events,state)
    assert not m['formal_recovery_to_ac']
    if corrupt!='review_hash':assert not m['recovered_to_ac']
    if corrupt=='missing_submission':assert m['first_try_ac'] is None and m['metrics_status']=='incomplete_trace'
