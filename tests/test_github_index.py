"""Synthetic Git/HTTP only: product context must never acquire benchmark data."""
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from issueintel.integrations.github_index import GitHubReader, RepositoryIndex, _writer_lock
from issueintel.runtime.io import digest


REPO = 'owner/project'
BASE = 'https://api.github.com/repos/' + REPO
STAMP = '2026-10-15T00:00:00Z'
SHA = 'a' * 40


def issue(number, *, pull=False, body='Parser fails on Unicode input'):
    return {'id': number + 100, 'number': number, 'title': 'Unicode parser failure', 'body': body,
            'html_url': f'https://github.com/{REPO}/{"pull" if pull else "issues"}/{number}',
            'created_at': STAMP, 'updated_at': STAMP, 'state': 'closed',
            **({'pull_request': {}} if pull else {})}


class Reader:
    def __init__(self, pages=None):
        self.calls = []
        self.pages = pages or {}

    def get(self, url):
        self.calls.append(url)
        if url == BASE:
            value = {'id': 42, 'full_name': REPO, 'private': False}
            return value, None, digest(value)
        for fragment, result in self.pages.items():
            if fragment in url:
                if isinstance(result, Exception):
                    raise result
                return result
        return [], None, digest([])


@pytest.fixture
def connected(tmp_path):
    origin = tmp_path/'synthetic'
    origin.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(origin), *args], check=True, capture_output=True).stdout.decode().strip()
    git('init', '-q')
    git('config', 'user.name', 'Synthetic')
    git('config', 'user.email', 'synthetic@example.test')
    (origin/'parser.py').write_text('def parse_unicode(value):\n    raise RuntimeError("never execute target")\n')
    git('add', '.')
    git('commit', '-qm', 'synthetic')
    commit = git('rev-parse', 'HEAD')
    index = RepositoryIndex(tmp_path, REPO)
    def builder(revision):
        with patch('issueintel.integrations.github_index.acquire_repository', return_value=SimpleNamespace(path=origin, commit_sha=commit)):
            return index._build_snapshot(revision)
    return index, builder


def test_resume_commits_page_and_cursor_together_without_rebuilding(connected):
    index, builder = connected
    first = index._url('/issues', per_page=100, page=1, state='all', sort='created', direction='asc')
    second = first.replace('page=1&', 'page=2&')
    reader = Reader({'/issues?': ([issue(1)], second, digest('first'))})
    report = index.sync(reader=reader, snapshot_builder=builder, max_requests=2)
    assert report['counts'] == {'issue': 1}
    assert not report['scan_complete']
    assert report['requests_this_invocation'] == 2
    with index.connect() as db:
        task = db.execute("SELECT * FROM tasks WHERE kind='issues'").fetchone()
        assert task['url'] == second and task['pages'] == 1
    # New instance simulates process restart. The committed first page is not fetched.
    index = RepositoryIndex(index.root, REPO)
    reader = Reader({'/issues?': ([issue(2)], None, digest('second'))})
    report = index.sync(reader=reader, snapshot_builder=lambda _: pytest.fail('snapshot rebuilt'))
    assert report['scan_complete'] and report['counts'] == {'issue': 2}
    assert second in reader.calls and first not in reader.calls
    reader.calls.clear()
    assert index.sync(reader=reader)['scan_complete']
    assert reader.calls == []  # Completed scan is never restarted implicitly.


def test_failure_preserves_cursor_records_and_restart_search(connected):
    index, builder = connected
    reader = Reader({'/issues?': ([issue(1)], None, digest('page')),
                     '/issues/comments?': ValueError('synthetic rate limit')})
    with pytest.raises(ValueError, match='rate limit'):
        index.sync(reader=reader, snapshot_builder=builder)
    assert index.status()['counts'] == {'issue': 1}
    assert not index.status()['scan_complete']
    assert index.item('issue:1')['record']['body'] == 'Parser fails on Unicode input'
    reader = Reader()
    index.sync(reader=reader)
    assert not any('/issues?' in url for url in reader.calls)


def test_pr_comments_review_files_commits_and_mentions_are_separate(connected):
    index, builder = connected
    pull = issue(2, pull=True, body='Might relate to #1; this is not a confirmed fix')
    pull.update(base={'sha': SHA, 'repo': {'full_name': REPO}}, head={'sha': 'b'*40}, merged_at=STAMP, merge_commit_sha=SHA)
    comment = {'id': 7, 'issue_url': BASE+'/issues/1', 'html_url': f'https://github.com/{REPO}/issues/1#issuecomment-7',
               'body': 'Unicode parser reproduction', 'created_at': STAMP, 'updated_at': STAMP}
    review = {'id': 8, 'pull_request_url': BASE+'/pulls/2', 'html_url': f'https://github.com/{REPO}/pull/2#discussion_r8',
              'body': 'Unicode review', 'created_at': STAMP, 'updated_at': STAMP, 'path': 'parser.py', 'commit_id': SHA}
    reader = Reader({
        '/issues?': ([issue(1), issue(2, pull=True)], None, digest('issues')),
        '/issues/comments?': ([comment], None, digest(comment)),
        '/pulls/comments?': ([review], None, digest(review)),
        '/pulls?': ([pull], None, digest(pull)),
        '/pulls/2/files?': ([{'filename': 'parser.py', 'status': 'modified', 'sha': SHA,
                             'patch': 'SECRET GOLD-LIKE DIFF MUST BE DISCARDED'}], None, digest('files')),
        '/pulls/2/commits?': ([{'sha': SHA, 'html_url': f'https://github.com/{REPO}/commit/{SHA}',
                              'commit': {'message': 'Unicode parser change'}}], None, digest('commits')),
    })
    result = index.sync(reader=reader, snapshot_builder=builder)
    assert result['scan_complete'] and result['counts'] == {'issue': 1, 'pull': 1, 'comments': 1, 'review_comments': 1, 'files': 1, 'commits': 1}
    record = index.item('pull:2')
    assert record['record']['base_sha'] == SHA and record['record']['merge_commit_sha'] == SHA
    assert record['mentions'] == [{'repository': REPO, 'number': 1, 'relationship': 'text_mention_unverified'}]
    assert 'patch' not in index.item('file:2:parser.py')['record']
    with index.connect() as db:
        assert 'SECRET GOLD-LIKE' not in '\n'.join(row[0] for row in db.execute('SELECT value FROM versions'))
    assert index.item('commit:2:'+SHA)['record']['number'] == 2
    hits = index.search('Unicode parser')['results']
    assert {'issue:1', 'comments:7'} <= {r['key'] for r in hits}
    assert all(r['historical_verified'] is False for r in hits)
    assert all(r['score_is_probability'] is False for r in hits)
    assert index.search('nonexistent')['results'] == []


def test_refresh_preserves_old_versions_and_replaces_search(connected):
    index, builder = connected
    index.sync(reader=Reader({'/issues?': ([issue(1, body='old sentinel')], None, digest('old'))}), snapshot_builder=builder)
    old_run = index.status()['run']['id']
    index.sync(reader=Reader({'/issues?': ([issue(1, body='new marker')], None, digest('new'))}), snapshot_builder=builder, refresh=True)
    assert index.status()['run']['id'] != old_run
    assert index.search('sentinel')['results'] == []
    assert len(index.search('marker')['results']) == 1
    with index.connect() as db:
        assert db.execute('SELECT count(*) FROM versions WHERE key=?', ('issue:1',)).fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM documents').fetchone()[0] == 1


@pytest.mark.parametrize('bad', [
    'https://evil.test/steal', BASE.replace(REPO, 'owner/other')+'/issues?page=2',
    BASE+'/pulls?per_page=100&page=2&state=all&sort=created&direction=asc',
    BASE+'/issues?per_page=100&page=1&state=all&sort=created&direction=asc',
    BASE+'/issues?per_page=100&page=2&state=open&sort=created&direction=asc',
])
def test_cursor_injection_and_cycles_leave_page_uncommitted(connected, bad):
    index, builder = connected
    with pytest.raises(ValueError):
        index.sync(reader=Reader({'/issues?': ([issue(1)], bad, digest('bad'))}), snapshot_builder=builder)
    assert index.status()['counts'] == {}
    assert index.status()['run']['pages'] == 0


def test_bad_record_rolls_back_whole_page(connected):
    index, builder = connected
    corrupt = issue(2)
    corrupt['html_url'] = 'https://github.com/someone/else/issues/2'
    with pytest.raises(ValueError, match='identity'):
        index.sync(reader=Reader({'/issues?': ([issue(1), corrupt], None, digest('bad'))}), snapshot_builder=builder)
    assert index.status()['counts'] == {}


@pytest.mark.parametrize('metadata', [
    {'id': 42, 'full_name': REPO, 'private': True},
    {'id': 42, 'full_name': 'owner/renamed', 'private': False},
    {'id': 43, 'full_name': REPO, 'private': False},
])
def test_private_renamed_or_recreated_repository_refused_before_pages(connected, metadata):
    index, builder = connected
    index.sync(reader=Reader(), snapshot_builder=builder, max_requests=1)
    class BadReader(Reader):
        def get(self, url):
            assert url == BASE
            return metadata, None, digest(metadata)
    with pytest.raises(ValueError, match='repository|repositories'):
        index.sync(reader=BadReader(), snapshot_builder=lambda _: pytest.fail('must not build'))
    assert index.status()['counts'] == {}


def test_snapshot_retrieval_is_offline_revision_bound_and_tamper_checked(connected):
    index, builder = connected
    index.sync(reader=Reader(), snapshot_builder=builder)
    result = RepositoryIndex(index.root, REPO).search_code('parse_unicode')
    assert result['results'] and result['historical_verified'] is False
    assert all(r['revision'] == result['commit_sha'] for r in result['results'])
    graph = index.root/index.status()['run']['snapshot']['graph_path']
    graph.write_text('{}')
    with pytest.raises(ValueError, match='integrity'):
        index.search_code('parse_unicode')


@pytest.mark.parametrize('target', ['document', 'receipt', 'search'])
def test_tampered_sources_cannot_appear_as_verified_search_results(connected, target):
    index, builder = connected
    index.sync(reader=Reader({'/issues?': ([issue(1)], None, digest('page'))}), snapshot_builder=builder)
    with index.connect() as db:
        if target == 'document':
            db.execute("UPDATE documents SET value='{}'")
        elif target == 'receipt':
            db.execute("UPDATE receipts SET value='{}'")
        else:
            db.execute("UPDATE search SET text='forged parser text'")
    with pytest.raises(ValueError, match='integrity|differs'):
        index.search('parser')


def test_refresh_cannot_discard_pending_scan_and_writer_lock_prevents_overlap(connected):
    index, builder = connected
    index.sync(reader=Reader(), snapshot_builder=builder, max_requests=1)
    with pytest.raises(ValueError, match='pending'):
        index.sync(refresh=True, reader=Reader())
    with pytest.raises(ValueError, match='original revision'):
        index.sync(revision='main', reader=Reader())
    with _writer_lock(index.directory/'writer.lock'):
        with pytest.raises(ValueError, match='active'):
            index.sync(reader=Reader())


class Response(io.BytesIO):
    def __init__(self, value, url=BASE, *, link='', status=200):
        super().__init__(json.dumps(value).encode())
        self.url, self.headers, self.status = url, {'Link': link}, status

    def geturl(self):
        return self.url


def test_transport_get_version_bounds_redirects_and_credential_isolation():
    calls = []
    def opener(request, **kwargs):
        calls.append(request)
        return Response({'id': 42})
    reader = GitHubReader(REPO, token='synthetic-secret', opener=opener)
    value, cursor, response_sha = reader.get(BASE)
    assert value == {'id': 42} and cursor is None and len(response_sha) == 64
    assert calls[0].get_method() == 'GET'
    assert calls[0].get_header('X-github-api-version') == '2026-03-10'
    assert calls[0].get_header('Authorization') == 'Bearer synthetic-secret'
    for url in ('http://api.github.com/repos/'+REPO, BASE+'/../other',
                'https://api.github.com@evil.test/repos/'+REPO, BASE+'/%2e%2e/steal'):
        with pytest.raises(ValueError):
            # Dot segments must be rejected before transport, including unencoded ones.
            reader.get(url)
    assert len(calls) == 1
    with pytest.raises(ValueError, match='redirect'):
        GitHubReader(REPO, opener=lambda *a, **k: Response({}, 'https://evil.test')).get(BASE)
    with pytest.raises(ValueError, match='header'):
        GitHubReader(REPO, token='secret\nheader')


def test_transport_rate_limit_is_safe_and_no_retry():
    from urllib.error import HTTPError
    calls = []
    def opener(request, **kwargs):
        calls.append(request)
        raise HTTPError('https://secret:token@evil.test', 429, 'SENSITIVE BODY', {}, io.BytesIO(b'private-secret'))
    with pytest.raises(ValueError) as error:
        GitHubReader(REPO, opener=opener).get(BASE)
    assert len(calls) == 1 and '429' in str(error.value)
    assert 'secret' not in str(error.value) and 'SENSITIVE' not in str(error.value)


def test_cli_status_search_and_item_require_no_runtime_model_or_benchmark(connected, capsys):
    from issueintel.interfaces.cli import main
    index, builder = connected
    index.sync(reader=Reader({'/issues?': ([issue(1)], None, digest('page'))}), snapshot_builder=builder)
    for command in (
        ['repository-status', '--repo', REPO],
        ['search-repository', '--repo', REPO, '--query', 'parser'],
        ['search-repository', '--repo', REPO, '--query', 'unicode', '--code'],
        ['repository-item', '--repo', REPO, '--key', 'issue:1'],
    ):
        assert main(['--root', str(index.root), *command]) == 0
        assert json.loads(capsys.readouterr().out)
    assert not (index.root/'data/benchmark').exists()
    assert not (index.root/'data/runtime').exists()


def test_invalid_bounds_fail_before_network(connected):
    index, _ = connected
    reader = Reader()
    for limit in (0, 501, True):
        with pytest.raises(ValueError):
            index.sync(max_requests=limit, reader=reader)
    assert reader.calls == []
    with pytest.raises(ValueError):
        RepositoryIndex(index.root, 'https://github.com/owner/project/issues/1')


def test_numeric_repository_pagination_is_bound_to_verified_global_id(connected):
    index, builder = connected
    next_url = 'https://api.github.com/repositories/42/issues?per_page=100&page=2&state=all&sort=created&direction=asc'
    index.sync(reader=Reader({'/issues?': ([issue(1)], next_url, digest('page'))}), snapshot_builder=builder, max_requests=2)
    reader = Reader({'/issues?': ([issue(2)], None, digest('second'))})
    assert index.sync(reader=reader)['scan_complete']
    assert next_url in reader.calls
    transport = GitHubReader(REPO, token='')
    transport.repository_id = 42
    assert transport.validate_url(next_url) == next_url
    with pytest.raises(ValueError):
        transport.validate_url(next_url.replace('/42/', '/43/'))


def test_issue_after_cursor_resume_keeps_exact_links_and_page_receipts(connected):
    index, builder = connected
    first = index._url('/issues', per_page=100, page=1, state='all', sort='created', direction='asc')
    numeric = 'https://api.github.com/repositories/42/issues'
    filters = 'per_page=100&state=all&sort=created&direction=asc'
    second = numeric + '?' + filters + '&page=2&after=opaque%2Bcursor%2Fone%3D'
    third = numeric + '?' + filters + '&page=3&after=opaque%2Bcursor%2Ftwo%3D'
    first_reader = Reader({first: ([issue(1)], second, digest('first cursor page'))})
    first_report = index.sync(reader=first_reader, snapshot_builder=builder, max_requests=2)
    original_snapshot = first_report['current_snapshot']

    def page_state(expected_url, expected_pages):
        with index.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE kind='issues'").fetchone()
            assert task['url'] == expected_url and task['pages'] == expected_pages
            receipts = [json.loads(row[0]) for row in db.execute('SELECT value FROM receipts ORDER BY rowid')]
            assert [(row['source_url'], row['next_url']) for row in receipts] == [
                (first, second), (second, third), (third, None)
            ][:expected_pages]
            assert db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == expected_pages

    page_state(second, 1)
    assert not first_report['scan_complete'] and first_report['counts'] == {'issue': 1}
    index = RepositoryIndex(index.root, REPO)
    second_reader = Reader({second: ([issue(2)], third, digest('second cursor page'))})
    second_report = index.sync(reader=second_reader, max_requests=2,
                               snapshot_builder=lambda _: pytest.fail('snapshot rebuilt on cursor resume'))
    page_state(third, 2)
    assert first not in second_reader.calls and second in second_reader.calls
    assert second_report['current_snapshot'] == original_snapshot
    index = RepositoryIndex(index.root, REPO)
    third_reader = Reader({third: ([issue(3)], None, digest('terminal cursor page'))})
    final_report = index.sync(reader=third_reader,
                              snapshot_builder=lambda _: pytest.fail('snapshot rebuilt on terminal page'))
    # Other empty endpoints also get receipts after the terminal issue page.
    with index.connect() as db:
        issue_receipts = [json.loads(row[0]) for row in db.execute('SELECT value FROM receipts ORDER BY rowid')
                          if '/issues?' in json.loads(row[0])['source_url']]
        assert [(row['source_url'], row['next_url']) for row in issue_receipts] == [
            (first, second), (second, third), (third, None)
        ]
        task = db.execute("SELECT * FROM tasks WHERE kind='issues'").fetchone()
        assert task['url'] == third and task['pages'] == 3 and task['done'] == 1
    assert first not in third_reader.calls and second not in third_reader.calls and third in third_reader.calls
    assert final_report['scan_complete'] and final_report['counts'] == {'issue': 3}
    assert final_report['current_snapshot'] == original_snapshot


@pytest.mark.parametrize('suffix', [
    'after=', 'after=one&after=two', 'after=one&after=one', 'after=bad%00cursor',
    'after=bad%0Acursor', 'after=bad%7Fcursor', 'after=white%20space', 'after=%C3%A9',
    'after=' + 'a' * 1025, 'before=one', 'cursor=one', 'after=one&before=two',
    'after=one&page=2', 'after=one&page=', 'after=one&state=', 'after=one&sort=created',
    'after=one&unknown=', 'after=one&unknown=value',
])
def test_invalid_issue_cursor_leaves_page_receipt_and_documents_uncommitted(connected, suffix):
    index, builder = connected
    first = index._url('/issues', per_page=100, page=1, state='all', sort='created', direction='asc')
    bad = first.replace('page=1&', 'page=2&') + '&' + suffix
    with pytest.raises(ValueError, match='GitHub'):
        index.sync(reader=Reader({first: ([issue(1)], bad, digest('invalid cursor page'))}),
                   snapshot_builder=builder)
    assert index.status()['counts'] == {} and index.status()['run']['pages'] == 0
    with index.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 0
        task = db.execute("SELECT * FROM tasks WHERE kind='issues'").fetchone()
        assert task['url'] == first and task['pages'] == 0


@pytest.mark.parametrize('next_changes', [
    {'after': None}, {'after': 'first'}, {'page': '2', 'after': 'second'},
    {'page': '4', 'after': 'second'}, {'page': '1', 'after': 'second'},
    {'page': '03', 'after': 'second'}, {'page': '+3', 'after': 'second'},
    {'page': '3', 'after': 'second', 'state': 'open'},
    {'page': '3', 'after': 'second', 'per_page': '50'},
    {'page': '3', 'after': 'second', 'sort': 'updated'},
    {'page': '3', 'after': 'second', 'direction': 'desc'},
])
def test_invalid_cursor_transition_preserves_previously_committed_page(connected, next_changes):
    from urllib.parse import urlencode
    index, builder = connected
    params = dict(per_page='100', page='2', state='all', sort='created', direction='asc', after='first')
    second = 'https://api.github.com/repositories/42/issues?' + urlencode(params)
    first = index._url('/issues', per_page=100, page=1, state='all', sort='created', direction='asc')
    index.sync(reader=Reader({first: ([issue(1)], second, digest('accepted cursor page'))}),
               snapshot_builder=builder, max_requests=2)
    params.update(page='3', after='second')
    params.update(next_changes)
    bad = 'https://api.github.com/repositories/42/issues?' + urlencode({k: v for k, v in params.items() if v is not None})
    with pytest.raises(ValueError, match='GitHub'):
        index.sync(reader=Reader({second: ([issue(2)], bad, digest('invalid next cursor page'))}))
    assert index.status()['counts'] == {'issue': 1} and index.status()['run']['pages'] == 1
    with index.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 1
        task = db.execute("SELECT * FROM tasks WHERE kind='issues'").fetchone()
        assert task['url'] == second and task['pages'] == 1


@pytest.mark.parametrize('suffix,parameters', [
    ('/issues/comments', dict(sort='created', direction='asc')),
    ('/pulls/comments', dict(sort='created', direction='asc')),
    ('/pulls', dict(state='all', sort='created', direction='asc')),
    ('/pulls/1/files', {}), ('/pulls/1/commits', {}),
])
def test_after_cursor_is_not_enabled_on_other_index_endpoints(connected, suffix, parameters):
    index, _ = connected
    current = index._url(suffix, per_page=100, page=1, **parameters)
    following = index._url(suffix, per_page=100, page=2, **parameters)
    assert index._next_page(current, following, 42) is None
    with pytest.raises(ValueError, match='GitHub'):
        index._next_page(current, following + '&after=cursor', 42)


@pytest.mark.parametrize('bad', [
    'https://evil.test/repositories/42/issues',
    'https://api.github.com/repositories/43/issues',
    BASE.replace(REPO, 'owner/other') + '/issues',
    'https://api.github.com/repositories/42/pulls',
    'https://api.github.com/repositories/42/issues/comments',
])
def test_cursor_does_not_authorize_different_repository_or_endpoint(connected, bad):
    index, builder = connected
    first = index._url('/issues', per_page=100, page=1, state='all', sort='created', direction='asc')
    next_url = bad + '?per_page=100&page=2&state=all&sort=created&direction=asc&after=cursor'
    with pytest.raises(ValueError):
        index.sync(reader=Reader({first: ([issue(1)], next_url, digest('wrong cursor target'))}),
                   snapshot_builder=builder)
    assert index.status()['counts'] == {} and index.status()['run']['pages'] == 0


def test_commit_api_cap_is_reported_without_claiming_full_git_history(connected):
    index, builder = connected
    pull = issue(2, pull=True)
    pull.update(base={'sha': SHA, 'repo': {'full_name': REPO}}, head={'sha': SHA})
    first = index._url('/pulls/2/commits', per_page=100, page=1)
    def commits(start, end):
        return [{'sha': f'{n:040x}', 'html_url': f'https://github.com/{REPO}/commit/{n:040x}',
                 'commit': {'message': 'synthetic commit'}} for n in range(start, end)]
    class CapReader(Reader):
        def get(self, url):
            if '/pulls/2/commits?' in url:
                page = int(url.rsplit('=', 1)[1])
                return commits((page-1)*100+1, min(page*100+1, 251)), (first[:-1]+str(page+1)) if page<3 else None, digest(url)
            return super().get(url)
    result = index.sync(reader=CapReader({'/pulls?': ([pull], None, digest('pull'))}), snapshot_builder=builder)
    assert result['scan_complete']
    assert result['run']['api_caps'] == [{'kind': 'commits', 'pull': 2, 'limit': 250}]
    assert result['counts']['commits'] == 250


def test_document_response_limit_fails_before_json_decode():
    response = Response({})
    response.seek(0)
    response.write(b'x'*10_000_001)
    response.seek(0)
    with pytest.raises(ValueError, match='10 MB'):
        GitHubReader(REPO, opener=lambda *a, **k: response).get(BASE)


def test_cancel_callback_preserves_committed_page_before_next_get(connected):
    index, builder = connected
    saved = []
    reader = Reader({'/issues?': ([issue(1)], None, digest('page'))})
    def progress(report):
        saved.append(report)
    def cancel():
        if saved and saved[-1]['counts'].get('issue') == 1:
            raise InterruptedError('synthetic cancellation')
    with pytest.raises(InterruptedError):
        index.sync(reader=reader, snapshot_builder=builder, on_progress=progress, check_cancelled=cancel)
    assert index.item('issue:1')['record']['number'] == 1
    assert not any('/issues/comments?' in url for url in reader.calls)
    assert index.status()['run']['pages'] == 1
    assert index.sync(reader=Reader())['scan_complete']


def test_workbench_repository_jobs_are_separate_and_never_construct_a_provider(connected, monkeypatch):
    import time
    from issueintel.investigation.workbench import Workbench
    index, builder = connected
    original = RepositoryIndex.sync
    monkeypatch.setattr(RepositoryIndex, 'sync', lambda self, **kwargs: original(
        self, **kwargs, reader=Reader({'/issues?': ([issue(1)], None, digest('page'))}), snapshot_builder=builder))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *_: pytest.fail('Model config requested'))
    wb = Workbench(index.root, provider_factory=lambda *_a, **_k: pytest.fail('Provider constructed'))
    try:
        job = wb.submit_repository({'repository': REPO, 'max_requests': 2})
        for _ in range(300):
            job = wb.job(job['id'])
            if job['status'] not in {'queued', 'running'}:
                break
            time.sleep(.01)
        assert job['status'] == 'completed' and job['sync_report']['scan_complete'] is False
        assert job['sync_report']['counts'] == {'issue': 1}
        assert job['provider'] == 'none' and 'result' not in job
        assert wb.history() == [] and len(wb.repository_history()) == 1
        assert 'request' not in wb.repository_history()[0]
        assert wb.repository_read('search', {'repository': REPO, 'query': 'unicode'})['results']
        with pytest.raises(ValueError):
            wb.preview_comment(job['id'])
    finally:
        wb.close()
    wb = Workbench(index.root)
    try:
        assert wb.repository_history()[0]['status'] == 'completed'
    finally:
        wb.close()


@pytest.mark.parametrize('payload', [
    {'repository': REPO, 'provider': 'groq'}, {'repository': REPO, 'max_requests': True},
    {'repository': REPO, 'refresh': 'true'}, {'repository': REPO, 'revision': '--bad'},
    {'repository': 'https://github.com/owner/project/issues/1'}, {'repository': REPO, 'split': 'main_test'},
])
def test_workbench_sync_input_rejects_inference_and_benchmark_overrides(tmp_path, payload):
    from issueintel.investigation.workbench import Workbench
    wb = Workbench(tmp_path)
    try:
        with pytest.raises(ValueError):
            wb.submit_repository(payload)
        assert wb.repository_history() == []
    finally:
        wb.close()


def test_repository_http_guards_read_routes_and_async_sync(connected, monkeypatch):
    import threading
    import time
    from http.client import HTTPConnection
    from issueintel.interfaces.web import make_server
    index, builder = connected
    original = RepositoryIndex.sync
    monkeypatch.setattr(RepositoryIndex, 'sync', lambda self, **kwargs: original(
        self, **kwargs, reader=Reader({'/issues?': ([issue(1)], None, digest('page'))}), snapshot_builder=builder))
    server = make_server(index.root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, body=None, *, auth=True, origin=None):
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['X-IssueIntel-Token'] = server.session_token
        if origin:
            headers['Origin'] = origin
        payload = json.dumps(body) if body is not None else None
        if origin and payload is not None:
            # The Origin guard rejects before reading a body. Advertise its
            # length but send headers only, avoiding Windows RST on unread bytes.
            headers['Content-Length'] = str(len(payload.encode()))
            payload = None
        connection.request('GET' if body is None else 'POST', path, payload, headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw
    try:
        assert request('/api/repositories/jobs', auth=False)[0] == 403
        assert request('/api/repositories/sync', {'repository': REPO}, origin='https://evil.test')[0] == 403
        assert request('/api/repositories/sync', {'repository': REPO, 'provider': 'groq'})[0] == 400
        status, raw = request('/api/repositories/sync', {'repository': REPO})
        assert status == 202
        identifier = json.loads(raw)['id']
        for _ in range(300):
            row = server.workbench.job(identifier)
            if row['status'] not in {'running', 'queued'}:
                break
            time.sleep(.01)
        assert row['status'] == 'completed' and row['sync_report']['scan_complete']
        assert request('/api/repositories/status', {'repository': REPO})[0] == 200
        status, raw = request('/api/repositories/search', {'repository': REPO, 'query': 'Unicode'})
        assert status == 200 and json.loads(raw)['results']
        status, raw = request('/api/repositories/item', {'repository': REPO, 'key': 'issue:1'})
        assert status == 200 and json.loads(raw)['record']['number'] == 1
        assert request('/api/repositories/search', {'repository': REPO, 'query': 'parser', 'code': 'yes'})[0] == 400
        assert request('/repository.js')[0] == 200
        assert json.loads(request('/api/jobs')[1]) == []
        assert len(json.loads(request('/api/repositories/jobs')[1])) == 1
    finally:
        server.shutdown(); server.server_close(); server.workbench.close(); thread.join(5)
