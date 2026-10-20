"""Owned Workbench memory sources, bounded local work and session-guarded HTTP."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import threading

import pytest

from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench
from issueintel.interfaces.web import make_server
from issueintel.runtime.io import write_json
from test_runtime import configured
from test_focused_retrieval import FakeModel
from test_workbench import completed,http


@pytest.fixture
def memory_workbench(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-memory-key')
    config=replace(configured(tmp_path),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    model=FakeModel(focus='parser parse_list items')
    def transport(request,**kwargs):
        body=json.loads(request.data);answer=model(body['messages'],body['max_completion_tokens'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    wb=Workbench(tmp_path,provider_factory=lambda cfg,**kw:GroqProvider(cfg,transport=transport,**kw))
    try:
        job=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert job['status']=='completed',job
        yield wb,job,model
    finally:wb.close()


def bound(wb,job,**values):
    return {'source_pack_sha256':sha256(wb.result_bytes(job['id'])).hexdigest(),**values}


def architecture(wb,job):
    return wb.memory.capture(job['id'],bound(wb,job,kind='architecture',max_facts=100))


def selected(wb,job,value,**extra):
    return bound(wb,job,capture_id=value['receipt']['id'],receipt_sha256=value['receipt']['sha256'],**extra)


def test_owned_source_capture_search_open_and_assessment_preserve_pack_and_usage(memory_workbench):
    wb,job,model=memory_workbench;raw=wb.result_bytes(job['id']);usage=wb.usage(job['id']);calls=len(model.calls)
    status=wb.memory.status(job['id'])
    assert status['available'] and status['can_capture_lesson'] and status['model_calls']==0
    value=architecture(wb,job)
    assert value==architecture(wb,job)
    assert value['receipt']['source_job_id']==job['id']
    assert value['receipt']['source_pack_sha256']==sha256(raw).hexdigest()
    listing=wb.memory.search(job['id'],bound(wb,job,query='parse_list',limit=20))
    assert listing['results'][0]['id']==value['receipt']['id'] and listing['source']==value['source']
    assert wb.memory.open(job['id'],selected(wb,job,value))==value
    result=wb.memory.assess(job['id'],selected(wb,job,value))
    assert set(result['assessment']['counts'])=={'revalidated_static'}
    assert raw==wb.result_bytes(job['id']) and usage==wb.usage(job['id']) and len(model.calls)==calls


def test_prior_hypotheses_are_unverified_and_sources_remain_at_capture_commit(memory_workbench):
    wb,job,_=memory_workbench
    value=wb.memory.capture(job['id'],bound(wb,job,kind='lesson',hypothesis_ids=['h0'],note='Review the input guard'))
    assert not value['memory']['historical_eligible'] and value['memory']['truth_level']=='unverified_prior_investigation'
    evidence=value['memory']['evidence'][0]
    source=wb.memory.source(job['id'],selected(wb,job,value,item_id=evidence['id'],role='source'))
    assert source['source_commit_sha']==value['memory']['source']['commit_sha']
    assert source['excerpt_sha256']==sha256(source['excerpt'].encode()).hexdigest()
    assert source['locator']==evidence['locator'] and source['model_calls']==0


def test_saved_memory_views_work_after_checkout_disappears_without_git_provider_or_config(memory_workbench,monkeypatch):
    wb,job,model=memory_workbench;value=architecture(wb,job);before=len(model.calls)
    pack=json.loads(wb.result_bytes(job['id']));path=Path(pack['snapshot']['repository']);moved=path.with_name('detached_memory_source')
    assert path.resolve().is_relative_to(wb.root.resolve()) and moved.resolve().is_relative_to(wb.root.resolve())
    path.rename(moved)
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Saved view read Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved view read API config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved view constructed provider')
    assert wb.memory.status(job['id'])['available']
    assert wb.memory.open(job['id'],selected(wb,job,value))==value
    assert wb.memory.search(job['id'],bound(wb,job,query='',limit=20))['results']
    fact=value['memory']['facts'][0]
    assert wb.memory.source(job['id'],selected(wb,job,value,item_id=fact['id'],role='source'))['excerpt']
    assert len(model.calls)==before


def test_other_revision_marks_affected_dependencies_and_keeps_original_capture(memory_workbench):
    wb,job,_=memory_workbench;value=architecture(wb,job);original=deepcopy(value)
    pack=json.loads(wb.result_bytes(job['id']));path=Path(pack['snapshot']['repository'])
    (path/'parser.py').write_text('def parse_list(items):\n    return []\n',encoding='utf-8')
    subprocess.run(['git','-C',str(path),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-c','core.hooksPath=','-C',str(path),'-c','user.name=Synthetic',
        '-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Changed parser'],check=True,capture_output=True)
    current=completed(wb,wb.submit({'mode':'example'})['id']);assert current['status']=='completed'
    result=wb.memory.assess(current['id'],selected(wb,current,value))
    assert result['source']['commit_sha']!=value['memory']['source']['commit_sha']
    assert result['assessment']['counts']['source_changed']>=1
    assert wb.memory.open(job['id'],selected(wb,job,value))==original


@pytest.mark.parametrize('action',['capture','search','open','assess','source'])
def test_stale_pack_hash_is_rejected_before_git_or_memory_access(memory_workbench,monkeypatch,action):
    wb,job,_=memory_workbench
    value=architecture(wb,job)
    request=(bound(wb,job,kind='architecture',max_facts=10) if action=='capture' else
        bound(wb,job,query='',limit=20) if action=='search' else selected(wb,job,value))
    if action=='source':request.update(item_id=value['memory']['facts'][0]['id'],role='source')
    request['source_pack_sha256']='f'*64
    monkeypatch.setattr(wb.memory.store,'read',lambda *a:pytest.fail('Stale source read memory'))
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Stale source read Git'))
    with pytest.raises(ValueError,match='source report changed'):getattr(wb.memory,action)(job['id'],request)


def test_benchmark_job_denied_before_pack_load(memory_workbench,monkeypatch):
    wb,job,_=memory_workbench;row=json.loads(wb._job_path(job['id']).read_bytes())
    row['mode']='main_dev';row['request']['mode']='main_dev';write_json(wb._job_path(job['id']),row)
    monkeypatch.setattr(wb,'_load_pack',lambda *a:pytest.fail('Benchmark memory source pack was read'))
    assert wb.memory.status(job['id'])['available'] is False
    with pytest.raises(ValueError,match='owned product'):wb.memory.capture(job['id'],{'source_pack_sha256':'a'*64,'kind':'architecture','max_facts':10})


@pytest.mark.parametrize('action',['open','assess','source'])
def test_wrong_receipt_or_repository_cannot_be_opened_or_assessed(memory_workbench,monkeypatch,action):
    wb,job,_=memory_workbench;value=architecture(wb,job);request=selected(wb,job,value)
    if action=='source':request.update(item_id=value['memory']['facts'][0]['id'],role='source')
    request['receipt_sha256']='f'*64
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Wrong receipt read Git'))
    with pytest.raises(ValueError,match='another repository or receipt'):getattr(wb.memory,action)(job['id'],request)
    changed=deepcopy(value);changed['receipt']['repository']='other/repo'
    monkeypatch.setattr(wb.memory.store,'read',lambda *a:changed)
    request['receipt_sha256']=value['receipt']['sha256']
    with pytest.raises(ValueError,match='another repository or receipt'):getattr(wb.memory,action)(job['id'],request)


def test_capture_and_assessment_share_bounded_work_capacity(memory_workbench):
    wb,job,_=memory_workbench;value=architecture(wb,job);taken=0
    while wb.capacity.acquire(blocking=False):taken+=1
    try:
        with pytest.raises(RuntimeError):architecture(wb,job)
        with pytest.raises(RuntimeError):wb.memory.assess(job['id'],selected(wb,job,value))
    finally:
        for _ in range(taken):wb.capacity.release()
    assert architecture(wb,job)==value


def test_failed_git_replay_releases_capacity_without_creating_a_capture(memory_workbench,monkeypatch):
    wb,job,_=memory_workbench
    original=__import__('issueintel.investigation.question_jobs',fromlist=['replay_snapshot']).replay_snapshot
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:(_ for _ in ()).throw(ValueError('Synthetic missing checkout')))
    with pytest.raises(ValueError,match='missing checkout'):architecture(wb,job)
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',original)
    assert architecture(wb,job)['receipt']['source_job_id']==job['id']


def test_http_session_origin_and_route_bindings(memory_workbench):
    wb,job,_=memory_workbench;server=make_server(wb.root,port=0,workbench=wb)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        route='/api/jobs/'+job['id']+'/memory-capture';request=json.dumps(bound(wb,job,kind='architecture',max_facts=10)).encode()
        assert http(server,'POST',route,request)[0]==403
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        assert http(server,'POST',route,request,headers=headers|{'Origin':'https://invalid.example'})[0]==403
        status,_,raw=http(server,'POST',route,request,headers=headers)
        assert status==200;value=json.loads(raw)
        status,_,again=http(server,'POST',route,request,headers=headers);assert json.loads(again)==value
        assert http(server,'GET','/api/jobs/'+job['id']+'/memory-status',headers=headers)[0]==200
        assert http(server,'POST','/api/jobs/'+job['id']+'/memory-open',json.dumps(selected(wb,job,value)).encode(),headers=headers)[0]==200
        assert http(server,'POST',route+'/extra',request,headers=headers)[0]==404
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('change',[
    lambda row:row.update(id='f'*24),
    lambda row:row.update(provider='none'),
    lambda row:row.update(status='failed'),
    lambda row:row.update(depth='deep'),
])
def test_mutated_job_authority_is_rejected_before_pack_load(memory_workbench,monkeypatch,change):
    wb,job,_=memory_workbench;row=json.loads(wb._job_path(job['id']).read_bytes());change(row);write_json(wb._job_path(job['id']),row)
    monkeypatch.setattr(wb,'_load_pack',lambda *a:pytest.fail('Invalid job authority read pack'))
    assert not wb.memory.status(job['id'])['available']


def test_original_request_cannot_be_reassigned_to_another_local_query(memory_workbench,monkeypatch):
    wb,job,_=memory_workbench;raw=wb.result_bytes(job['id']);pack=json.loads(raw)
    row=json.loads(wb._job_path(job['id']).read_bytes());row.update(mode='local',request={
        'mode':'local','repository':pack['snapshot']['repository'],'revision':pack['snapshot']['commit_sha'],
        'issue_text':'A different original query','provider':row['provider'],'depth':row['depth']})
    write_json(wb._job_path(job['id']),row)
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Forged origin read Git'))
    with pytest.raises(ValueError):wb.memory.capture(job['id'],{'source_pack_sha256':sha256(raw).hexdigest(),'kind':'architecture','max_facts':10})


def test_memory_capture_follows_owned_product_followup_lineage(memory_workbench):
    wb,parent,_=memory_workbench
    child=completed(wb,wb.submit({'mode':'followup','parent_job_id':parent['id'],
        'additional_context':'Expected an empty list; this context remains unverified.'})['id'])
    assert child['status']=='completed'
    assert wb.memory.status(child['id'])['available']
    saved=architecture(wb,child)
    assert saved['receipt']['source_job_id']==child['id']
    assert saved['receipt']['source_pack_sha256']==sha256(wb.result_bytes(child['id'])).hexdigest()


def test_architecture_excerpt_is_bounded_and_keeps_full_source_hash(memory_workbench):
    wb,parent,_=memory_workbench;pack=json.loads(wb.result_bytes(parent['id']));path=Path(pack['snapshot']['repository'])
    (path/'parser.py').write_text('def parse_list(items):\n'+''.join('    # '+str(i)+' source '+('x'*120)+'\n' for i in range(100))+
        '    return [item.strip() for item in items]\n',encoding='utf-8')
    subprocess.run(['git','-C',str(path),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-c','core.hooksPath=','-C',str(path),'-c','user.name=Synthetic',
        '-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Long synthetic source'],check=True,capture_output=True)
    current=completed(wb,wb.submit({'mode':'example'})['id']);assert current['status']=='completed'
    saved=architecture(wb,current);fact=next(row for row in saved['memory']['facts'] if row['source']['path']=='parser.py')
    excerpt=wb.memory.source(current['id'],selected(wb,current,saved,item_id=fact['id'],role='source'))
    assert excerpt['truncated'] and len(excerpt['excerpt'])<=6000 and len(excerpt['excerpt'].splitlines())<=80
    assert excerpt['source_text_sha256']!=excerpt['excerpt_sha256']
    assert excerpt['excerpt_sha256']==sha256(excerpt['excerpt'].encode()).hexdigest()


def test_file_node_excerpt_preserves_crlf_and_original_node_hash(memory_workbench):
    wb,parent,_=memory_workbench;pack=json.loads(wb.result_bytes(parent['id']));path=Path(pack['snapshot']['repository'])
    text='from parser import parse_list\r\n\r\ndef render(value):\r\n    return parse_list(value)\r\n'
    (path/'formatting.py').write_bytes(text.encode())
    subprocess.run(['git','-c','core.autocrlf=false','-C',str(path),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-c','core.hooksPath=','-C',str(path),'-c','user.name=Synthetic',
        '-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','CRLF synthetic source'],check=True,capture_output=True)
    current=completed(wb,wb.submit({'mode':'example'})['id']);assert current['status']=='completed'
    saved=architecture(wb,current)
    fact=next(row for row in saved['memory']['facts'] if row['source']['kind']=='file' and row['source']['path']=='formatting.py')
    excerpt=wb.memory.source(current['id'],selected(wb,current,saved,item_id=fact['id'],role='source'))
    assert excerpt['excerpt']==text and not excerpt['truncated']
    assert excerpt['source_text_sha256']==excerpt['excerpt_sha256']==fact['source']['sha256']
