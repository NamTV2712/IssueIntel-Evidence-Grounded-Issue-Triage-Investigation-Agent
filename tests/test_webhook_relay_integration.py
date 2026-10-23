"""Signed relay -> native HTTP route -> durable command admission, locally only."""
from functools import partial
from hashlib import sha256
import hmac
from http.client import HTTPConnection
import json
import threading
from types import SimpleNamespace

from issueintel.integrations.github_sync import GitHubSync
from issueintel.integrations.webhook_relay import make_relay
from issueintel.interfaces.web import make_server
from issueintel.investigation.workbench import Workbench
from test_webhook_relay import running


SECRET = 'synthetic-native-relay-secret'
DELIVERY = '00000000-0000-0000-0000-000000000001'
IDENTITY = {'id': 42, 'full_name': 'synthetic/example', 'private': False}


def envelope(payload, *, event='issue_comment', delivery=DELIVERY):
    raw = json.dumps(payload).encode('utf-8')
    return raw, {'Content-Type': 'application/json',
        'X-Hub-Signature-256': 'sha256=' + hmac.new(SECRET.encode(), raw, sha256).hexdigest(),
        'X-GitHub-Event': event, 'X-GitHub-Delivery': delivery,
        'Origin': 'https://synthetic-tunnel.invalid', 'Authorization': 'private-client-value',
        'Cookie': 'private-client-value', 'X-IssueIntel-Token': 'private-client-value'}


def command():
    return {'action': 'created', 'repository': IDENTITY, 'installation': {'id': 99},
        'issue': {'html_url': 'https://github.com/synthetic/example/issues/5', 'number': 5},
        'comment': {'body': '/issueintel investigate abc123', 'author_association': 'OWNER'}}


def post(server, payload, **kwargs):
    raw, headers = envelope(payload, **kwargs)
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        connection.request('POST', '/api/github/webhook', raw, headers)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def test_native_route_keeps_session_private_deduplicates_restart_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_WEBHOOK_SECRET', SECRET)
    monkeypatch.setenv('GITHUB_APP_ID', '77')
    monkeypatch.setenv('GITHUB_INSTALLATION_ID', '99')
    submitted, jobs = [], {}
    def deny(*args, **kwargs):
        raise AssertionError('Relay/command admission tried acquisition, publication or provider construction')
    for name in ('acquire_repository', 'fetch_github_issue', 'post_reviewed_comment'):
        monkeypatch.setattr('issueintel.investigation.workbench.' + name, deny)
    monkeypatch.setattr(Workbench, '__init__', deny)
    def host():
        directory = tmp_path / 'artifacts/demo'
        directory.mkdir(parents=True, exist_ok=True)
        def submit(request):
            submitted.append(request)
            job = {'id': 'a' * 24, 'status': 'queued', 'private_metadata': 'must-stay-local'}
            jobs[job['id']] = job
            return job
        value = SimpleNamespace(directory=directory, lock=threading.RLock(),
            github_sync=GitHubSync(tmp_path), submit=submit, job=lambda identifier: jobs[identifier])
        value.receive_webhook = partial(Workbench.receive_webhook, value)
        return value
    original = host()
    original.github_sync.connect('synthetic/example', repository_id=42, installation_id=99, app_id=77)
    for restart in (False, True):
        current = host() if restart else original
        with running(make_server(tmp_path, port=0, workbench=current)) as native:
            # The local receiver continues to reject an external Origin. Only
            # the fixed relay route may replace transport headers after HMAC.
            assert post(native, command())[0] == 403
            with running(make_relay(port=0, workbench_port=native.server_port, secret=SECRET)) as relay:
                for _ in range(2):
                    status, raw, headers = post(relay, command())
                    assert status == 202 and json.loads(raw) == {'status': 'accepted'}
                    assert b'must-stay-local' not in raw and ('a' * 24).encode() not in raw
                    assert 'Set-Cookie' not in headers
                assert len(submitted) == 1
                if restart:
                    removal = {'action': 'deleted', 'installation': {'id': 99, 'app_id': 77}}
                    assert post(relay, removal, event='installation',
                        delivery='00000000-0000-0000-0000-000000000002')[0] == 202
                    assert current.github_sync.connection('synthetic/example')['paused'] is True
                    # A valid old signature/GUID does not restore a removed grant.
                    status, raw, _ = post(relay, command())
                    assert status == 400 and json.loads(raw) == {'status': 'receiver_error'}
                    assert len(submitted) == 1
    assert submitted == [{'mode': 'github', 'issue_url': 'https://github.com/synthetic/example/issues/5',
                          'revision': 'abc123', 'provider': 'none', 'depth': 'quick'}]
    assert len(list((original.directory / 'github_deliveries').glob('*.json'))) == 1
    assert not list(tmp_path.rglob('github_publish_intent.json'))
