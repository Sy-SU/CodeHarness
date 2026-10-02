from dataclasses import asdict, replace
import json

import httpx
import pytest

from agent.core.checker import CheckerSpec, SampleGatePolicy, checker
from agent.core.generated_checker import checker_stdin, generated_decision
from agent.core.harness import HarnessLoop, HarnessPolicy
from agent.core.agent import CodingAgent
from agent.core.context import ContextBuilder
from agent.core.policy import ModelPolicy
from agent.oj_client.client import OJClient
from agent.oj_client.types import CustomRunResult, ProblemSample
from agent.tools.runtime import build_default_tools
from agent.workspace.task import TaskWorkspace
from client_tests.test_phase4_harness import OJ, Router, GOOD, BAD, make_agent, events, roles, good_variant


@pytest.mark.parametrize('kind,expected,actual,passed', [
    ('exact','1\n','1\n',True),('exact','1\n','1',False),
    ('token','a  b\n','a\nb',True),('token','a b','a c',False)])
def test_exact_and_token(kind,expected,actual,passed):
    result=checker(CheckerSpec(kind)).check(expected,actual)
    assert result.passed is passed
    assert result.status == ('sample_pass' if passed else 'sample_wrong_answer')


def test_exact_normalization_is_explicit():
    assert not checker(CheckerSpec('exact')).check('a\r\n','a\n').passed
    assert checker(CheckerSpec('exact',normalization='line_endings')).check('a\r\n','a\n').passed


@pytest.mark.parametrize('expected,actual,passed', [
    ('x 0','x 0.00005',True),('100','100.01',True),('100','100.2',False),
    ('x 1','y 1',False),('1','nan',False),('1','inf',False),('1 2','1',False),('1','no',False)])
def test_float_uses_both_explicit_bounds(expected,actual,passed):
    result=checker(CheckerSpec('float',absolute_tolerance=0.0001,relative_tolerance=0.001)).check(expected,actual)
    assert result.passed is passed


@pytest.mark.parametrize('spec', [CheckerSpec('float'),CheckerSpec('float',absolute_tolerance=0),CheckerSpec('unknown')])
def test_no_default_tolerance_or_implicit_token_checker(spec):
    result=checker(spec).check('1','1')
    assert result.status=='sample_check_unverifiable' and result.passed is None


@pytest.mark.parametrize('value',[True,-1,float('nan'),float('inf'),'0.1',10**400])
def test_invalid_float_configuration_is_rejected(value):
    with pytest.raises(ValueError):CheckerSpec('float',absolute_tolerance=value,relative_tolerance=0)


@pytest.mark.parametrize('reply,status,passed', [
    ({'checker_evaluated':True,'verdict':'AC'},'sample_pass',True),
    ({'checker_evaluated':True,'verdict':'WA'},'sample_wrong_answer',False),
    ({'status':'OK'},'sample_check_unverifiable',None),
    ({'checker_evaluated':True,'verdict':'IE'},'sample_check_unverifiable',None),
    ({'checker_evaluated':True,'verdict':'NEW'},'sample_check_unverifiable',None)])
def test_remote_checker_requires_explicit_judge_result(reply,status,passed):
    result=checker(CheckerSpec('special')).check('reference','different',judge=lambda **kw:reply)
    assert result.status==status and result.passed is passed


def construction_outputs():
    n=4
    expected=' '.join(str(i%n+1) for i in range(1,n+1))
    actual=' '.join(str(i) for i in range(n,0,-1))
    def valid(text):
        values=list(map(int,text.split()))
        return sorted(values)==list(range(1,n+1)) and all(i!=v for i,v in enumerate(values,1))
    assert expected != actual and valid(expected) and valid(actual)
    return expected,actual,valid


class ConstructionOJ(OJ):
    def __init__(self,kind='testlib',checker_replies=None,**kw):
        super().__init__(**kw)
        self.kind=kind;self.checker_replies=iter(checker_replies or [])
    def get_problem(self,problem_id):
        expected,_,_=construction_outputs()
        return replace(super().get_problem(problem_id),samples=[ProblemSample('4\n',expected)],
                       extra_fields={} if self.kind is None else {'checker':self.kind})
    def run_code(self,code,stdin):
        self.run_calls+=1
        if 'CHECKER_MARKER' in code:
            return CustomRunResult('OK',stdout=json.dumps({'valid':next(self.checker_replies)}),exit_code=0)
        return CustomRunResult('OK',stdout=construction_outputs()[1],exit_code=0)


@pytest.mark.parametrize('kind',['testlib',None,'unrecognized_checker'])
def test_multi_output_mismatch_never_enters_debug_without_verified_checker(tmp_path,kind):
    router=Router(['plan',GOOD]);agent,workspace,oj=make_agent(tmp_path,router,ConstructionOJ(kind))
    agent.sample_checking=SampleGatePolicy(llm_checker='disabled')
    result=agent.run_harness_loop()
    assert result.terminal_status=='sample_check_unverifiable'
    assert len(router.calls)==2 and not oj.submissions and workspace.state.debug_iterations==0
    sample=events(workspace,'SAMPLE_RESULT')[0]['payload']
    assert sample['passed'] is None and workspace.state.sample_check_unverifiable_count==1
    assert workspace.state.sample_gate_reject_count==0
    assert workspace.read_json('artifacts/result.json')['sample_gate_status']=='sample_check_unverifiable'


def test_remote_multi_output_checker_does_not_use_reference_equality():
    expected,actual,valid=construction_outputs()
    result=checker(CheckerSpec('special')).check(expected,actual,judge=lambda stdin,stdout:
        {'checker_evaluated':True,'verdict':'AC' if valid(stdout) else 'WA'})
    assert result.passed is True


def test_explicit_submit_of_unverifiable_is_not_sample_pass_or_recovery(tmp_path):
    agent,w,oj=make_agent(tmp_path,Router(['plan',GOOD]),ConstructionOJ())
    agent.sample_checking=SampleGatePolicy(on_unverifiable='submit',llm_checker='disabled')
    assert agent.run_harness_loop().solved and len(oj.submissions)==1
    metrics=w.state.recovery_metrics
    assert metrics['first_try_ac'] is True and not metrics['recovered_to_ac']
    assert metrics['first_candidate_sample_pass'] is None and metrics['sample_check_unverifiable_count']==1


def test_metadata_projection_does_not_return_rating_tags_or_other_content():
    requests=[]
    def handler(r):
        requests.append((r.method,r.url.path))
        return httpx.Response(200,json={'problem_id':'sum','checker':'testlib','rating':1800,
            'tags':['private_marker'],'editorial':'DO_NOT_COPY','statement':'other'})
    with OJClient('https://oj.test','test-token',client=httpx.Client(transport=httpx.MockTransport(handler))) as c:
        metadata=c.get_checker_metadata('sum')
    assert metadata=={'checker':'testlib','source':'GET /api/v1/problems/sum#checker'}
    assert requests==[('GET','/api/v1/problems/sum')]


CHECKER='```cpp\n#include <iostream>\n// CHECKER_MARKER\nint main(){std::cout<<"{}";}\n```'


@pytest.mark.parametrize('decision',[True,False,None])
def test_llm_generated_checker_is_budgeted_remote_and_explicitly_unverified(tmp_path,decision):
    router=Router(['plan',GOOD,CHECKER]);oj=ConstructionOJ(checker_replies=[True,decision])
    agent,w,oj=make_agent(tmp_path,router,oj)
    agent.sample_checking=SampleGatePolicy(llm_checker='submit_on_pass')
    result=agent.run_harness_loop()
    assert result.solved is (decision is True)
    assert len(router.calls)==3 and oj.run_calls==3 and w.state.custom_run_count==3
    assert w.state.budget_committed_cny>0
    sample=events(w,'SAMPLE_RESULT')[0]['payload']
    assert sample['passed'] is None and sample['generated_checker']['status']=='llm_generated_unverified'
    assert not events(w,'LLM_CALL')[-1]['payload']['messages'][-1]['content'].endswith('Implement the complete solution now.')
    assert events(w,'LLM_CALL')[-1]['payload']['purpose']=='sample_checker_generation'
    assert 'Current C++20 solution:' not in events(w,'LLM_CALL')[-1]['payload']['messages'][-1]['content']
    assert w.state.debug_iterations==0 and not w.state.recovery_metrics['recovered_to_ac']
    assert (w.root/'artifacts/checkers/checker-v1.cpp').is_file()
    metrics=w.state.recovery_metrics
    assert metrics['llm_checker_call_count']==1 and metrics['checker_custom_run_count']==2
    assert metrics['contestant_custom_run_count']==1
    assert metrics['llm_checker_reject_count']==int(decision is False)
    assert metrics['first_candidate_sample_pass'] is None and metrics['sample_gate_reject_count']==0
    observation=events(w,'GENERATED_CHECKER_RESULT')[0]['payload']
    assert observation['model_call_id']==w.state.solution_model_call_id
    assert observation['generated_checker']['model_call_id']!=observation['model_call_id']
    assert observation['generated_checker']['code_sha256']!=observation['code_sha256']
    assert events(w,'CHECKER_RUN')[0]['payload']['checker_sha256']==observation['generated_checker']['code_sha256']


def test_checker_rejecting_reference_cannot_approve_alternative_or_trigger_debug(tmp_path):
    agent,w,oj=make_agent(tmp_path,Router(['plan',GOOD,CHECKER]),ConstructionOJ(checker_replies=[False]))
    agent.sample_checking=SampleGatePolicy(llm_checker='submit_on_pass')
    assert agent.run_harness_loop().terminal_status=='sample_check_unverifiable'
    assert w.state.debug_iterations==0 and oj.run_calls==2 and not oj.submissions


def test_checker_cost_and_call_guards_apply_before_generation(tmp_path):
    router=Router(['plan',GOOD,CHECKER]);agent,w,oj=make_agent(tmp_path,router,ConstructionOJ())
    agent.sample_checking=SampleGatePolicy(llm_checker='submit_on_pass')
    result=agent.run_harness_loop(harness_policy=HarnessPolicy(max_llm_calls=2))
    assert result.terminal_status=='budget_exhausted' and len(router.calls)==2 and oj.run_calls==1
    assert not (w.root/'artifacts/checkers/checker-v1.cpp').exists()


@pytest.mark.parametrize('reply',[{'valid':'true'},{'valid':1},{'valid':True,'extra':0},{'valid':None}])
def test_generated_checker_unknown_output_is_not_success(reply):
    assert generated_decision(CustomRunResult('OK',stdout=json.dumps(reply))) is None


def test_length_prefixed_checker_input_uses_utf8_bytes():
    assert checker_stdin('汉\n','1 2')=='4\n汉\n3\n1 2'


def test_generated_checker_uncertain_call_is_not_reissued(tmp_path):
    router=Router(['plan',GOOD,KeyboardInterrupt()]);agent,w,oj=make_agent(tmp_path,router,ConstructionOJ())
    agent.sample_checking=SampleGatePolicy(llm_checker='submit_on_pass')
    with pytest.raises(KeyboardInterrupt):agent.run_harness_loop()
    restored=TaskWorkspace.load(tmp_path,w.state.task_id)
    resumed,w2,_=make_agent(tmp_path,router,oj,workspace=restored)
    resumed.sample_checking=SampleGatePolicy(llm_checker='submit_on_pass')
    result=resumed.run_harness_loop(resume=True)
    assert result.terminal_status=='result_unknown' and len(router.calls)==3 and oj.run_calls==1
    assert not oj.submissions


def test_sample_policy_drift_is_rejected_without_rewriting_checkpoint(tmp_path):
    agent,w,oj=make_agent(tmp_path,Router(['plan',GOOD]),OJ(interrupt='wait'))
    with pytest.raises(KeyboardInterrupt):agent.run_harness_loop()
    before=w.read_text('checkpoint.json')
    agent.sample_checking=SampleGatePolicy(on_unverifiable='submit')
    with pytest.raises(ValueError,match='Resume configuration'):agent.run_harness_loop(resume=True)
    assert w.read_text('checkpoint.json')==before


def test_default_generated_checker_positive_gate_and_remote_run_uncertainty(tmp_path):
    assert SampleGatePolicy().llm_checker=='submit_on_pass'
    class InterruptedChecker(ConstructionOJ):
        def run_code(self,code,stdin):
            if 'CHECKER_MARKER' in code:raise KeyboardInterrupt()
            return super().run_code(code,stdin)
    router=Router(['plan',GOOD,CHECKER]);agent,w,oj=make_agent(tmp_path,router,InterruptedChecker())
    with pytest.raises(KeyboardInterrupt):agent.run_harness_loop()
    calls=len(router.calls)
    assert agent.run_harness_loop(resume=True).terminal_status=='result_unknown'
    assert len(router.calls)==calls and not oj.submissions
    assert w.state.debug_iterations==0


def test_public_metadata_error_prose_never_reaches_trace(tmp_path):
    def handler(request):
        return httpx.Response(403,json={'detail':'EXCLUDED_EDITORIAL','code':'EXCLUDED_RATING'})
    with OJClient('https://oj.test','test-token',client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        workspace=TaskWorkspace.create(tmp_path,'metadata','sum','harness-loop')
        tools=build_default_tools(client,workspace)
        with pytest.raises(Exception):tools.call('get_checker_metadata',problem_id='sum')
        assert 'EXCLUDED' not in workspace.read_text('events.jsonl')


def test_generated_checker_reuse_does_not_replace_formal_debug_candidate_identity(tmp_path):
    router=Router(['plan',GOOD,CHECKER,good_variant(1)])
    agent,w,oj=make_agent(tmp_path,router,ConstructionOJ(verdicts=['WA','AC'],checker_replies=[True,True,True]))
    assert agent.run_harness_loop().solved
    m=w.state.recovery_metrics
    assert m['formal_recovery_to_ac'] and m['recovery_type']==['formal_WA']
    assert m['llm_checker_call_count']==1 and m['checker_custom_run_count']==3
    assert m['contestant_custom_run_count']==2 and m['sample_check_unverifiable_count']==2
    assert m['sample_gate_reject_count']==0 and m['successful_debug_count']==1
    generated=w.read_json('checkpoint.json')['generated_checker']
    proof=m['recovery_evidence'][0]
    assert proof['debug_model_call_id']==w.state.solution_model_call_id!=generated['model_call_id']
    assert len(router.calls)==4 and len(oj.submissions)==2
