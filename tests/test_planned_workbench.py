"""Planned product boundary, durable accounting and exports with synthetic IO."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import threading

import pytest

from issueintel.interfaces.cli import _investigate
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench, validate_request
from issueintel.runtime.io import write_json
from test_planned_pipeline import PlannedModel, planned_repository
from test_runtime import configured
from test_workbench import completed, local_server, http


def request_for(fixture):
    case,path=fixture
    return {'mode':'local','repository':str(path),'revision':case['revision'],
            'issue_text':case['problem_statement'],'depth':'planned','provider':'groq'}


@pytest.fixture
def planned_workbench(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret')
    config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    model=PlannedModel();calls=[];fail_at=[None]
    def transport(request,**kwargs):
        body=json.loads(request.data);calls.append(body)
        if len(calls)==fail_at[0]:raise RuntimeError('synthetic-secret private response')
        answer=model(body['messages'],body['max_completion_tokens'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kwargs:GroqProvider(cfg,transport=transport,**kwargs))
    try:yield wb,model,calls,fail_at
    finally:wb.close()


def test_planned_job_accounts_numbered_calls_and_reads_without_repeating(planned_repository,planned_workbench):
    wb,model,calls,_=planned_workbench
    job=completed(wb,wb.submit(request_for(planned_repository))['id'])
    assert job['status']=='completed',job
    pack=json.loads(wb.result_bytes(job['id']));plan=pack['workflow_plan']
    assert len(plan['rounds'])==3 and model.generations==4 and len(calls)==5
    usage=wb.usage(job['id'])
    assert usage['all_usage_settled'] and usage['tokens_charged_or_reserved']==75
    assert [row['stage'] for row in usage['receipts']]==[
        'seed_generation','planned_generation_1','planned_generation_2','planned_generation_3','verification']
    assert set(job['stage_checkpoints'])=={'planned_input','seed_generation','planned_retrieval_1',
        'planned_generation_1','planned_retrieval_2','planned_generation_2','planned_retrieval_3',
        'planned_generation_3','verification','planned_complete'}
    assert b'planned_investigation_v1' in wb.stage_bytes(job['id'],'planned_complete')
    report=wb.investigation_report(job['id'])
    provenance=next(row for row in report['provenance'] if row['kind']=='planned_workflow')
    assert json.loads(provenance['details'])['completed_retrieval_rounds']==3
    assert b'Planned investigation workflow' in wb.report_bytes(job['id'],'md')
    wb2=Workbench(wb.root,provider_factory=lambda *a,**k:pytest.fail('Saved read called provider'))
    try:
        assert wb2.result_bytes(job['id'])==wb.result_bytes(job['id'])
        assert wb2.usage(job['id'])==usage
        assert wb2.investigation_report(job['id'])==report
        assert wb2.resume_preview(job['id'])['available'] is False
    finally:wb2.close()
    assert len(calls)==5 and 'synthetic-secret' not in json.dumps(usage)


def test_numbered_generation_failure_keeps_receipt_and_does_not_resume(planned_repository,planned_workbench):
    wb,_,calls,fail_at=planned_workbench;fail_at[0]=3
    job=completed(wb,wb.submit(request_for(planned_repository))['id'])
    assert job['status']=='failed' and len(calls)==3
    assert set(job['stage_checkpoints'])=={'planned_input','seed_generation','planned_retrieval_1',
        'planned_generation_1','planned_retrieval_2'}
    assert wb.stage_bytes(job['id'],'planned_retrieval_2')
    usage=wb.usage(job['id'])
    assert usage['receipts'][-1]['stage']=='planned_generation_2'
    assert usage['receipts'][-1]['status']=='failed_bound' and usage['retained_bounds']==1
    assert wb.resume_preview(job['id'])['available'] is False
    assert 'private response' not in json.dumps(job) and 'synthetic-secret' not in json.dumps(usage)


@pytest.mark.parametrize('mode',['main_dev','followup','context','clarification'])
def test_planned_request_rejected_before_queue_or_provider(tmp_path,mode):
    with pytest.raises(ValueError,match='Planned'):
        validate_request({'mode':mode,'depth':'planned'})
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:pytest.fail('Provider constructed'))
    try:
        with pytest.raises(ValueError,match='Planned'):wb.submit({'mode':mode,'depth':'planned'})
        assert wb.history()==[]
    finally:wb.close()


def test_offline_planned_stage_http_is_guarded_and_static_module_served(tmp_path):
    with local_server(tmp_path) as server:
        job=completed(server.workbench,server.workbench.submit({'mode':'example','depth':'planned'})['id'])
        assert job['status']=='completed',job
        pack=json.loads(server.workbench.result_bytes(job['id']))
        assert pack['workflow_plan']['provider_calls']==0
        route='/api/jobs/'+job['id']+'/stages/planned_complete'
        assert http(server,'GET',route)[0]==403
        assert http(server,'GET',route,headers={'X-IssueIntel-Token':server.session_token})[0]==200
        assert http(server,'GET','/workflow.js')[0]==200
        assert server.workbench.resume_preview(job['id'])['available'] is False


def test_report_does_not_count_controller_stop_as_a_retrieval_round(planned_repository):
    from issueintel.investigation.controller import Action
    from issueintel.investigation.pipeline import investigate
    from issueintel.investigation.report import build_report
    case,path=planned_repository
    pack=investigate(case,path,planned_workflow=True,complete_json=PlannedModel(),
        controller=SimpleNamespace(name='selected',decide=lambda *a:Action.STOP))
    report=build_report(pack,source_pack_sha256='a'*64,job_id='b'*24)
    details=json.loads(next(row for row in report['provenance'] if row['kind']=='planned_workflow')['details'])
    assert details['completed_retrieval_rounds']==0


@pytest.mark.parametrize('mutation',['missing','route'])
def test_saved_planned_replay_rejects_tampering_even_when_export_hash_repaired(tmp_path,mutation):
    wb=Workbench(tmp_path)
    try:
        job=completed(wb,wb.submit({'mode':'example','depth':'planned'})['id'])
        pack=json.loads(wb.result_bytes(job['id']))
        if mutation=='missing':del pack['workflow_plan']
        else:
            from issueintel.investigation.planner import seal_plan
            pack['workflow_plan']['routing']['signals']['code_files']=99
            pack['workflow_plan']=seal_plan(pack['workflow_plan'])
        row=json.loads(wb._job_path(job['id']).read_bytes());ref=row['result']['pack.json']
        path=tmp_path/ref['path'];write_json(path,pack)
        ref['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();write_json(wb._job_path(job['id']),row)
        with pytest.raises(ValueError):wb.result_bytes(job['id'])
    finally:wb.close()


def test_cancel_during_planned_generation_retains_receipt_without_next_call(tmp_path,monkeypatch,planned_repository):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret')
    config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    model=PlannedModel();started=threading.Event();release=threading.Event();calls=[]
    def transport(request,**kwargs):
        body=json.loads(request.data);calls.append(body)
        if len(calls)==2:started.set();assert release.wait(10)
        answer=model(body['messages'],body['max_completion_tokens'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kwargs:GroqProvider(cfg,transport=transport,**kwargs))
    try:
        job=wb.submit(request_for(planned_repository));assert started.wait(10)
        assert wb.usage(job['id'])['receipts'][-1]['stage']=='planned_generation_1'
        wb.cancel(job['id']);release.set()
        assert completed(wb,job['id'])['status']=='cancelled'
        assert len(calls)==2 and wb.usage(job['id'])['tokens_charged_or_reserved']==30
        assert wb.stage_bytes(job['id'],'planned_retrieval_1')
        assert wb.resume_preview(job['id'])['available'] is False
    finally:release.set();wb.close()


def test_cli_planned_offline_and_early_benchmark_guard(tmp_path,planned_repository,monkeypatch,capsys):
    case,path=planned_repository;issue=tmp_path/'issue.txt';issue.write_text(case['problem_statement'])
    args=SimpleNamespace(root=tmp_path,output='artifacts/planned',planned=True,case_id=None,refinement_rounds=0,
        issue_file=issue,revision=case['revision'],repo=str(path),provider='none',no_critic=False)
    _investigate(args)
    output=capsys.readouterr().out.strip()
    pack=json.loads((Path(output).parent/'pack.json').read_bytes())
    assert pack['workflow_plan']['provider_calls']==0
    monkeypatch.setattr('issueintel.benchmarks.store.load_dev_cases',lambda *a:pytest.fail('Opened Dev data'))
    for invalid in [dict(case_id='verified:test-1'),dict(refinement_rounds=1)]:
        with pytest.raises(ValueError,match='--planned'):_investigate(SimpleNamespace(**(vars(args)|invalid)))
