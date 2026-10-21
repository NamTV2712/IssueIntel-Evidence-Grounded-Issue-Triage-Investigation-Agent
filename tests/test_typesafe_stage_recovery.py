"""Real product plumbing with two injected transports and no external API."""
from dataclasses import replace
from io import BytesIO
import json
import subprocess
from urllib.error import URLError

import pytest

from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.typesafe import TypesafeClient
from test_planned_pipeline import planned_repository,PlannedModel
from test_runtime import configured
from test_typesafe_workbench import enable_typesafe
from test_workbench import completed
from test_stage_jobs import approval


def jev_request(case,path):
    return {'mode':'local','repository':str(path),'revision':case['revision'],
        'issue_text':case['problem_statement'],'provider':'groq','depth':'planned','controller':'jev'}


@pytest.fixture
def jev_recovery_workbench(tmp_path,monkeypatch,planned_repository):
    case,path=planned_repository
    for n in range(16):
        (path/f'parser_context_{n:02}.py').write_text(f'def parse_list_context_{n}(items):\n    """parser parse_list crashes context"""\n    return items\n',encoding='utf-8')
    (path/'validation.md').write_text('validation validate_flag validity\nPinned validation contract.\n',encoding='utf-8')
    (path/'CHANGELOG.md').write_text('validation validate_flag validity\nRecorded validation behavior.\n',encoding='utf-8')
    def git(*args):return subprocess.check_output(['git','-c','core.hooksPath=','-C',str(path),*args]).decode().strip()
    git('add','.')
    git('-c','user.name=Synthetic','-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Bounded synthetic retrieval context')
    case=dict(case,revision=git('rev-parse','HEAD'))
    monkeypatch.setenv('GROQ_API_KEY','synthetic-jev-recovery-groq')
    enable_typesafe(tmp_path,monkeypatch)
    config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    model=PlannedModel();groq=[];typesafe=[]
    failures={'groq_at':set(),'decision_at':set(),'stage_after':None,'decision_tail':False}
    def generation(request,**kwargs):
        body=json.loads(request.data);groq.append(body)
        if len(groq) in failures['groq_at']:raise URLError('Synthetic Groq interruption')
        answer=model(body['messages'],body['max_completion_tokens'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    def decision(request,**kwargs):
        body=json.loads(request.data);typesafe.append(body)
        if len(typesafe) in failures['decision_at']:raise URLError('Synthetic Typesafe interruption')
        choices=body['questions']['next_action']['criteria'];action=failures.get('action') or ('retrieve' if 'retrieve' in choices else 'verify')
        return BytesIO(json.dumps({'model':body['model'],'answers':{'next_action':{'type':'choice','choice':action,
            'confidence':.9,'probabilities':{key:float(key==action) for key in choices}}},
            'usage':{'input_tokens':100,'output_tokens':10}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kw:GroqProvider(cfg,transport=generation,**kw),
        typesafe_factory=lambda cfg,**kw:TypesafeClient(cfg,transport=decision,**kw),automatic_sync=False)
    original=wb._save
    def save(row,**changes):
        value=original(row,**changes)
        if failures['stage_after'] and changes.get('stage')==failures['stage_after']:
            failures['stage_after']=None;raise OSError('Synthetic persisted stage interruption')
        if failures['decision_tail'] and changes.get('typesafe_decisions'):
            failures['decision_tail']=False;raise OSError('Synthetic persisted decision interruption')
        return value
    monkeypatch.setattr(wb,'_save',save)
    try:yield wb,case,path,model,groq,typesafe,failures
    finally:wb.close()


def test_initial_jev_product_has_owned_decision_journal(jev_recovery_workbench):
    wb,case,path,_,groq,typesafe,_=jev_recovery_workbench
    row=completed(wb,wb.submit(jev_request(case,path))['id']);assert row['status']=='completed',row
    pack=json.loads(wb.result_bytes(row['id']))
    assert pack['controller_decisions']['schema']=='typesafe_controller_v2'
    assert len(typesafe)==len(row['controller_attempts'])==4
    assert len(groq)==len(row['pipeline_attempts'])
    assert all(f'planned_decision_{n}' in row['stage_checkpoints'] for n in range(1,5))


@pytest.mark.parametrize('persisted_status',['queued','running'])
def test_crashed_jev_row_recovers_with_one_owned_source_lease(jev_recovery_workbench,persisted_status):
    wb,case,path,_,_,typesafe,failures=jev_recovery_workbench
    failures['stage_after']='planned_decision_1'
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    row=json.loads(wb._job_path(source['id']).read_bytes());wb._save(row,status=persisted_status)
    raw=wb._job_path(source['id']).read_bytes()
    preview=wb.stage_recoveries.preview(source['id']);assert preview['available'],preview
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id']);assert child['status']=='completed',child
    assert wb._job_path(source['id']).read_bytes()==raw and len(typesafe)==4


def test_persisted_decision_tail_is_reused_without_a_second_jev_call(jev_recovery_workbench):
    wb,case,path,_,groq,typesafe,failures=jev_recovery_workbench
    failures['decision_tail']=True
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    assert len(typesafe)==1 and len(source['typesafe_decisions'])==1
    assert 'planned_decision_1' not in source['stage_checkpoints']
    raw=wb._job_path(source['id']).read_bytes();preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'] and preview['prior_controller_calls']==1,preview
    assert len(typesafe)==1 and len(groq)==1
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['controller_decisions']['decision_owners'][0]==source['id']
    assert len(typesafe)==4 and len(child['controller_attempts'])==3
    assert wb._job_path(source['id']).read_bytes()==raw


@pytest.mark.parametrize('boundary',[
    'execution_input','initial_retrieval','planned_input','seed_generation',
    'planned_decision_1','planned_retrieval_1','planned_generation_1',
    'planned_decision_2','planned_retrieval_2','planned_generation_2',
    'planned_decision_3','planned_retrieval_3','planned_generation_3',
    'planned_decision_4','verification','planned_complete'])
def test_each_durable_jev_boundary_reuses_completed_work_and_immutable_receipts(jev_recovery_workbench,boundary):
    wb,case,path,model,groq,typesafe,failures=jev_recovery_workbench
    failures['stage_after']=boundary
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    assert boundary in source['stage_checkpoints'],source
    raw=wb._job_path(source['id']).read_bytes()
    stages={name:wb.stage_bytes(source['id'],name) for name in source['stage_checkpoints']}
    calls=(len(groq),len(typesafe));preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'],preview
    assert calls==(len(groq),len(typesafe))
    assert preview['controller']=='jev' and preview['prior_controller_calls']==len(source['controller_attempts'])
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']));sidecar=pack['controller_decisions']
    assert len(typesafe)==4 and len(source['controller_attempts'])+len(child['controller_attempts'])==4
    generations=set(stages)|set(child.get('stage_checkpoints',{}))
    assert model.generations==sum(name=='seed_generation' or name.startswith('planned_generation_') for name in generations)
    assert len(groq)==len(source['pipeline_attempts'])+len(child['pipeline_attempts'])
    assert all(record['binding']['job_id']==owner for record,owner in zip(sidecar['records'],sidecar['decision_owners']))
    assert not set(source['typesafe_receipts']) & set(child['typesafe_receipts'])
    assert wb._job_path(source['id']).read_bytes()==raw
    assert stages=={name:wb.stage_bytes(source['id'],name) for name in stages}


def test_critic_only_jev_recovery_does_not_load_typesafe_or_repeat_decisions(jev_recovery_workbench,monkeypatch):
    wb,case,path,_,groq,typesafe,failures=jev_recovery_workbench
    failures['groq_at'].add(5)
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    assert len(typesafe)==4 and len(groq)==5
    monkeypatch.setattr('issueintel.runtime.typesafe.TypesafeConfig.load',lambda *a:pytest.fail('Critic recovery loaded Typesafe config'))
    wb.typesafe_factory=lambda *a,**k:pytest.fail('Critic recovery constructed Typesafe provider')
    preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'] and preview['next_stage']=='verification' and preview['api_providers']==['groq'],preview
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id']);assert child['status']=='completed',child
    assert len(typesafe)==4 and len(child['controller_attempts'])==0 and child['typesafe_receipts']=={}
    assert [a['stage'] for a in child['pipeline_attempts']]==['verification']


def test_unsettled_jev_receipt_stops_recovery_without_another_provider_call(jev_recovery_workbench,monkeypatch):
    from issueintel.runtime.typesafe import _Ledger,TypesafeError
    wb,case,path,_,groq,typesafe,_=jev_recovery_workbench
    def unavailable(*a,**k):raise TypesafeError('ledger_unavailable')
    monkeypatch.setattr(_Ledger,'settle',unavailable)
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    assert len(typesafe)==len(groq)==1
    preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'] is False and preview['reason']=='controller_receipt_unsettled',preview
    assert len(typesafe)==len(groq)==1


def settled_orphan(fixture,monkeypatch):
    wb,case,path,_,groq,typesafe,failures=fixture
    original=wb._save;stop=[True]
    def interrupted(row,**changes):
        value=original(row,**changes)
        if stop[0] and any(r['status']=='settled' for r in changes.get('typesafe_receipts',{}).values()):
            stop[0]=False;raise InterruptedError('Synthetic interruption after final receipt before decision')
        return value
    monkeypatch.setattr(wb,'_save',interrupted)
    source=completed(wb,wb.submit(jev_request(case,path))['id'])
    assert source['status']=='cancelled' and source['typesafe_decisions']==[] and len(typesafe)==1,source
    return source


def test_terminal_orphan_is_disclosed_retained_and_debited_before_explicit_new_decision(jev_recovery_workbench,monkeypatch):
    wb,_,_,_,groq,typesafe,failures=jev_recovery_workbench
    source=settled_orphan(jev_recovery_workbench,monkeypatch);raw=wb._job_path(source['id']).read_bytes()
    preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'] and preview['next_provider']=='typesafe' and preview['orphan_terminal_receipts']==1,preview
    assert preview['prior_controller_calls']==1 and len(typesafe)==len(groq)==1
    failures['action']='verify'
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id']);assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['controller_decisions']['recovery']['inherited_calls']==1
    assert pack['controller_decisions']['recovery']['new_calls']==1
    assert len(typesafe)==2 and not set(source['typesafe_receipts']) & set(child['typesafe_receipts'])
    assert wb._job_path(source['id']).read_bytes()==raw


def test_exhausted_jev_allowance_blocks_a_generation_that_cannot_be_followed_by_a_decision(jev_recovery_workbench,monkeypatch):
    wb,_,_,_,groq,typesafe,failures=jev_recovery_workbench
    source=settled_orphan(jev_recovery_workbench,monkeypatch)
    preview=wb.stage_recoveries.preview(source['id']);assert preview['available'],preview
    failures['stage_after']='planned_retrieval_3'
    stopped=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert stopped['status']=='failed' and len(typesafe)==4,stopped
    before=(len(groq),len(typesafe));denied=wb.stage_recoveries.preview(stopped['id'])
    assert denied['available'] is False and denied['reason']=='original_controller_budget_exhausted',denied
    assert before==(len(groq),len(typesafe))


def test_resealed_orphan_from_another_annotation_cannot_authorize_a_new_call(jev_recovery_workbench,monkeypatch):
    from issueintel.runtime.io import digest
    wb,_,_,_,groq,typesafe,_=jev_recovery_workbench
    source=settled_orphan(jev_recovery_workbench,monkeypatch)
    row=json.loads(wb._job_path(source['id']).read_bytes())
    receipt=next(iter(row['typesafe_receipts'].values()));receipt['binding']['annotation_sha256']='0'*64
    receipt['sha256']=digest({k:v for k,v in receipt.items() if k!='sha256'})
    wb._save(row);before=(len(groq),len(typesafe))
    assert wb.stage_recoveries.preview(source['id'])['available'] is False
    assert before==(len(groq),len(typesafe))


def test_finished_jev_pipeline_exports_without_loading_provider_configuration(jev_recovery_workbench,monkeypatch):
    wb,case,path,_,groq,typesafe,failures=jev_recovery_workbench
    failures['stage_after']='planned_complete'
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    counts=(len(groq),len(typesafe))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Export loaded Groq config'))
    monkeypatch.setattr('issueintel.runtime.typesafe.TypesafeConfig.load',lambda *a:pytest.fail('Export loaded Typesafe config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Export constructed Groq')
    wb.typesafe_factory=lambda *a,**k:pytest.fail('Export constructed Typesafe')
    preview=wb.stage_recoveries.preview(source['id'])
    assert preview['available'] and not preview['requires_api'] and preview['api_providers']==[],preview
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id']);assert child['status']=='completed',child
    assert counts==(len(groq),len(typesafe)) and child['controller_attempts']==[] and child['pipeline_attempts']==[]
    raw=wb.result_bytes(child['id'])
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a,**k:pytest.fail('Saved read accessed Git'))
    assert wb.result_bytes(child['id'])==raw and wb.report_bytes(child['id'])


def test_saved_failed_bound_fallback_is_replayed_instead_of_retried(jev_recovery_workbench):
    wb,case,path,_,_,typesafe,failures=jev_recovery_workbench
    failures['decision_at'].add(1);failures['stage_after']='planned_decision_1'
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    first=source['typesafe_decisions'][0]
    assert first['fallback'] and first['receipt']['status']=='failed_bound'
    preview=wb.stage_recoveries.preview(source['id']);assert preview['available'],preview
    child=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id']);assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['controller_decisions']['records'][0]==first and len(typesafe)==4
    assert child['typesafe_decisions'][0]['decision_index']==2


def test_repeated_critic_failures_do_not_mint_more_jev_attempts_or_duplicate_intents(jev_recovery_workbench,tmp_path):
    from test_handoff import exported
    from issueintel.investigation.portable_verify import verify
    wb,case,path,_,groq,typesafe,failures=jev_recovery_workbench
    failures['groq_at'].update({5,6})
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    preview=wb.stage_recoveries.preview(source['id']);assert preview['available'],preview
    failed=completed(wb,wb.stage_recoveries.submit(source['id'],approval(preview))['id'])
    assert failed['status']=='failed' and len(typesafe)==4,failed
    assert wb.stage_recoveries.submit(source['id'],approval(preview))['id']==failed['id']
    preview=wb.stage_recoveries.preview(failed['id']);assert preview['available'] and preview['prior_controller_calls']==4,preview
    child=completed(wb,wb.stage_recoveries.submit(failed['id'],approval(preview))['id']);assert child['status']=='completed',child
    assert len(typesafe)==4 and len(groq)==7 and failed['controller_attempts']==child['controller_attempts']==[]
    _,raw=exported(wb,child['id']);archive=tmp_path/'repeated-jev.zip';archive.write_bytes(raw)
    assert verify(archive)['runs']==3


def test_durable_cli_can_create_a_jev_journal_with_the_same_product_recipe(jev_recovery_workbench,tmp_path,monkeypatch,capsys):
    from test_product_recovery_cli import invoke
    wb,case,path,_,_,typesafe,_=jev_recovery_workbench
    monkeypatch.setattr('issueintel.interfaces.cli._product_workbench',lambda root:Workbench(root,
        provider_factory=wb.provider_factory,typesafe_factory=wb.typesafe_factory,automatic_sync=False))
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda *a:None)
    issue=tmp_path/'synthetic-cli-issue.txt';issue.write_text(case['problem_statement'],encoding='utf-8')
    code,row,error=invoke(wb.root,capsys,'investigate','--durable','--repo',str(path),'--revision',case['revision'],
        '--issue-file',str(issue),'--planned','--controller','jev','--provider','groq')
    assert code==0 and row['status']=='completed' and row['controller']=='jev',(row,error)
    assert len(typesafe)==4 and 'planned_decision_4' in row['stage_checkpoints']
    assert json.loads(wb.result_bytes(row['id']))['controller_decisions']['schema']=='typesafe_controller_v2'


def test_jev_cli_preview_and_paid_recovery_keep_web_intent_and_exact_provider_owners(jev_recovery_workbench,monkeypatch,capsys):
    from test_product_recovery_cli import invoke
    wb,case,path,_,groq,typesafe,failures=jev_recovery_workbench
    failures['stage_after']='planned_decision_1'
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    monkeypatch.setattr('issueintel.interfaces.cli._product_workbench',lambda root:Workbench(root,
        provider_factory=wb.provider_factory,typesafe_factory=wb.typesafe_factory,automatic_sync=False))
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda *a:pytest.fail('Recovery CLI eagerly read env'))
    before=(len(groq),len(typesafe))
    code,preview,error=invoke(wb.root,capsys,'resume-stages','--job',source['id'])
    assert code==0 and preview['available'] and preview['controller']=='jev' and not error
    assert before==(len(groq),len(typesafe))
    code,child,error=invoke(wb.root,capsys,'resume-stages','--job',source['id'],
        '--binding-sha256',preview['binding_sha256'],'--with-api')
    assert code==0 and child['status']=='completed',(child,error)
    assert len(typesafe)==4 and len(child['typesafe_decisions'])==3
    assert wb.stage_recoveries.submit(source['id'],approval(preview))['id']==child['id']


def test_jev_http_recovery_binds_preview_cost_consent_session_and_origin(jev_recovery_workbench):
    import threading
    from issueintel.interfaces.web import make_server
    from test_workbench import http
    wb,case,path,_,groq,typesafe,failures=jev_recovery_workbench
    failures['stage_after']='planned_decision_1'
    source=completed(wb,wb.submit(jev_request(case,path))['id']);assert source['status']=='failed',source
    server=make_server(wb.root,port=0,workbench=wb);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        route='/api/jobs/'+source['id']+'/stage-recovery'
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        assert http(server,'GET',route)[0]==403
        before=(len(groq),len(typesafe));status,_,raw=http(server,'GET',route,headers=headers);preview=json.loads(raw)
        assert status==200 and preview['controller']=='jev' and preview['prior_controller_calls']==1,preview
        assert before==(len(groq),len(typesafe))
        body=json.dumps(approval(preview)).encode()
        assert http(server,'POST',route,body,headers=headers|{'Origin':'https://invalid.example'})[0]==403
        wrong=json.dumps(dict(approval(preview),confirmation='resume_product_stages_without_api')).encode()
        assert http(server,'POST',route,wrong,headers=headers)[0]==400
        status,_,raw=http(server,'POST',route,body,headers=headers);assert status==202
        child=completed(wb,json.loads(raw)['id']);assert child['status']=='completed',child
        assert len(typesafe)==4 and len(child['typesafe_receipts'])==3
    finally:server.shutdown();thread.join();server.server_close()
