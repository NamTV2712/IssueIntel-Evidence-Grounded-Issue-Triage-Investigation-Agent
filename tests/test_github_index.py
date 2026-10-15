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
