"""A failed answer segment retains its parent's authority and spent budget."""
import json
import subprocess
import sys
import threading

import pytest

from issueintel.investigation.portable_verify import verify
from issueintel.investigation.planned_waiting import totals,recovery_call_debit
from test_planned_continuations import waiting_workbench,request_for
from test_planned_pipeline import planned_repository
from test_stage_jobs import approval,stage_workbench
from test_workbench import completed
from test_handoff import exported,contents,repack
from issueintel.runtime.io import digest
from issueintel.investigation.portable_verify import encoded,sha


def failed_answer(fixture,*,critic=False,partial=False):
    wb,service,parent,calls,fail=fixture
    if critic:
        def reject(body,answer):
            if 'reviews' in answer:raise RuntimeError('Synthetic answer critic interruption')
        fail[0]=reject
    else:fail[0]=True
    request=request_for(service.preview(parent['id']),all_answers=not partial)
    child=completed(wb,service.submit(parent['id'],request)['id'])
    assert child['status']=='failed',child
    fail[0]=False
    return child,request


@pytest.mark.parametrize('critic',[False,True])
def test_failed_answer_segment_recovers_only_missing_stages_and_keeps_parent_budget(waiting_workbench,tmp_path,critic):
    wb,service,parent,calls,_=waiting_workbench
    source,request=failed_answer(waiting_workbench,critic=critic)
    before=wb._job_path(source['id']).read_bytes();parent_raw=wb.result_bytes(parent['id']);count=len(calls)
    preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'] and preview['budget_scope']=='planned_continuation_remaining',preview
    assert preview['root_max_provider_calls']==9 and preview['inherited_root_provider_calls']==1
    assert preview['max_provider_calls']==8 and preview['prior_provider_calls']==(2 if critic else 1)
    if critic:assert preview['next_stage']=='verification'
    result=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert result['status']=='completed',result
    pack=json.loads(wb.result_bytes(result['id']))
    assert pack['continued_from']['parent_job_id']==parent['id']
    assert pack['workflow_plan']['inherited']['counts']['provider_calls']==1
    assert pack['stage_recovery']['effective_max_provider_calls']==8
    assert pack['recovered_from']['source']['schema']=='product_stage_recovery_source_v3'
    assert len(calls)-count==(1 if critic else 2)
    assert wb._job_path(source['id']).read_bytes()==before and wb.result_bytes(parent['id'])==parent_raw
    assert service.submit(parent['id'],request)['id']==source['id']
    summary,raw=exported(wb,result['id']);archive=tmp_path/'answered-recovery.zip';archive.write_bytes(raw)
    assert set(summary['included_job_ids'])=={parent['id'],source['id'],result['id']}
    witness=json.loads(contents(raw)['runs/'+result['id']+'/stage-recovery.json'])
    assert witness['schema']=='product_stage_handoff_v3'
    assert verify(archive)['runs']==3


def test_recovered_partial_answers_can_continue_with_failed_attempt_charged_once(waiting_workbench,tmp_path):
    wb,service,parent,calls,_=waiting_workbench
    source,_=failed_answer(waiting_workbench,partial=True)
    p=wb.stage_recoveries.preview(source['id'])
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['stop_reason']=='waiting_for_user' and recovery_call_debit(pack)==1
    preview=service.preview(child['id']);assert preview['available'],preview
    assert preview['remaining']['provider_calls']==9-len(calls)
    answer=completed(wb,service.submit(child['id'],request_for(preview))['id'])
    assert answer['status']=='completed',answer
    final=json.loads(wb.result_bytes(answer['id']))
    assert totals(final['workflow_plan'])['provider_calls']==len(calls)
    _,raw=exported(wb,answer['id']);archive=tmp_path/'partial-answer-chain.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==4


def test_failed_recovery_of_an_answer_can_resume_again_without_seed_regeneration(waiting_workbench,tmp_path):
    wb,_,parent,calls,fail=waiting_workbench
    source,_=failed_answer(waiting_workbench)
    def critic_failure(body,answer):
        if 'reviews' in answer:raise RuntimeError('Synthetic descendant critic failure')
    fail[0]=critic_failure
    p=wb.stage_recoveries.preview(source['id'])
    failed=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert failed['status']=='failed' and 'seed_generation' in failed['stage_checkpoints'],failed
    fail[0]=False;count=len(calls)
    p=wb.stage_recoveries.preview(failed['id']);assert p['next_stage']=='verification'
    child=completed(wb,wb.stage_recoveries.submit(failed['id'],approval(p))['id'])
    assert child['status']=='completed' and len(calls)==count+1,child
    assert json.loads(wb.result_bytes(child['id']))['workflow_plan']['inherited']['counts']['provider_calls']==1
    _,raw=exported(wb,child['id']);files=contents(raw);archive=tmp_path/'recovered-answer-chain.zip';archive.write_bytes(raw)
    program=tmp_path/'verify.py';program.write_bytes(files['verify.py'])
    result=subprocess.run([sys.executable,'-I',str(program),str(archive)],text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert verify(archive)['runs']==4


def test_repeated_answer_failures_exhaust_only_the_remaining_root_budget(waiting_workbench):
    wb,_,_,calls,fail=waiting_workbench
    source,_=failed_answer(waiting_workbench);fail[0]=True
    for used in range(2,9):
        p=wb.stage_recoveries.preview(source['id'])
        assert p['available'] and p['max_provider_calls']==8 and p['prior_provider_calls']==used-1,p
        source=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
        assert source['status']=='failed' and len(calls)==used+1,source
    p=wb.stage_recoveries.preview(source['id'])
    assert p['available'] is False and p['reason']=='original_call_budget_exhausted',p
    assert p['prior_provider_calls']==p['max_provider_calls']==8 and len(calls)==9
    assert p['inherited_root_provider_calls']==1 and p['root_max_provider_calls']==9


@pytest.mark.parametrize('mutation',['answer','intent','parent_bytes','parent_receipts','missing_attempts','policy'])
def test_changed_answer_parent_or_intent_denies_new_calls(waiting_workbench,monkeypatch,mutation):
    wb,_,parent,calls,_=waiting_workbench;source,_=failed_answer(waiting_workbench)
    p=wb.stage_recoveries.preview(source['id']);assert p['available']
    if mutation=='intent':
        db=wb.planned_continuations._db();db.execute('DELETE FROM intents WHERE target_job=?',(source['id'],));db.commit();db.close()
    elif mutation=='parent_bytes':
        row=json.loads(wb._job_path(parent['id']).read_bytes());path=wb.root/row['result']['pack.json']['path']
        path.write_bytes(path.read_bytes()+b' ')
    elif mutation=='policy':monkeypatch.setattr('issueintel.investigation.stage_jobs.job_policy_sha256',lambda:'0'*64)
    else:
        row=json.loads(wb._job_path(parent['id'] if mutation=='parent_receipts' else source['id']).read_bytes())
        if mutation=='answer':row['request']['answers'][0]['answer']='Changed after consent'
        elif mutation=='parent_receipts':row['usage_receipts']={}
        else:row.pop('pipeline_attempts')
        wb._save(row)
    count=len(calls);assert not wb.stage_recoveries.preview(source['id'])['available']
    with pytest.raises((ValueError,KeyError)):wb.stage_recoveries.submit(source['id'],approval(p))
    assert len(calls)==count


def test_saved_answer_recovery_is_readable_without_current_git_config_or_policy(waiting_workbench,monkeypatch,tmp_path):
    wb,_,_,calls,_=waiting_workbench;source,_=failed_answer(waiting_workbench)
    p=wb.stage_recoveries.preview(source['id']);child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    before=wb.result_bytes(child['id']);count=len(calls)
    def denied(*args,**kwargs):pytest.fail('Saved recovery acquired Git/config/provider')
    monkeypatch.setattr('issueintel.investigation.stage_jobs.job_policy_sha256',lambda:'0'*64)
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',denied)
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',denied)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',denied);wb.provider_factory=denied
    assert wb.result_bytes(child['id'])==before and wb.report_bytes(child['id'])
    assert wb.stage_recoveries.submit(source['id'],approval(p))['id']==child['id']
    _,raw=exported(wb,child['id']);archive=tmp_path/'saved-answer.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==3 and len(calls)==count


@pytest.mark.parametrize('mutation',['request','inherited','parent','answers'])
def test_resealed_handoff_cannot_change_answer_recovery_authority(waiting_workbench,tmp_path,mutation):
    wb,_,_,_,_=waiting_workbench;source,_=failed_answer(waiting_workbench)
    p=wb.stage_recoveries.preview(source['id']);child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    _,raw=exported(wb,child['id']);files=contents(raw)
    target='runs/'+child['id']+'/stage-recovery.json';witness=json.loads(files[target])
    if mutation=='request':witness['source_chain'][0]['request']['answers'][0]['answer']='Replaced'
    else:
        target='runs/'+source['id']+'/stages/execution_input.json';record=json.loads(files[target])
        authority=record['payload']['context']['continuation']
        if mutation=='inherited':authority['inherited']['counts']['provider_calls']=0
        elif mutation=='parent':authority['link']['parent_job_id']='0'*24
        else:authority['link']['answers_receipt']['answers'][0]['answer']='Replaced'
        files[target]=encoded(record)
    if mutation=='request':files[target]=encoded(witness)
    manifest=json.loads(files['manifest.json'])
    for entry in manifest['entries']:entry.update(bytes=len(files[entry['path']]),sha256=sha(files[entry['path']]))
    manifest['sha256']=digest({k:v for k,v in manifest.items() if k!='sha256'});files['manifest.json']=encoded(manifest)
    archive=tmp_path/'changed-answer.zip';archive.write_bytes(repack(files))
    with pytest.raises(ValueError):verify(archive)


def test_answer_recovery_cli_uses_the_same_intent_and_remaining_budget(waiting_workbench,capsys,monkeypatch):
    from issueintel.investigation.workbench import Workbench
    from test_product_recovery_cli import invoke
    wb,_,_,calls,_=waiting_workbench;source,_=failed_answer(waiting_workbench)
    monkeypatch.setattr('issueintel.interfaces.cli._product_workbench',lambda root:
        Workbench(root,automatic_sync=False,provider_factory=wb.provider_factory))
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda *a:pytest.fail('Preview loaded credentials'))
    code,p,error=invoke(wb.root,capsys,'resume-stages','--job',source['id'])
    assert code==0 and p['available'] and p['max_provider_calls']==8 and not error
    args=['resume-stages','--job',source['id'],'--binding-sha256',p['binding_sha256'],'--with-api']
    code,child,error=invoke(wb.root,capsys,*args)
    assert code==0 and child['status']=='completed',child
    assert json.loads(error)=={'job_id':child['id'],'automatic_retry':False}
    count=len(calls)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Duplicate loaded config'))
    code,again,_=invoke(wb.root,capsys,*args)
    assert code==0 and again['id']==child['id'] and len(calls)==count


def test_answer_recovery_http_binds_consent_and_parent_without_preview_spending(waiting_workbench):
    from issueintel.interfaces.web import make_server
    from test_workbench import http
    wb,_,_,calls,_=waiting_workbench;source,_=failed_answer(waiting_workbench)
    count=len(calls);server=make_server(wb.root,port=0,workbench=wb)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        route='/api/jobs/'+source['id']+'/stage-recovery';headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        status,_,raw=http(server,'GET',route,headers=headers);p=json.loads(raw)
        assert status==200 and p['inherited_root_provider_calls']==1 and len(calls)==count
        body=json.dumps(approval(p)).encode()
        assert http(server,'POST',route,body,headers={})[0]==403
        assert http(server,'POST',route,body,headers=headers|{'Origin':'https://invalid.example'})[0]==403
        status,_,raw=http(server,'POST',route,body,headers=headers)
        assert status==202 and completed(wb,json.loads(raw)['id'])['status']=='completed'
    finally:server.shutdown();server.server_close();thread.join()


def test_parent_changed_in_recovery_call_stops_before_critic_and_retains_new_receipt(waiting_workbench):
    wb,_,parent,calls,fail=waiting_workbench;source,_=failed_answer(waiting_workbench)
    p=wb.stage_recoveries.preview(source['id']);count=len(calls)
    def changed(body,answer):
        row=json.loads(wb._job_path(parent['id']).read_bytes());row['request']['issue_text']='Changed during the resumed call';wb._save(row)
    fail[0]=changed
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert child['status']=='failed' and len(calls)==count+1,child
    saved=json.loads(wb._job_path(child['id']).read_bytes())
    assert len(saved['pipeline_attempts'])==len(saved['usage_receipts'])==1
    assert child['accounting']['reservations']==1


def test_answer_export_recovery_needs_no_new_api_or_config(waiting_workbench,monkeypatch):
    wb,service,parent,calls,_=waiting_workbench
    def interrupted(*a,**k):raise OSError('Synthetic answer export interruption')
    monkeypatch.setattr('issueintel.investigation.workbench.export_pack',interrupted)
    source=completed(wb,service.submit(parent['id'],request_for(service.preview(parent['id'])))['id'])
    assert source['status']=='failed',source
    count=len(calls);p=wb.stage_recoveries.preview(source['id'])
    assert p['available'] and p['requires_api'] is False and p['next_stage']=='export',p
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Export loaded config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Export created a provider')
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert child['status']=='completed' and len(calls)==count and child['accounting']['reservations']==0,child


def test_answer_of_recovered_parent_uses_readonly_intents_inside_new_recovery_transaction(stage_workbench,tmp_path):
    from test_recovered_planned_continuations import recovered_pause
    wb,_,calls,fail_at=stage_workbench;root,parent,_=recovered_pause(stage_workbench)
    service=wb.planned_continuations;fail_at.add(3)
    source=completed(wb,service.submit(parent['id'],request_for(service.preview(parent['id'])))['id'])
    assert source['status']=='failed' and len(calls)==3,source
    p=wb.stage_recoveries.preview(source['id'])
    assert p['available'] and p['inherited_root_provider_calls']==2,p
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert child['status']=='completed' and len(calls)==5,child
    assert totals(json.loads(wb.result_bytes(child['id']))['workflow_plan'])['provider_calls']+1==len(calls)
    _,raw=exported(wb,child['id']);archive=tmp_path/'two-recovery-kinds.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==4


def test_answer_recovery_retains_completed_retrieval_and_generation_before_round_failure(waiting_workbench,monkeypatch):
    from test_planned_pipeline import PlannedModel
    wb,service,parent,calls,fail=waiting_workbench
    original=PlannedModel.__call__;generations=[]
    def directed(model,messages,max_output_tokens):
        model.focuses=['fallback recover_none sentinel','formatting render_label display','validation validate_flag validity']
        answer=original(model,messages,max_output_tokens)
        if 'case' in json.loads(messages[1]['content']):generations.append(1)
        return answer
    monkeypatch.setattr(PlannedModel,'__call__',directed)
    def after_round(body,answer):
        if 'hypotheses' in answer and len(generations)==2:raise RuntimeError('Synthetic answer round failure')
    fail[0]=after_round
    source=completed(wb,service.submit(parent['id'],request_for(service.preview(parent['id'])))['id'])
    # Stage names are local to this answer segment; inherited rounds stay in
    # the parent plan instead of renumbering or copying their checkpoints.
    assert source['status']=='failed' and 'planned_retrieval_1' in source['stage_checkpoints'],source
    raw=wb.stage_bytes(source['id'],'planned_retrieval_1');fail[0]=False
    p=wb.stage_recoveries.preview(source['id']);assert p['next_stage']=='planned_generation_1',p
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert child['status']=='completed',child
    assert wb.stage_bytes(source['id'],'planned_retrieval_1')==raw
    assert 'planned_retrieval_1' not in child['stage_checkpoints'] and 'seed_generation' not in child['stage_checkpoints']


def test_combined_answer_and_recovery_ancestry_cannot_create_an_unreadable_child(waiting_workbench,monkeypatch):
    wb,service,parent,calls,fail=waiting_workbench
    with monkeypatch.context() as patch:
        def interrupted(*a,**k):raise OSError('Synthetic repeated answer export interruption')
        patch.setattr('issueintel.investigation.workbench.export_pack',interrupted)
        patch.setattr('issueintel.investigation.stage_jobs.export_pack',interrupted)
        source=completed(wb,service.submit(parent['id'],request_for(service.preview(parent['id']),all_answers=False))['id'])
        for _ in range(7):
            p=wb.stage_recoveries.preview(source['id']);assert p['available'] and not p['requires_api'],p
            source=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
            assert source['status']=='failed'
    p=wb.stage_recoveries.preview(source['id'])
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert child['status']=='completed',child
    assert len(wb.dossier(child['id'])['runs'])==10
    fail[0]=True
    source=completed(wb,service.submit(child['id'],request_for(service.preview(child['id'])))['id'])
    assert source['status']=='failed'
    count=len(calls);p=wb.stage_recoveries.preview(source['id'])
    assert not p['available'] and len(calls)==count
