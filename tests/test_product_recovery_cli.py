"""CLI shares owned product execution and recovery, without an idle sync worker."""
import json
import threading
from types import SimpleNamespace

import pytest

from issueintel.interfaces.cli import main
from issueintel.investigation.workbench import Workbench
from test_focused_retrieval import fixture_repo
from test_stage_jobs import stage_workbench,source
from test_workbench import completed


def invoke(root,capsys,*arguments):
    code=main(['--root',str(root),*arguments]);output=capsys.readouterr()
    return code,json.loads(output.out) if output.out.strip() else None,output.err


def arguments(tmp_path,fixture):
    case,path=fixture;issue=tmp_path/'synthetic-issue.txt';issue.write_text(case['problem_statement'],encoding='utf-8')
    return ['investigate','--durable','--repo',str(path),'--revision',case['revision'],'--issue-file',str(issue)]


@pytest.mark.parametrize('depth',['quick','deep','planned'])
def test_durable_offline_cli_captures_owned_journal_without_config_key_sync_or_provider(tmp_path,fixture_repo,capsys,monkeypatch,depth):
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda *a:pytest.fail('Offline command read credentials'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Offline command read provider config'))
    monkeypatch.setattr('issueintel.investigation.workbench.Workbench._auto_sync_loop',lambda *a:pytest.fail('CLI started sync scheduler'))
    args=arguments(tmp_path,fixture_repo)+(['--planned'] if depth=='planned' else ['--refinement-rounds','1'] if depth=='deep' else [])
    code,row,error=invoke(tmp_path,capsys,*args)
    assert code==0 and row['status']=='completed' and row['depth']==depth,(row,error)
    assert row['mode']=='local' and row['provider']=='none' and 'execution_input' in row['stage_checkpoints']
    assert row['pipeline_attempts']==[] and json.loads(error)['job_id']==row['id']
    wb=Workbench(tmp_path,automatic_sync=False,provider_factory=lambda *a,**k:pytest.fail('Saved read used provider'))
    try:
        pack=json.loads(wb.result_bytes(row['id']))
        assert pack['case']['case_id'].startswith('local:') and pack['stage_recovery']['max_provider_calls']==0
        assert wb.report_bytes(row['id'])
    finally:wb.close()


def test_cli_preview_and_confirmed_paid_recovery_reuse_the_web_owned_intent(stage_workbench,fixture_repo,capsys,monkeypatch):
    wb,_,calls,_=stage_workbench
    monkeypatch.setattr('issueintel.interfaces.cli._product_workbench',lambda root:
        Workbench(root,automatic_sync=False,provider_factory=wb.provider_factory))
    args=arguments(wb.root,fixture_repo)+['--provider','groq']
    code,root,_=invoke(wb.root,capsys,*args)
    assert code==2 and root['status']=='failed' and len(calls)==1
    source_bytes=wb._job_path(root['id']).read_bytes()
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda *a:pytest.fail('CLI recovery eagerly loaded credentials'))
    code,preview,error=invoke(wb.root,capsys,'resume-stages','--job',root['id'])
    assert code==0 and preview['available'] and preview['requires_api'] and preview['model_calls']==0 and not error
    assert len(calls)==1
    approved=['resume-stages','--job',root['id'],'--binding-sha256',preview['binding_sha256'],'--with-api']
    code,child,_=invoke(wb.root,capsys,*approved)
    assert code==0 and child['status']=='completed' and len(calls)==3,child
    count=len(calls);assert wb._job_path(root['id']).read_bytes()==source_bytes
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Duplicate loaded API config'))
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a,**k:pytest.fail('Duplicate used Git'))
    code,again,_=invoke(wb.root,capsys,*approved)
    assert code==0 and again['id']==child['id'] and len(calls)==count
    assert wb.result_bytes(child['id'])


def test_explicit_github_input_loads_local_credentials_before_remote_acquisition(tmp_path,fixture_repo,capsys,monkeypatch):
    case,path=fixture_repo;events=[];url='https://github.com/synthetic/repo/issues/1'
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda file:events.append(('env',file)))
    def issue(value):
        events.append(('issue',value));return {'url':url,'repository':'synthetic/repo',
            'problem_statement':case['problem_statement'],'created_at':'2026-01-01T00:00:00Z'}
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue',issue)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',lambda *a,**k:SimpleNamespace(path=path))
    code,row,error=invoke(tmp_path,capsys,'investigate','--durable','--repo',url,'--revision',case['revision'])
    assert code==0 and row['status']=='completed' and row['mode']=='github',(row,error)
    assert events==[('env',tmp_path/'.env'),('issue',url)]


@pytest.mark.parametrize('extra',[
    ['--case-id','synthetic:forbidden'],['--no-critic'],['--controller','jev','--planned'],
    ['--output','artifacts/other'],['--planned','--refinement-rounds','1'],
])
def test_unsupported_durable_recipes_deny_before_constructing_execution(tmp_path,fixture_repo,capsys,monkeypatch,extra):
    monkeypatch.setattr('issueintel.interfaces.cli._product_workbench',lambda *a:pytest.fail('Invalid request created execution'))
    code,result,error=invoke(tmp_path,capsys,*arguments(tmp_path,fixture_repo),*extra)
    assert code==2 and result is None and '--durable requires' in error


@pytest.mark.parametrize('extra',[['--with-api'],['--without-api'],['--binding-sha256','a'*64]])
def test_partial_recovery_consent_denies_before_constructing_execution(tmp_path,capsys,monkeypatch,extra):
    monkeypatch.setattr('issueintel.interfaces.cli._product_workbench',lambda *a:pytest.fail('Partial consent created execution'))
    code,result,error=invoke(tmp_path,capsys,'resume-stages','--job','a'*24,*extra)
    assert code==2 and result is None and 'Preview without flags' in error


def test_other_process_owner_remains_running_until_real_lease_releases(stage_workbench):
    wb,_,calls,_=stage_workbench;root=source(stage_workbench)
    row=json.loads(wb._job_path(root['id']).read_bytes());wb._save(row,status='running')
    owner=wb.stage_recoveries.lease(root['id'])
    observer=Workbench(wb.root,automatic_sync=False)
    try:
        assert observer.job(root['id'])['status']=='running'
        assert not observer.stage_recoveries.preview(root['id'])['available']
        owner.close();owner=None
        assert observer.job(root['id'])['status']=='interrupted'
        assert observer.stage_recoveries.preview(root['id'])['available']
        assert len(calls)==1
    finally:
        if owner is not None:owner.close()
        observer.close()


def test_queued_recovery_keeps_owner_across_processes_and_cancel_releases_it(stage_workbench):
    from test_stage_jobs import approval
    wb,_,calls,_=stage_workbench;root=source(stage_workbench)
    preview=wb.stage_recoveries.preview(root['id']);gate=threading.Event()
    blocker=wb.executor.submit(gate.wait)
    observer=Workbench(wb.root,automatic_sync=False)
    try:
        child=wb.stage_recoveries.submit(root['id'],approval(preview))
        assert child['status']=='queued' and observer.job(child['id'])['status']=='queued'
        assert observer.stage_recoveries.submit(root['id'],approval(preview))['id']==child['id']
        assert observer.job(child['id'])['status']=='queued' and len(calls)==1
        wb.cancel(child['id']);gate.set();blocker.result(timeout=5)
        assert completed(wb,child['id'])['status']=='cancelled'
        assert not wb.stage_recoveries.externally_owned(child['id']) and not wb.execution_leases
        assert observer.stage_recoveries.submit(root['id'],approval(preview))['id']==child['id'] and len(calls)==1
    finally:gate.set();observer.close()


def test_enqueue_failure_releases_lease_and_queue_slot(tmp_path):
    wb=Workbench(tmp_path,automatic_sync=False);wb.executor.shutdown(wait=True)
    try:
        with pytest.raises(RuntimeError):wb.submit({'mode':'example'})
        assert not wb.active and not wb.execution_leases
        row=wb.history()[0]
        assert row['status']=='interrupted' and not wb.stage_recoveries.externally_owned(row['id'])
        acquired=[wb.capacity.acquire(blocking=False) for _ in range(4)]
        assert acquired==[True]*4
        for _ in acquired:wb.capacity.release()
    finally:wb.close()
