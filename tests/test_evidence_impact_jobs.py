"""Owned version jobs preserve source authority and saved no-Git projections."""
import hashlib
import json
from pathlib import Path

import pytest

from issueintel.runtime.io import digest,write_json
from test_versions import versions,commit
from test_workbench import completed,local_server,http
from test_stage_jobs import stage_workbench,source as failed_stage_source,approval as approve_stages


def run_version(fixture, identifier=None):
    wb,source,pack=fixture
    source=identifier or source
    raw=wb.result_bytes(source)
    row=completed(wb,wb.submit_versions({'job_id':source,'revision':'HEAD',
        'source_pack_sha256':hashlib.sha256(raw).hexdigest()})['id'])
    assert row['status']=='completed',row
    return row,json.loads(wb.versions_bytes(row['id']))


def test_owned_followup_can_inspect_target_without_modifying_source(versions):
    wb,source,pack=versions;original=wb.result_bytes(source)
    followup=completed(wb,wb.submit({'mode':'followup','parent_job_id':source,
        'additional_context':'Synthetic caller supplies None; this is an unverified observation.'})['id'])
    assert followup['status']=='completed',followup
    raw=wb.result_bytes(followup['id'])
    assert wb.intake(followup['id'])['versions_available'] is True
    repo=Path(pack['snapshot']['repository'])
    path=repo/'parser.py';path.write_text('# later revision\n'+path.read_text(encoding='utf-8'),encoding='utf-8')
    target=commit(repo)
    row=completed(wb,wb.submit_versions({'job_id':followup['id'],'revision':target,
        'source_pack_sha256':hashlib.sha256(raw).hexdigest()})['id'])
    assert row['status']=='completed',row
    result=json.loads(wb.versions_bytes(row['id']))
    assert result['evidence_impact']['source']['job_id']==followup['id']
    assert result['evidence_impact']['after']['commit_sha']==target
    assert result['evidence_impact']['before']['commit_sha']==pack['snapshot']['commit_sha']
    assert result['evidence_impact']['counts']['moved']>=1
    assert wb.result_bytes(source)==original and wb.result_bytes(followup['id'])==raw


def test_owned_stage_recovery_is_a_version_source_without_new_provider_calls(stage_workbench):
    wb,_,calls,_=stage_workbench
    failed=failed_stage_source(stage_workbench)
    preview=wb.stage_recoveries.preview(failed['id'])
    recovered=completed(wb,wb.stage_recoveries.submit(failed['id'],approve_stages(preview))['id'])
    assert recovered['status']=='completed',recovered
    count=len(calls);original=wb.result_bytes(recovered['id'])
    assert wb.intake(recovered['id'])['versions_available']
    row=completed(wb,wb.submit_versions({'job_id':recovered['id'],'revision':'HEAD',
        'source_pack_sha256':hashlib.sha256(original).hexdigest()})['id'])
    assert row['status']=='completed',row
    report=json.loads(wb.versions_bytes(row['id']))
    assert report['evidence_impact']['source']['job_id']==recovered['id']
    assert len(calls)==count and wb.result_bytes(recovered['id'])==original


@pytest.mark.parametrize('expected',[None,False,12,'f'*64,'not-a-hash'])
def test_wrong_reviewed_source_never_enqueues(versions,monkeypatch,expected):
    wb,source,_=versions
    monkeypatch.setattr(wb,'_enqueue',lambda *args,**kwargs:pytest.fail('A different source was queued'))
    with pytest.raises(ValueError):
        wb.submit_versions({'job_id':source,'revision':'HEAD','source_pack_sha256':expected})


def test_saved_impact_read_needs_no_git_provider_prices_or_configuration(versions,monkeypatch):
    wb,_,_=versions;row,_=run_version(versions);raw=wb.versions_bytes(row['id'])
    def deny(*args,**kwargs):pytest.fail('Saved impact read acquired a new source')
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',deny)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',deny)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',deny)
    wb.provider_factory=deny
    assert wb.versions_bytes(row['id'])==raw


@pytest.mark.parametrize('change',['missing','status','source','target'])
def test_rehashed_saved_impact_cannot_change_its_source_or_observation(versions,change):
    wb,_,_=versions;row,result=run_version(versions)
    impact=result['evidence_impact']
    if change=='missing':result.pop('evidence_impact')
    elif change=='status':
        impact['entries'][0]['status']='missing'
        impact['counts']={status:sum(entry['status']==status for entry in impact['entries']) for status in impact['counts']}
    elif change=='source':impact['source']['job_id']='e'*24
    else:impact['after']['commit_sha']='e'*40
    if change!='missing':
        impact.pop('sha256');impact['sha256']=digest(impact)
    result.pop('sha256');result['sha256']=digest(result)
    path=wb.root/row['result']['versions.json']['path'];write_json(path,result)
    saved=json.loads(wb._job_path(row['id']).read_bytes())
    if change!='missing':saved['impact_sha256']=impact['sha256']
    saved['result']['versions.json']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    wb._save(saved)
    with pytest.raises(ValueError):wb.versions_bytes(row['id'])


def test_legacy_report_without_new_marker_keeps_original_file_diff_reader(versions,monkeypatch):
    wb,_,_=versions;row,result=run_version(versions)
    result.pop('evidence_impact');result.pop('sha256');result['sha256']=digest(result)
    path=wb.root/row['result']['versions.json']['path'];write_json(path,result)
    saved=json.loads(wb._job_path(row['id']).read_bytes())
    saved.pop('impact_schema');saved.pop('impact_sha256');saved['request'].pop('evidence_impact_schema')
    saved['result']['versions.json']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();wb._save(saved)
    raw=path.read_bytes()
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a,**kw:pytest.fail('Legacy read accessed Git'))
    assert wb.versions_bytes(row['id'])==raw


def test_http_impact_exports_keep_hashes_session_and_source(tmp_path):
    with local_server(tmp_path) as server:
        wb=server.workbench;source=completed(wb,wb.submit({'mode':'example'})['id'])['id']
        row=completed(wb,wb.submit_versions({'job_id':source,'revision':'HEAD'})['id'])
        assert row['status']=='completed',row
        report=json.loads(wb.versions_bytes(row['id']));impact=report['evidence_impact']
        headers={'X-IssueIntel-Token':server.session_token}
        for suffix in ('json','md'):
            route='/api/jobs/'+row['id']+'/evidence-impact.'+suffix
            assert http(server,'GET',route)[0]==403
            code,response,raw=http(server,'GET',route,headers=headers)
            assert code==200 and response['X-IssueIntel-Source-Pack-SHA256']==impact['source']['pack_sha256']
            assert response['X-IssueIntel-Evidence-Impact-SHA256']==impact['sha256']
            if suffix=='json':assert json.loads(raw)==impact
            else:assert b'Fix status: unverified' in raw
