"""Boundary and transport evidence, using local/Fake traffic only."""
from dataclasses import replace
import json
from pathlib import Path
import ssl

import httpx
import pytest

from agent.core.budget import BudgetStopped, reserve_model_cost
from agent.core.budget_diagnostics import prompt_breakdown
from agent.core.context import ContextBuilder
from agent.models.provider import OpenAICompatibleProvider
from agent.models.registry import ModelDefinition, ModelRegistry, ModelConfigurationError
from agent.models.transport import TransportObservation
from agent.models.types import AgentRole, ChatMessage, ModelProfile
from agent.workspace.task import TaskState


def provider(handler):
    return OpenAICompatibleProvider('test','https://example.invalid/v1','secret-that-must-not-leak',
        timeout_seconds=600, client=httpx.Client(timeout=600,transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize('exception,category',[(httpx.ConnectTimeout,'connect_timeout'),
    (httpx.ReadTimeout,'read_timeout_before_response_headers'),(httpx.WriteTimeout,'write_timeout'),
    (httpx.PoolTimeout,'pool_timeout'),(httpx.ConnectError,'connect_failure')])
def test_transport_classification_without_exception_prose_or_retries(exception,category):
    attempts=[]
    def handler(request):
        attempts.append(request)
        raise exception('secret-that-must-not-leak',request=request)
    p=provider(handler)
    try:
        response=p.complete([ChatMessage('user','hello')],model='same-model',profile=ModelProfile.STRONG,parameters={'max_tokens':8192})
        d=response.transport_diagnostics
        assert d['category']==category and d['automatic_retries']==0 and len(attempts)==1
        assert response.usage is None and response.actual_response_model is None
        assert 'secret-that-must-not-leak' not in json.dumps(d)
        assert 'secret-that-must-not-leak' not in str(response)
    finally:p.client.close()


def test_timeout_during_body_retains_observation_and_closes_stream():
    class Partial(httpx.SyncByteStream):
        closed=False
        def __iter__(self):
            yield b'{'
            raise httpx.ReadTimeout('secret-that-must-not-leak')
        def close(self):self.closed=True
    stream=Partial()
    p=provider(lambda request:httpx.Response(200,headers={'authorization':'secret-that-must-not-leak'},stream=stream))
    try:
        response=p.complete([ChatMessage('user','hello')],model='same-model',profile=ModelProfile.STRONG,parameters={})
        d=response.transport_diagnostics
        assert d['category']=='read_timeout_during_body' and d['http_status']==200
        assert d['response_headers_observed'] and d['first_byte_observed'] and d['partial_body_observed']
        assert d['body_bytes_observed']==1 and not d['body_complete'] and stream.closed
        assert d['unknown_charge_possible'] and response.usage is None
        assert 'secret-that-must-not-leak' not in str(response)
    finally:p.client.close()


def test_tls_failure_is_observable_without_tls_details():
    p=provider(lambda request:(_ for _ in ()).throw(httpx.ConnectError('hidden')))
    o=TransportObservation(timeout={},request_bytes=1,started=__import__('time').monotonic())
    e=httpx.ConnectError('secret-that-must-not-leak');e.__cause__=ssl.SSLError('secret-that-must-not-leak')
    assert o.error_category(e)=='tls_connect_failure'
    assert 'secret-that-must-not-leak' not in json.dumps(o.finish(category=o.error_category(e),error=e))
    p.client.close()


@pytest.mark.parametrize('status,category',[(429,'rate_limit'),(503,'provider_5xx')])
def test_http_failure_classification_has_no_automatic_retry(status,category):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,json={'error':'secret-that-must-not-leak'})
    p=provider(handler)
    try:
        r=p.complete([ChatMessage('user','hello')],model='m',profile=ModelProfile.STRONG,parameters={})
        assert r.transport_diagnostics['category']==category and len(calls)==1
        assert r.error.status_code==status and 'secret-that-must-not-leak' not in str(r)
    finally:p.client.close()


def test_success_records_actual_model_and_cached_usage_without_changing_request():
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'model':'m','choices':[{'message':{'content':'ok'}}],
            'usage':{'prompt_tokens':4,'completion_tokens':1,'prompt_tokens_details':{'cached_tokens':2}}})
    p=provider(handler)
    try:
        r=p.complete([ChatMessage('user','hello')],model='m',profile=ModelProfile.STRONG,parameters={'max_tokens':8192})
        assert r.actual_response_model=='m' and r.usage_metadata['prompt_tokens_details']['cached_tokens']==2
        assert captured==[{'model':'m','messages':[{'role':'user','content':'hello'}],'max_tokens':8192}]
        assert r.transport_diagnostics['body_complete'] and r.transport_diagnostics['category']=='success'
        assert r.transport_diagnostics['timeouts']=={'connect':600,'read':600,'write':600,'pool':600}
        assert r.transport_diagnostics['overall_request_deadline_seconds'] is None
    finally:p.client.close()


def test_compressed_response_is_decoded_once_with_body_observation():
    import gzip
    payload=gzip.compress(json.dumps({'model':'m','choices':[{'message':{'content':'ok'}}],
        'usage':{'prompt_tokens':1,'completion_tokens':1}}).encode())
    class Compressed(httpx.SyncByteStream):
        def __iter__(self):yield payload
    p=provider(lambda request:httpx.Response(200,headers={'content-encoding':'gzip'},stream=Compressed()))
    try:
        r=p.complete([ChatMessage('user','hello')],model='m',profile=ModelProfile.STRONG,parameters={})
        assert r.succeeded and r.content=='ok'
        assert r.transport_diagnostics['body_bytes_observed']==len(payload)
    finally:p.client.close()


def test_budget_decomposition_does_not_modify_content_and_rejected_call_is_audited():
    kwargs={'problem':{'title':'test','statement':'题目','samples':[{'input':'1','output':'2'}]},
        'role':AgentRole.DEBUG,'plan':'long plan','current_solution':'int main(){}',
        'feedback':{'verdict':'WA','details':'x'*9000},'recent_history':['old','new']}
    messages=ContextBuilder().build(**kwargs)
    before=[m.content for m in messages]
    parts=prompt_breakdown(messages,role='DEBUG',**{k:v for k,v in kwargs.items() if k not in {'problem','role'}})
    assert parts['status']=='complete_utf8_decomposition'
    assert sum(p['utf8_bytes'] for p in parts['components'])==sum(len(m.content.encode()) for m in messages)
    assert [m.content for m in messages]==before and all(p['actual_tokens'] is None for p in parts['components'])
    route=ModelDefinition('test','m',parameters={'max_tokens':8192},input_cost_per_million=.8,
        output_cost_per_million=2.7,currency='CNY',input_token_limit=1)
    state=TaskState('t','p');audits=[]
    with pytest.raises(BudgetStopped,match='prompt_exceeds_configured_input_limit'):
        reserve_model_cost(route,messages,state,1,audit=audits.append,components=parts)
    assert state.budget_committed_cny==0 and audits[0]['decision']=='rejected'
    assert audits[0]['configured_context_limit'] is None and audits[0]['actual_prompt_tokens'] is None


def test_original_rejected_prompt_passes_new_explicit_boundary_without_clipping():
    # Standalone local regression: exact original size, no provider request.
    messages=[ChatMessage('system','s'*205),ChatMessage('user','u'*31724)]
    base=ModelDefinition('test','m',parameters={'max_tokens':8192},input_cost_per_million=.8,
        output_cost_per_million=2.7,currency='CNY',input_token_limit=32768)
    with pytest.raises(BudgetStopped):reserve_model_cost(base,messages,TaskState('t','p'),1)
    state=TaskState('t','p');audits=[]
    limit,out,bound=reserve_model_cost(replace(base,input_token_limit=65536),messages,state,1,audit=audits.append)
    assert (limit,out)==(65536,8192) and bound==pytest.approx(.0745472)
    assert audits[0]['prompt_upper_bound_tokens']==33977 and audits[0]['decision']=='allowed'


def test_new_reservation_key_is_unambiguous_and_legacy_key_remains_supported(tmp_path):
    import yaml
    config={'providers':{'p':{'type':'openai-compatible','base_url_env':'URL','api_key_env':'KEY','timeout_seconds':600}},
        'models':{'standard':{'provider':'p','model':'m','input_reservation_boundary':65536}}}
    p=tmp_path/'models.yaml';p.write_text(yaml.safe_dump(config))
    registry=ModelRegistry.from_yaml(p,environ={'URL':'https://example.invalid','KEY':'secret'},
        required_profiles={ModelProfile.STANDARD})
    assert registry.definition(ModelProfile.STANDARD).input_reservation_boundary==65536
    for provider in registry.providers.values():provider.client.close()
    config['models']['standard']['input_token_limit']=32768;p.write_text(yaml.safe_dump(config))
    with pytest.raises(ModelConfigurationError,match='not both'):
        ModelRegistry.from_yaml(p,environ={'URL':'https://example.invalid','KEY':'secret'},
            required_profiles={ModelProfile.STANDARD})
