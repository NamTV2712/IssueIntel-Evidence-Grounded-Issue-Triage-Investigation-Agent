"""Owned stopped jobs, attempt/receipt inheritance and explicit stage recovery."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
import threading
from urllib.error import URLError

import pytest

from issueintel.investigation.workbench import Workbench
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.stage_jobs import StageRecoveryJobs
from issueintel.runtime.io import digest
from test_focused_retrieval import FakeModel
from test_runtime import configured
from test_workbench import completed,http


@pytest.fixture
def stage_workbench(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-stage-job-key')
    config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    model=FakeModel(focus='parser parse_list items');calls=[];fail_at={1}
    def transport(request,**kwargs):
        body=json.loads(request.data);calls.append(body)
        if len(calls) in fail_at:raise URLError('Synthetic transport failed')
        value=model(body['messages'],body['max_completion_tokens'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kw:GroqProvider(cfg,transport=transport,**kw))
    try:yield wb,model,calls,fail_at
    finally:wb.close()


def source(fixture,*,depth='quick'):
    wb,_,calls,_=fixture
    row=completed(wb,wb.submit({'mode':'example','provider':'groq','depth':depth})['id'])
    assert row['status']=='failed' and len(calls)==1,row
    return row


def approval(preview):
    return {'binding_sha256':preview['binding_sha256'],'confirmation':
        'resume_product_stages_with_api_cost' if preview['requires_api'] else 'resume_product_stages_without_api'}


@pytest.mark.parametrize('depth',['quick','deep','planned'])
def test_owned_recovery_resumes_at_first_missing_generation_and_keeps_source_receipts(stage_workbench,depth):
    wb,model,calls,_=stage_workbench;root=source(stage_workbench,depth=depth)
    before=wb._job_path(root['id']).read_bytes();usage=wb.usage(root['id'])
    preview=wb.stage_recoveries.preview(root['id'])
    assert preview['available'] and preview['requires_api'] and preview['prior_provider_calls']==1,preview
    assert preview['next_stage']=='seed_generation' and len(calls)==1
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']));records=pack['stage_recovery']
    assert records['inherited_provider_calls']==1 and records['new_provider_calls']==len(calls)-1
    assert records['new_provider_calls']==child['accounting']['reservations']
    assert len(child['accounting']['receipts'])==len(calls)-1
    assert wb._job_path(root['id']).read_bytes()==before and wb.usage(root['id'])==usage
    assert len({r['reservation_id'] for r in usage['receipts']+child['accounting']['receipts']})==len(calls)
    assert not set(root['stage_checkpoints'])&set(child.get('stage_checkpoints',{}))
    assert wb.stage_recoveries.submit(root['id'],approval(preview))['id']==child['id']
    assert wb.intake(child['id'])['check_journal_available'] and wb.report_bytes(child['id'])
    assert {r['job_id'] for r in wb.dossier(child['id'])['runs']}=={root['id'],child['id']}


def test_duplicate_after_restart_and_saved_reads_need_no_git_policy_key_or_provider(stage_workbench,monkeypatch):
    wb,_,calls,_=stage_workbench;root=source(stage_workbench);preview=wb.stage_recoveries.preview(root['id'])
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id']);data=wb.result_bytes(child['id']);count=len(calls)
    monkeypatch.setattr('issueintel.investigation.stage_jobs.job_policy_sha256',lambda:'f'*64)
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a,**k:pytest.fail('Saved read accessed Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved read loaded config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved read constructed provider');wb.stage_recoveries=StageRecoveryJobs(wb)
    assert wb.stage_recoveries.submit(root['id'],approval(preview))['id']==child['id']
    assert wb.result_bytes(child['id'])==data and wb.report_bytes(child['id'])
    assert len(wb.dossier(child['id'])['runs'])==2 and len(calls)==count


def test_concurrent_requests_keep_one_intent_and_failed_attempt_is_not_retried(stage_workbench):
    wb,_,calls,fail_at=stage_workbench;root=source(stage_workbench);preview=wb.stage_recoveries.preview(root['id']);fail_at.add(2)
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids=list(pool.map(lambda _:wb.stage_recoveries.submit(root['id'],approval(preview))['id'],range(3)))
    assert len(set(ids))==1;child=completed(wb,ids[0]);assert child['status']=='failed' and len(calls)==2
    assert child['accounting']['reservations']==1
    assert wb.stage_recoveries.submit(root['id'],approval(preview))['id']==child['id'] and len(calls)==2


@pytest.mark.parametrize('tamper',['request','attempts','checkpoint','policy','running'])
def test_stale_or_unowned_source_is_denied_without_queue_or_calls(stage_workbench,tamper,monkeypatch):
    wb,_,calls,_=stage_workbench;root=source(stage_workbench);preview=wb.stage_recoveries.preview(root['id'])
    row=json.loads(wb._job_path(root['id']).read_bytes())
    if tamper=='request':row['request']['issue_text']='Cannot override a fixed example';wb._save(row)
    elif tamper=='attempts':row.pop('pipeline_attempts');wb._save(row)
    elif tamper=='checkpoint':
        ref=row['stage_checkpoints']['execution_input'];path=wb.root/ref['path'];path.write_bytes(path.read_bytes()+b' ')
    elif tamper=='policy':monkeypatch.setattr('issueintel.investigation.stage_jobs.job_policy_sha256',lambda:'f'*64)
    else:
        wb._save(row,status='running');wb.active.add(root['id'])
    before=wb.history()
    try:
        assert not wb.stage_recoveries.preview(root['id'])['available']
        with pytest.raises((ValueError,FileNotFoundError)):wb.stage_recoveries.submit(root['id'],approval(preview))
        assert wb.history()==before and len(calls)==1
    finally:wb.active.discard(root['id'])


def test_source_owner_lease_blocks_other_workbench_even_if_its_local_active_set_is_empty(stage_workbench):
    wb,_,calls,_=stage_workbench;root=source(stage_workbench);lease=wb.stage_recoveries.lease(root['id'])
    other=Workbench(wb.root,provider_factory=lambda *a,**k:pytest.fail('Preview constructed provider'))
    try:
        assert not other.stage_recoveries.preview(root['id'])['available']
        assert len(calls)==1
    finally:lease.close();other.close()
    assert wb.stage_recoveries.preview(root['id'])['available']


def test_late_quick_checkpoint_stays_with_existing_verifier_and_export_recovery(stage_workbench):
    wb,_,calls,fail_at=stage_workbench;fail_at.clear();fail_at.add(2)
    root=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id']);assert root['status']=='failed' and len(calls)==2
    assert not wb.stage_recoveries.preview(root['id'])['available']
    assert wb.resume_preview(root['id'])['available']


def test_zero_api_recovery_never_loads_a_provider_or_key(stage_workbench,monkeypatch):
    wb,_,calls,_=stage_workbench
    root=completed(wb,wb.submit({'mode':'example','provider':'none'})['id']);assert root['status']=='completed'
    row=json.loads(wb._job_path(root['id']).read_bytes());wb._save(row,status='failed',stage='export')
    preview=wb.stage_recoveries.preview(root['id']);assert preview['available'] and not preview['requires_api']
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Zero-call recovery read config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Zero-call recovery constructed provider')
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    assert child['accounting']['reservations']==0 and child['accounting']['tracking_available'] and not calls


def test_http_preview_does_not_spend_and_post_requires_session_origin_and_exact_consent(stage_workbench):
    from issueintel.interfaces.web import make_server
    wb,_,calls,_=stage_workbench;root=source(stage_workbench);server=make_server(wb.root,port=0,workbench=wb)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        route='/api/jobs/'+root['id']+'/stage-recovery'
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        status,_,raw=http(server,'GET',route,headers=headers);preview=json.loads(raw);assert status==200 and preview['available'] and len(calls)==1
        body=json.dumps(approval(preview)).encode()
        assert http(server,'POST',route,body,headers={})[0]==403
        assert http(server,'POST',route,body,headers=headers|{'Origin':'https://invalid.example'})[0]==403
        wrong=json.dumps(dict(approval(preview),confirmation='resume_product_stages_without_api')).encode()
        assert http(server,'POST',route,wrong,headers=headers)[0]==400
        status,_,raw=http(server,'POST',route,body,headers=headers);assert status==202
        assert completed(wb,json.loads(raw)['id'])['status']=='completed'
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('mutation',['policy','source_stage','usage'])
def test_inflight_source_change_stops_after_the_paid_call_and_keeps_receipt(stage_workbench,monkeypatch,mutation):
    wb,_,calls,_=stage_workbench;root=source(stage_workbench);preview=wb.stage_recoveries.preview(root['id'])
    factory=wb.provider_factory
    def changed_factory(*a,**k):
        provider=factory(*a,**k);original=provider.transport
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            if mutation=='policy':monkeypatch.setattr('issueintel.investigation.stage_jobs.job_policy_sha256',lambda:'f'*64)
            elif mutation=='source_stage':
                row=json.loads(wb._job_path(root['id']).read_bytes());path=wb.root/row['stage_checkpoints']['execution_input']['path'];path.write_bytes(path.read_bytes()+b' ')
            else:
                row=json.loads(wb._job_path(root['id']).read_bytes());next(iter(row['usage_receipts'].values()))['tokens_charged_or_reserved']+=1;wb._save(row)
            return result
        provider.transport=changed;return provider
    wb.provider_factory=changed_factory
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id'])
    assert child['status']=='failed' and 'result' not in child and len(calls)==2
    assert child['accounting']['reservations']==1 and child['accounting']['all_usage_settled']


def test_resume_reuses_completed_generation_instead_of_calling_it_again(stage_workbench,monkeypatch):
    wb,model,calls,fail_at=stage_workbench;fail_at.clear();save=wb._save;injected=[]
    def fault(row,**changes):
        save(row,**changes)
        if changes.get('stage')=='seed_generation' and row['mode']=='example' and not injected:
            injected.append(True);raise OSError('Synthetic boundary after saved generation')
    monkeypatch.setattr(wb,'_save',fault)
    root=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id']);assert root['status']=='failed' and model.generations==1
    raw=wb.stage_bytes(root['id'],'seed_generation');preview=wb.stage_recoveries.preview(root['id'])
    assert preview['available'] and preview['next_stage']=='verification'
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    assert model.generations==1 and len(calls)==2 and child['accounting']['reservations']==1
    assert json.loads(wb.result_bytes(child['id']))['annotation']==json.loads(raw)['payload']['annotation']


@pytest.mark.parametrize('depth',['quick','planned'])
def test_handoff_replays_source_journal_without_git_config_or_api(stage_workbench,monkeypatch,tmp_path,depth):
    from test_handoff import exported,contents
    from issueintel.investigation.portable_verify import verify
    import subprocess,sys
    wb,_,calls,_=stage_workbench;root=source(stage_workbench,depth=depth);preview=wb.stage_recoveries.preview(root['id'])
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id']);assert child['status']=='completed'
    count=len(calls);monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a,**k:pytest.fail('Handoff accessed Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Handoff loaded config'))
    _,raw=exported(wb,child['id']);files=contents(raw);path=tmp_path/'recovered.zip';path.write_bytes(raw)
    assert verify(path)['runs']==2 and len(calls)==count
    assert all('runs/'+root['id']+'/stages/'+stage+'.json' in files for stage in root['stage_checkpoints'])
    program=tmp_path/'verify.py';program.write_bytes(files['verify.py'])
    process=subprocess.run([sys.executable,'-I',str(program),str(path)],text=True,capture_output=True)
    assert process.returncode==0,process.stdout+process.stderr


@pytest.mark.parametrize('mutation',['source_file','tail_file','attempts','owner'])
def test_portable_recovery_rejects_changes_even_after_outer_manifest_is_resealed(stage_workbench,tmp_path,mutation):
    from test_handoff import exported,contents,repack
    from issueintel.investigation.portable_verify import verify,sha,encoded
    wb,_,_,_=stage_workbench;root=source(stage_workbench);preview=wb.stage_recoveries.preview(root['id'])
    child=completed(wb,wb.stage_recoveries.submit(root['id'],approval(preview))['id']);_,raw=exported(wb,child['id']);files=contents(raw)
    if mutation=='source_file':files['runs/'+root['id']+'/stages/execution_input.json']+=b' '
    elif mutation=='tail_file':files['runs/'+child['id']+'/stages/verification.json']+=b' '
    else:
        path='runs/'+child['id']+'/stage-recovery.json';value=json.loads(files[path])
        if mutation=='attempts':value['source_attempts']=[]
        else:value['source_request']['mode']='main_dev'
        files[path]=encoded(value)
    manifest=json.loads(files['manifest.json'])
    for entry in manifest['entries']:entry.update(bytes=len(files[entry['path']]),sha256=sha(files[entry['path']]))
    manifest['sha256']=digest({k:v for k,v in manifest.items() if k!='sha256'});files['manifest.json']=encoded(manifest)
    path=tmp_path/'tampered.zip';path.write_bytes(repack(files))
    with pytest.raises(ValueError):verify(path)
