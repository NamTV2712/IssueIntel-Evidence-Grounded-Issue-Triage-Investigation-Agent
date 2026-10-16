"""Release jobs and HTTP boundaries using synthetic Git/REST fixtures only."""
from hashlib import sha256
import json
import threading

import pytest

from issueintel.investigation.workbench import Workbench
from issueintel.integrations.github_index import GitHubReadError
from issueintel.runtime.io import digest, write_json
from test_github_integration import github_workbench
from test_github_releases import ReleaseReader, release_pages, TAG, REFERENCE, REPO
from test_workbench import completed, http


@pytest.fixture
def release_workbench(tmp_path, monkeypatch):
    wb, revision = github_workbench(tmp_path, monkeypatch)
    pages = json.loads(json.dumps(release_pages()).replace(REPO, 'synthetic/example'))
    reader = ReleaseReader(pages)
    monkeypatch.setattr('issueintel.investigation.release_jobs.GitHubReader', lambda *_a, **_k: reader)
    source = completed(wb, wb.submit({'mode':'github',
        'issue_url':'https://github.com/synthetic/example/issues/5','revision':revision})['id'])
    assert source['status'] == 'completed'
    try:
        yield wb, source, reader
    finally:
        wb.close()


def request(source):
    return {'job_id':source['id'], 'tag':TAG, 'reference_commit':REFERENCE}


def check(wb, source):
    job = completed(wb, wb.submit_release(request(source))['id'])
    assert job['status'] == 'completed', job
    return job


def test_receipts_source_and_exports_survive_restart_without_network_or_git(release_workbench, monkeypatch):
    wb, source, reader = release_workbench
    original = wb.result_bytes(source['id'])
    job = check(wb, source)
    raw = wb.release_bytes(job['id'])
    result = json.loads(raw)
    assert result['source']['pack_sha256'] == sha256(original).hexdigest()
    assert result['source']['job_id'] == source['id']
    assert result['containment']['status'] == 'contained'
    assert result['fix_status'] == 'unverified'
    receipts = sorted((wb.directory/'runs'/job['id']/'release_receipts').glob('*.json'))
    assert [json.loads(p.read_bytes()) for p in receipts] == result['receipts']
    assert len(reader.calls) == len(receipts) == 9
    assert wb.result_bytes(source['id']) == original
    assert job['id'] not in {row['id'] for row in wb.history()}
    assert wb.release_history(source['id'])[0]['id'] == job['id']
    def denied(*_a, **_k):
        pytest.fail('Saved release read attempted network, Git or provider acquisition')
    monkeypatch.setattr(reader, 'get', denied)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository', denied)
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot', denied)
    other = Workbench(wb.root, provider_factory=denied)
    try:
        assert other.release_bytes(job['id']) == raw
        assert other.release_history(source['id'])[0]['id'] == job['id']
    finally:
        other.close()


@pytest.mark.parametrize('change', ['extra','unsafe_tag','long_tag','short_sha','upper_sha','missing'])
def test_invalid_release_input_never_enqueues_or_reads(release_workbench, change):
    wb, source, reader = release_workbench
    value = request(source)
    if change == 'extra': value['provider'] = 'groq'
    elif change == 'unsafe_tag': value['tag'] = '../secret'
    elif change == 'long_tag': value['tag'] = 'v' * 201
    elif change == 'short_sha': value['reference_commit'] = 'abc'
    elif change == 'upper_sha': value['reference_commit'] = 'A' * 40
    else: value.pop('tag')
    before = set(wb.jobs.glob('*.json'))
    with pytest.raises(ValueError): wb.submit_release(value)
    assert not reader.calls and set(wb.jobs.glob('*.json')) == before


def test_example_local_followup_and_benchmark_authority_cannot_read_releases(release_workbench):
    wb, source, reader = release_workbench
    example = completed(wb, wb.submit({'mode':'example'})['id'])
    followup = completed(wb, wb.submit({'mode':'followup','parent_job_id':source['id'],
                                       'additional_context':'Expected: no crash'})['id'])
    assert followup['status'] == 'completed'
    for candidate in (example, followup):
        with pytest.raises(ValueError): wb.submit_release(request(candidate))
    path = wb.root/source['result']['pack.json']['path']
    pack = json.loads(path.read_bytes()); pack['case']['split'] = 'main_test'
    write_json(path, pack)
    row = json.loads(wb._job_path(source['id']).read_bytes())
    row['result']['pack.json']['sha256'] = sha256(path.read_bytes()).hexdigest()
    write_json(wb._job_path(source['id']), row)
    with pytest.raises(ValueError): wb.submit_release(request(source))
    assert not reader.calls


def test_queued_source_change_is_rejected_before_network(release_workbench):
    wb, source, reader = release_workbench
    gate = threading.Event()
    wb.executor.submit(lambda:gate.wait(5))
    job = wb.submit_release(request(source))
    path = wb.root/source['result']['pack.json']['path']
    path.write_bytes(path.read_bytes() + b' ')
    gate.set()
    assert completed(wb, job['id'])['status'] == 'failed'
    assert not reader.calls


def test_partial_failure_keeps_completed_receipts_and_does_not_retry(release_workbench):
    wb, source, reader = release_workbench
    reader.pages['https://api.github.com/repos/synthetic/example/releases/tags/' + TAG] = GitHubReadError(429)
    job = completed(wb, wb.submit_release(request(source))['id'])
    assert job['status'] == 'failed' and job['completed_requests'] == 1
    assert len(reader.calls) == 2
    assert (wb.directory/'runs'/job['id']/'release_receipts/01.json').exists()
    with pytest.raises(ValueError): wb.release_bytes(job['id'])
    wb.release_history(source['id'])
    assert len(reader.calls) == 2


def test_cancellation_keeps_received_receipt_before_next_read(release_workbench):
    wb, source, reader = release_workbench
    def cancel_after_read(_reader, _url):
        wb.cancel(next(iter(wb.active)))
    reader.hook = cancel_after_read
    job = completed(wb, wb.submit_release(request(source))['id'])
    assert job['status'] == 'cancelled' and job['completed_requests'] == 1
    assert len(reader.calls) == 1


@pytest.mark.parametrize('change', ['source','request','receipt','outside_path'])
def test_repaired_outer_export_hash_does_not_override_bindings(release_workbench, change):
    wb, source, _ = release_workbench
    job = check(wb, source)
    path = wb.root/job['result']['releases.json']['path']
    result = json.loads(path.read_bytes())
    row = json.loads(wb._job_path(job['id']).read_bytes())
    if change == 'source': result['source']['pack_sha256'] = '0' * 64
    elif change == 'request': result['requested']['reference_commit'] = 'f' * 40
    elif change == 'receipt': result['receipts'][0]['response']['id'] = 999
    else: row['result']['releases.json']['path'] = 'proposal.md'
    result['sha256'] = digest({k:v for k,v in result.items() if k != 'sha256'})
    write_json(path, result)
    row['result']['releases.json']['sha256'] = sha256(path.read_bytes()).hexdigest()
    write_json(wb._job_path(job['id']), row)
    with pytest.raises(ValueError): wb.release_bytes(job['id'])


def test_http_release_routes_enforce_session_origin_and_saved_source(release_workbench):
    wb, source, reader = release_workbench
    # Reuse the same synthetic Workbench without any external service.
    from issueintel.interfaces.web import make_server
    server = make_server(wb.root, port=0, workbench=wb)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    token = {'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
    try:
        body = json.dumps(request(source)).encode()
        assert http(server, 'POST', '/api/releases', body)[0] == 403
        assert http(server, 'POST', '/api/releases', body,
                    headers={**token,'Origin':'https://attacker.invalid'})[0] == 403
        status, _, raw = http(server, 'POST', '/api/releases', body, headers=token)
        assert status == 202
        job = completed(wb, json.loads(raw)['id'])
        assert job['status'] == 'completed'
        route = '/api/jobs/' + job['id'] + '/releases.json'
        assert http(server, 'GET', route)[0] == 403
        status, headers, raw = http(server, 'GET', route, headers=token)
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert raw == wb.release_bytes(job['id'])
        history = '/api/jobs/' + source['id'] + '/releases'
        assert json.loads(http(server, 'GET', history, headers=token)[2])[0]['id'] == job['id']
        assert len(reader.calls) == 9
    finally:
        server.shutdown(); server.server_close(); worker.join()
