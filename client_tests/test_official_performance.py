import asyncio
from dataclasses import replace
import hashlib,json,re
from pathlib import Path

import httpx
import pytest

from agent.config import ClientSettings
from agent.execution import fingerprint
from agent.oj_client.client import OJClient
from agent.oj_client.standings import parse_performance, OfficialPerformance, account_identity
from experiments.contest import ContestRunner, contest_store, refresh_performance, read_report
from client_tests.test_contests import ContestOJ, request, web
from client_tests.test_phase5_experiments import service


IDENTITY={'user_id':3,'username':'CodingAgent'}

def standings(rows=None):
    return {'contest_id':1,'problems':[], 'rows': rows if rows is not None else [
        {'user_id':4,'username':'someone','performance':1200},
        {'user_id':3,'username':'CodingAgent','performance':800}]}


@pytest.mark.parametrize('value,status',[(0,'confirmed'),(800,'confirmed'),(4000,'confirmed'),
    (None,'pending'),(-1,'invalid'),(4001,'invalid'),(True,'invalid'),('800','invalid'),
    (float('nan'),'invalid'),(0.5,'invalid')])
def test_official_value_is_used_directly_or_null(value,status):
    observation=parse_performance(standings([{'user_id':3,'performance':value}]),'1',IDENTITY)
    assert observation.official_performance_status==status
    assert observation.official_performance==(value if status=='confirmed' else None)
    assert observation.official_performance_source=='GET /api/v1/contests/1/standings#rows[].performance'
    assert observation.official_performance_scope=='contest_account'


@pytest.mark.parametrize('body,identity,status',[
    (standings([{'user_id':3}]),IDENTITY,'unavailable'),
    (standings([{'user_id':4,'username':'CodingAgent','performance':999}]),IDENTITY,'account_not_ranked'),
    (standings([{'user_id':3,'performance':800},{'user_id':3,'performance':900}]),IDENTITY,'identity_ambiguous'),
    (standings(),None,'identity_unresolved'),(standings(),{'username':'CodingAgent'},'identity_unresolved'),
    (standings(),{'user_id':True},'identity_unresolved'),
    ({'contest_id':2,'rows':[]},IDENTITY,'invalid'),
    ({'contest_id':True,'rows':[]},IDENTITY,'invalid'),
    ({'contest_id':1.0,'rows':[]},IDENTITY,'invalid'),
    ({'contest_id':1,'rows':[{'user_id':'3','performance':800}]},IDENTITY,'invalid')])
def test_identity_never_uses_row_order_or_fuzzy_name(body,identity,status):
    observation=parse_performance(body,'1',identity)
    assert observation.official_performance is None and observation.official_performance_status==status


def test_account_parser_does_not_copy_email_role_tokens_or_unknown_fields():
    assert account_identity({'id':3,'username':'CodingAgent','email':'DO_NOT_COPY','token':'NO'})==IDENTITY
    assert account_identity({'username':'CodingAgent'}) is None


@pytest.mark.parametrize('http_status,status',[(200,'confirmed'),(404,'unavailable'),(503,'unavailable')])
def test_refresh_is_separate_from_execution_and_only_uses_get(tmp_path,http_status,status):
    executor,provider,oj=service(tmp_path,oj=ContestOJ())
    runner=ContestRunner(executor);runner.prepare(request(),'only-enrichment')
    store=contest_store(tmp_path,'only-enrichment')
    record=store.read_json('contest.json')
    record.update(account_identity=IDENTITY,account_identity_endpoint_sha256=fingerprint(executor.settings.oj_base_url))
    store.write_json('contest.json',record)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store.root.iterdir() if p.is_file()}
    calls=[]
    def handle(r):
        calls.append((r.method,r.url.path))
        assert r.method=='GET'
        if r.url.path.endswith('/me'):return httpx.Response(200,json={'id':3,'username':'CodingAgent'})
        assert r.url.path=='/api/v1/contests/1/standings'
        return httpx.Response(http_status,json=standings())
    def factory(*a,**kw):return OJClient(*a,**kw,client=httpx.Client(transport=httpx.MockTransport(handle)))
    report=refresh_performance(tmp_path,'only-enrichment',executor.settings,client_factory=factory)
    assert report['official_performance_status']==status
    assert report['official_performance']==(800 if http_status==200 else None)
    assert report['accepted']==0 and report['status']=='queued'
    assert not provider.calls and not oj.submissions and not oj.run_calls
    assert calls==[('GET','/api/v1/me'),('GET','/api/v1/contests/1/standings')]
    assert all(hashlib.sha256((store.root/name).read_bytes()).hexdigest()==digest for name,digest in before.items())
    assert read_report(tmp_path,'only-enrichment')['official_performance']==report['official_performance']


def test_old_run_without_frozen_identity_is_not_attached_to_current_account(tmp_path):
    executor,provider,oj=service(tmp_path,oj=ContestOJ())
    ContestRunner(executor).prepare(request(),'legacy')
    report=refresh_performance(tmp_path,'legacy',executor.settings,
        client_factory=lambda *a,**kw:pytest.fail('No guessed historical identity / remote execution'))
    assert report['official_performance'] is None and report['official_performance_status']=='identity_unresolved'
    assert not provider.calls and not oj.submissions


def test_changed_account_is_unresolved_and_does_not_fetch_another_row():
    calls=[]
    def handle(r):
        calls.append(r.url.path)
        return httpx.Response(200,json={'id':4,'username':'CodingAgent'})
    c=OJClient('https://oj.test','test-token',client=httpx.Client(transport=httpx.MockTransport(handle)))
    observation=c.get_official_performance('1',IDENTITY)
    assert observation.official_performance_status=='identity_unresolved'
    assert calls==['/api/v1/me']


@pytest.mark.parametrize('identity',[None,{},'3',{'user_id':True},{'user_id':-1}])
def test_invalid_frozen_identity_does_not_query_current_account(identity):
    c=OJClient('https://oj.test','test-token',client=httpx.Client(
        transport=httpx.MockTransport(lambda r:pytest.fail('No identity guess'))))
    assert c.get_official_performance('1',identity).official_performance_status=='identity_unresolved'


def test_bad_json_and_oversize_standings_are_not_scores():
    for reply in (httpx.Response(200,text='not-json'),httpx.Response(200,text=' '*2_000_001)):
        def handle(r):
            if r.url.path.endswith('/me'):return httpx.Response(200,json={'id':3})
            return reply
        c=OJClient('https://oj.test','test-token',client=httpx.Client(transport=httpx.MockTransport(handle)))
        assert c.get_official_performance('1',IDENTITY).official_performance_status=='invalid'


def test_dashboard_refresh_is_protected_and_read_get_is_local(web,monkeypatch):
    launcher,provider,oj,get,headers=web
    from client_tests.test_contests import payload
    job=get('/api/dashboard/contests/launch','POST',headers=headers,json=payload()).json()
    run_id=job['contest_run_id']
    import experiments.contest as module
    calls=[]
    def refresh(root,identifier,settings):
        calls.append(identifier)
        return {**read_report(root,identifier),**OfficialPerformance(800,'confirmed').as_dict()}
    monkeypatch.setattr(module,'refresh_performance',refresh)
    monkeypatch.setenv('OJ_BASE_URL','https://oj.test');monkeypatch.setenv('OJ_API_TOKEN','test-token')
    endpoint=f'/api/dashboard/contests/{run_id}/performance'
    data={'request_id':'a'*32,'confirm_remote_calls':True}
    assert get(endpoint,'POST',json=data).status_code==403
    assert get(endpoint,'POST',headers=headers,json={**data,'problem_id':'inject'}).status_code==422
    assert get(endpoint,'POST',headers=headers,json=data).json()['official_performance']==800
    assert calls==[run_id] and not provider.calls and not oj.run_calls and not oj.submissions
    for language in ['en','zh']:
        html=get(f'/contests/{run_id}',cookies={'codeharness_language':language}).text
        assert '800' not in html # refresh fixture returned value without mutating report
        assert ('Official Performance' if language=='en' else '官方 Performance') in html
    assert get(f'/api/dashboard/contests/{run_id}').json()['official_performance'] is None
    assert calls==[run_id]


def test_read_only_dashboard_has_no_performance_refresh_control(tmp_path):
    from dashboard.app import create_app
    from dashboard.config import DashboardSettings
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(
                DashboardSettings(tmp_path,env_file=None))),base_url='http://127.0.0.1') as client:
            response=await client.post('/api/dashboard/contests/anything/performance',json={})
            assert response.status_code in {404,405}
    asyncio.run(check())


@pytest.mark.parametrize('value,status',[(0,'confirmed'),(None,'pending')])
def test_bilingual_dashboard_preserves_null_and_official_zero(web,value,status):
    launcher,provider,oj,get,headers=web
    from client_tests.test_contests import payload
    run_id=get('/api/dashboard/contests/launch','POST',headers=headers,json=payload()).json()['contest_run_id']
    store=contest_store(launcher.settings.workspace_root,run_id)
    record=store.read_json('contest.json')
    store.write_json('performance.json',{'run_id':run_id,'contest_id':'1',
        'configuration_fingerprint':record['configuration_fingerprint'],
        **OfficialPerformance(value,status,official_performance_source='GET /api/v1/contests/1/standings#rows[].performance').as_dict()})
    for language,unknown in [('en','Unknown'),('zh','未知')]:
        html=get(f'/contests/{run_id}',cookies={'codeharness_language':language}).text
        actual=re.search(r'data-contest="official_performance">([^<]+)</dd>',html)[1]
        assert actual==('0' if value is not None else unknown)
        assert 'data-contest="official_performance_source"' in html
        assert get(f'/api/dashboard/contests/{run_id}').json()['official_performance']==value
    assert not provider.calls and not oj.run_calls and not oj.submissions
