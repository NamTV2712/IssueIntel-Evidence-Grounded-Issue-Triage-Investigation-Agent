"""Repeated explicit recovery retains global stages, attempts and ownership."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from issueintel.investigation.workbench import Workbench
from issueintel.investigation.stage_jobs import StageRecoveryJobs
from issueintel.runtime.io import digest
from test_stage_jobs import stage_workbench,source,approval
from test_workbench import completed
from test_planned_pipeline import planned_repository,PlannedModel
from test_focused_retrieval import fixture_repo
from test_handoff import exported,contents,repack
from issueintel.investigation.portable_verify import verify,encoded,sha


def failed_child(fixture,*,failure=2,depth='quick'):
    wb,_,calls,fail_at=fixture;root=source(fixture,depth=depth);fail_at.add(failure)
    preview=wb.stage_recoveries.preview(root['id'])
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id'])
    assert child['status']=='failed' and len(calls)==failure,child
    return root,child


def resume(wb,identifier):
    preview=wb.stage_recoveries.preview(identifier);assert preview['available'],preview
    child=completed(wb,wb.stage_recoveries.submit(identifier,approval(preview))['id'])
    assert child['status']=='completed',child
    return child


def test_failed_recovery_can_continue_without_resetting_attempts_or_copying_stages(stage_workbench):
    wb,model,calls,_=stage_workbench;root,parent=failed_child(stage_workbench)
    before={id:wb._job_path(id).read_bytes() for id in (root['id'],parent['id'])}
    _,records,_,binding=wb.stage_recoveries.source(parent['id'])
    assert binding['schema']=='product_stage_recovery_source_v2' and binding['prior_provider_calls']==2
    assert binding['root_job_id']==root['id'] and [s['job_id'] for s in binding['source_chain']]==[root['id'],parent['id']]
    assert all(s['owner_job_id']==root['id'] for s in binding['stages'])
    child=resume(wb,parent['id']);pack=json.loads(wb.result_bytes(child['id']))
    assert len(calls)==4 and model.generations==1
    assert pack['stage_recovery']['inherited_provider_calls']==2 and pack['stage_recovery']['new_provider_calls']==2
    assert pack['stage_recovery']['max_provider_calls']==4 and pack['recovered_from']['source']==binding
    assert all(wb._job_path(id).read_bytes()==raw for id,raw in before.items())
    assert not set(root['stage_checkpoints'])&set(child['stage_checkpoints'])
    assert {r['job_id'] for r in wb.dossier(child['id'])['runs']}=={root['id'],parent['id'],child['id']}


def test_critic_failure_reuses_generation_owned_by_failed_recovered_parent(stage_workbench):
    wb,model,calls,_=stage_workbench;root,parent=failed_child(stage_workbench,failure=3)
    assert 'seed_generation' in parent['stage_checkpoints'] and model.generations==1
    original=wb.stage_bytes(parent['id'],'seed_generation')
    preview=wb.stage_recoveries.preview(parent['id']);assert preview['next_stage']=='verification' and preview['prior_provider_calls']==3,preview
    child=resume(wb,parent['id']);pack=json.loads(wb.result_bytes(child['id']))
    assert model.generations==1 and len(calls)==4 and child['accounting']['reservations']==1
    assert 'seed_generation' not in child['stage_checkpoints'] and wb.stage_bytes(parent['id'],'seed_generation')==original
    assert {r['owner_job_id'] for r in pack['recovered_from']['source']['stages']}=={root['id'],parent['id']}


def test_each_new_failed_source_consumes_the_same_original_quick_bound(stage_workbench):
    wb,_,calls,fail_at=stage_workbench;root=source(stage_workbench);current=root
    fail_at.update({2,3,4})
    for attempt in range(2,5):
        p=wb.stage_recoveries.preview(current['id']);assert p['available'] and p['prior_provider_calls']==attempt-1
        current=completed(wb,wb.stage_recoveries.submit(current['id'],approval(p))['id'])
        assert current['status']=='failed' and len(calls)==attempt
    assert not wb.stage_recoveries.preview(current['id'])['available'] and len(calls)==4


def test_concurrent_descendant_requests_share_one_intent_without_nested_database_lock(stage_workbench):
    wb,_,calls,_=stage_workbench;_,parent=failed_child(stage_workbench)
    p=wb.stage_recoveries.preview(parent['id'])
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(wb.stage_recoveries.submit,parent['id'],approval(p)) for _ in range(3)]
        ids=[f.result(timeout=15)['id'] for f in futures]
    assert len(set(ids))==1 and completed(wb,ids[0])['status']=='completed' and len(calls)==4


@pytest.mark.parametrize('mutation',['root_attempts','parent_attempts','parent_request','stage_bytes','receipt_owner','missing_intent'])
def test_changed_ancestor_cannot_authorize_descendant_calls(stage_workbench,mutation):
    wb,_,calls,_=stage_workbench;root,parent=failed_child(stage_workbench,failure=3)
    p=wb.stage_recoveries.preview(parent['id']);assert p['available']
    row=json.loads(wb._job_path(root['id'] if mutation=='root_attempts' else parent['id']).read_bytes())
    if mutation in {'root_attempts','parent_attempts'}:row['pipeline_attempts']=[];wb._save(row)
    elif mutation=='parent_request':row['request']['provider']='none';wb._save(row)
    elif mutation=='stage_bytes':
        path=wb.root/row['stage_checkpoints']['seed_generation']['path'];path.write_bytes(path.read_bytes()+b' ')
    elif mutation=='receipt_owner':
        row['usage_receipts']=deepcopy(json.loads(wb._job_path(root['id']).read_bytes())['usage_receipts']);wb._save(row)
    else:
        db=wb.stage_recoveries._db();db.execute('DELETE FROM intents WHERE target_job=?',(parent['id'],));db.commit();db.close()
    count=len(calls);assert not wb.stage_recoveries.preview(parent['id'])['available']
    with pytest.raises((ValueError,KeyError)):wb.stage_recoveries.submit(parent['id'],approval(p))
    assert len(calls)==count


def test_saved_chain_reads_and_duplicate_submit_do_not_load_git_config_or_current_policy(stage_workbench,monkeypatch):
    wb,_,calls,_=stage_workbench;_,parent=failed_child(stage_workbench);p=wb.stage_recoveries.preview(parent['id'])
    child=resume(wb,parent['id']);raw=wb.result_bytes(child['id']);count=len(calls)
    monkeypatch.setattr('issueintel.investigation.stage_jobs.job_policy_sha256',lambda:'0'*64)
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a,**k:pytest.fail('Saved chain read Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved chain loaded config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved chain created provider');wb.stage_recoveries=StageRecoveryJobs(wb)
    assert wb.stage_recoveries.submit(parent['id'],approval(p))['id']==child['id']
    assert wb.result_bytes(child['id'])==raw and wb.report_bytes(child['id']) and len(calls)==count


@pytest.mark.parametrize('depth,failure',[('quick',2),('quick',3),('deep',3),('planned',2)])
def test_chain_handoff_includes_actual_stage_owners_and_verifies_without_project(stage_workbench,tmp_path,monkeypatch,depth,failure):
    wb,_,calls,_=stage_workbench;root,parent=failed_child(stage_workbench,depth=depth,failure=failure)
    child=resume(wb,parent['id']);count=len(calls)
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a,**k:pytest.fail('Handoff used Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Handoff loaded provider config'))
    preview,raw=exported(wb,child['id']);files=contents(raw)
    assert set(preview['included_job_ids'])=={root['id'],parent['id'],child['id']}
    witness=json.loads(files['runs/'+child['id']+'/stage-recovery.json'])
    assert witness['schema']=='product_stage_handoff_v2' and len(witness['source_chain'])==2
    for ref in witness['source']['stages']:
        assert sha(files['runs/'+ref['owner_job_id']+'/stages/'+ref['stage']+'.json'])==ref['sha256']
    archive=tmp_path/'chain.zip';archive.write_bytes(raw)
    program=tmp_path/'verify.py';program.write_bytes(files['verify.py'])
    process=subprocess.run([sys.executable,'-I',str(program),str(archive)],capture_output=True,text=True)
    assert process.returncode==0,process.stdout+process.stderr
    result=verify(archive)
    assert result['runs']==3 and result['model_calls']==0 and result['authorship_verified'] is False and len(calls)==count


@pytest.mark.parametrize('mutation',['ancestor_attempts','ancestor_intent','stage_owner','chain_order','source_usage'])
def test_resealed_outer_archive_cannot_change_chain_ownership_or_attempts(stage_workbench,tmp_path,mutation):
    wb,_,_,_=stage_workbench;_,parent=failed_child(stage_workbench,failure=3)
    child=resume(wb,parent['id']);_,raw=exported(wb,child['id']);files=contents(raw)
    path='runs/'+child['id']+'/stage-recovery.json';witness=json.loads(files[path])
    if mutation=='ancestor_attempts':witness['source_chain'][0]['attempts']=[]
    elif mutation=='ancestor_intent':witness['source_chain'][1]['input_binding']['prior_provider_calls']=0
    elif mutation=='stage_owner':witness['source_chain'][0]['job_id']=parent['id']
    elif mutation=='chain_order':witness['source_chain'].reverse()
    else:witness['source_chain'][0]['usage']['receipts']={}
    files[path]=encoded(witness);manifest=json.loads(files['manifest.json'])
    for entry in manifest['entries']:entry.update(bytes=len(files[entry['path']]),sha256=sha(files[entry['path']]))
    manifest['sha256']=digest({k:v for k,v in manifest.items() if k!='sha256'});files['manifest.json']=encoded(manifest)
    archive=tmp_path/'changed-chain.zip';archive.write_bytes(repack(files))
    with pytest.raises(ValueError):verify(archive)


def test_waiting_after_two_failures_keeps_both_debits_when_answering(stage_workbench,tmp_path):
    from test_planned_continuations import request_for
    from issueintel.investigation.planned_waiting import totals
    wb,_,calls,_=stage_workbench;root,parent=failed_child(stage_workbench,depth='planned')
    recovered=resume(wb,parent['id']);pack=json.loads(wb.result_bytes(recovered['id']))
    assert pack['stop_reason']=='waiting_for_user' and len(calls)==3
    preview=wb.planned_continuations.preview(recovered['id']);assert preview['available'],preview
    assert preview['remaining']['provider_calls']==pack['workflow_plan']['waiting']['remaining']['provider_calls']-2
    answered=completed(wb,wb.planned_continuations.submit(recovered['id'],request_for(preview))['id'])
    assert answered['status']=='completed',answered
    result=json.loads(wb.result_bytes(answered['id']))
    assert result['workflow_plan']['inherited']['counts']['provider_calls']==3
    assert result['workflow_plan']['inherited']['recovery_attempt_debit']==2
    assert totals(result['workflow_plan'])['provider_calls']==len(calls)==5
    preview,raw=exported(wb,answered['id']);archive=tmp_path/'chain-answered.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==4 and set(preview['included_job_ids'])=={root['id'],parent['id'],recovered['id'],answered['id']}
    from test_planned_handoff import reseal_changed_pack
    altered=reseal_changed_pack(contents(raw),answered['id'],lambda p:p['workflow_plan']['inherited'].pop('recovery_attempt_debit'))
    archive.write_bytes(altered)
    with pytest.raises(ValueError,match='inherited root budget'):verify(archive)


def test_partial_answer_descendants_preserve_explicit_recovery_debit_once(stage_workbench):
    from test_planned_continuations import request_for
    from issueintel.investigation.planned_waiting import totals
    wb,_,calls,_=stage_workbench;_,parent=failed_child(stage_workbench,depth='planned');recovered=resume(wb,parent['id'])
    service=wb.planned_continuations
    first=completed(wb,service.submit(recovered['id'],request_for(service.preview(recovered['id']),all_answers=False))['id'])
    assert first['status']=='completed',first
    pack=json.loads(wb.result_bytes(first['id']));assert pack['stop_reason']=='waiting_for_user'
    assert pack['workflow_plan']['inherited']['recovery_attempt_debit']==2
    second=completed(wb,service.submit(first['id'],request_for(service.preview(first['id'])))['id'])
    assert second['status']=='completed',second
    pack=json.loads(wb.result_bytes(second['id']));assert pack['workflow_plan']['inherited']['recovery_attempt_debit']==2
    assert pack['workflow_plan']['inherited']['counts']['provider_calls']==4
    assert totals(pack['workflow_plan'])['provider_calls']==len(calls)==6


@pytest.mark.parametrize('fail_at_call',[3,4,5])
def test_planned_chain_reuses_each_completed_round_before_missing_generation(stage_workbench,planned_repository,monkeypatch,fail_at_call):
    wb,model,calls,fail_at=stage_workbench;case,path=planned_repository;driver=PlannedModel()
    # Round three selects documentation/history gaps under rules v3. Supply
    # actual pinned documentation so that it legitimately adds evidence and
    # reaches generation_3, rather than weakening the planner's waiting gate.
    (path/'notes.md').write_text('validation validate_flag validity: bool conversion is expected.\n',encoding='utf-8')
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=','-C',str(path),*args]).decode().strip()
    git('add','notes.md');git('-c','user.name=Synthetic','-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Synthetic validation notes')
    case=dict(case,revision=git('rev-parse','HEAD'))
    monkeypatch.setattr(type(model),'__call__',lambda self,*a,**k:driver(*a,**k))
    fail_at.add(fail_at_call)
    root=completed(wb,wb.submit({'mode':'local','repository':str(path),'revision':case['revision'],
        'issue_text':case['problem_statement'],'depth':'planned','provider':'groq'})['id']);assert root['status']=='failed'
    p=wb.stage_recoveries.preview(root['id'])
    parent=completed(wb,wb.stage_recoveries.submit(root['id'],approval(p))['id']);assert parent['status']=='failed',parent
    saved={stage:wb.stage_bytes(parent['id'],stage) for stage in parent['stage_checkpoints']}
    generated=driver.generations;p=wb.stage_recoveries.preview(parent['id'])
    assert p['next_stage']=='planned_generation_'+str(fail_at_call-2) and p['prior_provider_calls']==fail_at_call,p
    assert driver.generations==generated
    child=resume(wb,parent['id']);pack=json.loads(wb.result_bytes(child['id']))
    assert driver.generations==4 and len(calls)==7
    assert pack['stage_recovery']['inherited_provider_calls']==fail_at_call and pack['stage_recovery']['max_provider_calls']==9
    assert not set(child['stage_checkpoints'])&set(saved)
    assert all(wb.stage_bytes(parent['id'],stage)==raw for stage,raw in saved.items())


def test_export_failures_cannot_create_unbounded_recovery_ancestry(tmp_path,monkeypatch):
    import issueintel.investigation.workbench as module
    import issueintel.investigation.stage_jobs as jobs
    def fail(*a,**k):raise OSError('Synthetic repeated export failure')
    monkeypatch.setattr(module,'export_pack',fail);monkeypatch.setattr(jobs,'export_pack',fail)
    wb=Workbench(tmp_path,automatic_sync=False,provider_factory=lambda *a,**k:pytest.fail('Unexpected provider'))
    try:
        current=completed(wb,wb.submit({'mode':'example'})['id'])
        for ancestors in range(1,9):
            p=wb.stage_recoveries.preview(current['id']);assert p['available'] and not p['requires_api'],p
            assert len(p['source_job_ids'])==ancestors
            current=completed(wb,wb.stage_recoveries.submit(current['id'],approval(p))['id'])
            assert current['status']=='failed' and current['pipeline_attempts']==[]
        assert not wb.stage_recoveries.preview(current['id'])['available'] and len(wb.history())==9
    finally:wb.close()


def test_planned_recovery_keeps_the_narrower_seed_route_bound_across_failures(stage_workbench,fixture_repo):
    wb,_,calls,fail_at=stage_workbench;case,path=fixture_repo
    request={'mode':'local','repository':str(path),'revision':case['revision'],'provider':'groq','depth':'planned',
        'issue_text':'Observed parser.parse_list crashes. Expected [] for None. Reproduction: parse_list(None). Python 3.12 on Windows.'}
    root=completed(wb,wb.submit(request)['id']);assert root['status']=='failed' and len(calls)==1
    p=wb.stage_recoveries.preview(root['id']);assert p['max_provider_calls']==9
    fail_at.update({3,4,5,6,7})
    current=completed(wb,wb.stage_recoveries.submit(root['id'],approval(p))['id'])
    assert current['status']=='failed' and len(calls)==3,current
    for attempt in range(4,8):
        p=wb.stage_recoveries.preview(current['id'])
        assert p['available'] and p['max_provider_calls']==7 and p['next_stage']=='verification',p
        current=completed(wb,wb.stage_recoveries.submit(current['id'],approval(p))['id'])
        assert current['status']=='failed' and len(calls)==attempt,current
    exhausted=wb.stage_recoveries.preview(current['id'])
    assert not exhausted['available'] and exhausted['reason']=='original_call_budget_exhausted' and exhausted['model_calls']==0
    assert exhausted['prior_provider_calls']==exhausted['max_provider_calls']==7 and len(calls)==7


def test_effective_route_bound_is_required_in_new_saved_recovery_telemetry(stage_workbench,tmp_path):
    from test_planned_handoff import reseal_changed_pack
    wb,_,_,_=stage_workbench;root=source(stage_workbench,depth='planned');child=resume(wb,root['id'])
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['stage_recovery']['max_provider_calls']==9 and pack['stage_recovery']['effective_max_provider_calls']==7
    _,raw=exported(wb,child['id']);archive=tmp_path/'effective.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==2
    for mutation in [lambda p:p['stage_recovery'].pop('effective_max_provider_calls'),
                     lambda p:p['stage_recovery'].update(effective_max_provider_calls=9)]:
        archive.write_bytes(reseal_changed_pack(contents(raw),child['id'],mutation))
        with pytest.raises(ValueError,match='effective recovery route budget'):verify(archive)


def test_zero_api_export_can_be_recovered_again_without_new_provider_or_copied_files(tmp_path,monkeypatch):
    import issueintel.investigation.workbench as module
    import issueintel.investigation.stage_jobs as jobs
    original=module.export_pack;exports=[]
    def fail_twice(*args,**kwargs):
        exports.append(1)
        if len(exports)<=2:raise OSError('Synthetic export interruption')
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'export_pack',fail_twice);monkeypatch.setattr(jobs,'export_pack',fail_twice)
    monkeypatch.setattr(module.RuntimeConfig,'load',lambda *a:pytest.fail('Offline recovery loaded API config'))
    wb=Workbench(tmp_path,automatic_sync=False,provider_factory=lambda *a,**k:pytest.fail('Unexpected provider'))
    try:
        root=completed(wb,wb.submit({'mode':'example'})['id']);assert root['status']=='failed'
        p=wb.stage_recoveries.preview(root['id']);assert p['available'] and not p['requires_api']
        parent=completed(wb,wb.stage_recoveries.submit(root['id'],approval(p))['id']);assert parent['status']=='failed'
        child=resume(wb,parent['id']);pack=json.loads(wb.result_bytes(child['id']))
        assert pack['stage_recovery']['inherited_provider_calls']==pack['stage_recovery']['new_provider_calls']==0
        assert not child.get('stage_checkpoints') and child['accounting']['reservations']==0
        _,raw=exported(wb,child['id']);archive=tmp_path/'zero-chain.zip';archive.write_bytes(raw)
        assert verify(archive)['runs']==3
    finally:wb.close()
