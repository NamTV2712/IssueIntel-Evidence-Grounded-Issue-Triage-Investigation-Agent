from copy import deepcopy
import hashlib
import json
import subprocess

import pytest

from issueintel.investigation.versions import compare_versions, product_version_source, _diff
from issueintel.investigation.workbench import Workbench
from issueintel.repository.snapshot import build_snapshot, SourceFile
from test_workbench import completed, local_server, http


@pytest.fixture
def versions(tmp_path):
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k:pytest.fail('Model called'))
    try:
        job = completed(wb, wb.submit({'mode':'example'})['id'])
        pack = json.loads(wb.result_bytes(job['id']))
        yield wb, job['id'], pack
    finally:
        wb.close()


def commit(repo, message='Synthetic revision'):
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=','-C',str(repo),*args],stderr=subprocess.PIPE).decode().strip()
    git('add','-A')
    git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','-c','commit.gpgsign=false','commit','-qm',message)
    return git('rev-parse','HEAD')


def test_version_job_diff_is_source_bound_separate_durable_and_no_model(versions):
    wb, source, pack = versions
    from pathlib import Path
    repo = Path(pack['snapshot']['repository'])
    (repo/'parser.py').write_text('def parse_list(items):\n    return [] if items is None else [item.strip() for item in items]\n',encoding='utf-8')
    (repo/'formatting.py').unlink()
    (repo/'new.py').write_text('VALUE = 2\n',encoding='utf-8')
    target = commit(repo)
    raw_before = wb.result_bytes(source)
    job = completed(wb, wb.submit_versions({'job_id':source,'revision':target})['id'])
    assert job['status'] == 'completed', job
    result = json.loads(wb.versions_bytes(job['id']))
    assert result['before']['commit_sha'] == pack['snapshot']['commit_sha']
    assert result['after']['commit_sha'] == target
    assert result['counts'] == {'added':1,'removed':1,'modified':1,'unchanged':0,'unavailable':0}
    parser = next(row for row in result['files'] if row['path']=='parser.py')
    assert parser['in_source_evidence'] and '+    return [] if items is None' in parser['diff']['text']
    assert result['fix_status'] == 'unverified' and result['model_calls'] == 0
    assert result['evaluation_performed'] is False and wb.result_bytes(source) == raw_before
    assert job['id'] not in [row['id'] for row in wb.history()]
    assert wb.versions_history(source)[0]['id'] == job['id']
    wb2 = Workbench(wb.root)
    try:
        assert wb2.versions_bytes(job['id']) == wb.versions_bytes(job['id'])
        assert wb2.versions_history(source)[0]['status'] == 'completed'
    finally:
        wb2.close()
    path = wb.root/job['result']['versions.json']['path']
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='integrity'):
        wb.versions_bytes(job['id'])


def test_skipped_file_is_not_reported_deleted_and_same_commit_is_unchanged(versions):
    from pathlib import Path
    wb, _, pack = versions
    repo = Path(pack['snapshot']['repository'])
    before = build_snapshot(repo,pack['snapshot']['commit_sha'])
    same = compare_versions(pack,before,before)
    assert same['counts']['unchanged'] == 2 and not same['files_omitted']
    (repo/'parser.py').write_bytes(b'\x00binary')
    after = build_snapshot(repo,commit(repo))
    result = compare_versions(pack,before,after)
    file = next(row for row in result['files'] if row['path']=='parser.py')
    assert file['status'] == 'unavailable' and file['unavailable_after'] == 'binary'
    assert result['counts']['removed'] == 0 and 'diff' not in file


@pytest.mark.parametrize('change', ['split','strict','verified','live','followup','citation','source_hash'])
def test_benchmark_and_tampered_source_rejected(versions, change):
    from pathlib import Path
    _, _, original = versions
    pack = deepcopy(original)
    if change=='split': pack['case']['split']='main_test'
    elif change=='strict': pack['snapshot']['protocol']='issue_time'
    elif change in {'verified','live'}: pack['case']['case_id']=change+':synthetic'
    elif change=='followup': pack['followup']={'source_case_id':'verified:synthetic'}
    elif change=='source_hash': pack['snapshot']['snapshot_sha256']='f'*64
    else:
        code = next(e for e in pack['evidence'] if e['kind']=='code')
        code['text']='invented source';code['sha256']=hashlib.sha256(code['text'].encode()).hexdigest()
    snapshot = build_snapshot(Path(original['snapshot']['repository']),original['snapshot']['commit_sha'])
    with pytest.raises(ValueError): compare_versions(pack,snapshot,snapshot)


def test_diff_input_and_output_are_bounded():
    def file(text): return SourceFile('file.py',text,hashlib.sha256(text.encode()).hexdigest())
    assert _diff(file('a\n'*2001),file('b\n'),'file.py')['reason']=='diff_input_limit'
    result = _diff(file('a'*15000),file('b'*15000),'file.py')
    assert result['truncated'] and len(result['text'])<=12000


def test_file_display_cap_preserves_full_counts_and_prioritizes_source_evidence(versions):
    from pathlib import Path
    _, _, pack = versions
    repo = Path(pack['snapshot']['repository'])
    before = build_snapshot(repo, pack['snapshot']['commit_sha'])
    for i in range(205):
        (repo/f'added_{i}.txt').write_text('Synthetic added file\n',encoding='utf-8')
    after = build_snapshot(repo, commit(repo))
    result = compare_versions(pack, before, after)
    assert result['counts']['added'] == 205 and len(result['files']) == 200
    assert result['files_omitted'] == 205+len(result['source_evidence_paths'])-200
    assert all(path in [row['path'] for row in result['files']] for path in result['source_evidence_paths'])


def test_queued_version_job_can_cancel_without_reading_target(versions, monkeypatch):
    import threading
    wb, source, _ = versions
    release = threading.Event(); started = threading.Event()
    def block():
        started.set();assert release.wait(10)
    wb.executor.submit(block)
    assert started.wait(5)
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot',lambda *a, **k:pytest.fail('Cancelled job read Git'))
    try:
        job = wb.submit_versions({'job_id':source,'revision':'HEAD'})
        wb.cancel(job['id']);release.set()
        assert completed(wb,job['id'])['status'] == 'cancelled'
    finally:
        release.set()


def test_invalid_revision_and_fields_rejected_before_git_or_queue(versions, monkeypatch):
    wb, source, _ = versions
    monkeypatch.setattr(wb,'_enqueue',lambda *a:pytest.fail('Invalid request queued'))
    for revision in ['--upload-pack=bad','../../main','HEAD\n','main:secret',None]:
        with pytest.raises(ValueError): wb.submit_versions({'job_id':source,'revision':revision})
    with pytest.raises(ValueError): wb.submit_versions({'job_id':source,'revision':'HEAD','provider':'groq'})
    original = wb._load_pack(source)
    tampered = deepcopy(original[0]);tampered['case']['split']='main_dev'
    monkeypatch.setattr(wb,'_load_pack',lambda *a:(tampered,original[1],0))
    with pytest.raises(ValueError): wb.submit_versions({'job_id':source,'revision':'HEAD'})


def test_http_versions_require_session_and_preserve_source_binding(tmp_path):
    with local_server(tmp_path) as server:
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        source=completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])['id']
        body=json.dumps({'job_id':source,'revision':'HEAD'})
        assert http(server,'POST','/api/versions',body,{'Content-Type':'application/json'})[0]==403
        status,_,raw=http(server,'POST','/api/versions',body,headers)
        assert status==202
        job=completed(server.workbench,json.loads(raw)['id'])
        assert job['status']=='completed'
        status,_,raw=http(server,'GET','/api/jobs/'+job['id']+'/versions.json',headers=headers)
        assert status==200 and json.loads(raw)['source']['job_id']==source
        assert http(server,'GET','/api/jobs/'+source+'/versions',headers=headers)[0]==200
        assert http(server,'GET','/api/jobs/'+job['id']+'/pack.json',headers=headers)[0]==409
        source_path=server.workbench.directory/'runs'/source/'pack.json'
        source_path.write_bytes(source_path.read_bytes()+b' ')
        with pytest.raises(ValueError):server.workbench.versions_bytes(job['id'])
