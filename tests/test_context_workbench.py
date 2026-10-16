from hashlib import sha256
from pathlib import Path
import json
import threading
from types import SimpleNamespace

import pytest

from issueintel.investigation.workbench import Workbench, validate_request
from issueintel.investigation.comparison import compare_packs
from issueintel.integrations.github_sync import GitHubSync
from issueintel.runtime.io import digest
from test_github_index import connected, REPO, STAMP, Reader, BASE, issue
from test_repository_context import context_repo
from test_workbench import completed, http
from issueintel.interfaces.web import make_server


@pytest.fixture
def selected_workbench(context_repo,monkeypatch):
    index,case,snapshot=context_repo
    descriptor=index.status()['current_snapshot']
    monkeypatch.setattr(index,'_build_snapshot',lambda revision:descriptor)
    monkeypatch.setattr(GitHubSync,'due',lambda self:[])
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue',lambda url:{
        'url':case['issue_url'],'repository':REPO,'problem_statement':case['problem_statement'],'created_at':STAMP})
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',lambda *_a,**_k:SimpleNamespace(path=Path(snapshot.repository),commit_sha=snapshot.commit_sha))
    wb=Workbench(index.root,provider_factory=lambda *_a,**_k:pytest.fail('Unexpected model call'))
    try:
        parent=completed(wb,wb.submit({'mode':'github','issue_url':case['issue_url'],'revision':'HEAD'})['id'])
        assert parent['status']=='completed',parent
        yield wb,index,parent
    finally:wb.close()


def choose(wb,parent):
    result=wb.context_search({'job_id':parent['id'],'query':'Unicode parser'})
    source=next(row for row in result['results'] if row['kind']=='pull')
    return {'mode':'context','parent_job_id':parent['id'],'parent_pack_sha256':result['source_pack_sha256'],
        'selected_sources':[{key:source[key] for key in ('key','record_sha256','receipt_sha256')}]}


def test_selected_context_is_same_snapshot_query_and_exact_explicit_sources(selected_workbench,monkeypatch):
    wb,index,parent=selected_workbench
    before=wb.result_bytes(parent['id']);pack=json.loads(before)
    request=choose(wb,parent)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',lambda *_a,**_k:pytest.fail('Context rerun fetched'))
    child=completed(wb,wb.submit(request)['id'])
    assert child['status']=='completed',child
    actual=json.loads(wb.result_bytes(child['id']))
    assert actual['case']['problem_statement']==pack['case']['problem_statement']
    assert actual['case']['revision']==pack['snapshot']['commit_sha']
    assert actual['snapshot']==pack['snapshot']
    assert [row['record']['key'] for row in actual['repository_context']['sources']]==['pull:2']
    assert len([e for e in actual['evidence'] if e['kind']=='repository_context'])==1
    assert actual['context_selection']['selected_sources']==request['selected_sources']
    assert actual['leakage_audit']['strict_issue_time_verified'] is False
    assert 'verification_input' not in actual.get('stage_checkpoints',{})
    assert compare_packs(pack,actual)['changed_bindings']==['repository_context_sha256']
    assert wb.result_bytes(parent['id'])==before
    with pytest.raises(ValueError):wb.preview_comment(child['id'])
    assert not wb.resume_preview(child['id'])['available']


def test_saved_context_replays_without_mutable_index_and_supports_followup(selected_workbench):
    wb,index,parent=selected_workbench
    child=completed(wb,wb.submit(choose(wb,parent))['id'])
    saved=wb.result_bytes(child['id'])
    index.sync(reader=Reader({'/issues?':([issue(1)],None,digest('refresh'))}),refresh=True)
    assert wb.result_bytes(child['id'])==saved
    followup=completed(wb,wb.submit({'mode':'followup','parent_job_id':child['id'],'additional_context':'Synthetic observation only'})['id'])
    assert followup['status']=='completed',followup
    assert json.loads(wb.result_bytes(followup['id']))['followup']['depth']==2
    restarted=Workbench(wb.root)
    try:assert restarted.result_bytes(child['id'])==saved
    finally:restarted.close()


def test_reviewed_excerpt_matches_the_selected_inference_excerpt(selected_workbench):
    wb,index,parent=selected_workbench
    body='Original context '+('filler '*400)+' rareproof at the end'
    index.sync(reader=Reader({'/issues?':([issue(1),issue(2,pull=True,body=body)],None,digest(body))}),refresh=True)
    preview=wb.context_search({'job_id':parent['id'],'query':'rareproof'})
    row=next(result for result in preview['results'] if result['key']=='pull:2')
    request={'mode':'context','parent_job_id':parent['id'],'parent_pack_sha256':preview['source_pack_sha256'],
        'selected_sources':[{k:row[k] for k in ('key','record_sha256','receipt_sha256')}]}
    child=completed(wb,wb.submit(request)['id'])
    pack=json.loads(wb.result_bytes(child['id']))
    evidence=next(e for e in pack['evidence'] if e['kind']=='repository_context')
    assert evidence['text']==row['excerpt']
    assert evidence['sha256']==row['excerpt_sha256']


def test_changed_selection_is_rejected_before_queue_or_provider(selected_workbench,monkeypatch):
    wb,index,parent=selected_workbench
    request=choose(wb,parent)
    request['selected_sources'][0]['receipt_sha256']='f'*64
    monkeypatch.setattr(wb,'_enqueue',lambda *_a,**_k:pytest.fail('Invalid source queued'))
    with pytest.raises(ValueError):wb.submit(request)
    request=choose(wb,parent);request['parent_pack_sha256']='f'*64
    with pytest.raises(ValueError):wb.submit(request)


def test_selected_sources_reach_both_generation_and_verifier(selected_workbench,monkeypatch):
    from test_focused_retrieval import FakeModel
    wb,_,parent=selected_workbench
    model=FakeModel();model.model='openai/gpt-oss-120b';model.config=SimpleNamespace(max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *_:SimpleNamespace(reasoning_effort='high'))
    wb.provider_factory=lambda *_a,**_k:model
    request=choose(wb,parent);request['provider']='groq'
    child=completed(wb,wb.submit(request)['id'])
    assert child['status']=='completed',child
    assert len(model.calls)==2
    for payload in model.calls:
        context=[e for e in payload['evidence'] if e['kind']=='repository_context']
        assert len(context)==1 and context[0]['locator'].endswith('/pull/2')
        assert 'receipt_sha256' not in context[0].get('provenance',{})


def test_context_versions_keep_original_remote_authority(selected_workbench):
    wb,_,parent=selected_workbench
    child=completed(wb,wb.submit(choose(wb,parent))['id'])
    pack=json.loads(wb.result_bytes(child['id']))
    version=completed(wb,wb.submit_versions({'job_id':child['id'],'revision':pack['snapshot']['commit_sha']})['id'])
    assert version['status']=='completed',version
    assert json.loads(wb.versions_bytes(version['id']))['source']['job_id']==child['id']


def test_queued_context_rechecks_records_before_provider(selected_workbench,monkeypatch):
    wb,index,parent=selected_workbench
    request=choose(wb,parent);request['provider']='groq'
    release=threading.Event();started=threading.Event()
    def block():started.set();assert release.wait(10)
    wb.executor.submit(block);assert started.wait(5)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *_:pytest.fail('Config loaded before context replay'))
    try:
        child=wb.submit(request)
        index.sync(reader=Reader({'/issues?':([issue(1),issue(2,pull=True,body='Changed source')],None,digest('changed'))}),refresh=True)
        release.set()
        assert completed(wb,child['id'])['status']=='failed'
    finally:release.set()


@pytest.mark.parametrize('mode',['example','main_dev','followup','local'])
def test_context_selection_cannot_be_injected_into_another_mode(mode):
    with pytest.raises(ValueError):validate_request({'mode':mode,'selected_sources':[]})


def test_context_search_and_creation_require_a_local_session(selected_workbench):
    wb,_,parent=selected_workbench
    server=make_server(wb.root,port=0,workbench=wb)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        query=json.dumps({'job_id':parent['id'],'query':'Unicode'})
        assert http(server,'POST','/api/context-search',query,{'Content-Type':'application/json'})[0]==403
        status,_,raw=http(server,'POST','/api/context-search',query,headers)
        assert status==200 and json.loads(raw)['job_id']==parent['id']
        body=json.dumps(choose(wb,parent))
        assert http(server,'POST','/api/investigations',body,headers)[0]==202
    finally:
        server.shutdown();server.server_close();thread.join(5)
