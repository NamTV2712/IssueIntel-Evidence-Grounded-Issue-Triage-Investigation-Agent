"""Private, resumable current-context GitHub indexing, never benchmark evidence.

REST pages and their cursors commit together. Re-running resumes the unfinished
scan; an explicit refresh starts a reconciliation scan. No provider is called,
no diff/patch or target code is executed, and no writes are sent to GitHub.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from issueintel.repository.acquire import acquire_repository, parse_github_reference
from issueintel.repository.cache import load_snapshot, save_snapshot
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot, validate_repo_path
from issueintel.runtime.io import canonical_json, digest, write_json


API_VERSION = '2026-03-10'
LIMITATIONS = [
    'Current REST text is not an opening-version or historical availability proof.',
    'Paginated live API results are not an atomic point-in-time export; refresh reconciles edits.',
    'Deleted/inaccessible records may remain in the local history; absence is not deletion proof.',
    'Body references are unverified mentions, not confirmed duplicate or closing relationships.',
    'PR commits/files reflect the current PR; API caps and force pushes can limit coverage.',
    'Review threads, reactions, timeline events and full Git history are not imported.',
    'This index is excluded from strict benchmark inference and evaluation.',
]


def now():
    return datetime.now(timezone.utc).isoformat()


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Expected a positive GitHub record identity')
    return value


def _timestamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('GitHub record timestamp must include timezone')
    return value


def _text(value, maximum=120000):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError('GitHub text exceeds index bounds or is invalid')
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise ValueError('GitHub commit/blob requires a full object ID')
    return value


def record_links(record):
    """Typed source relationships; a text mention never certifies a fix/duplicate."""
    key, number, kind = record['key'], record['number'], record['kind']
    edges = []
    if kind in {'issue','pull','comments','review_comments','commits'}:
        text = record['title']+'\n\n'+record['body']
        numbers = {int(n) for n in re.findall(r'(?<![\w/])#([1-9][0-9]{0,9})\b', text)}
        numbers.update(int(n) for n in re.findall(re.escape('https://github.com/'+record['repository'])+r'/(?:issues|pull)/([1-9][0-9]{0,9})\b',text))
        edges += [(key, f'thread:{n}', 'text_mention_unverified') for n in sorted(numbers)[:100]]
    if kind in {'comments','review_comments'}:
        edges.append((key, f'thread:{number}', 'comment_on_thread'))
    if kind in {'files','commits'}:
        edges.append((f'pull:{number}',key,'api_pr_file' if kind=='files' else 'api_pr_commit'))
    return [{'source':a,'target':b,'relation':c,'owner':key,'record_sha256':digest(record)} for a,b,c in edges]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Repository redirect/transfer requires explicit reconnection')


class GitHubReadError(ValueError):
    def __init__(self, status, retry_after=60, retryable=None):
        self.status = status
        self.retry_after = retry_after
        self.retryable = status in {0,429,500,502,503,504} if retryable is None else retryable
        super().__init__(f'GitHub read failed (HTTP {status}); checkpoint retained')


class GitHubReader:
    """GET-only transport with bounded bytes and credential-safe diagnostics."""
    def __init__(self, repository, token=None, *, opener=None):
        self.repository = parse_github_reference(repository).repository
        self.base = 'https://api.github.com/repos/' + self.repository
        self.repository_id = None
        self.token = os.environ.get('GITHUB_TOKEN', '') if token is None else token
        if not isinstance(self.token, str) or any(ord(c) < 32 or ord(c) > 126 for c in self.token):
            raise ValueError('Invalid GitHub credential header')
        self.opener = opener or build_opener(_NoRedirect()).open

    def validate_url(self, url):
        if not isinstance(url, str) or len(url) > 2000 or any(ord(c) < 32 for c in url):
            raise ValueError('Invalid GitHub pagination URL')
        parsed = urlsplit(url)
        prefixes = [urlsplit(self.base).path]
        if self.repository_id is not None:
            prefixes.append('/repositories/' + str(self.repository_id))
        if (parsed.scheme != 'https' or parsed.netloc != 'api.github.com'
                or parsed.fragment or '%' in parsed.path or '\\' in parsed.path
                or any(part in {'.', '..'} for part in parsed.path.split('/'))
                or not any(parsed.path == prefix or parsed.path.startswith(prefix + '/') for prefix in prefixes)):
            raise ValueError('GitHub URL escaped the selected repository')
        return url

    def get(self, url):
        self.validate_url(url)
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'IssueIntel-index',
                   'X-GitHub-Api-Version': API_VERSION}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        try:
            with self.opener(Request(url, headers=headers, method='GET'), timeout=30) as response:
                if response.geturl() != url:
                    raise ValueError('GitHub redirect requires explicit reconnection')
                if response.status != 200:
                    raise ValueError('Unexpected GitHub response status')
                raw = response.read(10_000_001)
                link = response.headers.get('Link', '')
        except HTTPError as error:
            # Do not expose provider response bodies, credentials or arbitrary URLs.
            try:
                import time
                delay = max(int(error.headers.get('Retry-After', '60')),
                            int(error.headers.get('X-RateLimit-Reset', '0')) - int(time.time()))
            except (TypeError, ValueError):
                delay = 60
            retryable = (error.code in {429,500,502,503,504} or error.code==403
                         and (error.headers.get('X-RateLimit-Remaining')=='0' or error.headers.get('Retry-After') is not None))
            raise GitHubReadError(error.code, max(60, min(delay, 21600)), retryable) from None
        except (URLError, TimeoutError):
            raise GitHubReadError(0, 60) from None
        if len(raw) > 10_000_000:
            raise ValueError('GitHub page exceeds 10 MB; checkpoint retained')
        value = json.loads(raw)
        next_url = None
        for part in link.split(',') if link else []:
            match = re.fullmatch(r'\s*<([^>]+)>;\s*rel="([a-z]+)"\s*', part)
            if not match:
                raise ValueError('Invalid GitHub pagination header')
            if match[2] == 'next':
                if next_url is not None:
                    raise ValueError('Repeated next-page link')
                next_url = self.validate_url(match[1])
        return value, next_url, sha256(raw).hexdigest()


@contextmanager
def _writer_lock(path):
    """OS releases the lock on process death; no stale lease or unsafe takeover."""
    if path.resolve().parent != path.parent.resolve() or path.is_symlink():
        raise ValueError('Unsafe repository writer lock path')
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Another repository sync is active') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RepositoryIndex:
    def __init__(self, root, repository):
        self.root = Path(root).resolve()
        reference = parse_github_reference(repository)
        if reference.issue_number is not None:
            raise ValueError('Connect a repository, not an individual issue')
        self.repository = reference.repository.lower()
        self.directory = (self.root/'data/repository_index'/digest(self.repository)[:24]).resolve()
        if not self.directory.is_relative_to(self.root/'data/repository_index'):
            raise ValueError('Repository index escaped private data directory')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory/'index.sqlite'
        if self.path.resolve().parent != self.directory or self.path.is_symlink():
            raise ValueError('Unsafe repository index database path')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                    run TEXT, kind TEXT, subject INTEGER, url TEXT, pages INTEGER DEFAULT 0,
                    count INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
                    PRIMARY KEY(run,kind,subject));
                CREATE TABLE IF NOT EXISTS receipts(
                    sha TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(
                    key TEXT, sha TEXT, value TEXT NOT NULL, PRIMARY KEY(key,sha));
                CREATE TABLE IF NOT EXISTS documents(
                    key TEXT PRIMARY KEY, sha TEXT NOT NULL, receipt TEXT NOT NULL,
                    run TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(key UNINDEXED, text);
                CREATE TABLE IF NOT EXISTS unavailable(key TEXT PRIMARY KEY, reason TEXT, receipt TEXT);
                CREATE TABLE IF NOT EXISTS updates(id TEXT PRIMARY KEY, sha TEXT, value TEXT, done INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS update_seen(delivery TEXT, kind TEXT, subject INTEGER, key TEXT,
                    PRIMARY KEY(delivery,kind,subject,key));
                CREATE TABLE IF NOT EXISTS links(owner TEXT, source TEXT, target TEXT, relation TEXT, sha TEXT,
                    PRIMARY KEY(owner,source,target,relation));
                CREATE INDEX IF NOT EXISTS links_target ON links(target);
                CREATE INDEX IF NOT EXISTS links_source ON links(source);
            ''')
            identity = self._meta(db, 'repository')
            if identity and identity != self.repository:
                raise ValueError('Repository index identity mismatch')
            self._set(db, 'repository', self.repository)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _meta(db, key):
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _set(db, key, value):
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, canonical_json(value)))

    def _url(self, suffix, **parameters):
        return 'https://api.github.com/repos/' + self.repository + suffix + ('?' + urlencode(parameters) if parameters else '')

    def _record(self, kind, item, subject=0):
        if not isinstance(item, dict):
            raise ValueError('Invalid GitHub record')
        base = {'repository': self.repository, 'kind': kind, 'historical_verified': False}
        if kind in {'issues', 'pulls'}:
            number = _positive(item.get('number'))
            pull = kind == 'pulls' or 'pull_request' in item
            record_kind = 'pull' if pull else 'issue'
            url = f'https://github.com/{self.repository}/{"pull" if pull else "issues"}/{number}'
            if item.get('html_url', '').lower() != url:
                raise ValueError('GitHub issue/PR identity mismatch')
            base.update(key=f'{record_kind}:{number}', kind=record_kind, number=number, url=url,
                        title=_text(item.get('title'), 2000), body=_text(item.get('body')),
                        created_at=_timestamp(item.get('created_at')), updated_at=_timestamp(item.get('updated_at')),
                        state=item.get('state'))
            if base['state'] not in {'open', 'closed'}:
                raise ValueError('Invalid issue/PR state')
            if kind == 'pulls':
                if item.get('base', {}).get('repo', {}).get('full_name', '').lower() != self.repository:
                    raise ValueError('Pull request base repository mismatch')
                base.update(base_sha=_sha(item['base'].get('sha')), head_sha=_sha(item.get('head', {}).get('sha')),
                            merged_at=item.get('merged_at'),
                            merge_commit_sha=_sha(item['merge_commit_sha']) if item.get('merge_commit_sha') else None)
        elif kind in {'comments', 'review_comments'}:
            identity = _positive(item.get('id'))
            prefix = self._url('/issues/' if kind == 'comments' else '/pulls/')
            target = item.get('issue_url' if kind == 'comments' else 'pull_request_url', '')
            if not target.lower().startswith(prefix) or not re.fullmatch('[1-9][0-9]*', target[len(prefix):]):
                raise ValueError('Comment belongs to a different repository or target')
            number = int(target[len(prefix):])
            anchor = f'#issuecomment-{identity}' if kind == 'comments' else f'#discussion_r{identity}'
            url = item.get('html_url', '')
            if not re.fullmatch(re.escape(f'https://github.com/{self.repository}/') + r'(?:issues|pull)/' + str(number) + re.escape(anchor), url.lower()):
                raise ValueError('Comment URL identity mismatch')
            base.update(key=f'{kind}:{identity}', number=number, url=url,
                        title='', body=_text(item.get('body')),
                        created_at=_timestamp(item.get('created_at')), updated_at=_timestamp(item.get('updated_at')))
            if kind == 'review_comments':
                base['path'] = validate_repo_path(item.get('path', ''))
                base['commit_sha'] = _sha(item.get('commit_id'))
        elif kind == 'files':
            path = validate_repo_path(item.get('filename', ''))
            base.update(key=f'file:{subject}:{path}', number=subject, path=path,
                        blob_sha=_sha(item.get('sha')), status=_text(item.get('status'), 30),
                        url=f'https://github.com/{self.repository}/pull/{subject}/files', title=path, body='')
            if item.get('previous_filename'):
                base['previous_path'] = validate_repo_path(item['previous_filename'])
        elif kind == 'commits':
            commit = _sha(item.get('sha'))
            if item.get('html_url', '').lower() != f'https://github.com/{self.repository}/commit/{commit}':
                raise ValueError('Commit URL identity mismatch')
            base.update(key=f'commit:{subject}:{commit}', number=subject, commit_sha=commit,
                        url=item['html_url'], title='', body=_text(item.get('commit', {}).get('message')))
        else:
            raise ValueError('Unknown repository index task')
        base['text_sha256'] = sha256((base['title'] + '\n\n' + base['body']).encode()).hexdigest()
        return base

    def _save_record(self, db, record, receipt, run):
        value, sha = canonical_json(record), digest(record)
        key = record['key']
        db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,?)', (key, sha, value))
        db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)',
                   (key, sha, receipt, run, record['kind'], value))
        db.execute('DELETE FROM search WHERE key=?', (key,))
        db.execute('INSERT INTO search VALUES (?,?)', (key, record['title'] + '\n\n' + record['body']))
        db.execute('DELETE FROM unavailable WHERE key=?', (key,))
        db.execute('DELETE FROM links WHERE owner=?', (key,))
        for link in record_links(record):
            db.execute('INSERT INTO links VALUES (?,?,?,?,?)',
                       (key,link['source'],link['target'],link['relation'],sha))

    def _next_page(self, current, next_url, repository_id):
        if next_url is None:
            return
        validator = GitHubReader(self.repository, token='')
        validator.repository_id = repository_id
        validator.validate_url(next_url)
        before, after = urlsplit(current), urlsplit(next_url)
        a, b = parse_qs(before.query), parse_qs(after.query)
        try:
            page = int(a.pop('page', ['1'])[0])
            next_page = int(b.pop('page')[0])
        except (ValueError, KeyError):
            raise ValueError('Invalid GitHub next-page cursor') from None
        canonical_path = lambda path: path.replace('/repositories/' + str(repository_id), '/repos/' + self.repository, 1)
        if canonical_path(before.path) != canonical_path(after.path) or a != b or next_page != page + 1:
            raise ValueError('GitHub pagination changed endpoint, filters or page sequence')

    def sync(self, *, revision='HEAD', max_requests=50, refresh=False, reader=None, snapshot_builder=None,
             on_progress=None, check_cancelled=None, expected_repository_id=None):
        if type(max_requests) is not int or not 1 <= max_requests <= 500:
            raise ValueError('Choose 1–500 GitHub requests per sync invocation')
        if not isinstance(revision, str) or not revision.strip() or len(revision) > 200:
            raise ValueError('Choose an explicit Git revision')
        if any(callback is not None and not callable(callback) for callback in (on_progress, check_cancelled)):
            raise ValueError('Invalid repository sync callbacks')
        check_cancelled = check_cancelled or (lambda: None)
        reader = reader or GitHubReader(self.repository)
        with _writer_lock(self.directory/'writer.lock'), self.connect() as db:
            run_id = self._meta(db, 'active_run')
            run = json.loads(db.execute('SELECT value FROM runs WHERE id=?', (run_id,)).fetchone()[0]) if run_id else None
            if refresh and run and run['status'] != 'complete':
                raise ValueError('Finish the pending scan before starting refresh')
            if run and run['requested_revision'] != revision and not refresh:
                raise ValueError('Resume requires the original revision; use refresh after completion')
            if run and run['status'] == 'complete' and not refresh:
                if expected_repository_id is not None and self._meta(db, 'github_repository_id') != expected_repository_id:
                    raise ValueError('Connected GitHub repository ID mismatch')
                self._verify_snapshot(run['snapshot'])
                return self.status()
            if not run or refresh:
                run_id = uuid.uuid4().hex
                run = {'id': run_id, 'repository': self.repository, 'started_at': now(), 'status': 'pending',
                       'requested_revision': revision, 'snapshot': None, 'api_caps': [], 'pages': 0}
                db.execute('INSERT INTO runs VALUES (?,?)', (run_id, canonical_json(run)))
                self._set(db, 'active_run', run_id)
                db.commit()
            requests = 0
            check_cancelled()
            # Revalidate public repository/global identity on every resumed invocation.
            metadata, next_url, response_sha = reader.get(self._url(''))
            requests += 1
            if (not isinstance(metadata, dict) or metadata.get('private') is not False
                    or metadata.get('full_name', '').lower() != self.repository or next_url):
                raise ValueError('Require the exact public repository; rename/transfer needs explicit reconnection')
            identity = _positive(metadata.get('id'))
            if expected_repository_id is not None and identity != expected_repository_id:
                raise ValueError('Connected GitHub repository ID mismatch')
            previous_id = self._meta(db, 'github_repository_id')
            if previous_id is not None and previous_id != identity:
                raise ValueError('GitHub global repository ID changed; refusing mixed histories')
            if isinstance(reader, GitHubReader):
                reader.repository_id = identity
            self._set(db, 'github_repository_id', identity)
            if isinstance(metadata.get('default_branch'), str):
                self._set(db, 'default_branch', metadata['default_branch'])
            self._set(db, 'metadata_receipt', {'source_url': self._url(''), 'response_sha256': response_sha,
                                             'fetched_at': now(), 'repository_id': identity})
            db.commit()
            check_cancelled()
            if run['snapshot'] is None:
                run['snapshot'] = (snapshot_builder or self._build_snapshot)(revision)
                self._set(db, 'current_snapshot', run['snapshot'])
                db.execute('UPDATE runs SET value=? WHERE id=?', (canonical_json(run), run_id))
                db.commit()
            self._verify_snapshot(run['snapshot'])
            for kind, suffix, params in (
                ('issues', '/issues', {'state': 'all', 'sort': 'created', 'direction': 'asc'}),
                ('comments', '/issues/comments', {'sort': 'created', 'direction': 'asc'}),
                ('review_comments', '/pulls/comments', {'sort': 'created', 'direction': 'asc'}),
                ('pulls', '/pulls', {'state': 'all', 'sort': 'created', 'direction': 'asc'}),
            ):
                db.execute('INSERT OR IGNORE INTO tasks(run,kind,subject,url) VALUES (?,?,?,?)',
                           (run_id, kind, 0, self._url(suffix, per_page=100, page=1, **params)))
            db.commit()
            if on_progress:
                on_progress(self.status())
            while requests < max_requests:
                check_cancelled()
                task = db.execute('SELECT * FROM tasks WHERE run=? AND done=0 ORDER BY rowid LIMIT 1', (run_id,)).fetchone()
                if task is None:
                    break
                # Network/validation failure happens before any page mutation.
                items, next_url, response_sha = reader.get(task['url'])
                requests += 1
                if not isinstance(items, list) or len(items) > 100:
                    raise ValueError('Expected a bounded GitHub list page')
                self._next_page(task['url'], next_url, identity)
                records = [self._record(task['kind'], item, task['subject']) for item in items]
                if len({r['key'] for r in records}) != len(records):
                    raise ValueError('Duplicate identities within a GitHub page')
                receipt = {'schema': 'github_index_page_v1', 'run': run_id, 'source_url': task['url'],
                           'response_sha256': response_sha, 'fetched_at': now(), 'next_url': next_url,
                           'records': {r['key']: digest(r) for r in records}, 'repository_id': identity}
                receipt_sha = digest(receipt)
                with db:
                    db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?)', (receipt_sha, canonical_json(receipt)))
                    for record in records:
                        self._save_record(db, record, receipt_sha, run_id)
                        if task['kind'] == 'pulls':
                            for kind in ('files', 'commits'):
                                db.execute('INSERT OR IGNORE INTO tasks(run,kind,subject,url) VALUES (?,?,?,?)',
                                           (run_id, kind, record['number'], self._url(f'/pulls/{record["number"]}/{kind}', per_page=100, page=1)))
                    count = task['count'] + len(records)
                    cap = 3000 if task['kind'] == 'files' else 250 if task['kind'] == 'commits' else None
                    if cap and count >= cap:
                        run['api_caps'].append({'kind': task['kind'], 'pull': task['subject'], 'limit': cap})
                    db.execute('UPDATE tasks SET url=?, pages=pages+1, count=?, done=? WHERE run=? AND kind=? AND subject=?',
                               (next_url or task['url'], count, int(next_url is None), run_id, task['kind'], task['subject']))
                    run['pages'] += 1
                    run['updated_at'] = now()
                    db.execute('UPDATE runs SET value=? WHERE id=?', (canonical_json(run), run_id))
                if on_progress:
                    on_progress(self.status())
            if not db.execute('SELECT 1 FROM tasks WHERE run=? AND done=0', (run_id,)).fetchone():
                run.update(status='complete', completed_at=now())
                db.execute('UPDATE runs SET value=? WHERE id=?', (canonical_json(run), run_id))
                db.commit()
            report = self.status()
            report['requests_this_invocation'] = requests
            return report

    def _build_snapshot(self, revision):
        acquired = acquire_repository(self.repository, revision, self.root/'data/repos')
        snapshot = build_snapshot(acquired.path, acquired.commit_sha)
        cache = (self.root/'data/derived/repository_index').resolve()
        if not cache.is_relative_to(self.root/'data'):
            raise ValueError('Repository cache escaped private data directory')
        index = save_snapshot(snapshot, cache)
        graph = build_graph(snapshot).to_dict()
        graph_sha = digest(graph)
        graph_path = cache/'graphs'/(graph_sha + '.json')
        if graph_path.exists():
            if digest(json.loads(graph_path.read_bytes())) != graph_sha:
                raise ValueError('Repository graph cache integrity failed')
        else:
            write_json(graph_path, graph)
        return {'commit_sha': snapshot.commit_sha, 'snapshot_sha256': snapshot.snapshot_sha256,
                'snapshot_index': index.relative_to(self.root).as_posix(),
                'graph_path': graph_path.relative_to(self.root).as_posix(), 'graph_sha256': graph_sha,
                'file_count': len(snapshot.files), 'skipped_count': len(snapshot.skipped)}

    def _verify_snapshot(self, record):
        cache = (self.root/'data/derived/repository_index').resolve()
        index = (self.root/record['snapshot_index']).resolve()
        graph_path = (self.root/record['graph_path']).resolve()
        if not index.is_relative_to(cache/'snapshots') or not graph_path.is_relative_to(cache/'graphs'):
            raise ValueError('Repository snapshot/graph escaped cache')
        snapshot = load_snapshot(index, cache)
        graph = json.loads(graph_path.read_bytes())
        if (snapshot.snapshot_sha256 != record['snapshot_sha256'] or snapshot.commit_sha != record['commit_sha']
                or digest(graph) != record['graph_sha256'] or graph['snapshot_sha256'] != snapshot.snapshot_sha256
                or graph['commit_sha'] != snapshot.commit_sha or snapshot.protocol != 'benchmark_base'):
            raise ValueError('Repository snapshot/graph integrity failed')
        return snapshot

    def status(self):
        with self.connect() as db:
            run_id = self._meta(db, 'active_run')
            run = json.loads(db.execute('SELECT value FROM runs WHERE id=?', (run_id,)).fetchone()[0]) if run_id else None
            counts = {r['kind']: r['n'] for r in db.execute('SELECT kind, count(*) AS n FROM documents GROUP BY kind')}
            tasks = [dict(r) for r in db.execute('SELECT kind,subject,pages,count,done FROM tasks WHERE run=? ORDER BY rowid', (run_id,))]
            return {'repository': self.repository, 'github_repository_id': self._meta(db, 'github_repository_id'),
                    'current_snapshot': self._meta(db, 'current_snapshot') or (run or {}).get('snapshot'),
                    'default_branch': self._meta(db, 'default_branch'),
                    'pending_updates': db.execute('SELECT count(*) FROM updates WHERE done=0').fetchone()[0],
                    'unavailable_records': db.execute('SELECT count(*) FROM unavailable').fetchone()[0],
                    'run': run, 'counts': counts, 'tasks': tasks, 'limitations': LIMITATIONS,
                    'scan_complete': bool(run and run['status'] == 'complete'),
                    'historical_verified': False, 'evaluation_performed': False,
                    'pending_tasks': sum(not r['done'] for r in tasks)}

    def _verified_record(self, db, row):
        record = json.loads(row['value'])
        receipt_row = db.execute('SELECT value FROM receipts WHERE sha=?', (row['receipt'],)).fetchone()
        receipt = json.loads(receipt_row[0]) if receipt_row else None
        if (digest(record) != row['sha'] or not receipt or digest(receipt) != row['receipt']
                or receipt['records'].get(row['key']) != row['sha'] or record['key'] != row['key']
                or record['repository'] != self.repository or receipt['repository_id'] != self._meta(db, 'github_repository_id')):
            raise ValueError('Repository document/receipt integrity failed')
        return record, receipt

    def search(self, query, *, limit=10):
        query = _text(query, 2000)
        terms = list(dict.fromkeys(re.findall(r'\w+', query, re.UNICODE)))[:32]
        if not terms or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Provide search terms and 1–20 results')
        expression = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
        with self.connect() as db:
            rows = db.execute('''SELECT documents.*, search.text AS indexed_text, bm25(search) AS rank
                FROM search JOIN documents ON documents.key=search.key WHERE search MATCH ?
                AND NOT EXISTS(SELECT 1 FROM unavailable WHERE unavailable.key=documents.key)
                ORDER BY rank, documents.key LIMIT ?''', (expression, limit)).fetchall()
            results = []
            for row in rows:
                record, receipt = self._verified_record(db, row)
                text = record['title'] + '\n\n' + record['body']
                if row['indexed_text'] != text:
                    raise ValueError('Repository search index differs from source document')
                lines = text.splitlines()
                line_number = next((i for i, line in enumerate(lines, 1) if any(t.casefold() in line.casefold() for t in terms)), 1)
                excerpt = lines[line_number-1][:800] if lines else ''
                results.append({'key': row['key'], 'kind': record['kind'], 'url': record['url'],
                                'number': record['number'], 'title': record['title'], 'record_sha256': row['sha'],
                                'receipt_sha256': row['receipt'], 'source_url': receipt['source_url'],
                                'fetched_at': receipt['fetched_at'], 'last_seen_run': row['run'],
                                'score': round(-row['rank'], 8), 'score_is_probability': False,
                                'snippet': {'line': line_number, 'text': excerpt, 'sha256': sha256(excerpt.encode()).hexdigest()},
                                'historical_verified': False, 'relationship': 'candidate_unverified'})
        return {'repository': self.repository, 'results': results, 'limitations': LIMITATIONS,
                'index_status': self.status()}

    def item(self, key):
        if not isinstance(key, str) or len(key) > 2000:
            raise ValueError('Invalid repository document key')
        with self.connect() as db:
            row = db.execute('SELECT * FROM documents WHERE key=?', (key,)).fetchone()
            if row is None:
                raise ValueError('Repository document is not indexed')
            record, receipt = self._verified_record(db, row)
            unavailable = db.execute('SELECT reason,receipt FROM unavailable WHERE key=?', (key,)).fetchone()
            # Extract mentions as candidates only. No linked issue is acquired.
            text = record['title'] + '\n\n' + record['body']
            mentions = sorted({int(m) for m in re.findall(r'(?<![\w/])#([1-9][0-9]{0,9})\b', text)})[:100]
            return {'record': record, 'record_sha256': row['sha'], 'receipt': receipt,
                    'availability': dict(unavailable) if unavailable else {'status': 'last_observed_available'},
                    'receipt_sha256': row['receipt'], 'links':record_links(record), 'mentions': [
                        {'repository': self.repository, 'number': number, 'relationship': 'text_mention_unverified'}
                        for number in mentions], 'limitations': LIMITATIONS}

    def search_code(self, query, *, limit=10):
        """Revision-bound retrieval over the connected snapshot, with no model."""
        from issueintel.repository.graph import CodeGraph, GraphEdge, GraphNode
        from issueintel.retrieval.search import retrieve_evidence
        query = _text(query, 2000)
        if not query.strip() or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Provide search terms and 1–20 results')
        status = self.status()
        record = status['current_snapshot']
        if not record:
            raise ValueError('Repository code snapshot has not been indexed')
        snapshot = self._verify_snapshot(record)
        value = json.loads((self.root/record['graph_path']).read_bytes())
        graph = CodeGraph(**{**value, 'nodes': tuple(GraphNode(**n) for n in value['nodes']),
                             'edges': tuple(GraphEdge(**e) for e in value['edges']),
                             'parse_errors': tuple(value['parse_errors']), 'limitations': tuple(value['limitations'])})
        return {'repository': self.repository, 'commit_sha': snapshot.commit_sha,
                'results': retrieve_evidence(query, snapshot, graph, top_k=limit, expand=0),
                'historical_verified': False, 'index_status': status}
