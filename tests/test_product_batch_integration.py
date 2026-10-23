"""Public batch API locks local Git input and survives lost acknowledgements."""
from contextlib import contextmanager
from copy import deepcopy
import json
import subprocess
import threading
import time

import pytest

from issueintel.interfaces.web import make_server
from issueintel.investigation.workbench import Workbench
from test_focused_retrieval import fixture_repo
from test_workbench import http


def manifest(repository, *, revision='HEAD', run_key='local-api-batch'):
    return {'schema': 'product_batch_manifest_v1', 'run_key': run_key, 'items': [
        {'key': 'parser', 'request': {'mode': 'local', 'repository': str(repository),
            'revision': revision, 'issue_text': 'Synthetic parser.parse_list fails for None input.'}}]}


def approval(value):
    return {'manifest': value['manifest'], 'preview': value,
            'confirmed': True, 'allow_api_calls': False}


@contextmanager
def api(wb):
    server = make_server(wb.root, port=0, workbench=wb)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def call(method, path, value=None):
        code, headers, raw = http(server, method, path,
            None if value is None else json.dumps(value),
            {'X-IssueIntel-Token': server.session_token, 'Content-Type': 'application/json'})
        assert headers['Cache-Control'] == 'no-store'
        return code, json.loads(raw)
    try: yield call
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def finished(wb, identifier):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        value = wb.batch(identifier)
        if all(item['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}
               for item in value['items']):
            return value
        time.sleep(.02)
    pytest.fail('Synthetic product batch did not reach terminal items')


def deny_external(monkeypatch):
    def deny(*args, **kwargs): pytest.fail('A local offline batch accessed an external/config source')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', deny)
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue', deny)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository', deny)
    return deny


def test_http_batch_pins_preview_commit_after_head_moves(tmp_path, fixture_repo, monkeypatch):
    case, repository = fixture_repo
    deny = deny_external(monkeypatch)
    wb = Workbench(tmp_path, automatic_sync=False, provider_factory=deny)
    try:
        with api(wb) as call:
            code, preview = call('POST', '/api/batches/preview', {'manifest': manifest(repository)})
            assert code == 200 and preview['can_start'] and preview['model_calls'] == 0, preview
            assert not wb.history()
            source = preview['items'][0]['source']
            assert source['requested_revision'] == 'HEAD' and source['revision'] == case['revision']
            (repository/'parser.py').write_text('def parse_list(items):\n    return ["changed after preview"]\n', encoding='utf-8')
            subprocess.run(['git', '-C', str(repository), '-c', 'core.hooksPath=', 'add', 'parser.py'], check=True)
            subprocess.run(['git', '-C', str(repository), '-c', 'core.hooksPath=', '-c', 'user.name=Synthetic',
                '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm',
                'Synthetic branch moved after batch preview'], check=True)
            code, accepted = call('POST', '/api/batches', approval(preview))
            assert code == 202, accepted
            code, duplicate = call('POST', '/api/batches', approval(preview))
            assert code == 202 and duplicate['id'] == accepted['id'], duplicate
            value = finished(wb, accepted['id'])
            assert len(value['items']) == 1 and value['items'][0]['status'] == 'completed', value
            identifier = value['items'][0]['job_id']
            pack = json.loads(wb.result_bytes(identifier))
            assert pack['snapshot']['commit_sha'] == case['revision']
            assert pack['case']['revision'] == case['revision']
            assert any('item.strip()' in item['text'] for item in pack['evidence'] if item['kind'] == 'code')
            code, saved = call('GET', '/api/batches/'+accepted['id'])
            assert code == 200 and saved['items'][0]['job_id'] == identifier
            changed = deepcopy(approval(preview))
            changed['manifest']['items'][0]['request']['issue_text'] += ' Changed after consent.'
            code, _ = call('POST', '/api/batches', changed)
            assert code in {400, 409} and len(wb.history()) == 1
    finally: wb.close()


def test_saved_batch_read_and_duplicate_acceptance_after_restart_need_no_git_or_provider(tmp_path, fixture_repo, monkeypatch):
    _, repository = fixture_repo
    deny = deny_external(monkeypatch)
    wb = Workbench(tmp_path, automatic_sync=False, provider_factory=deny)
    try:
        preview = wb.batch_preview(manifest(repository, run_key='restart-read'))
        accepted = wb.batch_submit(approval(preview))
        value = finished(wb, accepted['id'])
        assert value['items'][0]['status'] == 'completed', value
        child_id = value['items'][0]['job_id']
        original = wb.result_bytes(child_id)
    finally: wb.close()
    monkeypatch.setattr('issueintel.repository.snapshot.build_snapshot', deny)
    monkeypatch.setattr('issueintel.repository.acquire.acquire_repository', deny)
    observer = Workbench(tmp_path, automatic_sync=False, provider_factory=deny)
    try:
        with api(observer) as call:
            code, saved = call('GET', '/api/batches/'+accepted['id'])
            assert code == 200 and saved['items'][0]['job_id'] == child_id
            code, history = call('GET', '/api/batches')
            assert code == 200 and history['batches'][0]['id'] == accepted['id']
            code, duplicate = call('POST', '/api/batches', approval(preview))
            assert code == 202 and duplicate['id'] == accepted['id'], duplicate
            assert len(observer.history()) == 1 and observer.result_bytes(child_id) == original
    finally: observer.close()
