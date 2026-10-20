"""Standalone Planned lineage replay uses only private synthetic archive bytes."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from issueintel.investigation.portable_verify import encoded, sha, verify
from issueintel.investigation.planner import planned_run_id, seal_plan
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import digest, write_json
from test_handoff import contents, exported, repack
from test_planned_continuations import waiting_workbench, request_for
from test_planned_pipeline import planned_repository
from test_workbench import completed


def continue_source(wb, service, source, *, all_answers=True):
    child=completed(wb,service.submit(source['id'],
        request_for(service.preview(source['id']),all_answers=all_answers))['id'])
    assert child['status']=='completed',child
    return child


def isolated_verify(tmp_path, files, raw):
    moved=tmp_path/'another-machine';moved.mkdir()
    script=moved/'verify.py';script.write_bytes(files['verify.py'])
    archive=moved/'handoff.zip';archive.write_bytes(raw)
    result=subprocess.run([sys.executable,'-I',str(script),str(archive),
        '--expected-sha256',sha(raw)],cwd=moved,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_waiting_continuation_exports_parent_and_verifies_without_project(waiting_workbench,tmp_path,monkeypatch):
    wb,service,source,calls,_=waiting_workbench
    child=continue_source(wb,service,source)
    before=len(calls);parent_raw=wb.result_bytes(source['id']);child_raw=wb.result_bytes(child['id'])
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',
                        lambda *a,**k:pytest.fail('Handoff read Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda *a,**k:pytest.fail('Handoff read provider config'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Handoff constructed a provider')
    preview,raw=exported(wb,child['id']);files=contents(raw)
    assert preview['automatic_ancestor_job_ids']==[source['id']]
    assert files['runs/'+source['id']+'/pack.json']==parent_raw
    assert files['runs/'+child['id']+'/pack.json']==child_raw
    report=isolated_verify(tmp_path,files,raw)
    assert report['runs']==2 and report['integrity']=='consistent'
    assert report['model_calls']==0 and report['authorship_verified'] is False
    assert report['semantic_verified'] is False and report['evaluation_performed'] is False
    assert len(calls)==before


def test_waiting_root_alone_is_portable_without_resuming_or_spending(waiting_workbench,tmp_path):
    wb,_,source,calls,_=waiting_workbench
    before=len(calls);_,raw=exported(wb,source['id']);files=contents(raw)
    result=isolated_verify(tmp_path,files,raw)
    assert result['runs']==1 and result['integrity']=='consistent'
    assert len(calls)==before==1


def test_grandchild_inherits_all_ancestors_and_original_budget(waiting_workbench,tmp_path):
    wb,service,source,calls,_=waiting_workbench
    child=continue_source(wb,service,source,all_answers=False)
    grandchild=continue_source(wb,service,child)
    preview,raw=exported(wb,grandchild['id']);files=contents(raw)
    assert preview['included_job_ids']==sorted([source['id'],child['id'],grandchild['id']])
    assert preview['automatic_ancestor_job_ids']==sorted([source['id'],child['id']])
    pack=json.loads(files['runs/'+grandchild['id']+'/pack.json'])
    assert pack['workflow_plan']['inherited']['counts']['provider_calls']<len(calls)<=9
    assert isolated_verify(tmp_path,files,raw)['runs']==3


@pytest.mark.parametrize('waiting_workbench',[{'strict_rejection':True}],indirect=True)
def test_standalone_counts_recorded_schema_repair_calls(waiting_workbench,tmp_path):
    wb,service,source,calls,_=waiting_workbench
    child=continue_source(wb,service,source)
    _,raw=exported(wb,child['id']);files=contents(raw)
    parent=json.loads(files['runs/'+source['id']+'/pack.json'])
    assert parent['workflow_plan']['provider_calls']==2
    assert parent['annotation']['provenance']['repair_count']==1
    assert len(calls)<=9
    assert isolated_verify(tmp_path,files,raw)['integrity']=='consistent'


def reseal_changed_pack(files,identifier,mutation,*,report_mutation=None):
    """Rehash every containing byte declaration; invariants must still reject."""
    files=deepcopy(files);path='runs/'+identifier+'/pack.json';pack=json.loads(files[path])
    mutation(pack)
    link=pack.get('continued_from')
    if link:
        receipt=link['answers_receipt'];receipt['sha256']=digest({k:v for k,v in receipt.items() if k!='sha256'})
        link['sha256']=digest({k:v for k,v in link.items() if k!='sha256'})
    plan=pack['workflow_plan'];plan['sha256']=digest({k:v for k,v in plan.items() if k!='sha256'})
    identity={'case':pack['case'],'snapshot':pack['snapshot']['snapshot_sha256'],
        'provider':plan['provider'],'model':plan['model'],'evidence':[row['id'] for row in pack['evidence']],
        'annotation':pack['annotation'],'workflow_plan_sha256':plan['sha256']}
    if pack.get('repository_context') is not None:identity['repository_context']=pack['repository_context']['sha256']
    pack['run_id']=digest(identity)[:24];files[path]=encoded(pack)
    report_path='runs/'+identifier+'/report.json';report=json.loads(files[report_path])
    report['source']['pack_sha256']=sha(files[path]);report['source']['query_sha256']=sha(pack['case']['problem_statement'].encode())
    report['execution']['stop_reason']=pack['stop_reason']
    if report_mutation is not None:report_mutation(report)
    report['sha256']=digest({k:v for k,v in report.items() if k!='sha256'});files[report_path]=encoded(report)
    handoff=json.loads(files['handoff.json']);run=next(row for row in handoff['runs'] if row['job_id']==identifier)
    run['report']=report;run['binding'].update(pack_sha256=sha(files[path]),report_sha256=report['sha256'],
        query_sha256=report['source']['query_sha256'])
    if handoff['source']['selected_job_id']==identifier:handoff['source']['selected_pack_sha256']=sha(files[path])
    handoff['sha256']=digest({k:v for k,v in handoff.items() if k!='sha256'});files['handoff.json']=encoded(handoff)
    manifest=json.loads(files['manifest.json']);manifest['source']=handoff['source'];manifest['selection_sha256']=handoff['sha256']
    for entry in manifest['entries']:
        entry.update(bytes=len(files[entry['path']]),sha256=sha(files[entry['path']]))
    manifest['sha256']=digest({k:v for k,v in manifest.items() if k!='sha256'});files['manifest.json']=encoded(manifest)
    return repack(files)


def change_stop(pack, reason):
    pack['stop_reason']=pack['workflow_plan']['stop_reason']=reason


@pytest.mark.parametrize('mutation',[
    lambda pack:pack['continued_from'].update(parent_plan_sha256='0'*64),
    lambda pack:pack['continued_from'].update(waiting_sha256='0'*64),
    lambda pack:pack['continued_from'].update(policy_sha256='0'*64),
    lambda pack:pack['continued_from']['answers_receipt'].update(questions_sha256='0'*64),
    lambda pack:pack['continued_from']['answers_receipt']['answers'][0].update(question='A substituted question'),
    lambda pack:pack['continued_from']['answers_receipt']['answers'][0].update(answer='A forged answer without its query binding'),
    lambda pack:pack['case'].update(problem_statement=pack['case']['problem_statement']+' changed query'),
    lambda pack:pack['continued_from'].update(cue_text='A substituted cue source'),
    lambda pack:pack['continued_from'].update(answered_fields=[]),
    lambda pack:pack['workflow_plan']['inherited']['counts'].update(provider_calls=0),
    lambda pack:pack['workflow_plan']['inherited']['limits'].update(max_provider_calls=99),
    lambda pack:pack['workflow_plan']['inherited'].update(used_query_sha256s=['0'*64]),
    lambda pack:pack['workflow_plan'].update(provider_calls=0),
    lambda pack:pack['workflow_plan']['limits'].update(max_provider_calls=99),
    lambda pack:pack['workflow_plan']['source'].update(query_sha256='0'*64),
    lambda pack:pack['workflow_plan']['final_decision'].update(reason='unreviewed_user_input'),
    lambda pack:change_stop(pack,'an_unknown_reason'),
    lambda pack:change_stop(pack,'generation_limit'),
],ids=['parent_plan','waiting','policy','questions','question_text','answer','query','cue',
       'answered_fields','inherited_calls','inherited_limit','query_history','local_calls','local_limit','source',
       'final_reason','unknown_stop','unreached_generation_limit'])
def test_resealed_changed_lineage_answers_and_budgets_are_rejected(waiting_workbench,tmp_path,mutation):
    wb,service,source,_,_=waiting_workbench
    child=continue_source(wb,service,source)
    _,raw=exported(wb,child['id'])
    changed=reseal_changed_pack(contents(raw),child['id'],mutation)
    path=tmp_path/'changed.zip';path.write_bytes(changed)
    with pytest.raises(ValueError):verify(path)


def test_resealed_waiting_questions_and_remaining_cannot_drift(waiting_workbench,tmp_path):
    wb,_,source,_,_=waiting_workbench
    _,raw=exported(wb,source['id'])
    def changed(pack):
        waiting=pack['workflow_plan']['waiting'];waiting['remaining']['provider_calls']=99
        waiting['sha256']=digest({k:v for k,v in waiting.items() if k!='sha256'})
    path=tmp_path/'changed-waiting.zip';path.write_bytes(reseal_changed_pack(contents(raw),source['id'],changed))
    with pytest.raises(ValueError,match='waiting questions or remaining budget'):verify(path)


@pytest.mark.parametrize('change',[{'status':'completed'},{'stop_reason':'round_limit'}])
def test_resealed_report_cannot_hide_the_saved_waiting_state(waiting_workbench,tmp_path,change):
    wb,_,source,_,_=waiting_workbench
    _,raw=exported(wb,source['id'])
    path=tmp_path/'changed-report.zip'
    path.write_bytes(reseal_changed_pack(contents(raw),source['id'],lambda _:None,
        report_mutation=lambda report:report['execution'].update(change)))
    with pytest.raises(ValueError,match='Planned report execution status'):verify(path)


def test_resealed_offline_pack_cannot_claim_a_planned_model_decision(tmp_path):
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:pytest.fail('No provider'))
    try:
        source=completed(wb,wb.submit({'mode':'example','depth':'planned'})['id'])
        _,raw=exported(wb,source['id'])
        def changed(pack):
            change_stop(pack,'round_limit')
            pack['workflow_plan']['final_decision'].update(action='verify',reason='retrieval_complete')
        path=tmp_path/'changed-offline.zip'
        path.write_bytes(reseal_changed_pack(contents(raw),source['id'],changed))
        with pytest.raises(ValueError,match='Offline Planned source claimed a model decision'):
            verify(path)
    finally:wb.close()


@pytest.mark.parametrize('version',[1,2])
def test_older_planned_archive_versions_remain_portable(tmp_path,version):
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:pytest.fail('No provider'))
    try:
        source=completed(wb,wb.submit({'mode':'example','depth':'planned'})['id'])
        pack=json.loads(wb.result_bytes(source['id']))
        plan={key:value for key,value in pack['workflow_plan'].items()
              if key not in {'inherited','policy_sha256','waiting','sha256'}}
        plan.update(schema='planned_investigation_v'+str(version),policy='bounded_planner_v'+str(version))
        pack['workflow_plan']=seal_plan(plan)
        pack['run_id']=planned_run_id(pack['case'],pack['snapshot']['snapshot_sha256'],
            pack['evidence'],pack['annotation'],pack['workflow_plan'])
        # Replace only a synthetic old-format saved fixture. Production never
        # rewrites historical packs or upgrades old plan policy declarations.
        row=json.loads(wb._job_path(source['id']).read_bytes())
        path=wb.root/row['result']['pack.json']['path'];write_json(path,pack)
        row['result']['pack.json']['sha256']=sha(path.read_bytes());write_json(wb._job_path(source['id']),row)
        _,raw=exported(wb,source['id']);files=contents(raw)
        assert isolated_verify(tmp_path,files,raw)['runs']==1
    finally:wb.close()
