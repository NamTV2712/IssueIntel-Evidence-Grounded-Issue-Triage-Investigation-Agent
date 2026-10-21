"""Portable Jev recovery is inert source/accounting replay, never an API call."""
from copy import deepcopy
from io import BytesIO
import json
import subprocess
import sys

import pytest

from issueintel.investigation.portable_verify import (_typesafe_receipt, _typesafe_record,
    _typesafe_receipt_source, _verify_typesafe_plan, encoded, sha, verify)
from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import TypesafeClient
from test_planned_pipeline import planned_repository
from test_typesafe_controller import JOB_ID, run_controlled
from test_typesafe_stage_recovery import jev_recovery_workbench, jev_request
from test_stage_jobs import approval
from test_workbench import completed
from test_handoff import contents, exported, repack
from test_planned_handoff import reseal_changed_pack


@pytest.fixture
def decision_source(planned_repository):
    pack,_,_=run_controlled(planned_repository)
    plan=pack['workflow_plan'];seed=pack['evidence'][:len(plan['seed_evidence_ids'])]
    return pack,seed,plan['seed_annotation'],plan['routing']['max_retrieval_rounds'],plan['rounds'][0]


def reseal(value):
    value['sha256']=digest({k:v for k,v in value.items() if k!='sha256'})


def assert_exact_controller_json_types(files,identifier,tmp_path):
    for field in ('inherited_calls','new_calls','max_calls','automatic_retry','billing_verified','max_decisions'):
        def change(pack):
            sidecar=pack['controller_decisions']
            if field=='max_decisions':sidecar[field]=float(sidecar[field])
            elif field in {'automatic_retry','billing_verified'}:sidecar['recovery'][field]=0
            else:sidecar['recovery'][field]=float(sidecar['recovery'][field])
            reseal(sidecar)
        # Equivalent Python numeric values are still different JSON contract types.
        archive=tmp_path/('wrong-controller-type-'+field+'.zip')
        archive.write_bytes(reseal_changed_pack(files,identifier,change))
        with pytest.raises(ValueError):verify(archive)


def test_portable_typed_decision_rebuilds_exact_origin_tape_inputs(decision_source,monkeypatch):
    pack,seed,annotation,remaining,step=decision_source
    monkeypatch.setattr('sqlite3.connect',lambda *a,**k:pytest.fail('Portable decision accessed ledger'))
    monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    record=pack['controller_decisions']['records'][0]
    inputs=_typesafe_record(record,pack,seed,annotation,remaining,1,JOB_ID,step)
    assert inputs['decision_index']==1 and inputs['origin_binding']['job_id']==JOB_ID
    assert inputs['state']==record['state'] and inputs['allowed']==['retrieve','verify','stop']
    _typesafe_receipt(record['receipt'],record['binding'],record['request_sha256'],record['response'])


@pytest.mark.parametrize('mutation',['annotation_sha256','evidence_sha256','request_sha256'])
def test_orphan_terminal_receipt_cannot_reseal_a_different_decision_state(decision_source,mutation):
    pack,seed,annotation,remaining,step=decision_source
    record=pack['controller_decisions']['records'][0]
    inputs=_typesafe_record(record,pack,seed,annotation,remaining,1,JOB_ID,step)
    authorities={1:{'binding':inputs['origin_binding'],'request_sha256':record['request_sha256']}}
    receipt=deepcopy(pack['controller_decisions']['records'][0]['receipt'])
    _typesafe_receipt_source(receipt,authorities)
    if mutation=='request_sha256':receipt[mutation]='f'*64
    else:receipt['binding'][mutation]='f'*64
    reseal(receipt)
    # A valid receipt seal/common issue binding does not authorize another state.
    _typesafe_receipt(receipt)
    with pytest.raises(ValueError,match='decision state or request'):
        _typesafe_receipt_source(receipt,authorities)


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(status='reserved',response_sha256=None,input_tokens=None,output_tokens=None,micro_usd_charged_or_reserved=2688),
    lambda r:r.update(input_tokens=1000,micro_usd_charged_or_reserved=42),
    lambda r:r.update(price_source='https://untrusted.invalid'),
    lambda r:r.update(price_date='2026-01-01'),
    lambda r:r.update(is_provider_invoice=True),
    lambda r:r['binding'].update(decision_index=True),
])
def test_portable_receipt_rejects_unresolved_or_resealed_accounting_changes(decision_source,mutation):
    pack,*_=decision_source;record=pack['controller_decisions']['records'][0]
    receipt=deepcopy(record['receipt']);mutation(receipt);reseal(receipt)
    with pytest.raises(ValueError):
        _typesafe_receipt(receipt,record['binding'],record['request_sha256'],record['response'])


@pytest.mark.parametrize('mutation',[
    lambda r:r['state']['decision'].update(rounds_remaining=True),
    lambda r:r['state']['issue'].update(text='other issue'),
    lambda r:r['binding'].update(job_id='f'*24),
    lambda r:r.update(action='stop'),
    lambda r:r.update(allowed=['stop']),
    lambda r:r.update(fallback=True),
    lambda r:r['response']['answers']['next_action']['probabilities'].update(retrieve=False),
])
def test_portable_decision_rejects_resealed_state_choice_or_owner_changes(decision_source,mutation):
    pack,seed,annotation,remaining,step=decision_source
    record=deepcopy(pack['controller_decisions']['records'][0]);mutation(record)
    record['state_sha256']=digest(record['state'])
    if record['receipt'] is not None:
        record['receipt']['binding']=deepcopy(record['binding'])
        record['receipt']['response_sha256']=digest(record['response']) if record['response'] is not None else None
        reseal(record['receipt'])
    with pytest.raises(ValueError):_typesafe_record(record,pack,seed,annotation,remaining,1,JOB_ID,step)


def recovered(fixture):
    wb,case,path,_,_,_,failures=fixture
    failures['stage_after']='planned_retrieval_1'
    source=completed(wb,wb.submit(jev_request(case,path))['id'])
    assert source['status']=='failed',source
    preview=wb.stage_recoveries.preview(source['id']);assert preview['available'],preview
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    return wb,source,child


def test_jev_recovery_handoff_verifies_in_isolated_process_without_installation(jev_recovery_workbench,tmp_path,monkeypatch):
    wb,source,child=recovered(jev_recovery_workbench)
    before=[wb._job_path(r['id']).read_bytes() for r in (source,child)]
    calls=(len(jev_recovery_workbench[4]),len(jev_recovery_workbench[5]))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved handoff loaded Groq config'))
    monkeypatch.setattr('issueintel.runtime.typesafe.TypesafeConfig.load',lambda *a:pytest.fail('Saved handoff loaded Typesafe config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved handoff constructed Groq provider')
    wb.typesafe_factory=lambda *a,**k:pytest.fail('Saved handoff constructed Typesafe provider')
    preview,raw=exported(wb,child['id']);files=contents(raw)
    assert set(preview['included_job_ids'])=={source['id'],child['id']}
    witness=json.loads(files['runs/'+child['id']+'/stage-recovery.json'])
    assert witness['schema']=='product_stage_handoff_v5'
    assert witness['source']['controller']=='jev' and witness['source']['max_controller_calls']==4
    moved=tmp_path/'another-machine';moved.mkdir()
    archive=moved/'handoff.zip';archive.write_bytes(raw)
    script=moved/'verify.py';script.write_bytes(files['verify.py'])
    result=subprocess.run([sys.executable,'-I',str(script),str(archive),'--expected-sha256',sha(raw)],
        cwd=moved,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    report=verify(archive)
    assert report['runs']==2 and report['model_calls']==0 and report['authorship_verified'] is False
    assert report['semantic_verified'] is False and report['evaluation_performed'] is False
    assert [wb._job_path(r['id']).read_bytes() for r in (source,child)]==before
    assert calls==(len(jev_recovery_workbench[4]),len(jev_recovery_workbench[5]))
    assert_exact_controller_json_types(files,child['id'],tmp_path)


@pytest.mark.parametrize('mutation',['source_attempt','source_receipt','decision_owner','decision_debt','typed_action','reserved'])
def test_jev_portable_resealed_manifest_and_sidecar_cannot_change_owned_history(jev_recovery_workbench,tmp_path,mutation):
    wb,source,child=recovered(jev_recovery_workbench);_,raw=exported(wb,child['id']);files=contents(raw)
    if mutation in {'source_attempt','source_receipt'}:
        path='runs/'+child['id']+'/stage-recovery.json';witness=json.loads(files[path])
        if mutation=='source_attempt':witness['source_chain'][0]['controller']['attempts']=[]
        else:witness['source_chain'][0]['controller']['receipts']={}
        files[path]=encoded(witness);manifest=json.loads(files['manifest.json'])
        for entry in manifest['entries']:entry.update(bytes=len(files[entry['path']]),sha256=sha(files[entry['path']]))
        reseal(manifest);files['manifest.json']=encoded(manifest);changed=repack(files)
    else:
        def change(pack):
            sidecar=pack['controller_decisions'];record=sidecar['records'][0]
            if mutation=='decision_owner':sidecar['decision_owners'][0]=child['id']
            elif mutation=='decision_debt':sidecar['recovery']['inherited_calls']=0
            elif mutation=='typed_action':record['action']='stop'
            else:
                receipt=record['receipt'];receipt.update(status='reserved',response_sha256=None,
                    input_tokens=None,output_tokens=None,micro_usd_charged_or_reserved=2688);reseal(receipt)
            reseal(sidecar)
        changed=reseal_changed_pack(files,child['id'],change)
    archive=tmp_path/'forged-jev.zip';archive.write_bytes(changed)
    with pytest.raises(ValueError):verify(archive)


def test_original_jev_sidecar_remains_portable_without_recovery(jev_recovery_workbench,tmp_path):
    wb,case,path,_,_,_,_=jev_recovery_workbench
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='completed',source
    _,raw=exported(wb,source['id']);archive=tmp_path/'original-jev.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==1
    assert_exact_controller_json_types(contents(raw),source['id'],tmp_path)


def test_terminal_orphan_receipt_keeps_exact_request_when_only_jev_needs_a_new_call(jev_recovery_workbench,tmp_path,monkeypatch):
    wb,case,path,_,groq,typesafe,_=jev_recovery_workbench
    save=wb._save;interrupted=[False]
    def save_then_stop(row,**changes):
        value=save(row,**changes)
        if not interrupted[0] and any(r['status']=='settled' for r in changes.get('typesafe_receipts',{}).values()):
            interrupted[0]=True
            raise InterruptedError('Synthetic interruption after a terminal receipt but before decision persistence')
        return value
    monkeypatch.setattr(wb,'_save',save_then_stop)
    source=completed(wb,wb.submit(jev_request(case,path))['id'])
    assert source['status']=='cancelled' and not source['typesafe_decisions'],source
    assert len(source['controller_attempts'])==len(source['typesafe_receipts'])==1
    assert len(groq)==len(typesafe)==1
    def stop_response(request,**kwargs):
        body=json.loads(request.data);typesafe.append(body)
        choices=body['questions']['next_action']['criteria']
        return BytesIO(json.dumps({'model':body['model'],'answers':{'next_action':{
            'type':'choice','choice':'stop','confidence':1.,'probabilities':{k:float(k=='stop') for k in choices}}},
            'usage':{'input_tokens':100,'output_tokens':10}}).encode())
    wb.typesafe_factory=lambda cfg,**kw:TypesafeClient(cfg,transport=stop_response,**kw)
    wb.provider_factory=lambda *a,**k:pytest.fail('A Jev-only recovery constructed Groq')
    preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'] and preview['orphan_terminal_receipts']==1 and preview['next_provider']=='typesafe',preview
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert child['status']=='completed' and not child['pipeline_attempts'],child
    assert len(groq)==1 and len(typesafe)==2
    _,raw=exported(wb,child['id']);archive=tmp_path/'orphan-receipt.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==2
