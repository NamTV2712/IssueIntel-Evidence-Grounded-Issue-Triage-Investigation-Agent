"""Recover actual product forks with their original user/source provenance."""
import json
from copy import deepcopy
import subprocess
import sys
from urllib.error import URLError

import pytest

from test_stage_jobs import stage_workbench,approval
from test_workbench import completed
from test_handoff import exported,contents
from issueintel.investigation.portable_verify import verify
from test_clarification_workbench import answer_request
from test_product_memory import memory_workbench
from test_memory_followups import execution_source,request_for as memory_request
from test_check_plan_jobs import checking,generate as generate_plan
from test_check_observation_workbench import append as append_observation,followup_request
from test_context_workbench import selected_workbench,choose
from test_repository_context import context_repo
from test_github_index import connected


@pytest.mark.parametrize('depth',['quick','deep'])
def test_followup_failure_can_resume_without_changing_parent_or_user_context(stage_workbench,depth,tmp_path):
    wb,_,calls,_=stage_workbench
    parent=completed(wb,wb.submit({'mode':'example'})['id']);assert parent['status']=='completed'
    request={'mode':'followup','parent_job_id':parent['id'],'additional_context':'Observed None input; expected [].',
             'provider':'groq','depth':depth}
    source=completed(wb,wb.submit(request)['id']);assert source['status']=='failed' and len(calls)==1,source
    original=wb._job_path(source['id']).read_bytes();parent_raw=wb.result_bytes(parent['id'])
    preview=wb.stage_recoveries.preview(source['id']);assert preview['available'],preview
    assert preview['max_provider_calls']==(4 if depth=='quick' else 6)
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['case']['case_id']=='followup:'+source['id']
    assert pack['followup']['parent_job_id']==parent['id'] and pack['followup']['additional_context']==request['additional_context']
    assert pack['recovered_from']['source']['schema']=='product_stage_recovery_source_v4'
    assert wb.result_bytes(parent['id'])==parent_raw and wb._job_path(source['id']).read_bytes()==original
    assert len(wb.dossier(child['id'])['runs'])==3
    _,raw=exported(wb,child['id']);archive=tmp_path/'followup.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==3


def recover_and_export(wb,parent,source,tmp_path):
    assert source['status']=='failed',source
    p=wb.stage_recoveries.preview(source['id']);assert p['available'],p
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['recovered_from']['source']['schema']=='product_stage_recovery_source_v4'
    _,raw=exported(wb,child['id']);archive=tmp_path/'fork.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==3
    return child,pack


def test_clarification_generation_recovery_retains_exact_answers_and_source(stage_workbench,tmp_path):
    wb,_,calls,_=stage_workbench;parent=completed(wb,wb.submit({'mode':'example'})['id'])
    request=answer_request(wb,parent['id'],provider='groq')
    source=completed(wb,wb.submit(request)['id'])
    child,pack=recover_and_export(wb,parent,source,tmp_path)
    receipt=pack['followup']['clarification']
    assert [{k:r[k] for k in ('question_id','answer')} for r in receipt['answers']]==request['answers']
    assert receipt['questions_sha256']==request['questions_sha256'] and len(calls)==3
    assert wb.intake(child['id'])['check_journal_available']


def fail_export(monkeypatch):
    def interrupted(*a,**k):raise OSError('Synthetic fork export interruption')
    monkeypatch.setattr('issueintel.investigation.workbench.export_pack',interrupted)


def test_memory_fork_export_recovery_keeps_selection_without_calling_provider(execution_source,monkeypatch,tmp_path):
    wb,parent,_,preview=execution_source;request=memory_request(parent,preview)
    fail_export(monkeypatch);source=completed(wb,wb.memory_followups.submit(request)['id'])
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Export loaded API config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Export created provider')
    child,pack=recover_and_export(wb,parent,source,tmp_path)
    assert pack['followup']['memory_selection']==preview['selection']
    assert child['accounting']['reservations']==0


def test_check_observation_fork_export_recovery_keeps_receipt_and_reported_truth_levels(checking,monkeypatch,tmp_path):
    wb,_,parent=checking;plan_job,plan=generate_plan(wb,parent)
    journal=append_observation(wb,plan_job);request=followup_request(plan_job,plan,journal)
    fail_export(monkeypatch);source=completed(wb,wb.submit_check_followup(request)['id'])
    child,pack=recover_and_export(wb,parent,source,tmp_path)
    receipt=pack['followup']['check_observations']
    assert receipt['observation_ids']==request['observation_ids'] and receipt['executed_by_issueintel'] is False
    assert receipt['causal_verified'] is False and child['accounting']['reservations']==0


def test_selected_context_fork_export_recovery_keeps_frozen_repository_sources(selected_workbench,monkeypatch,tmp_path):
    wb,_,parent=selected_workbench;request=choose(wb,parent)
    fail_export(monkeypatch);source=completed(wb,wb.submit(request)['id'])
    child,pack=recover_and_export(wb,parent,source,tmp_path)
    assert pack['context_selection']['selected_sources']==request['selected_sources']
    assert pack['context_selection']['historical_verified'] is False and child['accounting']['reservations']==0


def stopped_followup(fixture):
    wb,_,_,_=fixture;parent=completed(wb,wb.submit({'mode':'example'})['id'])
    source=completed(wb,wb.submit({'mode':'followup','parent_job_id':parent['id'],
        'additional_context':'Observed None; expected an empty list.','provider':'groq'})['id'])
    assert source['status']=='failed'
    return parent,source


def test_failed_recovered_fork_reuses_seed_with_original_budget_and_independent_handoff(stage_workbench,tmp_path):
    wb,model,calls,fail_at=stage_workbench;parent,source=stopped_followup(stage_workbench);fail_at.add(3)
    p=wb.stage_recoveries.preview(source['id'])
    failed=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    assert failed['status']=='failed' and model.generations==1 and len(calls)==3,failed
    p=wb.stage_recoveries.preview(failed['id']);assert p['next_stage']=='verification' and p['prior_provider_calls']==3,p
    child=completed(wb,wb.stage_recoveries.submit(failed['id'],approval(p))['id'])
    assert child['status']=='completed' and model.generations==1 and len(calls)==4,child
    _,raw=exported(wb,child['id']);archive=tmp_path/'nested-fork.zip';archive.write_bytes(raw)
    program=tmp_path/'verify.py';program.write_bytes(contents(raw)['verify.py'])
    result=subprocess.run([sys.executable,'-I',str(program),str(archive)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert verify(archive)['runs']==4


@pytest.mark.parametrize('mutation',['user_context','origin_timestamp','missing_attempts','parent_bytes','policy'])
def test_changed_fork_authority_cannot_authorize_another_model_call(stage_workbench,monkeypatch,mutation):
    wb,_,calls,_=stage_workbench;parent,source=stopped_followup(stage_workbench)
    p=wb.stage_recoveries.preview(source['id']);assert p['available']
    if mutation=='policy':monkeypatch.setattr('issueintel.investigation.stage_jobs.job_policy_sha256',lambda:'0'*64)
    elif mutation=='parent_bytes':
        row=json.loads(wb._job_path(parent['id']).read_bytes());path=wb.root/row['result']['pack.json']['path']
        path.write_bytes(path.read_bytes()+b' ')
    else:
        row=json.loads(wb._job_path(source['id']).read_bytes())
        if mutation=='user_context':row['request']['additional_context']='Different input after consent'
        elif mutation=='origin_timestamp':row['created_at']='2020-01-01T00:00:00+00:00'
        else:row.pop('pipeline_attempts')
        wb._save(row)
    count=len(calls);assert not wb.stage_recoveries.preview(source['id'])['available']
    with pytest.raises((ValueError,KeyError)):wb.stage_recoveries.submit(source['id'],approval(p))
    assert len(calls)==count


def test_recovered_fork_can_have_another_user_followup_without_resetting_logical_depth(stage_workbench):
    wb,_,_,_=stage_workbench;parent,source=stopped_followup(stage_workbench)
    p=wb.stage_recoveries.preview(source['id'])
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(p))['id'])
    next_job=completed(wb,wb.submit({'mode':'followup','parent_job_id':child['id'],'additional_context':'Python 3.12; user report only.'})['id'])
    assert next_job['status']=='completed',next_job
    pack=json.loads(wb.result_bytes(next_job['id']))
    assert pack['followup']['depth']==2 and pack['followup']['parent_job_id']==child['id']
    assert len(wb.dossier(next_job['id'])['runs'])==4


def test_paid_memory_fork_recovery_reuses_selection_and_saved_reads_do_not_need_live_store(execution_source,monkeypatch,tmp_path):
    wb,parent,_,preview=execution_source;factory=wb.provider_factory;fail=[True]
    def interrupted_factory(*args,**kwargs):
        provider=factory(*args,**kwargs);transport=provider.transport
        def interrupted(*a,**k):
            if fail[0]:fail[0]=False;raise URLError('Synthetic memory generation interruption')
            return transport(*a,**k)
        provider.transport=interrupted;return provider
    wb.provider_factory=interrupted_factory
    source=completed(wb,wb.memory_followups.submit(memory_request(parent,preview,provider='groq'))['id'])
    child,pack=recover_and_export(wb,parent,source,tmp_path)
    assert pack['followup']['memory_selection']==preview['selection']
    raw=wb.result_bytes(child['id'])
    monkeypatch.setattr(wb.memory.store,'read',lambda *a,**k:pytest.fail('Saved fork read live memory store'))
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Saved fork replayed Git'))
    assert wb.result_bytes(child['id'])==raw and wb.report_bytes(child['id'])
    _,bundle=exported(wb,child['id']);archive=tmp_path/'saved-memory-fork.zip';archive.write_bytes(bundle)
    assert verify(archive)['runs']==3


def test_saved_context_fork_recovery_uses_frozen_context_after_current_index_changes(selected_workbench,monkeypatch,tmp_path):
    wb,_,parent=selected_workbench;request=choose(wb,parent)
    fail_export(monkeypatch);source=completed(wb,wb.submit(request)['id'])
    child,_=recover_and_export(wb,parent,source,tmp_path);raw=wb.result_bytes(child['id'])
    monkeypatch.setattr(wb,'_selected_context',lambda *a:pytest.fail('Saved fork queried current index'))
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Saved fork replayed Git'))
    assert wb.result_bytes(child['id'])==raw
    _,bundle=exported(wb,child['id']);archive=tmp_path/'saved-context-fork.zip';archive.write_bytes(bundle)
    assert verify(archive)['runs']==3
