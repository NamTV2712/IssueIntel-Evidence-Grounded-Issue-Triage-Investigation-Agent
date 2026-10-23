"""Signed App commands must retain their connected repository authority.

The host is deliberately only the receive_webhook adapter with submit/job spies:
these tests never start a Workbench worker, acquire Git, or construct a provider.
"""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
import threading
from types import SimpleNamespace

import pytest

from issueintel.integrations.github_sync import GitHubSync
from issueintel.investigation.workbench import Workbench


REPOSITORY = 'synthetic/example'
IDENTITY = {'id': 42, 'full_name': REPOSITORY, 'private': False}
DELIVERY = '00000000-0000-0000-0000-000000000001'
REMOVAL = '00000000-0000-0000-0000-000000000002'
SECRET = 'synthetic-command-secret'


@pytest.fixture
def host(tmp_path, monkeypatch):
    for key in ('GITHUB_APP_ID', 'GITHUB_INSTALLATION_ID'):
        monkeypatch.delenv(key, raising=False)
    directory = tmp_path / 'artifacts/demo'
    directory.mkdir(parents=True)
    calls = []
    jobs = {}
    def submit(request):
        calls.append(request)
        value = {'id': 'a' * 24, 'status': 'queued'}
        jobs[value['id']] = value
        return value
    return SimpleNamespace(directory=directory, lock=threading.RLock(),
        github_sync=GitHubSync(tmp_path), submit=submit, job=lambda identifier: jobs[identifier],
        submitted=calls)


def payload(*, installation=True):
    value = {'action': 'created', 'repository': deepcopy(IDENTITY),
             'issue': {'html_url': 'https://github.com/' + REPOSITORY + '/issues/5', 'number': 5},
             'comment': {'body': '/issueintel investigate abc123', 'author_association': 'OWNER'}}
    if installation:
        value['installation'] = {'id': 99}
    return value


def envelope(data, event='issue_comment', delivery=DELIVERY):
    raw = json.dumps(data).encode('utf-8')
    return raw, {'signature': 'sha256=' + hmac.new(SECRET.encode(), raw, sha256).hexdigest(),
                 'event': event, 'delivery': delivery, 'secret': SECRET}


def deliver(host, data, delivery=DELIVERY):
    raw, kwargs = envelope(data, delivery=delivery)
    return Workbench.receive_webhook(host, raw, **kwargs)


def connect(host, monkeypatch, *, app=True, install=True):
    if app:
        monkeypatch.setenv('GITHUB_APP_ID', '77')
    if install:
        monkeypatch.setenv('GITHUB_INSTALLATION_ID', '99')
    return host.github_sync.connect(REPOSITORY, repository_id=42, installation_id=99, app_id=77)


def rejected_before_intent(host, data):
    with pytest.raises(ValueError):
        deliver(host, data)
    assert host.submitted == []
    assert not (host.directory / 'github_deliveries').exists()


def test_revoked_app_command_is_rejected_before_intent_and_submit(host, monkeypatch):
    connect(host, monkeypatch)
    host.github_sync.change(REPOSITORY, paused=True, error='installation_removed')
    rejected_before_intent(host, payload())


@pytest.mark.parametrize('event,action,extra', [
    ('installation', 'deleted', {}),
    ('installation', 'suspend', {}),
    ('installation_repositories', 'removed', {'repositories_removed': [IDENTITY]}),
])
def test_actual_removal_receipt_blocks_command(event, action, extra, host, monkeypatch):
    connect(host, monkeypatch)
    raw, kwargs = envelope({'installation': {'id': 99, 'app_id': 77}, 'action': action, **extra},
                           event=event, delivery=REMOVAL)
    host.github_sync.receive(raw, **kwargs)
    assert host.github_sync.connection(REPOSITORY)['paused'] is True
    rejected_before_intent(host, payload())


@pytest.mark.parametrize('app,install', [(True, True), (True, False), (False, True)])
def test_active_bound_initial_connection_accepts_once_without_spend(app, install, host, monkeypatch):
    connect(host, monkeypatch, app=app, install=install)
    assert host.github_sync.connection(REPOSITORY)['phase'] == 'initial'
    first = deliver(host, payload())
    assert first == deliver(host, payload())
    assert host.submitted == [{'mode': 'github', 'issue_url': 'https://github.com/' + REPOSITORY + '/issues/5',
                               'revision': 'abc123', 'provider': 'none', 'depth': 'quick'}]


def test_installation_only_configuration_can_use_connection_without_app_id(host, monkeypatch):
    monkeypatch.setenv('GITHUB_INSTALLATION_ID', '99')
    host.github_sync.connect(REPOSITORY, repository_id=42, installation_id=99)
    assert deliver(host, payload())['status'] == 'queued'
    assert len(host.submitted) == 1


def test_delivery_guid_case_is_one_durable_command_identity(host, monkeypatch):
    connect(host, monkeypatch)
    delivery = 'abcdefab-abcd-abcd-abcd-abcdefabcdef'
    first = deliver(host, payload(), delivery=delivery.upper())
    assert deliver(host, payload(), delivery=delivery) == first
    assert len(host.submitted) == 1
    assert [path.name for path in (host.directory / 'github_deliveries').glob('*.json')] == [delivery + '.json']


def test_legacy_mixed_case_receipt_is_reused_without_rewriting(host, monkeypatch):
    connect(host, monkeypatch)
    delivery = 'abcdefab-abcd-abcd-abcd-abcdefabcdef'
    first = deliver(host, payload(), delivery=delivery)
    directory = host.directory / 'github_deliveries'
    canonical = directory / (delivery + '.json')
    legacy = directory / (delivery.upper() + '.json')
    before = canonical.read_bytes()
    canonical.rename(legacy)
    assert deliver(host, payload(), delivery=delivery) == first
    assert len(host.submitted) == 1 and legacy.read_bytes() == before
    assert [path.name for path in directory.iterdir()] == [legacy.name]


def test_legacy_case_alias_conflict_is_not_silently_selected(host, monkeypatch):
    from pathlib import Path
    connect(host, monkeypatch)
    delivery = 'abcdefab-abcd-abcd-abcd-abcdefabcdef'
    deliver(host, payload(), delivery=delivery)
    directory = host.directory / 'github_deliveries'
    canonical = directory / (delivery + '.json')
    before = canonical.read_bytes()
    original = Path.iterdir
    # Windows cannot store both names; emulate the case-sensitive inventory.
    monkeypatch.setattr(Path, 'iterdir', lambda path: iter([canonical, directory / (delivery.upper() + '.json')])
                        if path == directory else original(path))
    with pytest.raises(ValueError, match='case aliases'):
        deliver(host, payload(), delivery=delivery)
    assert len(host.submitted) == 1 and canonical.read_bytes() == before


@pytest.mark.parametrize('field,value', [('GITHUB_APP_ID', '78'), ('GITHUB_INSTALLATION_ID', '100')])
def test_two_configured_bindings_must_both_match(field, value, host, monkeypatch):
    connect(host, monkeypatch)
    monkeypatch.setenv(field, value)
    rejected_before_intent(host, payload())


@pytest.mark.parametrize('value', [None, True, '42', 0])
def test_payload_repository_requires_positive_integer_global_id(value, host, monkeypatch):
    connect(host, monkeypatch)
    data = payload()
    data['repository']['id'] = value
    rejected_before_intent(host, data)


@pytest.mark.parametrize('change', ['installation', 'repository', 'private', 'payload_app',
                                    'stored_app', 'stored_repository', 'stored_installation',
                                    'missing_installation', 'missing_connection', 'unconfigured'])
def test_app_identity_and_configuration_fail_before_intent(change, host, monkeypatch):
    connect(host, monkeypatch)
    data = payload()
    if change == 'installation':
        data['installation']['id'] = 100
    elif change == 'repository':
        data['repository']['id'] = 43
    elif change == 'private':
        data['repository']['private'] = True
    elif change == 'payload_app':
        data['installation']['app_id'] = 78
    elif change == 'stored_app':
        host.github_sync.change(REPOSITORY, app_id=78)
    elif change == 'stored_repository':
        host.github_sync.change(REPOSITORY, repository_id=43)
    elif change == 'stored_installation':
        host.github_sync.change(REPOSITORY, installation_id=100)
    elif change == 'missing_installation':
        del data['installation']
    elif change == 'missing_connection':
        with host.github_sync.db() as db:
            db.execute('DELETE FROM connections WHERE repo=?', (REPOSITORY,))
    else:
        monkeypatch.delenv('GITHUB_APP_ID')
        monkeypatch.delenv('GITHUB_INSTALLATION_ID')
    rejected_before_intent(host, data)


@pytest.mark.parametrize('field', ['GITHUB_APP_ID', 'GITHUB_INSTALLATION_ID'])
@pytest.mark.parametrize('value', ['bad', '0', '-1', ' ', '1.0', '９９'])
def test_invalid_config_never_downgrades_to_legacy(field, value, host, monkeypatch):
    connect(host, monkeypatch)
    monkeypatch.setenv(field, value)
    rejected_before_intent(host, payload(installation=False))


@pytest.mark.parametrize('value', [None, {}, [], {'id': None}, {'id': True}, {'id': '99'}, {'id': 0}])
def test_malformed_installation_is_rejected_before_intent(value, host, monkeypatch):
    connect(host, monkeypatch)
    data = payload()
    data['installation'] = value
    rejected_before_intent(host, data)


@pytest.mark.parametrize('field', ['repository_id', 'installation_id', 'app_id'])
@pytest.mark.parametrize('value', [None, True, '99', 0])
def test_invalid_stored_binding_cannot_authorize_command(field, value, host, monkeypatch):
    connect(host, monkeypatch)
    host.github_sync.change(REPOSITORY, **{field: value})
    rejected_before_intent(host, payload())


def test_payload_installation_requires_configured_scope_even_when_manual_connection_exists(host):
    host.github_sync.connect(REPOSITORY, repository_id=42)
    rejected_before_intent(host, payload())


def test_known_app_connection_cannot_downgrade_when_installation_and_config_are_missing(host):
    host.github_sync.connect(REPOSITORY, repository_id=42, installation_id=99, app_id=77)
    rejected_before_intent(host, payload(installation=False))


@pytest.mark.parametrize('manual_connection', [False, True])
def test_legacy_manual_signed_webhook_mode_is_preserved(manual_connection, host):
    if manual_connection:
        host.github_sync.connect(REPOSITORY)
        host.github_sync.change(REPOSITORY, paused=True)
    data = payload(installation=False)
    del data['repository']['id']  # Legacy commands did not require a registry/global ID.
    first = deliver(host, data)
    assert first == deliver(host, data) and len(host.submitted) == 1


def test_old_delivery_does_not_bypass_later_app_revocation(host, monkeypatch):
    connect(host, monkeypatch)
    accepted = deliver(host, payload())
    intent = host.directory / 'github_deliveries' / (DELIVERY + '.json')
    original = intent.read_bytes()
    host.github_sync.change(REPOSITORY, paused=True, error='installation_removed')
    with pytest.raises(ValueError):
        deliver(host, payload())
    assert intent.read_bytes() == original and len(host.submitted) == 1
    assert host.job(accepted['id']) == accepted  # Reading an existing report/job stays independent.
