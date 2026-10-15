from dataclasses import replace
from io import BytesIO
import json

import pytest

from issueintel.investigation.providers import GroqProvider, ProviderError
from issueintel.investigation.usage import job_usage
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.budget import BudgetDenied
from test_runtime import configured
from test_workbench import SyntheticProvider, completed, local_server, http


def test_receipts_are_published_before_http_then_settled_without_prompt_or_key(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret')
    config=configured(tmp_path);receipts=[]
    def transport(*args,**kwargs):
        assert receipts[-1]['status']=='reserved'
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    provider=GroqProvider(config,transport=transport,on_receipt=receipts.append)
    provider.complete_json([{'role':'user','content':'private synthetic issue text'}])
    assert [r['status'] for r in receipts]==['reserved','settled']
    assert receipts[0]['reservation_id']==receipts[1]['reservation_id']
    assert receipts[-1]['tokens_charged_or_reserved']==15
    assert 'private synthetic' not in json.dumps(receipts) and 'synthetic-secret' not in json.dumps(receipts)


def test_receipt_persistence_failure_prevents_the_http_request(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret')
    def cannot_save(_):raise OSError('Synthetic disk failure')
    provider=GroqProvider(configured(tmp_path),transport=lambda *a,**k:pytest.fail('Spent without receipt ownership'),on_receipt=cannot_save)
    with pytest.raises(OSError):provider.complete_json([{'role':'user','content':'Synthetic'}])
    assert provider.config.ledger().receipt(provider.last_reservation)['status']=='reserved'


@pytest.mark.parametrize('breach',[False,True])
def test_missing_or_breached_usage_is_published_conservatively(tmp_path,monkeypatch,breach):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret');receipts=[]
    usage={'prompt_tokens':999999,'completion_tokens':1,'total_tokens':1000000} if breach else None
    def transport(*args,**kwargs):
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],'usage':usage}).encode())
    provider=GroqProvider(configured(tmp_path),transport=transport,on_receipt=receipts.append)
    if breach:
        with pytest.raises(BudgetDenied):provider.complete_json([{'role':'user','content':'Synthetic'}])
        assert receipts[-1]['status']=='breach'
        report=job_usage({'id':'breach','status':'failed','provider':'groq','usage_tracking':True,'usage_receipts':{receipts[-1]['reservation_id']:receipts[-1]}})
        assert report['usage_anomalies']==1 and report['retained_bounds']==0
    else:
        provider.complete_json([{'role':'user','content':'Synthetic'}])
        assert receipts[-1]['status']=='missing_usage_bound'
        assert receipts[-1]['tokens_charged_or_reserved']==receipts[0]['tokens_charged_or_reserved']


@pytest.mark.parametrize('fail_second',[False,True])
def test_workbench_usage_binds_exact_calls_and_keeps_failure_bounds(tmp_path,monkeypatch,fail_second):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret')
    config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda root:config)
    # A different workload in the same ledger must never be attributed to this job.
    foreign=config.ledger().reserve(config.price(),100,100)
    fixture=SyntheticProvider();calls=[]
    def transport(request,**kwargs):
        body=json.loads(request.data);calls.append(body)
        if fail_second and len(calls)==2:raise RuntimeError('synthetic-secret private response')
        answer=fixture.complete_structured(body['messages'],max_output_tokens=body['max_completion_tokens'],schema=body['response_format']['json_schema']['schema'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kwargs:GroqProvider(cfg,transport=transport,**kwargs))
    try:
        job=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert job['status']==('failed' if fail_second else 'completed')
        report=wb.usage(job['id'])
        assert report['tracking_available'] and report['reservations']==2
        assert foreign not in [r['reservation_id'] for r in report['receipts']]
        assert [r['stage'] for r in report['receipts']]==['seed_generation','verification']
        if fail_second:
            assert report['retained_bounds']==1 and report['tokens_charged_or_reserved']>30
            assert report['receipts'][1]['status']=='failed_bound'
        else:
            assert report['all_usage_settled'] and report['tokens_charged_or_reserved']==30
        wb2=Workbench(tmp_path)
        try:assert wb2.usage(job['id'])==report
        finally:wb2.close()
        serialized=json.dumps(report)
        assert 'private response' not in serialized and 'synthetic-secret' not in serialized
    finally:wb.close()


def test_legacy_missing_receipts_is_unknown_and_offline_jobs_are_zero():
    legacy=job_usage({'id':'old','status':'interrupted','provider':'groq'})
    assert legacy['reservations'] is None and legacy['usd_charged_or_reserved'] is None
    assert legacy['all_usage_settled'] is False
    offline=job_usage({'id':'offline','status':'completed','provider':'none'})
    assert offline['reservations']==0 and offline['usd_charged_or_reserved']=='0'


def test_cancel_during_call_retains_settled_receipt_and_does_not_start_verifier(tmp_path,monkeypatch):
    import threading
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret')
    config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda root:config)
    started=threading.Event();release=threading.Event();calls=[];fixture=SyntheticProvider()
    def transport(request,**kwargs):
        calls.append(request);started.set();assert release.wait(10)
        body=json.loads(request.data)
        answer=fixture.complete_structured(body['messages'],max_output_tokens=2048,schema=body['response_format']['json_schema']['schema'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kwargs:GroqProvider(cfg,transport=transport,**kwargs))
    try:
        job=wb.submit({'mode':'example','provider':'groq'});assert started.wait(10)
        assert wb.usage(job['id'])['receipts'][0]['status']=='reserved'
        wb.cancel(job['id']);release.set()
        assert completed(wb,job['id'])['status']=='cancelled'
        assert wb.usage(job['id'])['tokens_charged_or_reserved']==15 and len(calls)==1
    finally:release.set();wb.close()


def test_usage_api_is_session_guarded_and_does_not_repeat_work(tmp_path):
    with local_server(tmp_path) as server:
        job=completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])
        route='/api/jobs/'+job['id']+'/usage'
        assert http(server,'GET',route)[0]==403
        status,_,raw=http(server,'GET',route,headers={'X-IssueIntel-Token':server.session_token})
        assert status==200 and json.loads(raw)['reservations']==0
        assert json.loads(raw)['job_id']==job['id']
