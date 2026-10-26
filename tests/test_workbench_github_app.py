"""App-bound source and exact reviewed writes using synthetic credentials only."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from issueintel.runtime.io import digest, write_json
from test_github_integration import github_workbench
from test_workbench import completed


URL = 'https://github.com/synthetic/example/issues/5'
REPO = 'synthetic/example'


@pytest.fixture
def ready(tmp_path, monkeypatch):
    wb, revision = github_workbench(tmp_path, monkeypatch)
    try:
        job = completed(wb, wb.submit({'mode': 'github', 'issue_url': URL, 'revision': revision})['id'])
        assert job['status'] == 'completed', job
        yield wb, job['id'], wb.directory/'runs'/job['id']
    finally:
        wb.close()


def bind(wb):
    wb.github_sync.connect(REPO, repository_id=99, app_id=123, installation_id=77)
    wb.github_sync.change(REPO, phase='ready')


def approval(draft):
    return {'approved_sha256': draft['body_sha256'],
            'approved_context_sha256': draft['approval_context_sha256'],
            'confirmation': 'publish_reviewed_comment'}


def credential(purpose='comment'):
    return SimpleNamespace(repository=REPO, repository_id=99, app_id=123,
                           installation_id=77, purpose=purpose, token='synthetic-installation-token')


def test_preview_is_offline_and_locks_public_app_identity(ready, monkeypatch):
    wb, identifier, directory = ready
    bind(wb)
    monkeypatch.setattr(wb.github_sync, '_broker', lambda *a: pytest.fail('Preview must stay offline'))
    draft = wb.preview_comment(identifier)
    assert draft['authentication'] == {'kind': 'github_app', 'repository': REPO,
        'repository_id': 99, 'app_id': 123, 'installation_id': 77, 'grant_epoch': 0, 'purpose': 'comment'}
    assert draft['approval_context_sha256'] == digest({k: v for k, v in draft.items()
                                                      if k not in {'body', 'approval_context_sha256'}})
    assert not (directory/'github_publish_intent.json').exists()


def test_app_publish_uses_fresh_installation_token_and_once_intent_without_pat(ready, monkeypatch):
    wb, identifier, directory = ready
    bind(wb); monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    grants, posts = [], []
    def grant(repository, **kwargs):
        grants.append((repository, kwargs)); return credential()
    def post(draft, **kwargs):
        assert (directory/'github_publish_intent.json').is_file()
        posts.append(kwargs)
        return {'comment_id': 7, 'comment_url': URL+'#issuecomment-7'}
    monkeypatch.setattr(wb.github_sync, 'authorize_repository', grant)
    monkeypatch.setattr('issueintel.investigation.workbench.post_reviewed_comment', post)
    draft = wb.preview_comment(identifier)
    receipt = wb.publish_comment(identifier, approval(draft))
    assert grants == [(REPO, {'purpose': 'comment', 'fresh_grant': True})]
    assert posts == [{'approved_sha256': draft['body_sha256'], 'token': credential().token}]
    assert receipt['authentication'] == draft['authentication']
    assert credential().token not in json.dumps(receipt)
    assert credential().token not in (directory/'github_publish_intent.json').read_text()
    with pytest.raises(ValueError, match='already attempted'):
        wb.publish_comment(identifier, approval(draft))
    assert len(grants) == len(posts) == 1


@pytest.mark.parametrize('change', ['paused', 'grant_pending', 'epoch', 'installation', 'app', 'repository_id'])
def test_connection_change_after_preview_denies_before_auth_or_post(ready, monkeypatch, change):
    wb, identifier, directory = ready
    bind(wb); draft = wb.preview_comment(identifier)
    update = {'paused': {'paused': True}, 'grant_pending': {'grant_pending': True},
              'epoch': {'grant_epoch': 1}, 'installation': {'installation_id': 78},
              'app': {'app_id': 124}, 'repository_id': {'repository_id': 100}}[change]
    wb.github_sync.change(REPO, **update)
    monkeypatch.setattr(wb.github_sync, 'authorize_repository', lambda *a, **k: pytest.fail('Stale approval'))
    with pytest.raises(ValueError):
        wb.publish_comment(identifier, approval(draft))
    assert not (directory/'github_publish_intent.json').exists()


def test_app_requires_context_approval_and_missing_signing_cannot_fall_back_to_pat(ready, monkeypatch):
    wb, identifier, directory = ready
    bind(wb); monkeypatch.setenv('GITHUB_TOKEN', 'synthetic-pat-must-not-be-used')
    for name in ('GITHUB_APP_ID', 'GITHUB_APP_CLIENT_ID', 'GITHUB_APP_PRIVATE_KEY_PATH', 'GITHUB_INSTALLATION_ID'):
        monkeypatch.delenv(name, raising=False)
    draft = wb.preview_comment(identifier)
    body_only = approval(draft); body_only.pop('approved_context_sha256')
    with pytest.raises(ValueError, match='approval'):
        wb.publish_comment(identifier, body_only)
    with pytest.raises(ValueError, match='not configured'):
        wb.publish_comment(identifier, approval(draft))
    assert not (directory/'github_publish_intent.json').exists()


@pytest.mark.parametrize('tamper', ['target', 'body', 'job', 'source'])
def test_consistently_rehashed_saved_preview_cannot_change_source_or_target(ready, monkeypatch, tamper):
    wb, identifier, directory = ready
    monkeypatch.setenv('GITHUB_TOKEN', 'synthetic-key')
    draft = wb.preview_comment(identifier); edited = deepcopy(draft)
    if tamper == 'target':
        edited.update(issue_number=6, issue_url=URL.replace('/5', '/6'))
    elif tamper == 'body':
        edited.update(body='A different report', body_sha256=__import__('hashlib').sha256(b'A different report').hexdigest())
    elif tamper == 'job':
        edited['job_id'] = 'f'*24
    else:
        edited['pack_sha256'] = 'f'*64
    edited['approval_context_sha256'] = digest({k: v for k, v in edited.items()
                                               if k not in {'body', 'approval_context_sha256'}})
    write_json(directory/'github_preview.json', edited)
    monkeypatch.setattr(wb.github_sync, 'authorize_repository', lambda *a, **k: pytest.fail('Retargeted draft'))
    with pytest.raises(ValueError, match='changed'):
        wb.publish_comment(identifier, approval(edited))
    assert not (directory/'github_publish_intent.json').exists()


def test_grant_changed_while_minting_requires_new_review_before_intent(ready, monkeypatch):
    wb, identifier, directory = ready
    bind(wb); draft = wb.preview_comment(identifier)
    def grant(*args, **kwargs):
        wb.github_sync.change(REPO, grant_epoch=1)
        return credential()
    monkeypatch.setattr(wb.github_sync, 'authorize_repository', grant)
    with pytest.raises(ValueError, match='changed'):
        wb.publish_comment(identifier, approval(draft))
    assert not (directory/'github_publish_intent.json').exists()


def test_failed_post_remains_unknown_and_no_retry_even_with_fresh_approval(ready, monkeypatch):
    wb, identifier, directory = ready
    bind(wb); posts = []
    monkeypatch.setattr(wb.github_sync, 'authorize_repository', lambda *a, **k: credential())
    def fail(*args, **kwargs):
        posts.append(1); raise ConnectionResetError('Synthetic provider detail')
    monkeypatch.setattr('issueintel.investigation.workbench.post_reviewed_comment', fail)
    draft = wb.preview_comment(identifier)
    with pytest.raises(ValueError, match='inspect GitHub'):
        wb.publish_comment(identifier, approval(draft))
    assert json.loads((directory/'github_publish_failure.json').read_bytes())['publication_outcome'] == 'unknown'
    draft = wb.preview_comment(identifier)
    with pytest.raises(ValueError, match='already attempted'):
        wb.publish_comment(identifier, approval(draft))
    assert posts == [1]


def test_fetch_uses_app_token_and_refuses_connection_changed_during_get(ready, monkeypatch):
    wb, _, _ = ready
    bind(wb); calls = []
    monkeypatch.setattr(wb.github_sync, 'authorize_repository', lambda *a, **k: credential('read'))
    def fetch(reference, **kwargs):
        calls.append((reference, kwargs)); return {'repository': REPO}
    assert wb.fetch_issue(URL, fetcher=fetch) == {'repository': REPO}
    assert calls == [(URL, {'token': credential().token})]
    def revoke(reference, **kwargs):
        wb.github_sync.change(REPO, paused=True)
        return {'repository': REPO}
    with pytest.raises(ValueError):
        wb.fetch_issue(URL, fetcher=revoke)


def cli_args(root):
    return SimpleNamespace(root=root, repo=URL, revision='a'*40, case_id=None,
        issue_file=None, output='artifacts/cli-test', planned=False, controller='rules',
        provider='groq', refinement_rounds=0, no_critic=False)


def cli_source(root, monkeypatch):
    from issueintel.integrations.github_sync import GitHubSync
    sync = GitHubSync(root)
    sync.connect(REPO, repository_id=99, app_id=123, installation_id=77)
    sync.change(REPO, phase='ready')
    monkeypatch.setattr(GitHubSync, 'authorize_repository',
                        lambda *a, **k: credential('read'))
    monkeypatch.setattr('issueintel.repository.acquire.fetch_github_issue', lambda reference, **kwargs: {
        'url': URL, 'repository': REPO, 'problem_statement': 'Synthetic parser problem',
        'created_at': '2020-01-01T00:00:00Z'})
    return sync


@pytest.mark.parametrize('change', [{'paused': True}, {'grant_epoch': 1}, {'grant_pending': True}])
def test_cli_issue_get_revocation_stops_before_git_or_provider(tmp_path, monkeypatch, change):
    from issueintel.interfaces.cli import _investigate
    sync = cli_source(tmp_path, monkeypatch)
    fetched = []
    def fetch(reference, **kwargs):
        fetched.append((reference, kwargs))
        sync.change(REPO, **change)
        return {'url': URL, 'repository': REPO, 'problem_statement': 'Synthetic parser problem',
                'created_at': '2020-01-01T00:00:00Z'}
    monkeypatch.setattr('issueintel.repository.acquire.fetch_github_issue', fetch)
    monkeypatch.setattr('issueintel.repository.acquire.acquire_repository',
                        lambda *a, **k: pytest.fail('Git started after issue GET revocation'))
    monkeypatch.setattr('issueintel.investigation.providers.GroqProvider',
                        lambda *a, **k: pytest.fail('Provider constructed after issue GET revocation'))
    with pytest.raises(ValueError):
        _investigate(cli_args(tmp_path))
    assert fetched == [(URL, {'token': credential('read').token})]


@pytest.mark.parametrize('kind', ['call', 'structured', 'json'])
def test_cli_revocation_between_groq_calls_prevents_next_provider_attempt(tmp_path, monkeypatch, kind):
    from issueintel.interfaces.cli import _investigate
    sync = cli_source(tmp_path, monkeypatch)
    calls = []
    config = SimpleNamespace(max_output_tokens=128)
    class Provider:
        model = 'openai/gpt-oss-120b'
        def __init__(self, loaded):
            assert loaded is config
            self.config = config
        def __call__(self, messages, max_output_tokens=2048):
            calls.append(('call', messages, max_output_tokens)); return {'synthetic': True}
        def complete_structured(self, messages, *, max_output_tokens, schema):
            calls.append(('structured', messages, max_output_tokens)); return {'synthetic': True}
        complete_json = __call__
    monkeypatch.setattr('issueintel.interfaces.cli.RuntimeConfig.load', lambda root: config)
    monkeypatch.setattr('issueintel.investigation.providers.GroqProvider', Provider)
    monkeypatch.setattr('issueintel.repository.acquire.acquire_repository',
                        lambda *a, **k: SimpleNamespace(path=tmp_path))
    monkeypatch.setattr('issueintel.retrieval.repository_context.collect_context', lambda *a, **k: None)
    def investigate(case, local, *, complete_json, **kwargs):
        messages = [{'role': 'user', 'content': 'Synthetic request'}]
        if kind == 'structured':
            run = lambda: complete_json.complete_structured(messages, max_output_tokens=128,
                                                             schema={'type': 'object'})
        elif kind == 'json':
            run = lambda: complete_json.complete_json(messages, max_output_tokens=128)
        else:
            run = lambda: complete_json(messages, max_output_tokens=128)
        assert run() == {'synthetic': True}
        sync.change(REPO, grant_epoch=1)
        run()  # Must fail in the wrapper before a second native call.
        pytest.fail('CLI continued after the App grant changed')
    monkeypatch.setattr('issueintel.investigation.pipeline.investigate', investigate)
    monkeypatch.setattr('issueintel.investigation.pipeline.export_pack',
                        lambda *a, **k: pytest.fail('Export published after grant revocation'))
    with pytest.raises(ValueError, match='connection changed'):
        _investigate(cli_args(tmp_path))
    assert len(calls) == 1
    assert calls[0][0] == ('structured' if kind == 'structured' else 'call')


def test_batch_commit_get_revocation_cannot_return_captured_revision(ready, monkeypatch):
    from io import BytesIO
    wb, _, _ = ready
    bind(wb)
    monkeypatch.setattr(wb.github_sync, 'authorize_repository', lambda *a, **k: credential('read'))
    requests = []
    def fetch(request, *, timeout):
        requests.append(request)
        wb.github_sync.change(REPO, grant_epoch=1)
        return BytesIO(json.dumps({'sha': 'a'*40}).encode())
    monkeypatch.setattr('issueintel.repository.acquire.open_github_request', fetch)
    with pytest.raises(ValueError, match='changed during revision resolution'):
        wb.batch_jobs._remote_revision(REPO, 'HEAD')
    assert len(requests) == 1
    assert requests[0].full_url == 'https://api.github.com/repos/'+REPO+'/commits/HEAD'
    assert requests[0].get_header('Authorization') == 'Bearer '+credential('read').token
