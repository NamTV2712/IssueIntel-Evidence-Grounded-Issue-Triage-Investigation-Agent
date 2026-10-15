"""Durable automatic connection lifecycle; read-only work runs on the shared queue."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time

from issueintel.repository.acquire import parse_github_reference, _validate_revision
from issueintel.runtime.io import canonical_json
from .github_index import RepositoryIndex, GitHubReadError, _positive, _writer_lock
from .repository_updates import authenticate, repository_identity, event_update, enqueue_update, process_updates, EVENTS


class SyncServerStopping(Exception):
    """Preserve automatic eligibility when the local server stops normally."""


class GitHubSync:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = (self.root/'data/github_sync').resolve()
        if not self.directory.is_relative_to(self.root/'data'):
            raise ValueError('Automatic sync storage escaped private data')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory/'connections.sqlite'
        if self.path.resolve().parent != self.directory or self.path.is_symlink():
            raise ValueError('Unsafe automatic sync storage')
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS connections(repo TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, sha TEXT, value TEXT);''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def connection(self, repository):
        name = parse_github_reference(repository).repository.lower()
        with self.db() as db:
            row = db.execute('SELECT value FROM connections WHERE repo=?', (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def change(self, repository, **changes):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM connections WHERE repo=?', (repository,)).fetchone()
            if row is None:
                raise ValueError('Repository has not been connected')
            value = json.loads(row[0])
            value.update(changes, updated_at=time.time())
            db.execute('UPDATE connections SET value=? WHERE repo=?', (canonical_json(value), repository))
        return value

    def connect(self, repository, *, revision='HEAD', repository_id=None, installation_id=None, app_id=None):
        source = parse_github_reference(repository)
        if source.issue_number is not None:
            raise ValueError('Connect a repository, not an issue')
        name = source.repository.lower()
        revision = _validate_revision(revision)
        if repository_id is not None:
            _positive(repository_id)
        if installation_id is not None:
            _positive(installation_id)
        if app_id is not None:
            _positive(app_id)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM connections WHERE repo=?', (name,)).fetchone()
            if row:
                value = json.loads(row[0])
                if (repository_id is not None and value.get('repository_id') not in {None,repository_id}
                        or installation_id is not None and value.get('installation_id') not in {None,installation_id}
                        or value['revision'] != revision):
                    raise ValueError('Connection identity/revision changed; reconnect explicitly with a new identity')
                value.update(paused=False, next_attempt=0, error=None)
                if json.loads(row[0])['paused']:
                    value.update(phase='initial', initial_started=False)
                if repository_id is not None:
                    value['repository_id'] = repository_id
                if installation_id is not None:
                    value['installation_id'] = installation_id
                if app_id is not None:
                    if value.get('app_id') not in {None,app_id}:
                        raise ValueError('Connected GitHub App identity changed')
                    value['app_id'] = app_id
            else:
                value = {'repository':name, 'revision':revision, 'repository_id':repository_id,
                         'installation_id':installation_id, 'app_id':app_id, 'paused':False, 'phase':'initial',
                         'next_attempt':0, 'error':None, 'failures':0, 'created_at':time.time(), 'job_id':None}
            db.execute('INSERT OR REPLACE INTO connections VALUES (?,?)', (name, canonical_json(value)))
        RepositoryIndex(self.root, name)  # Create the durable inbox before acknowledging connection.
        return value

    def connections(self):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT value FROM connections ORDER BY repo')]

    def due(self):
        return sorted([c for c in self.connections() if not c['paused'] and c['next_attempt'] <= time.time()
                and c['phase'] in {'initial','updates'}], key=lambda c:c.get('scheduled_at',0))

    def receive(self, raw, *, signature, event, delivery, secret):
        payload = authenticate(raw, signature, delivery, secret)
        delivery = delivery.lower()
        fingerprint = sha256(event.encode()+b'\0'+raw).hexdigest()
        with self.db() as db:
            old = db.execute('SELECT sha,value FROM deliveries WHERE id=?', (delivery,)).fetchone()
            if old:
                if old['sha'] != fingerprint:
                    raise ValueError('Delivery ID changed content or event type')
                return json.loads(old['value'])
        repositories = []
        if event in {'installation', 'installation_repositories'}:
            install_id = _positive(payload.get('installation', {}).get('id'))
            configured = os.environ.get('GITHUB_INSTALLATION_ID', '')
            app = os.environ.get('GITHUB_APP_ID', '')
            payload_app = payload['installation'].get('app_id')
            known_app = next((c.get('app_id') for c in self.connections() if c['installation_id']==install_id and c.get('app_id')), None)
            allowed = (configured.isdigit() and int(configured) == install_id
                       or app.isdigit() and int(app)>0 and (payload_app if payload_app is not None else known_app) == int(app))
            if not allowed:
                raise ValueError('GitHub App installation is not configured locally')
            action = payload.get('action')
            added = payload.get('repositories', []) if event == 'installation' and action == 'created' else payload.get('repositories_added', []) if action == 'added' else []
            removed = payload.get('repositories', []) if event == 'installation' and action in {'deleted','suspend'} else payload.get('repositories_removed', []) if action == 'removed' else []
            if not isinstance(added, list) or not isinstance(removed, list) or len(added)+len(removed) > 100:
                raise ValueError('Installation repository list exceeds bounds')
            for item in added:
                if item.get('private') is True:
                    continue
                identity = repository_identity(item)
                existing = self.connection(identity['repository'])
                self.connect(identity['repository'], revision=existing['revision'] if existing else 'HEAD',
                             repository_id=identity['repository_id'], installation_id=install_id,
                             app_id=payload_app or known_app)
                repositories.append(identity['repository'])
            for item in removed:
                identity = repository_identity(item)
                existing = self.connection(identity['repository'])
                if existing and existing['installation_id'] == install_id and existing['repository_id'] == identity['repository_id']:
                    self.change(identity['repository'], paused=True, error='installation_removed')
            if event == 'installation' and action in {'deleted','suspend'}:
                for connection in self.connections():
                    if connection['installation_id'] == install_id:
                        self.change(connection['repository'], paused=True, error='installation_removed')
        elif event in EVENTS:
            identity = repository_identity(payload.get('repository'))
            connection = self.connection(identity['repository'])
            if not connection or connection['paused']:
                return {'status':'ignored', 'reason':'repository_not_connected_or_paused'}
            installation_id = (payload.get('installation') or {}).get('id')
            if installation_id is not None:
                _positive(installation_id)
            if installation_id != connection['installation_id']:
                raise ValueError('Event installation differs from connected installation')
            if connection['repository_id'] is not None and identity['repository_id'] != connection['repository_id']:
                raise ValueError('Event global repository ID mismatch')
            value = event_update(payload, event, delivery, sha256(raw).hexdigest())
            inserted = enqueue_update(RepositoryIndex(self.root, identity['repository']), value)
            if inserted and connection['phase'] != 'initial':
                self.change(identity['repository'], phase='updates', next_attempt=0)
            repositories.append(identity['repository'])
        else:
            return {'status':'ignored', 'reason':'event_not_used_for_repository_sync'}
        result = {'status':'accepted', 'delivery':delivery, 'repositories':repositories, 'automatic':True,
                  'provider':'none'}
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?)', (delivery, fingerprint, canonical_json(result)))
        return result

    def batch(self, repository, *, check_cancelled=None):
        """One fair batch. Scheduler continues persisted work while server is running."""
        with _writer_lock(self.directory/'worker.lock'):
            connection = self.connection(repository)
            if not connection or connection['paused']:
                return None
            index = RepositoryIndex(self.root, repository)
            def check():
                if check_cancelled:
                    check_cancelled()
                current = self.connection(repository)
                if current['paused']:
                    raise InterruptedError('Automatic sync paused')
            try:
                check()
                if connection['phase'] == 'initial':
                    refresh = index.status()['scan_complete'] and not connection.get('initial_started')
                    status = index.sync(revision=connection['revision'], max_requests=20, refresh=refresh,
                                        expected_repository_id=connection['repository_id'], check_cancelled=check)
                    phase = 'updates' if status['scan_complete'] and status['pending_updates'] else 'ready' if status['scan_complete'] else 'initial'
                else:
                    if index.status()['github_repository_id'] != connection['repository_id']:
                        raise ValueError('Connected repository identity changed')
                    status = process_updates(index, revision=connection['revision'], max_requests=20, check_cancelled=check)
                    phase = 'updates' if status['pending_updates'] else 'ready'
                # A delivery can arrive while the batch finishes. Check its inbox under registry lock.
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if phase == 'ready' and index.status()['pending_updates']:
                        phase = 'updates'
                    current = json.loads(db.execute('SELECT value FROM connections WHERE repo=?', (repository,)).fetchone()[0])
                    current.update(phase=phase, repository_id=status['github_repository_id'], initial_started=True,
                                   failures=0, next_attempt=0, error=None, progress={k:v for k,v in status.items() if k != 'tasks'})
                    db.execute('UPDATE connections SET value=? WHERE repo=?', (canonical_json(current),repository))
                return status
            except InterruptedError:
                self.change(repository, paused=True, error='paused')
                raise
            except SyncServerStopping:
                raise
            except GitHubReadError as error:
                transient = error.retryable
                self.change(repository, error=f'github_http_{error.status}', paused=not transient,
                            next_attempt=time.time()+max(error.retry_after,60*2**min(connection['failures'],6)),
                            failures=connection['failures']+1)
                return None
            except (OSError, TimeoutError):
                self.change(repository, error='network_or_local_io', next_attempt=time.time()+60,
                            failures=connection['failures']+1)
                return None
            except Exception:
                self.change(repository, error='identity_or_integrity_check_failed', paused=True)
                raise
