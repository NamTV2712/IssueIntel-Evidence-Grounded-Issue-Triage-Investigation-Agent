"""Product dossiers retain source and reported-versus-predicted boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import pytest

from issueintel.runtime.io import digest,write_json
from test_check_plan_jobs import checking,generate
from test_check_observation_workbench import append,followup_request
from issueintel.interfaces.web import make_server
from test_workbench import completed,http
from test_product_finalization import finalization_source,approval


@pytest.fixture
def family(checking):
    wb,provider,source=checking
    plan_job,plan=generate(wb,source)
    journal=append(wb,plan_job)
    request=followup_request(plan_job,plan,journal,provider='groq')
    child=completed(wb,wb.submit_check_followup(request)['id'])
    assert child['status']=='completed',child
    return wb,provider,source,plan_job,journal,child


def relation_request(dossier,source,child,**changes):
    runs={r['job_id']:r for r in dossier['runs']}
    parent_id,child_id=source['id'],child['id']
    parent,after=runs[parent_id]['report'],runs[child_id]['report']
    relation={'parent_job_id':parent_id,'child_job_id':child_id,
        'parent_hypothesis_id':parent['hypotheses'][0]['id'],
        'child_hypothesis_id':after['hypotheses'][0]['id'],
        'kind':'refined','reason':'Synthetic manual interpretation; causal mechanism remains unverified.',
        'evidence_refs':[{'job_id':parent_id,'evidence_id':parent['hypotheses'][0]['support_ids'][0]}],
        'supersedes':None}
    relation.update(changes)
    return {'source_pack_sha256':dossier['source']['selected_pack_sha256'],
            'expected_sha256':dossier['hypothesis_links']['sha256'],'relation':relation}


def deny_provider(wb):
    wb.provider_factory=lambda *a,**k:pytest.fail('Dossier must not construct a provider')


def test_complete_family_timeline_predictions_and_source_bindings(family,monkeypatch):
    wb,provider,source,plan_job,journal,child=family
    snapshots={i:wb.result_bytes(i) for i in (source['id'],child['id'])}
    calls=len(provider.calls),len(provider.check_calls)
    deny_provider(wb)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *_:pytest.fail('No API config read'))
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',lambda *a,**k:pytest.fail('No Git snapshot read'))
    monkeypatch.setattr('issueintel.investigation.resume.build_snapshot',lambda *a,**k:pytest.fail('No Git snapshot read'))
    dossier=wb.dossier(child['id'])
    assert dossier['schema']=='investigation_dossier_v1'
    assert dossier['sha256']==digest({k:v for k,v in dossier.items() if k!='sha256'})
    assert dossier['source']['selected_pack_sha256']==hashlib.sha256(snapshots[child['id']]).hexdigest()
    assert {r['job_id'] for r in dossier['runs']}==set(snapshots)
    assert {e['kind'] for e in dossier['timeline']}=={'investigation','check_plan','check_observation'}
    assert len(dossier['plans'])==1 and dossier['plans'][0]['plan_job_id']==plan_job['id']
    transition=dossier['transitions'][0]
    assert transition['parent_job_id']==source['id'] and transition['child_job_id']==child['id']
    assert transition['observation_source']['journal_sha256']==journal['sha256']
    assert [r['comparison'] for r in transition['prediction_delta']]==['matches_prediction','conflicts_with_prediction','undetermined']
    assert all(r['revision_applicability']=='unknown_commit' and r['causal_verified'] is False
               and r['conditions_verified'] is False for r in transition['prediction_delta'])
    assert transition['semantic_hypothesis_correspondence']=='not_inferred'
    assert dossier['model_calls']==0 and dossier['evaluation_performed'] is False
    assert (len(provider.calls),len(provider.check_calls))==calls
    assert all(wb.result_bytes(i)==raw for i,raw in snapshots.items())


def test_correction_preserves_child_receipt_and_marks_later_supersession(family):
    wb,_,source,plan_job,journal,child=family
    before=wb.result_bytes(child['id'])
    corrected=append(wb,plan_job,outcome='B',supersedes='observation_1')
    dossier=wb.dossier(source['id'])
    assert dossier['observations'][0]['journal']['sha256']==corrected['sha256']
    assert len([e for e in dossier['timeline'] if e['kind']=='check_observation'])==2
    receipt=dossier['transitions'][0]['observation_source']
    assert receipt['journal_sha256']==journal['sha256']
    assert all(r['superseded_later'] is True and r['reported_outcome']=='A'
               for r in dossier['transitions'][0]['prediction_delta'])
    assert wb.result_bytes(child['id'])==before


@pytest.mark.parametrize('outcome,tested,expected,applicability',[
    ('A',None,'matches_prediction','unknown_commit'),
    ('B','different','conflicts_with_prediction','different_commit'),
    ('other','same','undetermined','same_analysis_commit'),
    ('inconclusive',None,'undetermined','unknown_commit')])
def test_prediction_delta_is_only_label_comparison(checking,outcome,tested,expected,applicability):
    wb,_,source=checking
    plan_job,plan=generate(wb,source)
    commit=json.loads(wb.result_bytes(source['id']))['snapshot']['commit_sha']
    tested=commit if tested=='same' else 'f'*40 if tested else None
    journal=append(wb,plan_job,outcome=outcome,tested_commit_sha=tested)
    child=completed(wb,wb.submit_check_followup(followup_request(plan_job,plan,journal))['id'])
    rows=wb.dossier(child['id'])['transitions'][0]['prediction_delta']
    assert rows[0]['comparison']==expected
    assert all(r['revision_applicability']==applicability for r in rows)


def test_manual_hypothesis_links_are_source_bound_append_only_and_correctable(family):
    wb,_,source,_,_,child=family
    before=wb.dossier(source['id'])
    request=relation_request(before,source,child)
    saved=wb.dossier_link(source['id'],request)
    event=saved['hypothesis_links']['events'][0]
    assert event['relation']==request['relation']
    assert event['provenance']=='user_report_unverified' and event['causal_verified'] is False
    assert event['bindings']['parent']['pack_sha256']==next(r for r in saved['runs'] if r['job_id']==source['id'])['binding']['pack_sha256']
    with pytest.raises(ValueError,match='changed'):wb.dossier_link(source['id'],request)
    correction=relation_request(saved,source,child,kind='retained',supersedes='link_1',reason='Corrected user interpretation, still unverified.')
    corrected=wb.dossier_link(source['id'],correction)
    assert corrected['hypothesis_links']['revision']==2
    assert corrected['hypothesis_links']['events'][0]==event
    assert {e['event']['link_id'] for e in corrected['timeline'] if e['kind']=='hypothesis_link'}=={'link_1','link_2'}
    assert wb.dossier(child['id'])['hypothesis_links']==corrected['hypothesis_links']


@pytest.mark.parametrize('mutation',['fake_parent','fake_child','bad_hypothesis','new_with_parent','empty_reason',
                                    'missing_evidence','foreign_evidence','duplicate_evidence','control_text','supersede_missing'])
def test_invalid_manual_links_never_enter_history(family,mutation):
    wb,_,source,_,_,child=family
    dossier=wb.dossier(child['id']);request=relation_request(dossier,source,child)
    row=request['relation']
    if mutation=='fake_parent':row['parent_job_id']='a'*24
    elif mutation=='fake_child':row['child_job_id']='b'*24
    elif mutation=='bad_hypothesis':row['child_hypothesis_id']='missing'
    elif mutation=='new_with_parent':row['kind']='new'
    elif mutation=='empty_reason':row['reason']=' '
    elif mutation=='missing_evidence':row['evidence_refs']=[]
    elif mutation=='foreign_evidence':row['evidence_refs'][0]['job_id']='a'*24
    elif mutation=='duplicate_evidence':row['evidence_refs']*=2
    elif mutation=='control_text':row['reason']='misleading\u202e source'
    else:row['supersedes']='link_99'
    with pytest.raises(ValueError):wb.dossier_link(child['id'],request)
    assert wb.dossier(child['id'])['hypothesis_links']['revision']==0


def test_concurrent_link_writes_share_one_revision(family):
    wb,_,source,_,_,child=family
    dossier=wb.dossier(child['id']);request=relation_request(dossier,source,child)
    def write(_):
        try:return wb.dossier_link(child['id'],request)['hypothesis_links']['revision']
        except ValueError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,range(2)))
    assert sorted(results,key=str)==[1,'conflict']
    assert wb.dossier(child['id'])['hypothesis_links']['revision']==1


@pytest.mark.parametrize('mutation',['truth','hash','extra','source'])
def test_rehashed_link_forgery_cannot_change_truth_or_source(family,mutation):
    wb,_,source,_,_,child=family
    saved=wb.dossier_link(child['id'],relation_request(wb.dossier(child['id']),source,child))
    event=deepcopy(saved['hypothesis_links']['events'][0])
    if mutation=='truth':event['causal_verified']=0
    elif mutation=='hash':event['sha256']='f'*64
    elif mutation=='extra':event['verified']=True
    else:event['bindings']['parent']['report_sha256']='f'*64
    if mutation!='hash':event['sha256']=digest({k:v for k,v in event.items() if k!='sha256'})
    with sqlite3.connect(wb.directory/'dossiers.sqlite') as db:
        db.execute('UPDATE links SET value=?',(json.dumps(event),))
    with pytest.raises(ValueError):wb.dossier(child['id'])


def test_unrelated_issue_and_benchmark_sources_do_not_enter_dossier(checking,monkeypatch):
    wb,_,source=checking
    local=completed(wb,wb.submit({'mode':'local','repository':str(wb.directory/'example_repo'),
        'revision':'HEAD','issue_text':'Entirely different local issue'})['id'])
    assert local['status']=='completed'
    assert [r['job_id'] for r in wb.dossier(source['id'])['runs']]==[source['id']]
    with pytest.raises(FileNotFoundError):wb.dossier('a'*24)


def test_corrupt_related_pack_is_visible_but_selected_source_is_rejected(family):
    wb,_,source,_,_,child=family
    path=wb.root/child['result']['pack.json']['path']
    path.write_bytes(path.read_bytes()+b' ')
    dossier=wb.dossier(source['id'])
    assert next(r for r in dossier['runs'] if r['job_id']==child['id'])['integrity']=='unavailable'
    assert {'job_id':child['id'],'artifact':'pack','reason':'source_replay_failed'} in dossier['warnings']
    with pytest.raises(ValueError):wb.dossier(child['id'])


def test_guarded_http_dossier_and_relation_exports(family):
    wb,_,source,_,_,child=family
    server=make_server(wb.root,port=0,workbench=wb)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        route='/api/jobs/'+child['id']+'/dossier'
        assert http(server,'GET',route)[0]==403
        status,response,raw=http(server,'GET',route,headers=headers)
        assert status==200
        dossier=json.loads(raw)
        assert response['X-IssueIntel-Dossier-SHA256']==dossier['sha256']
        body=json.dumps(relation_request(dossier,source,child))
        assert http(server,'POST','/api/jobs/'+child['id']+'/dossier-links',body,headers)[0]==200
        assert http(server,'GET',route+'.json',headers=headers)[0]==200
        assert http(server,'POST','/api/jobs/'+child['id']+'/dossier-links',body,headers)[0]==400
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_changed_original_request_cannot_reassign_an_existing_dossier(family):
    wb,_,source,_,_,child=family
    row=json.loads(wb._job_path(source['id']).read_bytes())
    row['mode']=row['request']['mode']='local'
    row['request'].update(repository=str(wb.directory/'example_repo'),revision='HEAD',issue_text='Forged dossier identity')
    write_json(wb._job_path(source['id']),row)
    for job in (source,child):
        with pytest.raises(ValueError):wb.dossier(job['id'])


def test_local_case_groups_independent_runs_without_grouping_other_queries(checking):
    wb,_,_=checking
    request={'mode':'local','repository':str(wb.directory/'example_repo'),'revision':'HEAD',
             'issue_text':'parse_list receives a missing value'}
    first=completed(wb,wb.submit(request)['id'])
    second=completed(wb,wb.submit(dict(request,provider='groq',depth='deep'))['id'])
    other=completed(wb,wb.submit(dict(request,issue_text='A different caller issue'))['id'])
    assert all(row['status']=='completed' for row in (first,second,other))
    value=wb.dossier(first['id'])
    assert {row['job_id'] for row in value['runs']}=={first['id'],second['id']}
    assert len({row['root_job_id'] for row in value['runs']})==2


def test_github_identity_groups_current_descriptions_but_not_different_issues(checking,monkeypatch):
    wb,_,_=checking
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',lambda *_a,**_k:SimpleNamespace(path=wb.directory/'example_repo'))
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue',lambda url:{
        'url':url,'repository':'example/repo','problem_statement':'A current description','created_at':None})
    first=completed(wb,wb.submit({'mode':'github','issue_url':'https://github.com/example/repo/issues/1'})['id'])
    other=completed(wb,wb.submit({'mode':'github','issue_url':'https://github.com/example/repo/issues/2'})['id'])
    assert first['status']==other['status']=='completed'
    assert [row['job_id'] for row in wb.dossier(first['id'])['runs']]==[first['id']]


@pytest.mark.parametrize('use_child',[False,True])
def test_benchmark_authority_cannot_enter_product_dossier_through_lineage(family,use_child):
    wb,_,source,_,_,child=family
    row=json.loads(wb._job_path(source['id']).read_bytes())
    row['mode']=row['request']['mode']='main_dev';row['request']['case_id']='verified:synthetic'
    write_json(wb._job_path(source['id']),row)
    with pytest.raises(ValueError):wb.dossier(child['id'] if use_child else source['id'])


def test_recovered_export_includes_failed_source_without_requiring_its_pack(finalization_source):
    wb,service,source,_=finalization_source
    row=json.loads(wb._job_path(source['id']).read_bytes());row.pop('result',None)
    write_json(wb._job_path(source['id']),row)
    child=completed(wb,service.submit(source['id'],approval(service.preview(source['id'])))['id'])
    assert child['status']=='completed'
    value=wb.dossier(child['id']);runs={r['job_id']:r for r in value['runs']}
    assert runs[source['id']]['status']=='failed' and runs[source['id']]['report'] is None
    assert runs[child['id']]['parent_job_id']==source['id'] and runs[child['id']]['report']


@pytest.mark.parametrize('kind,parent_id,child_id',[('new',None,'h0'),('not_carried','h0',None),('retained','h0','h0')])
def test_each_manual_relation_kind_preserves_nullable_reference_rules(family,kind,parent_id,child_id):
    wb,_,source,_,_,child=family
    dossier=wb.dossier(child['id'])
    request=relation_request(dossier,source,child,kind=kind,parent_hypothesis_id=parent_id,child_hypothesis_id=child_id)
    saved=wb.dossier_link(child['id'],request)
    assert saved['hypothesis_links']['events'][0]['relation']==request['relation']


def test_dossier_is_not_limited_to_recent_fifty_history_entries(checking):
    wb,_,source=checking
    original=json.loads(wb._job_path(source['id']).read_bytes())
    for _ in range(51):
        identifier=uuid.uuid4().hex[:24]
        row=deepcopy(original);row.update(id=identifier,status='failed',stage='failed')
        row.pop('result',None);row.pop('stage_checkpoints',None)
        write_json(wb._job_path(identifier),row)
    assert len(wb.history())==50
    dossier=wb.dossier(source['id'])
    assert len(dossier['runs'])==52
    assert sum(row['report'] is not None for row in dossier['runs'])==1
