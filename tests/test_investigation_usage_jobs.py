"""Saved, synthetic owner inventories: no live API, Git or global ledger reads."""
from copy import deepcopy
from hashlib import sha256
from itertools import count
import json
import sqlite3
from types import SimpleNamespace

import pytest

from issueintel.investigation.investigation_usage_jobs import InvestigationUsageJobs, _Capture
from issueintel.runtime.io import canonical_json, digest, write_json
from test_stage_jobs import stage_workbench, source as failed_stage_source, approval as stage_approval
from test_product_finalization import finalization_source, approval as finalization_approval
from test_planned_pipeline import planned_repository
from test_typesafe_stage_recovery import jev_recovery_workbench, jev_request, settled_orphan
from test_workbench import completed, local_server, http


def receipt(identifier, *, status='settled', cost=3):
    return {'reservation_id':f'{identifier:032x}','created_at':'2026-10-22T00:00:00+00:00',
        'model':'openai/gpt-oss-120b','input_bound':100,'output_bound':100,
        'tokens_charged_or_reserved':15 if status=='settled' else 200,
        'micro_usd_charged_or_reserved':cost,'status':status,'price_date':'2026-10-20',
        'observed_input_tokens':10 if status=='settled' else None,
        'observed_output_tokens':5 if status=='settled' else None,'stage':'seed_generation'}


class Saved:
    def __init__(self, root):
        self.root=root;self.directory=root/'artifacts/demo';self.jobs=self.directory/'jobs'
        self.jobs.mkdir(parents=True);self.ids=count(1)
        self.service=InvestigationUsageJobs(self)

    def _job_path(self, identifier):
        return self.jobs/(identifier+'.json')

    def save(self, row):
        write_json(self._job_path(row['id']),row)
        return row

    def row(self, *, mode='example', provider='groq', status='completed', request=None, cost=3, pack=None):
        number=next(self.ids);identifier=f'{number:024x}'
        request=dict(request or {},mode=mode,provider=provider,depth=(request or {}).get('depth','quick'))
        row={'id':identifier,'mode':mode,'provider':provider,'depth':request['depth'],
             'status':status,'request':request,'created_at':'2026-10-22T00:00:00+00:00'}
        if provider=='groq':
            item=receipt(number,cost=cost)
            row.update(usage_tracking=True,usage_receipts={item['reservation_id']:item},
                pipeline_context_sha256='1'*64,pipeline_attempts=[{
                    'schema':'product_pipeline_call_attempt_v1','context_sha256':'1'*64,
                    'stage':'seed_generation','provider_calls':1,'max_provider_calls':4}])
        if pack is not False:
            self.pack(row,pack or {'schema_version':1,'private_input':'Never export this issue text.',
                'case':{'case_id':'synthetic:parse-list','repository':'synthetic/example','revision':'4'*40,
                        'problem_statement':'parser.parse_list fails on None input. Inspect how missing values are handled.',
                        'issue_created_at':None}})
        return self.save(row)

    def pack(self,row,pack):
        path=self.directory/'runs'/row['id']/'pack.json';write_json(path,pack)
        row['result']={'pack.json':{'path':path.relative_to(self.root).as_posix(),'sha256':sha256(path.read_bytes()).hexdigest()}}
        return row['result']['pack.json']['sha256']

    def fork(self,parent,*,cost=5,status='completed'):
        source_sha=parent['result']['pack.json']['sha256']
        request={'parent_job_id':parent['id'],'additional_context':'Private synthetic follow-up.',
                 'provider':'groq','depth':'quick'}
        pack={'followup':{'parent_job_id':parent['id'],'parent_pack_sha256':source_sha}}
        return self.row(mode='followup',cost=cost,status=status,request=request,pack=pack)

    def sql(self,name,statement,params=()):
        db=sqlite3.connect(self.directory/name)
        try:
            with db:db.execute(statement,params)
        finally:db.close()

    def sidecar(self,source,mode='check_plan',*,status='completed',cost=7):
        names={'adaptive_questions':('adaptive_questions.sqlite','generate_questions_with_api_cost','questions'),
               'check_plan':('check_plans.sqlite','generate_check_plan_with_api_cost','checks'),
               'claim_review':('claim_reviews.sqlite','generate_claim_review_with_api_cost','claims')}
        name,confirmation,depth=names[mode]
        row=self.row(mode=mode,status=status,cost=cost,pack=False,request={
            'source_job_id':source['id'],'source_pack_sha256':source['result']['pack.json']['sha256'],
            'confirmation':confirmation,'depth':depth})
        # These runners currently have captured receipts but no attempt journal.
        row.pop('pipeline_attempts');self.save(row)
        self.sql(name,'CREATE TABLE IF NOT EXISTS intents (source_job TEXT,source_sha TEXT,target_job TEXT)')
        self.sql(name,'INSERT INTO intents VALUES (?,?,?)',
                 (source['id'],source['result']['pack.json']['sha256'],row['id']))
        return row


@pytest.fixture
def saved(tmp_path):
    return Saved(tmp_path)


def test_selected_chain_excludes_independent_same_issue_root_and_sibling(saved):
    root=saved.row(cost=3);child=saved.fork(root,cost=5)
    saved.fork(root,cost=101);saved.row(cost=1009)
    bundle=saved.service.bundle(child['id'])
    assert set(bundle['lineage']['job_ids'])=={root['id'],child['id']}
    assert bundle['current']['known_micro_usd_charged_or_reserved']==5
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved']==8
    serialized=canonical_json(bundle)
    assert 'Never export this issue text' not in serialized
    assert 'Private synthetic follow-up' not in serialized and str(saved.root) not in serialized
    assert saved.service.capture(child['id'])==saved.service.capture(child['id'])


@pytest.mark.parametrize('mode',['adaptive_questions','check_plan','claim_review'])
@pytest.mark.parametrize('status',['failed','cancelled','interrupted','completed'])
def test_sidecars_use_owned_exact_source_intents_and_preserve_terminal_costs(saved,mode,status):
    root=saved.row(cost=3);sidecar=saved.sidecar(root,mode,status=status,cost=7)
    unrelated=saved.row(cost=101);saved.sidecar(unrelated,mode,cost=1009)
    bundle=saved.service.bundle(root['id'])
    assert set(bundle['lineage']['job_ids'])=={root['id'],sidecar['id']}
    assert bundle['lineage']['known_micro_usd_charged_or_reserved']==10
    assert bundle['current']['complete_micro_usd_charged_or_reserved']==3
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert 'attempt_count_unknown' in bundle['lineage']['providers']['groq']['gap_codes']
    assert next(node for node in bundle['snapshot']['nodes'] if node['job_id']==sidecar['id'])['job_status']==status


@pytest.mark.parametrize('missing',['registry','owner','source_hash'])
def test_broken_sidecar_proof_keeps_partial_known_sum_and_gap(saved,missing):
    root=saved.row(cost=3);sidecar=saved.sidecar(root)
    if missing=='registry':(saved.directory/'check_plans.sqlite').unlink()
    elif missing=='owner':saved._job_path(sidecar['id']).unlink()
    else:
        sidecar['request']['source_pack_sha256']='f'*64;saved.save(sidecar)
    bundle=saved.service.bundle(root['id'])
    assert bundle['lineage']['known_micro_usd_charged_or_reserved']==3
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert any(g['code']=='sidecar_source_unavailable' for g in bundle['gaps'])
    if missing=='registry':assert not (saved.directory/'check_plans.sqlite').exists()


def test_corrupt_sidecar_authority_and_missing_registry_cannot_hide_named_source_cost(saved):
    root=saved.row();sidecar=saved.sidecar(root);(saved.directory/'check_plans.sqlite').unlink()
    sidecar['mode']=sidecar['request']['mode']='example';saved.save(sidecar)
    bundle=saved.service.bundle(root['id'])
    assert bundle['lineage']['known_micro_usd_charged_or_reserved']==3
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert any(g['code']=='sidecar_source_unavailable' for g in bundle['gaps'])


def test_missing_ancestor_does_not_hide_current_receipt_or_invent_zero_history(saved):
    root=saved.row(cost=3);child=saved.fork(root,cost=5)
    saved._job_path(root['id']).unlink()
    bundle=saved.service.bundle(child['id'])
    assert bundle['current']['known_micro_usd_charged_or_reserved']==5
    assert bundle['lineage']['known_micro_usd_charged_or_reserved']==5
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert any(g['code']=='lineage_source_unavailable' for g in bundle['gaps'])


def test_source_hash_tampering_is_not_repaired_or_hidden(saved):
    root=saved.row();child=saved.fork(root)
    path=saved.root/root['result']['pack.json']['path'];original=path.read_bytes();path.write_bytes(original+b' ')
    bundle=saved.service.bundle(child['id'])
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert set(bundle['lineage']['job_ids'])=={child['id']}
    assert path.read_bytes()==original+b' '


def test_legacy_receipts_have_known_subtotal_with_unknown_attempt_inventory(saved):
    root=saved.row(cost=13);root.pop('pipeline_attempts');root.pop('usage_tracking');saved.save(root)
    bundle=saved.service.bundle(root['id'])
    assert bundle['current']['known_micro_usd_charged_or_reserved']==13
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] is None
    assert {'attempt_count_unknown','tracking_unavailable'}<=set(bundle['current']['providers']['groq']['gap_codes'])


def test_nonpublic_receipt_fields_are_never_exported_and_break_completeness(saved):
    root=saved.row();next(iter(root['usage_receipts'].values()))['api_key']='synthetic-never-publish';saved.save(root)
    bundle=saved.service.bundle(root['id'])
    assert 'synthetic-never-publish' not in canonical_json(bundle)
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] is None
    assert any(g['code']=='receipt_public_fields_invalid' for g in bundle['gaps'])


def test_malformed_attempt_inventory_does_not_manufacture_complete_total(saved):
    root=saved.row();root['pipeline_attempts'][0]['provider_calls']=True;saved.save(root)
    bundle=saved.service.bundle(root['id'])
    assert bundle['current']['known_micro_usd_charged_or_reserved']==3
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] is None
    assert any(g['code']=='attempt_inventory_invalid' for g in bundle['gaps'])


def controller_inventory(saved,attempt_slots,receipt_slots,*,prior_calls=0):
    """Native, independently sealed receipts on synthetic saved controller slots."""
    from test_investigation_usage import _typesafe
    row=saved.row()
    row['controller_attempts']=[{'schema':'product_typesafe_call_attempt_v1',
        'context_sha256':row['pipeline_context_sha256'],'stage':'planned_decision_'+str(slot),
        'controller_calls':prior_calls+offset,'max_controller_calls':4,
        'decision_index':slot,'owner_job_id':row['id']}
        for offset,slot in enumerate(attempt_slots,1)]
    row['typesafe_receipts']={}
    for offset,slot in enumerate(receipt_slots,100):
        item=_typesafe(offset,row['id'])
        item['binding']['decision_index']=slot
        item['sha256']=digest({key:value for key,value in item.items() if key!='sha256'})
        row['typesafe_receipts'][item['reservation_id']]=item
    return saved.save(row)


@pytest.mark.parametrize('attempt_slots,receipt_slots',[
    ([1],[2]),                 # A valid receipt belongs to an unattempted decision.
    ([1,2],[1,1]),             # Different reservations claim the same attempted slot.
    ([1,1],[1,2]),             # The attempts themselves repeat a decision slot.
])
def test_controller_slot_mismatch_keeps_known_receipts_but_complete_total_unknown(saved,attempt_slots,receipt_slots):
    from issueintel.runtime.typesafe import validate_receipt
    row=controller_inventory(saved,attempt_slots,receipt_slots)
    for item in row['typesafe_receipts'].values():validate_receipt(item)
    bundle=saved.service.bundle(row['id'])
    summary=bundle['current']['providers']['typesafe']
    assert summary['known_micro_usd_charged_or_reserved']==42*len(receipt_slots)
    assert summary['reservations']==len(receipt_slots)
    assert summary['complete_micro_usd_charged_or_reserved'] is None
    assert summary['attempted_calls'] is None
    assert 'attempt_inventory_invalid' in summary['gap_codes']


def test_controller_accounting_accepts_unique_absolute_slots_after_recovery(saved):
    row=controller_inventory(saved,[3,4],[3,4],prior_calls=2)
    # A recovery owner starts at the inherited absolute decision/call count,
    # rather than renumbering its local calls and receipt bindings from one.
    reader=_Capture(saved,row['id'])
    recovered=dict(row,mode='stage_resume')
    assert reader.attempt_count(recovered,'typesafe')==2 and not reader.gaps
    bundle=saved.service.bundle(row['id'])
    summary=bundle['current']['providers']['typesafe']
    assert summary['attempted_calls']==2 and summary['inventory_complete']
    assert summary['reservations']==2 and summary['complete_micro_usd_charged_or_reserved']==84


def test_cycle_is_bounded_and_keeps_missing_parent_uncertainty(saved):
    root=saved.row(mode='followup',pack=False,request={'parent_job_id':'0'*24,'additional_context':'Private.'})
    root['request']['parent_job_id']=root['id'];saved.save(root)
    bundle=saved.service.bundle(root['id'])
    assert len(bundle['snapshot']['nodes'])==1 and not bundle['snapshot']['edges']
    assert any(g['code']=='lineage_cycle' for g in bundle['gaps'])
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None


def test_snapshot_retries_changed_receipt_instead_of_mixing_states(saved,monkeypatch):
    root=saved.row(cost=3);stable=_Capture.stable;attempts=[]
    def changing(reader):
        attempts.append(1)
        if len(attempts)==1:
            next(iter(root['usage_receipts'].values()))['micro_usd_charged_or_reserved']=11;saved.save(root)
        return stable(reader)
    monkeypatch.setattr(_Capture,'stable',changing)
    bundle=saved.service.bundle(root['id'])
    assert len(attempts)==2 and bundle['current']['complete_micro_usd_charged_or_reserved']==11


def test_snapshot_fails_after_bounded_concurrent_changes(saved,monkeypatch):
    root=saved.row();attempts=[]
    def changing(reader):attempts.append(1);return False
    monkeypatch.setattr(_Capture,'stable',changing)
    with pytest.raises(ValueError,match='changed during'):saved.service.capture(root['id'])
    assert len(attempts)==3


def test_optional_registry_absence_does_not_create_files_or_invent_unknown_spend(saved):
    root=saved.row();before=set(saved.directory.iterdir())
    assert saved.service.bundle(root['id'])['lineage']['inventory_complete']
    assert set(saved.directory.iterdir())==before


def test_intent_queries_are_read_only_and_never_open_global_provider_ledger(saved,monkeypatch):
    root=saved.row();saved.sidecar(root)
    connect=sqlite3.connect;opened=[]
    def readonly(database,*args,**kwargs):
        assert kwargs.get('uri') is True and str(database).endswith('?mode=ro')
        opened.append(database)
        db=connect(database,*args,**kwargs)
        def authorize(action,*unused):
            assert action not in {sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,
                                  sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_TRANSACTION}
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        return db
    monkeypatch.setattr('issueintel.investigation.investigation_usage_jobs.sqlite3.connect',readonly)
    assert saved.service.bundle(root['id'])['lineage']['known_micro_usd_charged_or_reserved']==10
    assert opened and all('check_plans.sqlite' in p for p in opened)


def test_directory_discovery_bound_does_not_claim_complete_inventory(saved,monkeypatch):
    root=saved.row();saved.row();saved.row()
    monkeypatch.setattr('issueintel.investigation.investigation_usage_jobs.MAX_SCAN_JOBS',2)
    bundle=saved.service.bundle(root['id'])
    assert bundle['lineage']['known_micro_usd_charged_or_reserved']==3
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert any(g['code']=='sidecar_discovery_bound' for g in bundle['gaps'])


def test_issue_pair_bill_attaches_only_to_left_source_not_candidate_history(saved):
    left=saved.row(cost=3);right=saved.row(cost=101)
    source_sha=left['result']['pack.json']['sha256'];pair_sha='a'*64;policy_sha='b'*64
    request={'source_job_id':left['id'],'source_pack_sha256':source_sha,
        'candidate':{'kind':'investigation','job_id':right['id'],'pack_sha256':right['result']['pack.json']['sha256']},
        'comparison_source_sha256':pair_sha,'policy_sha256':policy_sha,
        'confirmation':'compare_issue_pair_with_api_cost','depth':'relation'}
    relation=saved.row(mode='issue_relation',request=request,pack=False,status='failed',cost=7)
    relation.pop('pipeline_attempts')
    relation['relation_attempts']=[{'schema':'issue_relation_attempt_v1','owner_job_id':relation['id'],
        'source_sha256':pair_sha,'policy_sha256':policy_sha,'call_index':1,'max_calls':2}]
    relation['relation_receipt_owners']={key:{'owner_job_id':relation['id'],'call_index':1,'source_sha256':pair_sha}
                                          for key in relation['usage_receipts']}
    saved.save(relation)
    witness={'schema':'issue_relation_inputs_v1','owner_job_id':relation['id'],'request':relation['request'],
        'policy_sha256':policy_sha,'source':{'left':{'reference':{'job_id':left['id'],'pack_sha256':source_sha}}},
        'left':{'private_input':'Private comparison issue text.'},'right':{'job_id':right['id']}}
    witness['sha256']=digest(witness)
    saved.sql('issue_relations.sqlite','CREATE TABLE intents (pair_sha TEXT,source_job TEXT,source_sha TEXT,target_job TEXT,request BLOB,inputs BLOB)')
    saved.sql('issue_relations.sqlite','INSERT INTO intents VALUES (?,?,?,?,?,?)',
              (pair_sha,left['id'],source_sha,relation['id'],canonical_json(relation['request']),canonical_json(witness)))
    bundle=saved.service.bundle(left['id'])
    assert set(bundle['lineage']['job_ids'])=={left['id'],relation['id']}
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved']==10
    assert 'Private comparison issue text' not in canonical_json(bundle)
    other=saved.service.bundle(right['id'])
    assert other['lineage']['job_ids']==[right['id']]
    assert other['lineage']['complete_micro_usd_charged_or_reserved']==101


def test_new_sidecar_during_capture_retries_directory_and_registry_snapshot(saved,monkeypatch):
    root=saved.row();stable=_Capture.stable;calls=[]
    def change(reader):
        calls.append(1)
        if len(calls)==1:saved.sidecar(root,cost=7)
        return stable(reader)
    monkeypatch.setattr(_Capture,'stable',change)
    bundle=saved.service.bundle(root['id'])
    assert len(calls)==2 and bundle['lineage']['known_micro_usd_charged_or_reserved']==10


def test_unrelated_job_progress_does_not_invalidate_selected_accounting_snapshot(saved,monkeypatch):
    root=saved.row();unrelated=saved.row(status='running',cost=101);stable=_Capture.stable;calls=[]
    def progress(reader):
        calls.append(1);unrelated['stage']='unrelated_progress_'+str(len(calls));saved.save(unrelated)
        return stable(reader)
    monkeypatch.setattr(_Capture,'stable',progress)
    bundle=saved.service.bundle(root['id'])
    assert len(calls)==1 and bundle['lineage']['complete_micro_usd_charged_or_reserved']==3


def deny_resources(wb,monkeypatch):
    def denied(*args,**kwargs):pytest.fail('Saved cost read used provider, Git, prices, configuration or global ledger')
    wb.provider_factory=denied;wb.typesafe_factory=denied
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',denied)
    monkeypatch.setattr('issueintel.runtime.typesafe.TypesafeConfig.load',denied)
    monkeypatch.setattr('issueintel.runtime.budget.BudgetLedger.receipt',denied)
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',denied)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',denied)


def test_real_recovery_owner_receipts_remain_once_and_reads_need_no_resources(stage_workbench,monkeypatch):
    wb,_,calls,_=stage_workbench;root=failed_stage_source(stage_workbench)
    preview=wb.stage_recoveries.preview(root['id'])
    child=completed(wb,wb.stage_recoveries.submit(root['id'],stage_approval(preview))['id'])
    assert child['status']=='completed'
    expected=sum(r['micro_usd_charged_or_reserved'] for owner in (root['id'],child['id'])
                 for r in json.loads(wb._job_path(owner).read_bytes())['usage_receipts'].values())
    count_before=len(calls);deny_resources(wb,monkeypatch)
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved']==expected
    assert bundle['lineage']['providers']['groq']['retained_bounds']==1
    assert len(calls)==count_before


@pytest.mark.parametrize('keep_parent_fields',[False,True])
def test_actual_followup_cannot_be_laundered_into_original_root_to_hide_parent_cost(stage_workbench,keep_parent_fields):
    wb,_,calls,failures=stage_workbench;failures.clear()
    root=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id']);assert root['status']=='completed'
    child=completed(wb,wb.submit({'mode':'followup','provider':'groq','parent_job_id':root['id'],
        'additional_context':'The same missing input fails in a second caller.'})['id'])
    assert child['status']=='completed' and len(calls)==4
    row=json.loads(wb._job_path(child['id']).read_bytes());row['mode']='example'
    row['request']=dict(row['request'],mode='example') if keep_parent_fields else {'mode':'example','provider':'groq','depth':'quick'}
    write_json(wb._job_path(child['id']),row)
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['current']['known_micro_usd_charged_or_reserved']>0
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert any(g['code']=='root_authority_unverified' for g in bundle['gaps'])


def test_real_finalization_has_zero_current_and_retains_failed_parent_history(finalization_source,monkeypatch):
    wb,service,source,provider=finalization_source
    child=completed(wb,service.submit(source['id'],finalization_approval(service.preview(source['id'])))['id'])
    deny_resources(wb,monkeypatch)
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['current']['complete_micro_usd_charged_or_reserved']==0
    assert set(bundle['lineage']['job_ids'])=={source['id'],child['id']}
    # This synthetic provider does not publish receipts; parent is unknown.
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert bundle['lineage']['providers']['groq']['applicable']
    assert len(provider.calls)==2


def test_real_finalization_displays_paid_parent_receipts_with_zero_new_cost(stage_workbench,monkeypatch):
    import issueintel.investigation.workbench as module
    wb,_,calls,failures=stage_workbench;failures.clear();export=module.export_pack
    def fail_export(pack,directory):
        export(pack,directory)
        raise OSError('Synthetic export failure after both paid passes')
    monkeypatch.setattr(module,'export_pack',fail_export)
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
    assert source['status']=='failed' and len(calls)==2
    preview=wb.finalization_preview(source['id']);assert preview['available'],preview
    child=completed(wb,wb.submit_finalization(source['id'],finalization_approval(preview))['id'])
    assert child['status']=='completed'
    expected=sum(item['micro_usd_charged_or_reserved'] for item in source['accounting']['receipts'])
    deny_resources(wb,monkeypatch)
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['current']['complete_micro_usd_charged_or_reserved']==0
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved']==expected and expected>0
    assert bundle['lineage']['providers']['groq']['reservations']==2 and len(calls)==2


def test_real_reserved_jev_receipt_is_visible_even_when_recovery_is_blocked(jev_recovery_workbench,monkeypatch):
    from issueintel.runtime.typesafe import _Ledger,TypesafeError
    wb,case,path,_,groq,typesafe,_=jev_recovery_workbench
    def unavailable(*args,**kwargs):raise TypesafeError('ledger_unavailable')
    monkeypatch.setattr(_Ledger,'settle',unavailable)
    source=completed(wb,wb.submit(jev_request(case,path))['id'])
    assert source['status']=='failed' and len(typesafe)==len(groq)==1
    deny_resources(wb,monkeypatch)
    bundle=wb.investigation_costs.bundle(source['id'])
    jev=bundle['current']['providers']['typesafe']
    assert jev['known_micro_usd_charged_or_reserved']==2688
    assert jev['retained_bounds']==1 and jev['inventory_complete'] and not jev['all_usage_settled']


def test_real_terminal_jev_orphan_and_recovery_are_counted_under_original_owners(jev_recovery_workbench,monkeypatch):
    wb,_,_,_,groq,typesafe,failures=jev_recovery_workbench
    source=settled_orphan(jev_recovery_workbench,monkeypatch)
    preview=wb.stage_recoveries.preview(source['id']);assert preview['available'],preview
    failures['action']='verify'
    child=completed(wb,wb.stage_recoveries.submit(source['id'],stage_approval(preview))['id'])
    assert child['status']=='completed' and len(typesafe)==2
    expected=sum(item['micro_usd_charged_or_reserved'] for owner in (source['id'],child['id'])
                 for item in json.loads(wb._job_path(owner).read_bytes())['typesafe_receipts'].values())
    before=(len(groq),len(typesafe));deny_resources(wb,monkeypatch)
    bundle=wb.investigation_costs.bundle(child['id'])
    assert bundle['lineage']['providers']['typesafe']['complete_micro_usd_charged_or_reserved']==expected
    assert bundle['lineage']['providers']['typesafe']['reservations']==2
    assert bundle['current']['providers']['typesafe']['reservations']==1
    assert before==(len(groq),len(typesafe))


def test_http_usage_chain_and_export_are_session_guarded_and_do_not_repeat_work(tmp_path):
    with local_server(tmp_path) as server:
        job=completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])
        route='/api/jobs/'+job['id']+'/investigation-usage'
        assert http(server,'GET',route)[0]==403
        headers={'X-IssueIntel-Token':server.session_token}
        status,_,raw=http(server,'GET',route,headers=headers)
        bundle=json.loads(raw)
        assert status==200 and bundle['source']['selected_job_id']==job['id']
        assert bundle['lineage']['complete_micro_usd_charged_or_reserved']==0
        for suffix in ('.json','.md'):
            status,returned,export=http(server,'GET',route+suffix,headers=headers)
            assert status==200 and export
            assert returned['X-IssueIntel-Accounting-SHA256']==bundle['sha256']
