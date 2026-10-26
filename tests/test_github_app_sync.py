"""Synthetic installation grants and revocation; no real account or network."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

from test_github_index import connected, REPO, BASE
from test_github_auto_sync import automatic, deliver, IDENTITY
from issueintel.integrations.github_app import GitHubAppError
from issueintel.integrations.github_index import GitHubReader, RepositoryIndex
from issueintel.integrations.github_sync import GitHubSync


@pytest.fixture
def app_sync(tmp_path, monkeypatch):
    for name in ('GITHUB_APP_CLIENT_ID', 'GITHUB_INSTALLATION_ID', 'GITHUB_APP_PRIVATE_KEY_PATH', 'GITHUB_APP_ID'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('GITHUB_TOKEN', 'synthetic-PAT-must-never-be-inherited')
    monkeypatch.setenv('GITHUB_APP_ID', '77')
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_PATH', 'synthetic-key-not-opened-by-fake.pem')
    state = SimpleNamespace(calls=[], invalidations=[], instances=[], error=None, after=None)
    class Broker:
        def __init__(self, config):
            self.config = config
            state.instances.append(self)
        def authorize(self, repo, repo_id, installation_id, *, purpose, fresh_grant):
            state.calls.append((repo, repo_id, installation_id, purpose, fresh_grant))
            if state.error:
                raise state.error
            if state.after:
                callback, state.after = state.after, None
                callback()
            return SimpleNamespace(token='synthetic-installation-' + str(len(state.calls)),
                repository=repo, repository_id=repo_id, installation_id=installation_id,
                app_id=77, purpose=purpose,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        def invalidate(self, **kwargs):
            state.invalidations.append(kwargs)
    monkeypatch.setattr('issueintel.integrations.github_sync.GitHubAppAuth', Broker)
    state.sync = GitHubSync(tmp_path)
    state.sync.connect(REPO, repository_id=42, installation_id=99, app_id=77)
    return state


def test_manual_public_mode_does_not_become_app_bound_from_global_config(app_sync):
    sync = app_sync.sync
    assert sync.authorize_repository('other/public') is None
    sync.connect('other/public', repository_id=43)
    sync.change('other/public', paused=True)
    assert sync.read_token_provider('other/public', fresh_grant=True) is None
    assert app_sync.calls == []


def test_offline_authority_binding_tracks_epoch_without_signing_or_environment(app_sync, monkeypatch):
    sync = app_sync.sync
    monkeypatch.delenv('GITHUB_APP_PRIVATE_KEY_PATH')
    monkeypatch.delenv('GITHUB_APP_ID')
    assert sync.authority_binding('other/public') is None
    assert sync.authority_binding(REPO) == (REPO, 42, 77, 99, 0)
    sync.change(REPO, grant_epoch=1)
    assert sync.authority_binding(REPO) == (REPO, 42, 77, 99, 1)
    sync.change(REPO, grant_pending=True)
    with pytest.raises(ValueError):
        sync.authority_binding(REPO)
    sync.change(REPO, grant_pending=False, paused=True)
    with pytest.raises(ValueError):
        sync.authority_binding(REPO)
    assert app_sync.calls == []


def test_bound_read_and_comment_are_distinct_and_config_change_replaces_broker(app_sync, monkeypatch):
    sync = app_sync.sync
    assert sync.authorize_repository(REPO, fresh_grant=True).token == 'synthetic-installation-1'
    assert sync.authorize_repository(REPO, purpose='comment', fresh_grant=True).purpose == 'comment'
    assert app_sync.calls == [(REPO, 42, 99, 'read', True), (REPO, 42, 99, 'comment', True)]
    assert len(app_sync.instances) == 1
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_PATH', 'different-synthetic.pem')
    sync.authorize_repository(REPO)
    assert len(app_sync.instances) == 2


@pytest.mark.parametrize('changes', [
    {'paused': True}, {'grant_pending': True}, {'phase': 'unknown'},
    {'repository_id': None}, {'repository_id': True}, {'installation_id': None},
    {'app_id': None}, {'app_id': '77'},
])
def test_bound_connection_cannot_downgrade_to_pat(app_sync, changes):
    app_sync.sync.change(REPO, **changes)
    with pytest.raises(ValueError):
        app_sync.sync.authorize_repository(REPO)
    assert app_sync.calls == []


@pytest.mark.parametrize('key,value', [
    ('GITHUB_APP_PRIVATE_KEY_PATH', ''), ('GITHUB_APP_ID', ''),
    ('GITHUB_APP_ID', '78'), ('GITHUB_INSTALLATION_ID', '100'),
])
def test_bound_config_failure_never_uses_pat(app_sync, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        app_sync.sync.reader(REPO, fresh_grant=True)
    assert app_sync.calls == []


def test_local_revocation_during_remote_authorization_discards_credential(app_sync):
    app_sync.after = lambda: app_sync.sync.change(REPO, grant_epoch=1, grant_pending=True)
    with pytest.raises(ValueError):
        app_sync.sync.authorize_repository(REPO, fresh_grant=True)
    # The cached credential is unusable while the local grant is pending.
    with pytest.raises(ValueError):
        app_sync.sync.authorize_repository(REPO)
    assert len(app_sync.calls) == 1


def test_signing_configuration_changed_during_request_denies_credential(app_sync, monkeypatch):
    app_sync.after = lambda: monkeypatch.setenv('GITHUB_APP_ID', '78')
    with pytest.raises(ValueError, match='changed during authorization'):
        app_sync.sync.authorize_repository(REPO)
    assert app_sync.invalidations == [{'installation_id': 99, 'repository': REPO}]


def test_token_provider_refreshes_each_page_and_revocation_blocks_next_get(app_sync):
    sync = app_sync.sync
    provider = sync.read_token_provider(REPO, fresh_grant=True)
    requests = []
    class Response(BytesIO):
        status = 200
        headers = {}
        def geturl(self):
            return BASE
    def opener(request, *, timeout):
        requests.append(request)
        return Response(b'{}')
    reader = GitHubReader(REPO, token_provider=provider, opener=opener)
    assert reader.token == ''
    reader.get(BASE); reader.get(BASE)
    assert [request.get_header('Authorization') for request in requests] == [
        'Bearer synthetic-installation-2', 'Bearer synthetic-installation-3']
    sync.change(REPO, paused=True)
    with pytest.raises(ValueError):
        reader.get(BASE)
    assert len(requests) == 2 and len(app_sync.calls) == 3


def test_retained_reader_cannot_follow_reinstalled_identity(app_sync):
    provider = app_sync.sync.read_token_provider(REPO)
    app_sync.sync.change(REPO, grant_epoch=1)
    with pytest.raises(ValueError, match='no longer matches'):
        provider()
    assert app_sync.calls == []


def test_revocation_during_response_download_does_not_return_page(app_sync):
    reader = app_sync.sync.reader(REPO)
    class Response(BytesIO):
        status = 200
        headers = {}
        def geturl(self):
            return BASE
        def read(self, size):
            app_sync.sync._grant_changed(REPO)
            return super().read(size)
    reader.opener = lambda *_a, **_kw: Response(b'{"sensitive":"synthetic page not returned"}')
    with pytest.raises(ValueError, match='inactive'):
        reader.get(BASE)
    assert len(app_sync.calls) == 1


def test_manual_reader_does_not_keep_pat_after_connection_becomes_app_bound(app_sync):
    sync = app_sync.sync
    reader = sync.reader('other/public')
    reader.opener = lambda *_a, **_kw: pytest.fail('HTTP attempted with old manual credential')
    sync.connect('other/public', repository_id=43, installation_id=99, app_id=77)
    with pytest.raises(ValueError, match='no longer matches'):
        reader.get('https://api.github.com/repos/other/public')
    assert app_sync.calls == []


@pytest.mark.parametrize('result', ['', None, 'bad\r\nheader', 'token with space'])
def test_reader_refuses_bad_provider_value_before_http(result):
    reader = GitHubReader(REPO, token_provider=lambda: result,
                          opener=lambda *_a, **_k: pytest.fail('HTTP attempted'))
    with pytest.raises(ValueError, match='credential'):
        reader.get(BASE)


def test_reader_provider_errors_never_trigger_pat_fallback(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'synthetic-pat')
    def fail():
        raise GitHubAppError('request_failed')
    reader = GitHubReader(REPO, token_provider=fail,
                          opener=lambda *_a, **_k: pytest.fail('HTTP attempted'))
    with pytest.raises(GitHubAppError):
        reader.get(BASE)
    assert reader.token == ''


def selection_payload(**extra):
    return {'installation': {'id': 99, 'app_id': 77, 'repository_selection': 'selected'},
            'action': 'removed', 'repositories_removed': [], **extra}


def test_all_to_selected_empty_removed_closes_admission_without_remote_call(app_sync):
    sync = app_sync.sync
    sync.change(REPO, phase='ready')
    deliver(sync, 'installation_repositories', selection_payload())
    connection = sync.connection(REPO)
    assert connection['grant_pending'] is True and connection['grant_epoch'] == 1
    assert connection['paused'] is False
    assert [c['repository'] for c in sync.due()] == [REPO]
    assert app_sync.calls == []
    command = {'repository': IDENTITY, 'installation': {'id': 99}}
    with pytest.raises(ValueError, match='actively connected'):
        sync.admit_command(command)
    with pytest.raises(ValueError):
        sync.authorize_repository(REPO)


@pytest.mark.parametrize('error', [None, 'paused'])
def test_selection_change_preserves_manual_pause(app_sync, error):
    sync = app_sync.sync
    sync.change(REPO, paused=True, error=error, phase='ready')
    deliver(sync, 'installation_repositories', selection_payload())
    assert sync.connection(REPO)['paused'] and sync.connection(REPO)['error'] == error
    assert sync.due() == [] and app_sync.calls == []


def test_repository_added_event_does_not_restart_manual_pause(app_sync):
    sync = app_sync.sync
    sync.change(REPO, paused=True, phase='ready', error=None)
    deliver(sync, 'installation_repositories', selection_payload(action='added', repositories_added=[IDENTITY]))
    current = sync.connection(REPO)
    assert current['paused'] and current['phase'] == 'ready' and current['error'] is None
    assert sync.due() == [] and app_sync.calls == []


def test_reconnect_epoch_invalidates_retained_reader(app_sync):
    provider = app_sync.sync.read_token_provider(REPO)
    app_sync.sync.change(REPO, paused=True)
    app_sync.sync.connect(REPO)
    with pytest.raises(ValueError, match='no longer matches'):
        provider()


@pytest.mark.parametrize('action', ['deleted', 'suspend'])
@pytest.mark.parametrize('repositories', [None, 'irrelevant malformed list', [IDENTITY] * 101])
def test_installation_removal_pauses_all_bindings_independent_of_repository_array(app_sync, action, repositories):
    sync = app_sync.sync
    sync.authorize_repository(REPO)
    sync.connect('owner/another', repository_id=43, installation_id=99, app_id=77)
    sync.connect('owner/elsewhere', repository_id=44, installation_id=100, app_id=77)
    deliver(sync, 'installation', {'installation': {'id': 99, 'app_id': 77}, 'action': action,
                                  **({'repositories': repositories} if repositories is not None else {})})
    assert sync.connection(REPO)['paused'] and sync.connection('owner/another')['paused']
    assert not sync.connection('owner/elsewhere')['paused']
    assert app_sync.invalidations == [{'installation_id': 99}]
    assert len(app_sync.calls) == 1


def test_configured_app_and_installation_must_both_match(app_sync, monkeypatch):
    monkeypatch.setenv('GITHUB_INSTALLATION_ID', '100')
    with pytest.raises(ValueError, match='not configured'):
        deliver(app_sync.sync, 'installation_repositories', selection_payload())
    assert not app_sync.sync.connection(REPO).get('grant_pending')


def test_reconciliation_worker_proves_grant_before_indexing(automatic, app_sync):
    index, pages = automatic
    sync = GitHubSync(index.root)
    sync.connect(REPO, repository_id=42, installation_id=99, app_id=77)
    deliver(sync, 'installation_repositories', selection_payload())
    assert pages.calls == []
    result = sync.batch(REPO)
    assert result['scan_complete'] and sync.connection(REPO)['phase'] == 'ready'
    assert not sync.connection(REPO)['grant_pending']
    assert app_sync.calls[0] == (REPO, 42, 99, 'read', True)
    assert pages.calls


@pytest.mark.parametrize('status,paused', [(404, True), (403, True), (429, False), (503, False)])
def test_denied_or_unavailable_grant_does_not_index_or_fallback(automatic, app_sync, status, paused):
    index, pages = automatic
    sync = GitHubSync(index.root)
    sync.connect(REPO, repository_id=42, installation_id=99, app_id=77)
    deliver(sync, 'installation_repositories', selection_payload())
    app_sync.error = GitHubAppError('request_failed', http_status=status)
    assert sync.batch(REPO) is None
    connection = sync.connection(REPO)
    assert connection['paused'] is paused and connection['grant_pending'] is True
    assert connection['error'] == 'github_app_request_failed'
    assert pages.calls == [] and sync.due() == []


def test_new_selection_event_during_reconciliation_is_not_cleared(automatic, app_sync):
    index, pages = automatic
    sync = GitHubSync(index.root)
    sync.connect(REPO, repository_id=42, installation_id=99, app_id=77)
    deliver(sync, 'installation_repositories', selection_payload())
    app_sync.after = lambda: sync.change(REPO, grant_epoch=2, grant_pending=True)
    with pytest.raises(ValueError, match='changed during authorization'):
        sync.batch(REPO)
    assert sync.connection(REPO)['grant_pending'] is True
    assert pages.calls == []


@pytest.mark.parametrize('failure', [GitHubAppError('request_failed', http_status=429),
                                  OSError('synthetic network'), ValueError('synthetic integrity')])
def test_late_worker_failure_does_not_clear_concurrent_revocation(automatic, app_sync, failure):
    index, pages = automatic
    sync = GitHubSync(index.root)
    sync.connect(REPO, repository_id=42, installation_id=99, app_id=77)
    def fail(*_args, **_kwargs):
        sync._grant_changed(REPO)
        raise failure
    app_sync.instances.clear()
    sync._authorize = fail
    if isinstance(failure, ValueError) and not isinstance(failure, GitHubAppError):
        with pytest.raises(ValueError):
            sync.batch(REPO)
    else:
        assert sync.batch(REPO) is None
    current = sync.connection(REPO)
    assert current['paused'] and current['error'] == 'installation_removed'
    assert current['grant_epoch'] == 1 and pages.calls == []


def test_direct_index_and_update_defaults_use_bound_reader(automatic, app_sync, monkeypatch):
    from issueintel.integrations.repository_updates import process_updates
    index, pages = automatic
    sync = GitHubSync(index.root)
    sync.connect(REPO, repository_id=42, installation_id=99, app_id=77)
    index.sync(max_requests=20)
    assert app_sync.calls and app_sync.calls[0][-1] is True
    before = len(app_sync.calls)
    process_updates(index)
    assert len(app_sync.calls) > before and app_sync.calls[before][-1] is True
    sync.change(REPO, paused=True)
    before_pages = len(pages.calls)
    with pytest.raises(ValueError):
        process_updates(index)
    assert len(pages.calls) == before_pages


def test_revocation_during_git_snapshot_does_not_publish_index_snapshot(automatic, app_sync, monkeypatch):
    index, _pages = automatic
    sync = GitHubSync(index.root)
    build = RepositoryIndex._build_snapshot
    def revoked(self, revision):
        snapshot = build(self, revision)
        sync._grant_changed(REPO)
        return snapshot
    monkeypatch.setattr(RepositoryIndex, '_build_snapshot', revoked)
    with pytest.raises(ValueError, match='inactive'):
        index.sync()
    status = index.status()
    assert status['current_snapshot'] is None and status['run']['snapshot'] is None
    assert status['scan_complete'] is False


def test_revocation_during_incremental_conversion_preserves_old_record(automatic, app_sync, monkeypatch):
    from test_github_index import issue
    from issueintel.integrations.repository_updates import process_updates
    from issueintel.runtime.io import digest
    index, pages = automatic
    sync = GitHubSync(index.root)
    pages.pages['/issues?'] = ([issue(1, body='previous canonical text')], None, digest('old-page'))
    index.sync()
    sync.change(REPO, phase='ready')
    deliver(sync, 'issues', {'repository': IDENTITY, 'installation': {'id': 99},
                            'action': 'edited', 'issue': {'number': 1}})
    pages.pages['/issues/1'] = (issue(1, body='new text must not be committed'), None, digest('new-page'))
    normal = index._record
    def revoked(*args):
        record = normal(*args)
        sync._grant_changed(REPO)
        return record
    monkeypatch.setattr(index, '_record', revoked)
    with pytest.raises(ValueError, match='inactive'):
        process_updates(index)
    assert index.item('issue:1')['record']['body'] == 'previous canonical text'
    assert index.status()['pending_updates'] == 1
