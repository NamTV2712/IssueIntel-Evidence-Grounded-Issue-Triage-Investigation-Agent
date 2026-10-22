"""Two-source product comparison admission, frozen replay and owned API costs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading

import pytest

from issueintel.investigation.workbench import Workbench
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.issue_relation_jobs import CONFIRMATION,policy_sha256
from issueintel.interfaces.web import make_server
from issueintel.runtime.io import digest,write_json
from test_workbench import completed,http
from test_runtime import configured
from test_github_index import connected,Reader,issue,REPO,STAMP


def relation_response(source):
    refs=[]
    for key in ('left','right'):
        side=source[key];record=next(r for r in side['sources'] if r['kind']=='issue')
        quote=record['text'][:min(80,len(record['text']))]
        refs.append({'side':key,'source_id':record['id'],'start':0,'end':len(quote),'quote':quote})
    return {'classification':'related','reason':'Similar reported symptoms need mechanism evidence before a duplicate judgment.',
        'dimensions':[{'name':name,'judgment':'similar' if name=='symptoms' else 'unknown',
            'explanation':'Only the reported symptoms overlap; other evidence remains unresolved.',
            'support':deepcopy(refs) if name=='symptoms' else [],'counter':[],
            'contextual_fit':'demonstrated' if name=='symptoms' else 'unknown',
            'inferential_link':'unknown','counter_resolution':'not_supplied'}
            for name in ('symptoms','mechanism','version','differences')],
        'mechanism_basis':{'left':'unknown','right':'unknown'},
        'versions':{key:{'status':'unknown','value':None,'citations':[]} for key in ('left','right')},
        'assumptions':[],'unknowns':['The affected versions and shared mechanism have not been established.']}


class RelationProvider:
    model='openai/gpt-oss-120b'
    def __init__(self):
        self.config=SimpleNamespace(max_output_tokens=8192)
        self.calls=[];self.transform=lambda value:value;self.before=lambda:None
    def complete_structured(self,messages,*,max_output_tokens,schema):
        payload=json.loads(messages[1]['content']);self.calls.append(payload);self.before()
        return self.transform(relation_response(payload))


@pytest.fixture
def relating(tmp_path,monkeypatch):
    config=SimpleNamespace(model='openai/gpt-oss-120b',reasoning_effort='high',max_output_tokens=8192)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    provider=RelationProvider();wb=Workbench(tmp_path,provider_factory=lambda *a,**k:provider,automatic_sync=False)
    try:
        case,path=wb._example()
        base={'mode':'local','repository':str(path),'revision':case['revision']}
        left=completed(wb,wb.submit(dict(base,issue_text='parse_list parser fails on None input in caller A.'))['id'])
        right=completed(wb,wb.submit(dict(base,issue_text='parse_list parser fails for missing input in caller B.'))['id'])
        assert left['status']==right['status']=='completed',(left,right)
        yield wb,provider,left,right
    finally:wb.close()


def selection(wb,left,right):
    return {'source_job_id':left['id'],'source_pack_sha256':hashlib.sha256(wb.result_bytes(left['id'])).hexdigest(),
        'candidate':{'kind':'investigation','job_id':right['id'],'pack_sha256':hashlib.sha256(wb.result_bytes(right['id'])).hexdigest()}}


def request(wb,left,right):
    selected=selection(wb,left,right);preview=wb.relation_preview(selected)
    return dict(selected,comparison_source_sha256=preview['comparison_source_sha256'],
        policy_sha256=preview['policy_sha256'],confirmation=CONFIRMATION)


def generate(wb,left,right):
    row=completed(wb,wb.submit_issue_relation(request(wb,left,right))['id'])
    assert row['status']=='completed',row
    return row,wb.issue_relation_bundle(row['id'])


def test_offline_candidates_and_preview_do_not_require_hypotheses_or_api(relating,monkeypatch):
    wb,provider,left,right=relating
    assert json.loads(wb.result_bytes(left['id']))['annotation'] is None
    monkeypatch.setattr('issueintel.integrations.github_index.RepositoryIndex',
        lambda *a,**k:pytest.fail('Local-only search must not open a GitHub index'))
    selected=selection(wb,left,right)
    result=wb.relation_candidates({k:selected[k] for k in ('source_job_id','source_pack_sha256')})
    assert result['model_calls']==0 and result['candidates'][0]['selector']==selected['candidate']
    assert result['warnings']==[]
    preview=wb.relation_preview(selected)
    assert preview['available'] and preview['model_calls']==0 and preview['max_provider_calls']==2
    assert preview['comparison_source_sha256']==preview['source']['sha256']
    assert preview['source']['left']['reference']['job_id']==left['id']
    assert preview['source']['right']['reference']['job_id']==right['id'] and provider.calls==[]


def test_pair_is_immutable_owned_and_reuses_saved_intent_without_git_config_or_model(relating,monkeypatch):
    wb,provider,left,right=relating;body=request(wb,left,right)
    before=[wb.result_bytes(row['id']) for row in (left,right)]
    row,bundle=generate(wb,left,right)
    assert len(provider.calls)==1 and bundle['assessment']['classification']=='related'
    assert row['id'] not in {r['id'] for r in wb.history()}
    assert wb.issue_relation_history(left['id'])['relation_jobs'][0]['id']==row['id']
    saved=json.loads(wb._job_path(row['id']).read_bytes())
    assert saved['relation_attempts']==[{'schema':'issue_relation_attempt_v1','owner_job_id':row['id'],
        'source_sha256':bundle['source']['sha256'],'policy_sha256':body['policy_sha256'],'call_index':1,'max_calls':2}]
    witness=wb.issue_relations.witness(row['id'])
    assert witness['source']==bundle['source'] and witness['owner_job_id']==row['id']
    assert witness['left']['pack_json'].encode()==before[0] and witness['right']['pack_json'].encode()==before[1]
    wb.close();other=Workbench(wb.root,automatic_sync=False,provider_factory=lambda *a,**k:pytest.fail('Saved provider'))
    try:
        monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',lambda *a,**k:pytest.fail('Saved Git'))
        monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved config'))
        monkeypatch.setattr('issueintel.integrations.github_index.RepositoryIndex.item',lambda *a:pytest.fail('Saved index'))
        assert other.issue_relation_bundle(row['id'])==bundle
        assert other.submit_issue_relation(body)['id']==row['id']
        for extension in ('json','md'):
            raw,source_sha,result_sha=other.issue_relation_export(row['id'],extension)
            assert raw and source_sha==body['source_pack_sha256'] and result_sha==bundle['sha256']
        assert [other.result_bytes(r['id']) for r in (left,right)]==before
    finally:other.close()


def test_concurrent_pair_has_one_owned_attempt_and_no_replacement_after_failure(relating):
    wb,provider,left,right=relating;body=request(wb,left,right);provider.transform=lambda _:{}
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda _:wb.submit_issue_relation(body),range(4)))
    assert len({r['id'] for r in rows})==1
    row=completed(wb,rows[0]['id']);assert row['status']=='failed'
    assert len(provider.calls)==2 and wb.submit_issue_relation(body)['id']==row['id']
    assert len(wb.issue_relation_history(left['id'])['relation_jobs'])==1


@pytest.mark.parametrize('mutation',['confirmation','source_sha','candidate_sha','pair_sha','policy','extra','self','same_issue','cross_repo','left_benchmark','right_benchmark'])
def test_invalid_pair_never_queues_or_constructs_provider(relating,monkeypatch,mutation):
    wb,provider,left,right=relating;body=request(wb,left,right)
    if mutation=='confirmation':body['confirmation']='implicit'
    elif mutation=='source_sha':body['source_pack_sha256']='a'*64
    elif mutation=='candidate_sha':body['candidate']['pack_sha256']='a'*64
    elif mutation=='pair_sha':body['comparison_source_sha256']='a'*64
    elif mutation=='policy':body['policy_sha256']='a'*64
    elif mutation=='extra':body['gold_patch']='not allowed'
    elif mutation=='self':body['candidate']={'kind':'investigation','job_id':left['id'],'pack_sha256':body['source_pack_sha256']}
    elif mutation in {'same_issue','cross_repo'}:
        from test_clarification_workbench import repair_export
        def change(pack):
            if mutation=='same_issue':pack['case']['problem_statement']=json.loads(wb.result_bytes(left['id']))['case']['problem_statement']
            else:pack['case']['repository']='another/repository'
        repair_export(wb,right,change)
        body['candidate']['pack_sha256']=hashlib.sha256(wb._export_bytes(right['id'])).hexdigest()
    else:
        target=left if mutation=='left_benchmark' else right
        job=json.loads(wb._job_path(target['id']).read_bytes());job['mode']=job['request']['mode']='main_dev'
        job['request']['case_id']='verified:synthetic';write_json(wb._job_path(target['id']),job)
    monkeypatch.setattr(wb,'_enqueue',lambda *a,**k:pytest.fail('Invalid pair queued'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Invalid pair spent')
    with pytest.raises(ValueError):wb.submit_issue_relation(body)
    assert provider.calls==[]


def test_queued_pair_replays_both_actual_git_snapshots_before_provider(relating,monkeypatch):
    wb,provider,left,right=relating;body=request(wb,left,right);seen=[]
    def missing(pack):seen.append(pack['case']['case_id']);raise ValueError('Synthetic unavailable Git')
    monkeypatch.setattr('issueintel.investigation.issue_relation_jobs.replay_snapshot',missing)
    wb.provider_factory=lambda *a,**k:pytest.fail('Git replay must precede provider')
    row=completed(wb,wb.submit_issue_relation(body)['id'])
    assert row['status']=='failed' and seen and provider.calls==[]
    assert wb.submit_issue_relation(body)['id']==row['id']


@pytest.mark.parametrize('side',['left','right'])
def test_source_change_while_call_in_flight_stops_result_without_retry(relating,side):
    wb,provider,left,right=relating;body=request(wb,left,right);target=left if side=='left' else right
    path=wb.root/target['result']['pack.json']['path'];original=path.read_bytes()
    provider.before=lambda:path.write_bytes(original+b' ')
    row=completed(wb,wb.submit_issue_relation(body)['id'])
    assert row['status']=='failed' and len(provider.calls)==1
    path.write_bytes(original)
    assert wb.submit_issue_relation(body)['id']==row['id']


@pytest.mark.parametrize('mutation',['intent','request','bundle','classification','result_path','attempt_owner','attempt_count'])
def test_saved_pair_cannot_be_reassigned_or_resealed_to_change_result(relating,mutation):
    wb,_,left,right=relating;row,bundle=generate(wb,left,right)
    path=wb.root/row['issue_relation_result']['path'];job_path=wb._job_path(row['id']);job=json.loads(job_path.read_bytes())
    if mutation=='intent':
        with sqlite3.connect(wb.directory/'issue_relations.sqlite') as db:db.execute('DELETE FROM intents')
    elif mutation=='request':job['request']['confirmation']='implicit';write_json(job_path,job)
    elif mutation=='result_path':job['issue_relation_result']['path']='outside.json';write_json(job_path,job)
    elif mutation.startswith('attempt'):
        job['relation_attempts'][0]['owner_job_id']='f'*24
        if mutation=='attempt_count':job['relation_attempts'].append(deepcopy(job['relation_attempts'][0]))
        write_json(job_path,job)
    elif mutation=='bundle':path.write_bytes(path.read_bytes()+b' ')
    else:
        bundle['assessment']['classification']='possible_duplicate';bundle['sha256']=digest({k:v for k,v in bundle.items() if k!='sha256'})
        write_json(path,bundle);job['issue_relation_result']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();write_json(job_path,job)
    with pytest.raises((ValueError,FileNotFoundError)):wb.issue_relation_bundle(row['id'])


def test_failed_real_provider_keeps_owned_terminal_receipt_and_redacts_error(relating,monkeypatch):
    wb,_,left,right=relating;body=request(wb,left,right)
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret');config=replace(configured(wb.root),reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    calls=[]
    def transport(*a,**k):calls.append(1);raise RuntimeError('synthetic-secret transport')
    wb.provider_factory=lambda config,**kw:GroqProvider(config,transport=transport,**kw)
    row=completed(wb,wb.submit_issue_relation(body)['id']);assert row['status']=='failed' and len(calls)==1
    saved=json.loads(wb._job_path(row['id']).read_bytes());usage=wb.usage(row['id'])
    assert usage['retained_bounds']==usage['reservations']==1 and usage['receipts'][0]['status']=='failed_bound'
    reservation=usage['receipts'][0]['reservation_id']
    assert saved['relation_receipt_owners'][reservation]=={'owner_job_id':row['id'],'call_index':1,'source_sha256':body['comparison_source_sha256']}
    assert wb.submit_issue_relation(body)['id']==row['id'] and 'synthetic-secret' not in row['error']


def test_cancelled_pair_preserves_single_intent_and_does_not_start_repair(relating):
    wb,provider,left,right=relating;body=request(wb,left,right);entered=threading.Event();release=threading.Event()
    def block():entered.set();assert release.wait(10)
    provider.before=block
    row=wb.submit_issue_relation(body);assert entered.wait(10)
    wb.cancel(row['id']);release.set();row=completed(wb,row['id'])
    assert row['status']=='cancelled' and len(provider.calls)==1 and wb.submit_issue_relation(body)['id']==row['id']


def test_another_workbench_observes_the_live_execution_owner_without_replacement(relating):
    wb,provider,left,right=relating;body=request(wb,left,right);entered=threading.Event();release=threading.Event()
    def block():entered.set();assert release.wait(10)
    provider.before=block;row=wb.submit_issue_relation(body);assert entered.wait(10)
    other=Workbench(wb.root,automatic_sync=False,provider_factory=lambda *a,**k:pytest.fail('Other process model'))
    try:
        assert other.job(row['id'])['status']=='running'
        assert other.submit_issue_relation(body)['id']==row['id'] and len(provider.calls)==1
    finally:release.set();other.close()
    assert completed(wb,row['id'])['status']=='completed'


def test_same_relative_repository_spelling_cannot_join_different_actual_git_directories(relating,monkeypatch):
    wb,provider,left,_=relating
    original=Path(json.loads(wb.result_bytes(left['id']))['snapshot']['repository'])
    revision=json.loads(wb.result_bytes(left['id']))['snapshot']['commit_sha'];sources=[]
    for name in ('first','second'):
        cwd=wb.root/name;target=cwd/'owner/project';target.parent.mkdir(parents=True)
        shutil.copytree(original,target)
        monkeypatch.chdir(cwd)
        row=completed(wb,wb.submit({'mode':'local','repository':'owner/project','revision':revision,
            'issue_text':'Parser missing value failure in '+name+' caller.'})['id'])
        assert row['status']=='completed',row;sources.append(row)
    monkeypatch.chdir(wb.root)
    with pytest.raises(ValueError,match='bound repository'):
        wb.relation_preview(selection(wb,*sources))
    assert provider.calls==[]


def test_remote_owner_repo_inputs_reuse_the_bound_acquisition_cache(relating,monkeypatch):
    wb,provider,left,_=relating
    pack=json.loads(wb.result_bytes(left['id']));original=Path(pack['snapshot']['repository'])
    cache=wb.root/'data/repos/owner__project.git';cache.parent.mkdir(parents=True)
    shutil.copytree(original,cache)
    monkeypatch.chdir(wb.root)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',lambda *a,**k:SimpleNamespace(path=cache))
    sources=[]
    for label in ('first','second'):
        row=completed(wb,wb.submit({'mode':'local','repository':'owner/project','revision':pack['snapshot']['commit_sha'],
            'issue_text':'Parser missing value failure in '+label+' caller.'})['id'])
        assert row['status']=='completed',row;sources.append(row)
    row,bundle=generate(wb,*sources)
    assert row['status']=='completed' and bundle['source']['left']['repository']=='owner/project'
    assert len(provider.calls)==1


def test_http_pair_routes_require_session_origin_and_exact_export_bindings(relating):
    wb,_,left,right=relating;selected=selection(wb,left,right);body=request(wb,left,right)
    server=make_server(wb.root,port=0,workbench=wb);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        headers={'Content-Type':'application/json','X-IssueIntel-Token':server.session_token,
            'Origin':'http://127.0.0.1:'+str(server.server_port)}
        for route,payload in [('/api/issue-relation-candidates',{k:selected[k] for k in ('source_job_id','source_pack_sha256')}),
                              ('/api/issue-relations/preview',selected),('/api/issue-relations',body)]:
            assert http(server,'POST',route,json.dumps(payload),{'Content-Type':'application/json'})[0]==403
            assert http(server,'POST',route,json.dumps(payload),dict(headers,Origin='https://evil.invalid'))[0]==403
        status,_,raw=http(server,'POST','/api/issue-relations',json.dumps(body),headers);assert status==202
        row=completed(wb,json.loads(raw)['id']);assert row['status']=='completed',row
        for extension in ('json','md'):
            status,response,raw=http(server,'GET','/api/jobs/'+row['id']+'/issue-relation.'+extension,headers=headers)
            expected,source_sha,relation_sha=wb.issue_relation_export(row['id'],extension)
            assert status==200 and raw==expected
            assert response['X-IssueIntel-Source-Pack-SHA256']==source_sha and response['X-IssueIntel-Issue-Relation-SHA256']==relation_sha
    finally:server.shutdown();server.server_close();thread.join(5)


@pytest.fixture
def synced_relating(connected,monkeypatch):
    index,builder=connected
    index.sync(reader=Reader({'/issues?':([issue(1),issue(2,body='Parser Unicode input also fails in another caller.')],None,digest('issues'))}),snapshot_builder=builder)
    snapshot=index.status()['current_snapshot'];path=index.root/'synthetic'
    from issueintel.repository.snapshot import build_snapshot
    commit=build_snapshot(path,'HEAD').commit_sha
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue',lambda _: {
        'url':'https://github.com/'+REPO+'/issues/1','repository':REPO,'problem_statement':'Unicode parser failure\n\nParser fails on Unicode input',
        'created_at':STAMP})
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',lambda *a,**k:SimpleNamespace(path=path))
    config=SimpleNamespace(model='openai/gpt-oss-120b',reasoning_effort='high',max_output_tokens=8192)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    provider=RelationProvider();wb=Workbench(index.root,provider_factory=lambda *a,**k:provider,automatic_sync=False)
    try:
        left=completed(wb,wb.submit({'mode':'github','issue_url':'https://github.com/'+REPO+'/issues/1','revision':commit})['id'])
        assert left['status']=='completed',left
        item=index.item('issue:2');selected={'source_job_id':left['id'],
            'source_pack_sha256':hashlib.sha256(wb.result_bytes(left['id'])).hexdigest(),
            'candidate':{'kind':'synced_issue','key':'issue:2','record_sha256':item['record_sha256'],'receipt_sha256':item['receipt_sha256']}}
        preview=wb.relation_preview(selected);body=dict(selected,comparison_source_sha256=preview['comparison_source_sha256'],
            policy_sha256=preview['policy_sha256'],confirmation=CONFIRMATION)
        yield wb,index,builder,provider,left,body
    finally:wb.close()


def test_synced_issue_source_is_frozen_and_saved_reads_ignore_later_sync(synced_relating,monkeypatch):
    wb,index,builder,provider,left,body=synced_relating
    candidates=wb.relation_candidates({k:body[k] for k in ('source_job_id','source_pack_sha256')})['candidates']
    assert any(r['selector']==body['candidate'] for r in candidates)
    assert all(r['issue_key']!='https://github.com/'+REPO+'/issues/1' for r in candidates)
    row=completed(wb,wb.submit_issue_relation(body)['id']);assert row['status']=='completed',row
    before=wb.issue_relation_bundle(row['id']);witness=wb.issue_relations.witness(row['id'])
    assert witness['right']['item']['record']['body']=='Parser Unicode input also fails in another caller.'
    index.sync(reader=Reader({'/issues?':([issue(1),issue(2,body='Completely updated source text')],None,digest('updated'))}),snapshot_builder=builder,refresh=True)
    monkeypatch.setattr('issueintel.integrations.github_index.RepositoryIndex.item',lambda *a:pytest.fail('Saved comparison read current index'))
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',lambda *a,**k:pytest.fail('Saved comparison read Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved comparison read config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved comparison constructed model')
    assert wb.issue_relation_bundle(row['id'])==before and wb.submit_issue_relation(body)['id']==row['id']
    assert len(provider.calls)==1


def test_github_index_read_failure_remains_an_explicit_zero_api_warning(synced_relating,monkeypatch):
    wb,_,_,provider,_,body=synced_relating
    def fail(*a,**k):raise OSError('Synthetic unavailable local index')
    monkeypatch.setattr('issueintel.integrations.github_index.RepositoryIndex.search',fail)
    result=wb.relation_candidates({k:body[k] for k in ('source_job_id','source_pack_sha256')})
    assert result['warnings']==['synced_issue_index_unavailable']
    assert result['model_calls']==0 and provider.calls==[]


def test_synced_source_change_during_paid_call_is_not_silently_accepted(synced_relating):
    wb,index,builder,provider,_,body=synced_relating
    def refresh():index.sync(reader=Reader({'/issues?':([issue(1),issue(2,body='Changed during the API call')],None,digest('changed'))}),snapshot_builder=builder,refresh=True)
    provider.before=refresh;row=completed(wb,wb.submit_issue_relation(body)['id'])
    assert row['status']=='failed' and len(provider.calls)==1
    assert wb.submit_issue_relation(body)['id']==row['id']
