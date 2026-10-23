"""Forward source-artifact tracking, using only injected local transports."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
from types import SimpleNamespace
import threading
from urllib.error import URLError

import pytest

from issueintel.investigation.artifact_accounting import ArtifactCalls,initialize_artifact_accounting,validate_artifact_accounting
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import digest,write_json
from test_adaptive_question_jobs import adaptive_response
from test_claim_review_jobs import ClaimProvider
from test_investigation_usage_jobs import receipt
from test_runtime import configured
from test_workbench import completed


def unit(tmp_path,mode='check_plan'):
    source={'source_job_id':'1'*24,'source_pack_sha256':'2'*64,'private_input':'Synthetic source text'}
    row={'id':'3'*24,'mode':mode,'provider':'groq','depth':'checks','request':{
        'mode':mode,'provider':'groq','depth':'checks','source_job_id':'1'*24,'source_pack_sha256':'2'*64},
        'status':'queued','artifact_attempts':[],'artifact_receipt_owners':{},'usage_tracking':True,'usage_receipts':{}}
    path=tmp_path/'owner.json';write_json(path,row)
    def save(value,**changes):value.update(changes);write_json(path,value)
    host=SimpleNamespace(_job_path=lambda _:path,_save=save,_check_cancelled=lambda _:None)
    return ArtifactCalls(host,row,source),path,row,source


@pytest.mark.parametrize('mode,maximum',[('adaptive_questions',1),('check_plan',2),('claim_review',2)])
def test_attempt_slots_are_durable_source_bound_and_never_count_final_guard(tmp_path,mode,maximum):
    calls,path,row,source=unit(tmp_path,mode)
    assert validate_artifact_accounting(row,source)==0
    for index in range(1,maximum+1):
        calls.begin();saved=json.loads(path.read_bytes())
        assert saved['artifact_attempts'][-1]=={'schema':'source_artifact_attempt_v1','owner_job_id':row['id'],
            'mode':mode,'source_job_id':'1'*24,'source_pack_sha256':'2'*64,'source_sha256':digest(source),
            'call_index':index,'max_calls':maximum}
        calls.fresh();calls.fresh()
        assert len(json.loads(path.read_bytes())['artifact_attempts'])==index
    with pytest.raises(ValueError,match='bound exhausted'):calls.begin()
    assert validate_artifact_accounting(row,source)==maximum


def test_one_reservation_cannot_move_to_repair_call_or_gain_a_second_owner(tmp_path):
    calls,path,row,_=unit(tmp_path);calls.begin();item=receipt(1);calls.capture(item)
    before=path.read_bytes();calls.begin()
    with pytest.raises(ValueError,match='cannot move'):calls.capture(item)
    saved=json.loads(path.read_bytes())
    assert saved['artifact_receipt_owners'][item['reservation_id']]['call_index']==1
    assert len(saved['usage_receipts'])==1


def test_terminal_receipt_preserves_inflight_request_change_then_admission_stops(tmp_path):
    calls,path,row,_=unit(tmp_path);calls.begin()
    reserved=receipt(1,status='reserved',cost=100);calls.capture(reserved)
    changed=json.loads(path.read_bytes());changed['request']['confirmation']='changed-in-flight';write_json(path,changed)
    calls.capture(receipt(1,cost=3))
    saved=json.loads(path.read_bytes())
    assert saved['request']['confirmation']=='changed-in-flight'
    assert saved['usage_receipts'][reserved['reservation_id']]['status']=='settled'
    with pytest.raises(ValueError,match='authority changed'):calls.fresh()


def test_fresh_journal_corruption_prevents_another_call_slot(tmp_path):
    calls,path,row,_=unit(tmp_path);calls.begin()
    changed=json.loads(path.read_bytes());changed['artifact_attempts'][0]['call_index']=2;write_json(path,changed)
    with pytest.raises(ValueError):calls.begin()
    assert len(json.loads(path.read_bytes())['artifact_attempts'])==1


def test_legacy_tracking_is_never_filled_in_by_read_validation(tmp_path):
    calls,path,row,source=unit(tmp_path)
    row.pop('artifact_attempts');row.pop('artifact_receipt_owners');write_json(path,row)
    before=path.read_bytes();assert validate_artifact_accounting(row,source) is None
    assert path.read_bytes()==before
    row['artifact_attempts']=[]
    with pytest.raises(ValueError):validate_artifact_accounting(row,source)


@pytest.mark.parametrize('nonempty',[False,True])
def test_worker_reentry_cannot_reset_initialized_attempt_and_receipt_inventory(tmp_path,nonempty):
    calls,path,row,source=unit(tmp_path)
    calls.host.lock=threading.RLock()
    if nonempty:
        calls.begin();calls.capture(receipt(1))
    before=path.read_bytes();stale=deepcopy(row)
    stale.pop('artifact_attempts');stale.pop('artifact_receipt_owners');stale.pop('usage_receipts')
    with pytest.raises(ValueError,match='initialized already'):
        initialize_artifact_accounting(calls.host,stale,status='running')
    assert path.read_bytes()==before
    assert stale['artifact_attempts']==row['artifact_attempts']
    assert stale['usage_receipts']==row['usage_receipts']


@pytest.fixture
def artifacts(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-artifact-accounting-key')
    config=replace(configured(tmp_path),reasoning_effort='high',max_output_tokens=8192)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    model=ClaimProvider();transport_calls=[];artifact_calls=[];behavior={}
    def transport(request,**kwargs):
        body=json.loads(request.data);schema=body['response_format']['json_schema']['schema']
        properties=schema.get('properties',{});is_artifact=(set(properties) in ({'questions'},{'checks'})
            or 'claims' in properties.get('reviews',{}).get('items',{}).get('properties',{}))
        transport_calls.append(body)
        if is_artifact:
            artifact_calls.append(body)
            if behavior.get('fail'):raise URLError('Synthetic artifact transport failure')
            if behavior.get('cancel'):
                owner=next(json.loads(p.read_bytes()) for p in wb.jobs.glob('*.json')
                           if json.loads(p.read_bytes()).get('mode') in {'adaptive_questions','check_plan','claim_review'})
                wb.cancel(owner['id'])
            if behavior.get('corrupt_ledger'):
                owner=next(json.loads(p.read_bytes()) for p in wb.jobs.glob('*.json')
                           if json.loads(p.read_bytes()).get('mode') in {'adaptive_questions','check_plan','claim_review'})
                owner['artifact_attempts'][0]['call_index']=2;write_json(wb._job_path(owner['id']),owner)
            if behavior.get('authority_change'):
                owner=next(json.loads(p.read_bytes()) for p in wb.jobs.glob('*.json')
                           if json.loads(p.read_bytes()).get('mode') in {'adaptive_questions','check_plan','claim_review'})
                owner['request']['confirmation']='changed-in-flight';write_json(wb._job_path(owner['id']),owner)
        if is_artifact and behavior.get('repair') and len(artifact_calls)==1:
            answer={}
        elif set(properties)=={'questions'}:
            answer=adaptive_response(json.loads(body['messages'][1]['content']))
        else:
            answer=model.complete_structured(body['messages'],max_output_tokens=body['max_completion_tokens'],schema=schema)
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kw:GroqProvider(cfg,transport=transport,**kw),automatic_sync=False)
    try:
        source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert source['status']=='completed',source
        yield wb,source,transport_calls,artifact_calls,behavior
    finally:wb.close()


def submit(wb,source,mode):
    confirmation={'adaptive_questions':'generate_questions_with_api_cost','check_plan':'generate_check_plan_with_api_cost',
                  'claim_review':'generate_claim_review_with_api_cost'}[mode]
    raw=json.loads(wb._job_path(source['id']).read_bytes())
    request={'source_job_id':source['id'],'source_pack_sha256':raw['result']['pack.json']['sha256'],'confirmation':confirmation}
    method={'adaptive_questions':wb.submit_questions,'check_plan':wb.submit_check_plan,'claim_review':wb.submit_claim_review}[mode]
    return method,request


@pytest.mark.parametrize('mode',['adaptive_questions','check_plan','claim_review'])
def test_new_native_calls_have_complete_current_and_chain_inventory(artifacts,mode):
    wb,source,_,calls,_=artifacts;method,request=submit(wb,source,mode)
    child=completed(wb,method(request)['id']);assert child['status']=='completed',child
    saved=json.loads(wb._job_path(child['id']).read_bytes())
    assert validate_artifact_accounting(saved)==1 and len(calls)==1
    assert len(saved['usage_receipts'])==len(saved['artifact_receipt_owners'])==1
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['current']['inventory_complete'] and bundle['lineage']['inventory_complete'],bundle['gaps']
    assert bundle['lineage']['providers']['groq']['reservations']==3
    assert method(request)['id']==child['id'] and len(calls)==1


@pytest.mark.parametrize('mode',['check_plan','claim_review'])
def test_schema_repair_gets_second_owned_slot_and_final_guard_does_not_get_third(artifacts,mode):
    wb,source,_,calls,behavior=artifacts;behavior['repair']=True;method,request=submit(wb,source,mode)
    child=completed(wb,method(request)['id']);assert child['status']=='completed',child
    saved=json.loads(wb._job_path(child['id']).read_bytes())
    assert validate_artifact_accounting(saved)==2 and len(calls)==2
    assert sorted(owner['call_index'] for owner in saved['artifact_receipt_owners'].values())==[1,2]
    assert wb.investigation_costs.bundle(child['id'])['current']['inventory_complete']


@pytest.mark.parametrize('mode',['adaptive_questions','check_plan','claim_review'])
@pytest.mark.parametrize('behavior_name,status',[('fail','failed'),('cancel','cancelled')])
def test_failed_or_cancelled_artifact_preserves_owned_receipt_without_retry(artifacts,mode,behavior_name,status):
    wb,source,_,calls,behavior=artifacts;behavior[behavior_name]=True;method,request=submit(wb,source,mode)
    child=completed(wb,method(request)['id']);assert child['status']==status,child
    saved=json.loads(wb._job_path(child['id']).read_bytes())
    assert validate_artifact_accounting(saved)==1 and len(calls)==1
    item=next(iter(saved['usage_receipts'].values()))
    assert item['status']==('failed_bound' if behavior_name=='fail' else 'settled')
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['current']['inventory_complete'] and bundle['current']['known_micro_usd_charged_or_reserved']>0
    assert method(request)['id']==child['id'] and len(calls)==1


@pytest.mark.parametrize('mode',['adaptive_questions','check_plan','claim_review'])
def test_configuration_failure_before_attempt_is_explicit_zero(artifacts,mode,monkeypatch):
    wb,source,_,calls,_=artifacts;method,request=submit(wb,source,mode)
    def unavailable(_):raise ValueError('Synthetic unavailable configuration')
    monkeypatch.setattr('issueintel.investigation.question_jobs.RuntimeConfig.load',unavailable)
    child=completed(wb,method(request)['id']);assert child['status']=='failed',child
    saved=json.loads(wb._job_path(child['id']).read_bytes())
    assert validate_artifact_accounting(saved)==0 and not calls
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['current']['complete_micro_usd_charged_or_reserved']==0


@pytest.mark.parametrize('mode',['adaptive_questions','check_plan','claim_review'])
def test_ledger_corruption_inflight_stops_before_repair_or_publication(artifacts,mode):
    wb,source,_,calls,behavior=artifacts;behavior.update(corrupt_ledger=True,repair=True)
    method,request=submit(wb,source,mode);child=completed(wb,method(request)['id'])
    assert child['status']=='failed' and len(calls)==1,child
    saved=json.loads(wb._job_path(child['id']).read_bytes())
    assert len(saved['usage_receipts'])==1
    assert next(iter(saved['usage_receipts'].values()))['status']=='reserved'
    assert wb.investigation_costs.bundle(child['id'])['current']['complete_micro_usd_charged_or_reserved'] is None


@pytest.mark.parametrize('mode',['adaptive_questions','check_plan','claim_review'])
def test_terminal_receipt_survives_changed_authority_and_output_is_not_published(artifacts,mode):
    wb,source,_,calls,behavior=artifacts;behavior['authority_change']=True
    method,request=submit(wb,source,mode);child=completed(wb,method(request)['id'])
    assert child['status']=='failed' and len(calls)==1,child
    saved=json.loads(wb._job_path(child['id']).read_bytes())
    assert saved['request']['confirmation']=='changed-in-flight'
    assert next(iter(saved['usage_receipts'].values()))['status']=='settled'
    assert not any(key.endswith('_result') for key in saved)


def test_restart_preserves_new_accounting_and_legacy_absence_remains_unknown(artifacts,monkeypatch):
    wb,source,_,calls,_=artifacts;method,request=submit(wb,source,'check_plan')
    child=completed(wb,method(request)['id']);assert child['status']=='completed'
    before=wb.investigation_costs.bundle(child['id'])
    other=Workbench(wb.root,provider_factory=lambda *a,**k:pytest.fail('Saved cost read spent'),automatic_sync=False)
    try:
        assert other.investigation_costs.bundle(child['id'])==before
        saved=json.loads(wb._job_path(child['id']).read_bytes())
        saved.pop('artifact_attempts');saved.pop('artifact_receipt_owners');write_json(wb._job_path(child['id']),saved)
        legacy=other.investigation_costs.bundle(child['id'])
        assert legacy['current']['known_micro_usd_charged_or_reserved']==before['current']['known_micro_usd_charged_or_reserved']
        assert legacy['current']['complete_micro_usd_charged_or_reserved'] is None
        assert len(calls)==1
    finally:other.close()
