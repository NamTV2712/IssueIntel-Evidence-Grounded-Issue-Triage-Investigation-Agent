"""Command-looking comment edits/deletions remain public context updates."""
from functools import partial
import json
import threading
from types import SimpleNamespace

import pytest

from issueintel.integrations.github_index import RepositoryIndex
from issueintel.integrations.github_sync import GitHubSync
from issueintel.integrations.webhook_relay import make_relay
from issueintel.interfaces.web import make_server
from issueintel.investigation.workbench import Workbench
from test_webhook_relay import running
from test_webhook_relay_integration import SECRET, IDENTITY, command, envelope, post


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_WEBHOOK_SECRET', SECRET)
    monkeypatch.setenv('GITHUB_APP_ID', '77')
    monkeypatch.setenv('GITHUB_INSTALLATION_ID', '99')
    submitted, jobs = [], {}
    def deny(*args, **kwargs):
        pytest.fail('Webhook routing must not fetch a repository, publish or construct a provider')
    for name in ('acquire_repository', 'fetch_github_issue', 'post_reviewed_comment', 'GroqProvider'):
        monkeypatch.setattr('issueintel.investigation.workbench.' + name, deny)
    monkeypatch.setattr('issueintel.integrations.github_index.GitHubReader.get', deny)
    monkeypatch.setattr(RepositoryIndex, '_build_snapshot', deny)
    monkeypatch.setattr(Workbench, '__init__', deny)
    directory = tmp_path / 'artifacts/demo'
    directory.mkdir(parents=True)
    def build():
        def submit(request):
            submitted.append(request)
            job = {'id': 'a' * 24, 'status': 'queued'}
            jobs[job['id']] = job
            return job
        value = SimpleNamespace(directory=directory, lock=threading.RLock(),
            github_sync=GitHubSync(tmp_path), submit=submit, job=lambda identifier: jobs[identifier])
        value.receive_webhook = partial(Workbench.receive_webhook, value)
        return value
    current = build()
    current.github_sync.connect(IDENTITY['full_name'], repository_id=42, installation_id=99, app_id=77)
    return tmp_path, build, current, submitted


def updates(root):
    index = RepositoryIndex(root, IDENTITY['full_name'])
    with index.connect() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT value FROM updates ORDER BY rowid')]


@pytest.mark.parametrize('action', ['edited', 'deleted'])
@pytest.mark.parametrize('association', ['OWNER', 'NONE'])
def test_native_relay_comment_update_queues_context_without_redispatch_even_after_restart(host, action, association):
    root, build, original, submitted = host
    created = command()
    created['comment'].update(id=7, body='/issueintel investigate HEAD')
    edited = {**created, 'action': action,
              'comment': {**created['comment'], 'author_association': association}}
    delivery = '00000000-0000-0000-0000-000000000002'
    for restart in (False, True):
        current = build() if restart else original
        with running(make_server(root, port=0, workbench=current)) as native:
            with running(make_relay(port=0, workbench_port=native.server_port, secret=SECRET)) as relay:
                assert post(relay, created)[0] == 202
                for _ in range(2):
                    status, body, _ = post(relay, edited, delivery=delivery)
                    assert status == 202 and json.loads(body) == {'status': 'accepted'}
                assert len(submitted) == 1
    rows = updates(root)
    assert len(rows) == 1 and rows[0]['delivery'] == delivery
    assert rows[0]['event'] == 'issue_comment' and rows[0]['action'] == action
    assert rows[0]['installation_id'] == 99
    assert rows[0]['operations'] == [
        {'kind': 'comments', 'subject': 7, 'remove': action == 'deleted', 'done': False, 'count': 0},
        {'kind': 'issues', 'subject': 5, 'remove': False, 'done': False, 'count': 0}]
    assert submitted == [{'mode': 'github', 'issue_url': 'https://github.com/synthetic/example/issues/5',
        'revision': 'HEAD', 'provider': 'none', 'depth': 'quick'}]
    assert not list((root / 'artifacts').rglob('github_publish_intent.json'))


@pytest.mark.parametrize('action', ['edited', 'deleted'])
@pytest.mark.parametrize('change', ['paused', 'installation'])
def test_comment_update_retains_connection_and_installation_admission(host, action, change):
    root, _, current, submitted = host
    value = command()
    value['action'] = action
    value['comment']['id'] = 7
    if change == 'paused':
        current.github_sync.change(IDENTITY['full_name'], paused=True, error='installation_removed')
    else:
        value['installation'] = {'id': 100}
    with running(make_server(root, port=0, workbench=current)) as native:
        with running(make_relay(port=0, workbench_port=native.server_port, secret=SECRET)) as relay:
            status, _, _ = post(relay, value)
            assert status == (202 if change == 'paused' else 400)
    assert not submitted and not updates(root)


@pytest.mark.parametrize('action', ['edited', 'deleted'])
def test_comment_update_still_authenticates_raw_bytes_before_routing(host, action):
    root, _, current, submitted = host
    value = command(); value['action'] = action; value['comment']['id'] = 7
    raw, headers = envelope(value)
    with pytest.raises(ValueError, match='signature'):
        current.receive_webhook(raw + b' ', signature=headers['X-Hub-Signature-256'],
            event='issue_comment', delivery=headers['X-GitHub-Delivery'], secret=SECRET)
    assert not submitted and not updates(root)
