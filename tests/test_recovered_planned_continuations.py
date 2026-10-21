"""Continue a recovered pause without losing failed original API attempts."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from issueintel.investigation.planned_waiting import continuation_remaining, recovery_call_debit, totals, validate_plan
from issueintel.investigation.planned_continuations import PlannedContinuations
from issueintel.investigation.portable_verify import verify
from test_stage_jobs import stage_workbench,source,approval
from test_planned_pipeline import planned_repository
from test_planned_continuations import request_for
from test_handoff import exported,contents
from test_workbench import completed


def recovered_pause(fixture,repository=None):
    wb,_,calls,_=fixture
    if repository is None:root=source(fixture,depth='planned')
    else:
        case,path=repository
        root=completed(wb,wb.submit({'mode':'local','provider':'groq','depth':'planned','repository':str(path),
            'revision':case['revision'],'issue_text':case['problem_statement']})['id'])
        assert root['status']=='failed' and len(calls)==1,root
    preview=wb.stage_recoveries.preview(root['id'])
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['stop_reason']=='waiting_for_user',pack['workflow_plan']
    assert recovery_call_debit(pack)==1 and len(calls)==2
    return root,child,pack


def test_answers_after_recovery_inherit_failed_attempt_without_rewriting_parent(stage_workbench):
    wb,_,calls,_=stage_workbench;root,parent,pack=recovered_pause(stage_workbench)
    raw=wb.result_bytes(parent['id']);root_usage=wb.usage(root['id']);parent_usage=wb.usage(parent['id'])
    service=wb.planned_continuations;preview=service.preview(parent['id'])
    assert preview['available'] and len(calls)==2,preview
    assert preview['remaining']==continuation_remaining(pack)
    assert preview['remaining']['provider_calls']==pack['workflow_plan']['waiting']['remaining']['provider_calls']-1
    request=request_for(preview)
    child=completed(wb,service.submit(parent['id'],request)['id'])
    assert child['status']=='completed',child
    result=json.loads(wb.result_bytes(child['id']));plan=validate_plan(result,parent=pack)
    assert plan['inherited']['counts']['provider_calls']==2
    assert totals(plan)['provider_calls']==len(calls)<=plan['limits']['max_provider_calls']
    assert result['verification']['kind']=='llm_separate_pass'
    assert wb.result_bytes(parent['id'])==raw and wb.usage(root['id'])==root_usage and wb.usage(parent['id'])==parent_usage
    assert len(wb.dossier(child['id'])['runs'])==3
    count=len(calls);assert service.submit(parent['id'],request)['id']==child['id'] and len(calls)==count


def test_recovered_answer_handoff_verifies_on_another_machine_and_saved_reads_do_not_spend(stage_workbench,tmp_path,monkeypatch):
    wb,_,calls,_=stage_workbench;root,parent,_=recovered_pause(stage_workbench)
    request=request_for(wb.planned_continuations.preview(parent['id']))
    child=completed(wb,wb.planned_continuations.submit(parent['id'],request)['id']);assert child['status']=='completed',child
    raw=wb.result_bytes(child['id']);count=len(calls)
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',lambda *a,**k:pytest.fail('Saved read used Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved read loaded API config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved read constructed provider')
    service=PlannedContinuations(wb)
    assert service.submit(parent['id'],request)['id']==child['id'] and wb.result_bytes(child['id'])==raw
    preview,bundle=exported(wb,child['id']);files=contents(bundle)
    assert set(preview['included_job_ids'])=={root['id'],parent['id'],child['id']}
    archive=tmp_path/'recovered-answers.zip';archive.write_bytes(bundle)
    program=tmp_path/'verify.py';program.write_bytes(files['verify.py'])
    process=subprocess.run([sys.executable,'-I',str(program),str(archive)],capture_output=True,text=True)
    assert process.returncode==0,process.stdout+process.stderr
    assert verify(archive)['runs']==3 and len(calls)==count
    from test_planned_handoff import reseal_changed_pack
    changed=reseal_changed_pack(files,child['id'],lambda p:
        p['workflow_plan']['inherited']['counts'].update(provider_calls=1))
    altered=tmp_path/'reset-debit.zip';altered.write_bytes(changed)
    with pytest.raises(ValueError,match='inherited root budget'):verify(altered)


@pytest.mark.parametrize('mutation',['missing_receipt','duplicate_receipt','changed_attempt','missing_tracking'])
def test_unknown_recovery_accounting_cannot_authorize_more_calls(stage_workbench,mutation):
    wb,_,calls,_=stage_workbench;root,parent,_=recovered_pause(stage_workbench)
    row=json.loads(wb._job_path(root['id']).read_bytes())
    if mutation=='missing_receipt':row['usage_receipts']={}
    elif mutation=='duplicate_receipt':
        current=json.loads(wb._job_path(parent['id']).read_bytes())
        row['usage_receipts']=deepcopy(current['usage_receipts'])
    elif mutation=='changed_attempt':row['pipeline_attempts'][0]['provider_calls']=0
    else:row['usage_tracking']=False
    wb._save(row);count=len(calls)
    assert not wb.planned_continuations.preview(parent['id'])['available'] and len(calls)==count


def test_attempt_budget_below_three_cannot_authorize_generation_repair_and_critic(stage_workbench):
    # The pure admission helper must reserve all three calls even if the saved
    # logical waiting budget looks larger; it never edits that waiting receipt.
    wb,_,_,_=stage_workbench;_,parent,pack=recovered_pause(stage_workbench)
    from issueintel.runtime.io import digest
    from issueintel.investigation.planned_waiting import build_continuation
    changed=deepcopy(pack);value=changed['stage_recovery']
    value['inherited_provider_calls']=pack['workflow_plan']['limits']['max_provider_calls']-2;value['new_provider_calls']=1
    binding=changed['recovered_from']['source'];binding['prior_provider_calls']=value['inherited_provider_calls']
    binding['sha256']=digest({k:v for k,v in binding.items() if k!='sha256'})
    assert continuation_remaining(changed)['provider_calls']==1
    assert pack['workflow_plan']['waiting']==changed['workflow_plan']['waiting']
    answers=request_for(wb.planned_continuations.preview(parent['id']))['answers']
    with pytest.raises(ValueError,match='remaining generation budget'):build_continuation(changed,answers,'vi')


def test_partial_answer_descendants_carry_recovery_debit_exactly_once(stage_workbench,planned_repository,tmp_path):
    wb,_,calls,_=stage_workbench;root,parent,pack=recovered_pause(stage_workbench,planned_repository)
    service=wb.planned_continuations
    first=completed(wb,service.submit(parent['id'],request_for(service.preview(parent['id']),all_answers=False))['id'])
    assert first['status']=='completed',first
    first_pack=json.loads(wb.result_bytes(first['id']))
    assert first_pack['stop_reason']=='waiting_for_user'
    assert first_pack['workflow_plan']['inherited']['counts']['provider_calls']==2
    assert totals(first_pack['workflow_plan'])['provider_calls']==len(calls)==3
    second=completed(wb,service.submit(first['id'],request_for(service.preview(first['id'])))['id'])
    assert second['status']=='completed',second
    second_pack=json.loads(wb.result_bytes(second['id']))
    assert second_pack['workflow_plan']['inherited']['counts']['provider_calls']==3
    assert totals(second_pack['workflow_plan'])['provider_calls']==len(calls)==5
    preview,bundle=exported(wb,second['id']);archive=tmp_path/'partial-recovered.zip';archive.write_bytes(bundle)
    assert set(preview['included_job_ids'])=={root['id'],parent['id'],first['id'],second['id']}
    assert verify(archive)['runs']==4


def test_source_changed_during_answer_call_stops_before_critic_and_keeps_new_receipt(stage_workbench,monkeypatch):
    wb,model,calls,_=stage_workbench;root,parent,_=recovered_pause(stage_workbench)
    request=request_for(wb.planned_continuations.preview(parent['id']))
    before=len(calls);original=type(model).__call__
    def changed(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        row=json.loads(wb._job_path(root['id']).read_bytes());row['pipeline_attempts'][0]['stage']='verification';wb._save(row)
        return result
    monkeypatch.setattr(type(model),'__call__',changed)
    child=completed(wb,wb.planned_continuations.submit(parent['id'],request)['id'])
    assert child['status']=='failed' and len(calls)==before+1,child
    assert child['accounting']['reservations']==1
