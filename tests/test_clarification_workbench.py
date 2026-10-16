"""Synthetic product-only guided answers; no network, benchmark or model calls."""
import hashlib
import json
import threading

import pytest

from issueintel.investigation.workbench import Workbench, validate_request
from issueintel.runtime.io import write_json
from test_workbench import completed, local_server, http, SyntheticProvider
from test_github_index import connected
from test_repository_context import context_repo
from test_context_workbench import selected_workbench, choose


@pytest.fixture
def wb(tmp_path):
    workbench=Workbench(tmp_path,provider_factory=lambda *a,**k:pytest.fail('Unexpected provider'))
    try:yield workbench
    finally:workbench.close()


def source(wb):
    job=completed(wb,wb.submit({'mode':'example'})['id'])
    assert job['status']=='completed'
    return job


def answer_request(wb,identifier,**overrides):
    intake=wb.intake(identifier)
    result={'mode':'clarification','parent_job_id':identifier,
            'parent_pack_sha256':intake['source_pack_sha256'],
            'questions_sha256':intake['clarifications']['sha256'],'language':'vi',
            'answers':[{'question_id':intake['clarifications']['questions'][0]['id'],'answer':'chưa biết'}]}
    return dict(result,**overrides)


def repair_export(wb,job,mutate):
    path=wb.root/job['result']['pack.json']['path']
    pack=json.loads(path.read_bytes());mutate(pack);write_json(path,pack)
    row=json.loads(wb._job_path(job['id']).read_bytes())
    row['result']['pack.json']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(wb._job_path(job['id']),row)


def test_read_questions_is_source_bound_and_never_loads_provider(wb,monkeypatch):
    parent=source(wb);raw=wb.result_bytes(parent['id'])
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('No API for question review'))
    result=wb.intake(parent['id'])
    assert result['clarifications_available']
    bundle=result['clarifications']
    assert bundle['query_sha256']==result['query_sha256']
    assert bundle['cue_basis']=='public_query'
    assert {q['id'] for q in bundle['questions']}=={'missing:expected_behavior','missing:reproduction','missing:environment'}
    assert wb.result_bytes(parent['id'])==raw


@pytest.mark.parametrize('override',[
    {'language':'fr'},{'answers':[]},{'answers':[{'question_id':'missing:environment','answer':''}]},
    {'questions_sha256':'bad'},{'parent_pack_sha256':'bad'},{'revision':'HEAD'},
    {'issue_text':'override'},{'selected_sources':[]},{'additional_context':'override'},
    {'case_id':'verified:hidden'},
])
def test_clarification_input_rejects_source_override(wb,override):
    request=answer_request(wb,source(wb)['id'],**override)
    with pytest.raises(ValueError):validate_request(request)


@pytest.mark.parametrize('change',['parent','questions','unoffered'])
def test_stale_source_question_or_unoffered_answer_rejected_before_queue(wb,change,monkeypatch):
    parent=source(wb);request=answer_request(wb,parent['id'])
    if change=='parent':request['parent_pack_sha256']='0'*64
    elif change=='questions':request['questions_sha256']='0'*64
    else:request['answers']=[{'question_id':'missing:observed_behavior','answer':'already mentioned'}]
    monkeypatch.setattr(wb,'_enqueue',lambda *a,**k:pytest.fail('Invalid answers queued'))
    with pytest.raises(ValueError):wb.submit(request)


def test_partial_unknown_answers_pin_snapshot_and_replay_after_restart(wb):
    parent=source(wb);original=wb.result_bytes(parent['id']);old=json.loads(original)
    request=answer_request(wb,parent['id'])
    child=completed(wb,wb.submit(request)['id']);assert child['status']=='completed',child
    raw=wb.result_bytes(child['id']);pack=json.loads(raw);link=pack['followup'];receipt=link['clarification']
    assert pack['snapshot']==old['snapshot']
    assert link['parent_pack_sha256']==hashlib.sha256(original).hexdigest()
    assert receipt['questions_sha256']==request['questions_sha256']
    assert receipt['language']=='vi' and receipt['answers'][0]['answer']=='chưa biết'
    assert 'mong đợi' in receipt['answers'][0]['question']
    assert pack['leakage_audit']['strict_issue_time_verified'] is False
    assert pack['case']['issue_created_at'] is None
    assert pack['annotation'] is None
    assert pack['trace'][-1]['stage']=='clarification_answers'
    assert pack['intake']['fields']['expected_behavior']['status']=='not_found'
    assert 'missing:expected_behavior' in [q['id'] for q in wb.intake(child['id'])['clarifications']['questions']]
    assert b'Guided clarification answers' in wb.result_bytes(child['id'],'report.md')
    assert wb.result_bytes(parent['id'])==original
    comparison=wb.compare({'before_id':parent['id'],'after_id':child['id']})
    assert not comparison['same_context']
    with pytest.raises(ValueError):wb.preview_comment(child['id'])
    assert wb.resume_preview(child['id'])['available'] is False
    other=Workbench(wb.root)
    try:
        assert other.result_bytes(child['id'])==raw
        assert other.intake(child['id'])==wb.intake(child['id'])
    finally:other.close()


def test_derived_cues_refer_to_exact_projection_not_generated_question_text(wb):
    parent=source(wb)
    request=answer_request(wb,parent['id'],answers=[{'question_id':'missing:environment','answer':'Python 3.12\nWindows 11'}])
    child=completed(wb,wb.submit(request)['id']);assert child['status']=='completed'
    pack=json.loads(wb.result_bytes(child['id']));intake=wb.intake(child['id'])
    assert intake['cue_basis']=='user_content_excluding_generated_questions'
    assert intake['query_sha256']==hashlib.sha256(pack['case']['problem_statement'].encode()).hexdigest()
    assert intake['cue_text_sha256']==hashlib.sha256(intake['cue_text'].encode()).hexdigest()
    assert intake['fields']['environment']['status']=='mentioned'
    assert intake['fields']['expected_behavior']['status']=='not_found'
    for field in intake['fields'].values():
        for cue in field['sources']:
            assert cue['text']==intake['cue_text'].splitlines()[cue['line']-1]
            assert cue['source_text_sha256']==intake['cue_text_sha256']
    assert pack['intake']['fields']==intake['fields']


@pytest.mark.parametrize('mutation',['answer','question','language','hash','additional','intake','snapshot','case','remove_receipt','remove_followup'])
def test_receipt_or_projection_tamper_denied_even_with_repaired_own_export(wb,mutation):
    parent=source(wb);child=completed(wb,wb.submit(answer_request(wb,parent['id']))['id'])
    def mutate(pack):
        receipt=pack['followup']['clarification']
        if mutation in {'answer','question'}:receipt['answers'][0][mutation]='changed'
        elif mutation=='language':receipt['language']='en'
        elif mutation=='hash':receipt['questions_sha256']='0'*64
        elif mutation=='additional':receipt['additional_context']+='changed'
        elif mutation=='snapshot':pack['snapshot']['files'][0]['raw_sha256']='0'*64
        elif mutation=='case':pack['case']['split']='main_test'
        elif mutation=='remove_receipt':pack['followup'].pop('clarification')
        elif mutation=='remove_followup':pack.pop('followup')
        else:pack['intake']['cue_text']+='changed'
    repair_export(wb,child,mutate)
    with pytest.raises(ValueError):wb.result_bytes(child['id'])


@pytest.mark.parametrize('mutation',['excerpt','snapshot','missing_git'])
def test_parent_snapshot_and_citations_replayed_before_provider(wb,monkeypatch,mutation):
    parent=source(wb);pack=json.loads(wb.result_bytes(parent['id']))
    if mutation=='excerpt':
        def mutate(value):
            record=next(e for e in value['evidence'] if e['kind']=='code')
            record['text']='forged source';record['sha256']=hashlib.sha256(record['text'].encode()).hexdigest()
        repair_export(wb,parent,mutate)
    elif mutation=='snapshot':
        repair_export(wb,parent,lambda value:value['snapshot']['files'][0].update(raw_sha256='0'*64))
    else:
        git_dir=wb.root/'artifacts/demo/example_repo/.git'
        assert str(git_dir.resolve()).startswith(str(wb.root.resolve()))
        git_dir.rename(git_dir.with_name('.git-unavailable'))
    request=answer_request(wb,parent['id'],provider='groq')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Provider must follow proof replay'))
    child=completed(wb,wb.submit(request)['id'])
    assert child['status']=='failed'


def test_source_changed_between_submit_and_worker_denies_before_provider(wb,monkeypatch):
    parent=source(wb);request=answer_request(wb,parent['id'],provider='groq')
    captured=[];ready=threading.Event();original=wb._run
    def defer(row):captured.append(row);ready.set()
    monkeypatch.setattr(wb,'_run',defer)
    child=wb.submit(request);assert ready.wait(5)
    repair_export(wb,parent,lambda pack:pack.update(confidence={'level':'changed','probability':None}))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Stale source cannot call provider'))
    original(captured[0])
    assert wb.job(child['id'])['status']=='failed'


@pytest.mark.parametrize('descendant',[False,True])
def test_original_request_authority_blocks_benchmark_through_followup(wb,descendant):
    parent=source(wb);request=answer_request(wb,parent['id'])
    chosen=parent
    if descendant:
        chosen=completed(wb,wb.submit({'mode':'followup','parent_job_id':parent['id'],'additional_context':'More context'})['id'])
        request=answer_request(wb,chosen['id'])
    row=json.loads(wb._job_path(parent['id']).read_bytes())
    row['request']={'mode':'main_dev','case_id':'verified:synthetic'}
    write_json(wb._job_path(parent['id']),row)
    assert wb.intake(chosen['id'])['clarifications_available'] is False
    with pytest.raises(ValueError):wb.submit(request)


def test_mixed_freeform_guided_chain_shares_eight_generation_limit(wb):
    parent=source(wb)
    for depth in range(1,9):
        request=(answer_request(wb,parent['id']) if depth%2 else
                 {'mode':'followup','parent_job_id':parent['id'],'additional_context':'chưa biết'})
        parent=completed(wb,wb.submit(request)['id']);assert parent['status']=='completed',parent
        assert json.loads(wb.result_bytes(parent['id']))['followup']['depth']==depth
    assert not wb.intake(parent['id'])['clarifications_available']
    pack,raw,_=wb._load_pack(parent['id'])
    request={'mode':'clarification','parent_job_id':parent['id'],'parent_pack_sha256':hashlib.sha256(raw).hexdigest(),
             'questions_sha256':wb._questions_for_pack(pack)['sha256'],'answers':[{'question_id':'missing:environment','answer':'unknown'}],'language':'en'}
    with pytest.raises(ValueError):wb.submit(request)


def test_http_session_bound_question_review_and_explicit_submission(tmp_path):
    with local_server(tmp_path) as server:
        wb=server.workbench;parent=source(wb);path='/api/jobs/'+parent['id']+'/intake'
        status,_,_=http(server,'GET',path);assert status==403
        headers={'X-IssueIntel-Token':server.session_token,'Origin':'http://127.0.0.1:'+str(server.server_port),'Content-Type':'application/json'}
        status,_,raw=http(server,'GET',path,headers=headers);assert status==200
        assert json.loads(raw)['clarifications_available']
        request=answer_request(wb,parent['id'])
        status,_,body=http(server,'POST','/api/investigations',json.dumps(request).encode(),headers)
        assert status==202,body
        assert completed(wb,json.loads(body)['id'])['status']=='completed'
        status,_,_=http(server,'POST','/api/investigations',json.dumps(dict(request,questions_sha256='0'*64)).encode(),headers)
        assert status==400


def test_provider_runs_only_on_explicit_groq_submission(wb,monkeypatch):
    from types import SimpleNamespace
    parent=source(wb);provider=SyntheticProvider();factories=[]
    config=SimpleNamespace(reasoning_effort='high',model='openai/gpt-oss-120b',max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    def factory(*a,**k):factories.append(True);return provider
    wb.provider_factory=factory
    request=answer_request(wb,parent['id'],provider='groq')
    assert not factories
    child=completed(wb,wb.submit(request)['id']);assert child['status']=='completed',child
    assert len(factories)==1
    assert not wb.resume_preview(child['id'])['available']


def test_saved_request_cannot_silently_change_submitted_answers(wb):
    parent=source(wb);child=completed(wb,wb.submit(answer_request(wb,parent['id']))['id'])
    row=json.loads(wb._job_path(child['id']).read_bytes())
    row['request']['answers'][0]['answer']='changed after submission'
    write_json(wb._job_path(child['id']),row)
    with pytest.raises(ValueError):wb.result_bytes(child['id'])


def test_selected_context_ancestry_is_preserved_without_carrying_query_bound_sources(selected_workbench,monkeypatch):
    wb,index,parent=selected_workbench
    selected=completed(wb,wb.submit(choose(wb,parent))['id']);before=wb.result_bytes(selected['id'])
    monkeypatch.setattr('issueintel.retrieval.repository_context.collect_context',lambda *a,**k:pytest.fail('No automatic current context rebind'))
    child=completed(wb,wb.submit(answer_request(wb,selected['id']))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert pack['followup']['depth']==2
    assert pack.get('repository_context') is None and pack.get('context_selection') is None
    assert not any(e['kind']=='repository_context' for e in pack['evidence'])
    assert wb.result_bytes(selected['id'])==before
    # Context + guided + freeform descendants share the same eight-generation bound.
    for depth in range(3,9):
        request=(answer_request(wb,child['id']) if depth%2 else
                 {'mode':'followup','parent_job_id':child['id'],'additional_context':'unknown'})
        child=completed(wb,wb.submit(request)['id']);assert child['status']=='completed',child
        assert json.loads(wb.result_bytes(child['id']))['followup']['depth']==depth
    assert not wb.intake(child['id'])['clarifications_available']
