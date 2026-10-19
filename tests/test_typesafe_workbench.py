"""Real client plumbing with injected HTTP responses; no external API calls."""
from datetime import datetime,timezone
from io import BytesIO
import hashlib
import json

import pytest

from issueintel.investigation.workbench import Workbench,validate_request
from issueintel.runtime.io import write_json
from issueintel.runtime.typesafe import TypesafeClient,TypesafeConfig
from test_planned_pipeline import planned_repository
from test_planned_workbench import planned_workbench,request_for
from test_workbench import completed,local_server,http


def enable_typesafe(root,monkeypatch):
    (root/'configs').mkdir(exist_ok=True)
    (root/'configs/typesafe.toml').write_text('''provider="typesafe"
model="jev-1.13.0"
enabled=true
daily_requests=100
daily_usd="1"
monthly_usd="2"
price_file="configs/typesafe_prices.json"
ledger="artifacts/typesafe.sqlite"
''',encoding='utf-8')
    write_json(root/'configs/typesafe_prices.json',{'checked_at':datetime.now(timezone.utc).date().isoformat(),
        'currency':'USD','source':'https://docs.typesafe.ai/models','models':{'jev-1.13.0':{
            'input_per_million':'0.042','output_per_million':'0','per_request_context':64000,'per_question_context':32000}}})
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-typesafe-secret')


def test_opt_in_planned_job_has_separate_controller_receipts_and_saved_replay(planned_repository,planned_workbench,monkeypatch):
    wb,_,groq_calls,_=planned_workbench;enable_typesafe(wb.root,monkeypatch);typesafe_calls=[]
    def transport(request,**kwargs):
        body=json.loads(request.data);typesafe_calls.append(body)
        options=body['questions']['next_action']['criteria']
        selected='retrieve' if 'retrieve' in options else 'verify'
        return BytesIO(json.dumps({'model':body['model'],'answers':{'next_action':{'type':'choice',
            'choice':selected,'confidence':0.9,'probabilities':{key:float(key==selected) for key in options}}},
            'usage':{'input_tokens':100,'output_tokens':10}}).encode())
    wb.typesafe_factory=lambda config,**kwargs:TypesafeClient(config,transport=transport,**kwargs)
    job=completed(wb,wb.submit(dict(request_for(planned_repository),controller='jev'))['id'])
    assert job['status']=='completed',job
    pack=json.loads(wb.result_bytes(job['id']))
    sidecar=pack['controller_decisions']
    assert sidecar['schema']=='typesafe_controller_v1'
    assert pack['workflow_plan']['policy']=='bounded_planner_v2'
    assert len(typesafe_calls)==4 and len(groq_calls)==4
    assert pack['workflow_plan']['provider_calls']==4
    assert len(sidecar['records'])==4 and all(not row['fallback'] for row in sidecar['records'])
    assert len(job['typesafe_receipts'])==4 and len(wb.usage(job['id'])['receipts'])==4
    assert all(receipt['provider']=='typesafe' and receipt['status']=='settled' for receipt in job['typesafe_receipts'].values())
    assert 'synthetic-typesafe-secret' not in json.dumps(job) and 'parser.parse_list crashes' not in json.dumps(job['typesafe_receipts'])
    before=len(typesafe_calls)
    wb2=Workbench(wb.root,provider_factory=lambda *a,**k:pytest.fail('Groq on read'),
        typesafe_factory=lambda *a,**k:pytest.fail('Typesafe on read'))
    try:
        assert wb2.result_bytes(job['id'])==wb.result_bytes(job['id'])
        assert wb2.report_bytes(job['id'])
        assert wb2.resume_preview(job['id'])['available'] is False
    finally:wb2.close()
    assert len(typesafe_calls)==before
    row=json.loads(wb._job_path(job['id']).read_bytes());ref=row['result']['pack.json']
    original=json.loads(json.dumps(pack))
    from issueintel.runtime.io import digest
    sidecar=pack['controller_decisions'];sidecar['job_id']='f'*24
    for record in sidecar['records']:
        record['binding']['job_id']='f'*24
        if record['receipt'] is not None:
            receipt=record['receipt'];receipt['binding']['job_id']='f'*24
            receipt['sha256']=digest({key:value for key,value in receipt.items() if key!='sha256'})
    sidecar['sha256']=digest({key:value for key,value in sidecar.items() if key!='sha256'})
    from issueintel.investigation.typesafe_controller import validate_controller_decisions
    validate_controller_decisions(pack)  # A valid sidecar still cannot own another Workbench job.
    target=wb.root/ref['path'];write_json(target,pack)
    ref['sha256']=hashlib.sha256(target.read_bytes()).hexdigest();write_json(wb._job_path(job['id']),row)
    with pytest.raises(ValueError,match='ownership'):wb.result_bytes(job['id'])
    pack=original
    del pack['controller_decisions'];target=wb.root/ref['path'];write_json(target,pack)
    ref['sha256']=hashlib.sha256(target.read_bytes()).hexdigest();write_json(wb._job_path(job['id']),row)
    with pytest.raises(ValueError,match='Missing saved Typesafe'):wb.result_bytes(job['id'])


def test_provider_failure_falls_back_and_preserves_reserved_cost(planned_repository,planned_workbench,monkeypatch):
    wb,_,_,_=planned_workbench;enable_typesafe(wb.root,monkeypatch);calls=[]
    def failed(request,**kwargs):calls.append(request);raise RuntimeError('synthetic-typesafe-secret private provider text')
    wb.typesafe_factory=lambda config,**kwargs:TypesafeClient(config,transport=failed,**kwargs)
    job=completed(wb,wb.submit(dict(request_for(planned_repository),controller='jev'))['id'])
    assert job['status']=='completed',job
    pack=json.loads(wb.result_bytes(job['id']))
    assert len(calls)==4
    assert all(step['fallback'] for step in pack['workflow_plan']['rounds'])
    assert pack['workflow_plan']['final_decision']['fallback'] is True
    assert all(row['status']=='failed_bound' for row in job['typesafe_receipts'].values())
    assert all(row['micro_usd_charged_or_reserved']==2688 for row in job['typesafe_receipts'].values())
    assert 'private provider text' not in json.dumps(job)


def test_unsettled_paid_attempt_stops_before_any_further_call(planned_repository,planned_workbench,monkeypatch):
    from issueintel.runtime.typesafe import _Ledger,TypesafeError
    wb,_,groq_calls,_=planned_workbench;enable_typesafe(wb.root,monkeypatch);calls=[]
    def transport(request,**kwargs):
        body=json.loads(request.data);calls.append(body)
        options=body['questions']['next_action']['criteria'];selected='retrieve'
        return BytesIO(json.dumps({'model':body['model'],'answers':{'next_action':{'type':'choice',
            'choice':selected,'confidence':0.9,'probabilities':{key:float(key==selected) for key in options}}},
            'usage':{'input_tokens':100,'output_tokens':10}}).encode())
    def fail_settlement(*a,**k):raise TypesafeError('ledger_unavailable')
    monkeypatch.setattr(_Ledger,'settle',fail_settlement)
    wb.typesafe_factory=lambda config,**kwargs:TypesafeClient(config,transport=transport,**kwargs)
    job=completed(wb,wb.submit(dict(request_for(planned_repository),controller='jev'))['id'])
    assert job['status']=='failed' and len(calls)==len(groq_calls)==1
    assert wb.stage_bytes(job['id'],'seed_generation')
    receipt=next(iter(job['typesafe_receipts'].values()))
    assert receipt['status']=='reserved' and receipt['micro_usd_charged_or_reserved']==2688
    assert wb.resume_preview(job['id'])['available'] is False


@pytest.mark.parametrize('input_request',[
    {'mode':'example','controller':'jev'},
    {'mode':'example','depth':'planned','controller':'jev','provider':'none'},
    {'mode':'main_dev','depth':'planned','controller':'jev','provider':'groq'},
    {'mode':'example','depth':'planned','controller':{}},
])
def test_controller_request_boundary_precedes_api_or_data(input_request):
    with pytest.raises(ValueError):validate_request(input_request)


def test_key_alone_does_not_enable_queue_and_status_is_local_session_guarded(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-typesafe-secret')
    with local_server(tmp_path) as server:
        server.workbench.provider_factory=lambda *a,**k:pytest.fail('Provider constructed')
        assert http(server,'GET','/api/typesafe/status')[0]==403
        status,_,raw=http(server,'GET','/api/typesafe/status',headers={'X-IssueIntel-Token':server.session_token})
        assert status==200 and not json.loads(raw)['ready'] and json.loads(raw)['key_configured']
        assert b'synthetic-typesafe-secret' not in raw
        with pytest.raises(ValueError,match='Typesafe'):
            server.workbench.submit({'mode':'example','depth':'planned','provider':'groq','controller':'jev'})
        assert server.workbench.history()==[]


@pytest.mark.parametrize('options',[{}, {'refinement_rounds':1}, {'protocol':'issue_time'}])
def test_shared_pipeline_cannot_reuse_real_controller_outside_planned(tmp_path,monkeypatch,planned_repository,options):
    from issueintel.investigation.pipeline import investigate
    from issueintel.investigation.typesafe_controller import TypesafePlannedController
    enable_typesafe(tmp_path,monkeypatch)
    client=TypesafeClient(TypesafeConfig.load(tmp_path),transport=lambda *a,**k:pytest.fail('Typesafe HTTP'))
    controller=TypesafePlannedController(client,'a'*24)
    case,path=planned_repository
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',lambda *a,**k:pytest.fail('Snapshot IO'))
    with pytest.raises(ValueError,match='Typesafe'):
        investigate(case,path,controller=controller,complete_json=lambda *a,**k:pytest.fail('Groq call'),**options)
