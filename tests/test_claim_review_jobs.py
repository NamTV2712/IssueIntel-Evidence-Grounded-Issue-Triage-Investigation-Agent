"""Product claim critique uses durable intents, source replay and owned costs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from issueintel.investigation.workbench import Workbench
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.portable_verify import verify
from issueintel.interfaces.web import make_server
from issueintel.runtime.io import write_json
from test_check_plan_jobs import CheckProvider
from test_claim_review import review_response
from test_handoff import exported,contents,repack
from test_runtime import configured
from test_workbench import completed,http


class ClaimProvider(CheckProvider):
    def __init__(self):super().__init__();self.claim_calls=[];self.claim_transform=lambda v:v

    def complete_structured(self,messages,*,max_output_tokens,schema):
        if 'claims' not in schema.get('properties',{}).get('reviews',{}).get('items',{}).get('properties',{}):
            return super().complete_structured(messages,max_output_tokens=max_output_tokens,schema=schema)
        payload=json.loads(messages[1]['content']);self.claim_calls.append(payload)
        return self.claim_transform(review_response(payload))


@pytest.fixture
def claiming(tmp_path,monkeypatch):
    config=SimpleNamespace(model='openai/gpt-oss-120b',reasoning_effort='high',max_output_tokens=8192)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    provider=ClaimProvider();wb=Workbench(tmp_path,provider_factory=lambda *a,**k:provider)
    try:
        source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert source['status']=='completed',source
        yield wb,provider,source
    finally:wb.close()


def request(wb,source):
    return {'source_job_id':source['id'],'source_pack_sha256':hashlib.sha256(wb.result_bytes(source['id'])).hexdigest(),
            'confirmation':'generate_claim_review_with_api_cost'}


def generate_review(wb,source):
    row=completed(wb,wb.submit_claim_review(request(wb,source))['id'])
    assert row['status']=='completed',row
    return row,wb.claim_review_bundle(row['id'])


def test_claim_review_is_separate_immutable_and_reusable_across_restart(claiming,monkeypatch):
    wb,provider,source=claiming;raw=wb.result_bytes(source['id']);report=wb.report_bytes(source['id']);body=request(wb,source)
    row,bundle=generate_review(wb,source)
    assert len(provider.claim_calls)==1 and not wb.claim_review_history(source['id'])['review_jobs']==[]
    assert row['id'] not in {j['id'] for j in wb.history()}
    assert wb.submit_claim_review(body)['id']==row['id']
    assert wb.result_bytes(source['id'])==raw and wb.report_bytes(source['id'])==report
    wb.close();other=Workbench(wb.root,provider_factory=lambda *a,**k:pytest.fail('Saved read constructed provider'))
    try:
        monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *_:pytest.fail('Saved read config'))
        monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',lambda *a,**k:pytest.fail('Saved read Git'))
        assert other.claim_review_bundle(row['id'])==bundle
        assert other.submit_claim_review(body)['id']==row['id']
        for extension in ('json','md'):
            data,report_sha,review_sha=other.claim_review_export(row['id'],extension)
            assert report_sha==bundle['source']['report_sha256'] and review_sha==bundle['sha256'] and data
    finally:other.close()


def test_concurrent_submits_create_one_owned_attempt_and_failed_intent_is_not_retried(claiming):
    wb,provider,source=claiming;provider.claim_transform=lambda value:{}
    body=request(wb,source)
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda _:wb.submit_claim_review(body),range(4)))
    assert len({r['id'] for r in rows})==1
    row=completed(wb,rows[0]['id']);assert row['status']=='failed'
    assert len(provider.claim_calls)==2 and wb.submit_claim_review(body)['id']==row['id']
    assert len(wb.claim_review_history(source['id'])['review_jobs'])==1


@pytest.mark.parametrize('mutation',['confirmation','source_sha','extra','benchmark'])
def test_invalid_or_benchmark_request_stops_before_queue_or_provider(claiming,monkeypatch,mutation):
    wb,_,source=claiming;body=request(wb,source)
    if mutation=='confirmation':body['confirmation']='implicit'
    elif mutation=='source_sha':body['source_pack_sha256']='a'*64
    elif mutation=='extra':body['gold_patch']='hidden'
    else:
        row=json.loads(wb._job_path(source['id']).read_bytes());row['mode']=row['request']['mode']='main_dev';row['request']['case_id']='verified:synthetic';write_json(wb._job_path(source['id']),row)
    monkeypatch.setattr(wb,'_enqueue',lambda *a,**k:pytest.fail('Invalid request queued'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Invalid request created provider')
    with pytest.raises(ValueError):wb.submit_claim_review(body)


def test_actual_git_snapshot_replayed_before_a_paid_review(claiming,monkeypatch):
    wb,_,source=claiming;body=request(wb,source)
    pack=json.loads(wb.result_bytes(source['id']))
    path=wb.root/source['result']['pack.json']['path'];pack['snapshot']['commit_sha']='a'*40
    write_json(path,pack)
    row=json.loads(wb._job_path(source['id']).read_bytes());row['result']['pack.json']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();write_json(wb._job_path(source['id']),row)
    wb.provider_factory=lambda *a,**k:pytest.fail('Invalid snapshot spent')
    with pytest.raises(ValueError):wb.submit_claim_review(body)


def test_valid_source_with_unavailable_git_fails_before_provider_and_keeps_intent(claiming,monkeypatch):
    wb,provider,source=claiming;body=request(wb,source)
    def missing(*args,**kwargs):raise ValueError('Synthetic unavailable Git snapshot')
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',missing)
    wb.provider_factory=lambda *a,**k:pytest.fail('Git replay failed before provider construction')
    row=completed(wb,wb.submit_claim_review(body)['id'])
    assert row['status']=='failed' and provider.claim_calls==[]
    assert wb.submit_claim_review(body)['id']==row['id']


@pytest.mark.parametrize('mutation',['intent','bundle','rehash','path','authority'])
def test_saved_review_cannot_be_forged_or_reassigned(claiming,mutation):
    wb,_,source=claiming;row,bundle=generate_review(wb,source)
    path=wb.root/row['claim_review_result']['path']
    if mutation=='intent':
        with sqlite3.connect(wb.directory/'claim_reviews.sqlite') as db:db.execute('DELETE FROM intents')
    elif mutation=='authority':
        job=json.loads(wb._job_path(row['id']).read_bytes());job['request']['confirmation']='forged';write_json(wb._job_path(row['id']),job)
    elif mutation=='path':
        job=json.loads(wb._job_path(row['id']).read_bytes());job['claim_review_result']['path']='outside.json';write_json(wb._job_path(row['id']),job)
    else:
        path.write_bytes(path.read_bytes()+b' ')
        if mutation=='rehash':
            data=json.loads(path.read_bytes());data['assessment'][0]['claims'][0]['assessment']='supported'
            from issueintel.runtime.io import digest
            data['sha256']=digest({k:v for k,v in data.items() if k!='sha256'});write_json(path,data)
            job=json.loads(wb._job_path(row['id']).read_bytes());job['claim_review_result']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();write_json(wb._job_path(row['id']),job)
    with pytest.raises((ValueError,FileNotFoundError)):wb.claim_review_bundle(row['id'])


def test_dossier_and_handoff_include_source_bound_claim_review_without_model(claiming,tmp_path):
    wb,_,source=claiming;row,bundle=generate_review(wb,source)
    wb.provider_factory=lambda *a,**k:pytest.fail('Read called provider')
    dossier=wb.dossier(source['id'])
    assert dossier['claim_reviews']==[{'review_job_id':row['id'],'source_job_id':source['id'],'bundle':bundle}]
    assert any(r['kind']=='claim_review' and r['sha256']==bundle['sha256'] for r in dossier['timeline'])
    _,raw=exported(wb,source['id']);data=json.loads(contents(raw)['handoff.json'])
    assert data['claim_reviews']==dossier['claim_reviews']
    path=tmp_path/'handoff.zip';path.write_bytes(raw);assert verify(path)['integrity']=='consistent'


def test_portable_claim_quote_binding_survives_rehashed_container_tampering(claiming,tmp_path):
    from issueintel.runtime.io import digest
    wb,_,source=claiming;generate_review(wb,source);_,raw=exported(wb,source['id']);files=contents(raw)
    handoff=json.loads(files['handoff.json']);bundle=handoff['claim_reviews'][0]['bundle']
    bundle['reviews'][0]['claims'][0]['support'][0]['quote']='Not in the original code'
    bundle['sha256']=digest({k:v for k,v in bundle.items() if k!='sha256'})
    handoff['sha256']=digest({k:v for k,v in handoff.items() if k!='sha256'});files['handoff.json']=json.dumps(handoff).encode()
    manifest=json.loads(files['manifest.json']);manifest['selection_sha256']=handoff['sha256']
    for row in manifest['entries']:
        if row['path']=='handoff.json':row.update(bytes=len(files['handoff.json']),sha256=hashlib.sha256(files['handoff.json']).hexdigest())
    manifest['sha256']=digest({k:v for k,v in manifest.items() if k!='sha256'});files['manifest.json']=json.dumps(manifest).encode()
    path=tmp_path/'changed.zip';path.write_bytes(repack(files))
    with pytest.raises(ValueError,match='quote'):verify(path)


def test_failed_provider_keeps_owned_reservation_and_never_retries(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret');config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    fixture=ClaimProvider();calls=[];wb=None
    def transport(request,**kwargs):
        body=json.loads(request.data);schema=body['response_format']['json_schema']['schema']
        if 'claims' in schema.get('properties',{}).get('reviews',{}).get('items',{}).get('properties',{}):
            calls.append(1);rows=wb.claim_review_history(source['id'])['review_jobs']
            usage=wb.usage(rows[0]['id']);assert usage['receipts'][0]['status']=='reserved'
            assert usage['receipts'][0]['stage']=='claim_review'
            raise RuntimeError('synthetic-secret private transport text')
        answer=fixture.complete_structured(body['messages'],max_output_tokens=body['max_completion_tokens'],schema=schema)
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda config,**kwargs:GroqProvider(config,transport=transport,**kwargs))
    try:
        source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id']);body=request(wb,source)
        row=completed(wb,wb.submit_claim_review(body)['id']);assert row['status']=='failed'
        usage=wb.usage(row['id']);assert usage['retained_bounds']==1 and usage['reservations']==1 and len(calls)==1
        assert usage['receipts'][0]['stage']=='claim_review' and usage['receipts'][0]['status']=='failed_bound'
        assert wb.submit_claim_review(body)['id']==row['id']
        assert 'synthetic-secret' not in row['error']+json.dumps(usage)
    finally:wb.close()


def test_http_claim_review_intent_and_exports_are_session_and_hash_bound(claiming):
    wb,_,source=claiming;server=make_server(wb.root,port=0,workbench=wb);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        headers={'Content-Type':'application/json','X-IssueIntel-Token':server.session_token}
        assert http(server,'POST','/api/claim-reviews',json.dumps(request(wb,source)),{'Content-Type':'application/json'})[0]==403
        status,_,raw=http(server,'POST','/api/claim-reviews',json.dumps(request(wb,source)),headers)
        assert status==202
        row=completed(wb,json.loads(raw)['id']);assert row['status']=='completed'
        for format in ('json','md'):
            status,response,raw=http(server,'GET','/api/jobs/'+row['id']+'/claim-review.'+format,headers=headers)
            data,report_sha,review_sha=wb.claim_review_export(row['id'],format)
            assert status==200 and raw==data
            assert response['X-IssueIntel-Report-SHA256']==report_sha and response['X-IssueIntel-Claim-Review-SHA256']==review_sha
    finally:server.shutdown();server.server_close();thread.join(5)
