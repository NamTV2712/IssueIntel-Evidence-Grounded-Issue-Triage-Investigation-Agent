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
from .github_app import GitHubAppAuth, GitHubAppConfig, GitHubAppError


class SyncServerStopping(Exception):
    """Preserve automatic eligibility when the local server stops normally."""


class GitHubSync:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._app_auth = None
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

    @staticmethod
    def _app_bound(connection):
        return connection is not None and any(connection.get(key) is not None
                                               for key in ('app_id', 'installation_id'))

    @staticmethod
    def _binding(connection):
        return tuple(connection.get(key) for key in ('repository', 'repository_id',
            'app_id', 'installation_id')) + (connection.get('grant_epoch', 0),)

    def _active_connection(self, repository, *, allow_pending=False):
        name = parse_github_reference(repository).repository.lower()
        connection = self.connection(name)
        if not self._app_bound(connection):
            return None
        if (connection.get('repository') != name or connection.get('paused') is not False
                or connection.get('phase') not in {'initial', 'updates', 'ready'}
                or connection.get('grant_pending', False) and not allow_pending
                or type(connection.get('grant_epoch', 0)) is not int
                or connection.get('grant_epoch', 0) < 0):
            raise ValueError('GitHub App repository grant is inactive or awaiting reconciliation')
        for key in ('repository_id', 'installation_id', 'app_id'):
            _positive(connection.get(key))
        return connection

    def authority_binding(self, repository):
        """Offline, immutable admission binding; None denotes manual mode.

        This only reads local metadata. It never reads keys/configuration, signs
        a JWT, refreshes a token or calls GitHub. A caller can compare this tuple
        around remote work without treating that comparison as a fresh grant.
        """
        connection = self._active_connection(repository)
        return self._binding(connection) if connection is not None else None

    def _broker(self, connection):
        config = GitHubAppConfig.load(self.root, required=True)
        if (config.app_id != connection['app_id'] or config.installation_id is not None
                and config.installation_id != connection['installation_id']):
            raise ValueError('GitHub App signing identity differs from the connected installation')
        if self._app_auth is None or self._app_auth.config != config:
            self._app_auth = GitHubAppAuth(config)
        return self._app_auth

    def _authorize(self, repository, *, purpose='read', fresh_grant=False, allow_pending=False):
        if purpose not in {'read', 'comment'} or type(fresh_grant) is not bool:
            raise ValueError('Invalid GitHub App authorization purpose')
        connection = self._active_connection(repository, allow_pending=allow_pending)
        if connection is None:
            return None
        broker = self._broker(connection)
        credential = broker.authorize(connection['repository'], connection['repository_id'],
            connection['installation_id'], purpose=purpose, fresh_grant=fresh_grant)
        current = self._active_connection(repository, allow_pending=allow_pending)
        if (current is None or self._binding(current) != self._binding(connection)
                or GitHubAppConfig.load(self.root, required=True) != broker.config):
            broker.invalidate(installation_id=connection['installation_id'], repository=connection['repository'])
            raise ValueError('GitHub App repository grant changed during authorization')
        return credential

    def authorize_repository(self, repository, *, purpose='read', fresh_grant=False):
        """Return None only for a manual connection, never as App fallback."""
        return self._authorize(repository, purpose=purpose, fresh_grant=fresh_grant)

    def read_token_provider(self, repository, *, fresh_grant=False):
        connection = self._active_connection(repository)
        if connection is None:
            return None
        binding = self._binding(connection)
        if fresh_grant:
            self.authorize_repository(repository, fresh_grant=True)
        def token():
            current = self._active_connection(repository)
            if current is None or self._binding(current) != binding:
                raise ValueError('GitHub App reader no longer matches the connected grant')
            return self.authorize_repository(repository).token
        return token

    def reader(self, repository, *, fresh_grant=False):
        # Import through the module so synthetic transport replacements remain
        # shared by direct indexing and the automatic scheduler.
        from .github_index import GitHubReader
        binding = self.authority_binding(repository)
        provider = self.read_token_provider(repository, fresh_grant=fresh_grant)
        def check():
            if self.authority_binding(repository) != binding:
                raise ValueError('GitHub App reader no longer matches the connected grant')
        return (GitHubReader(repository, token_provider=provider, check_authority=check) if provider is not None
                else GitHubReader(repository, check_authority=check))

    def _invalidate(self, installation_id):
        if self._app_auth is not None:
            self._app_auth.invalidate(installation_id=installation_id)

    def _pending(self, connection):
        return self._grant_changed(connection['repository'], pending=True)

    def _grant_changed(self, repository, *, pending=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM connections WHERE repo=?', (repository,)).fetchone()
            if row is None:
                raise ValueError('Repository has not been connected')
            current = json.loads(row[0])
            if pending and current['paused']:
                return current  # A selection change never undoes a manual pause.
            current.update(grant_epoch=current.get('grant_epoch', 0) + 1,
                           next_attempt=0, updated_at=time.time())
            if pending:
                current.update(grant_pending=True, error='installation_grant_reconciliation_pending')
            else:
                current.update(paused=True, error='installation_removed')
            db.execute('UPDATE connections SET value=? WHERE repo=?', (canonical_json(current), repository))
        return current

    def _batch_failure(self, repository, connection, **changes):
        """Never let a late worker error erase a concurrent pause/revocation."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM connections WHERE repo=?', (repository,)).fetchone()
            if row is None:
                return None
            current = json.loads(row[0])
            if (current['paused'] or self._binding(current) != self._binding(connection)
                    or current.get('grant_pending') is True and connection.get('grant_pending') is not True):
                return current
            current.update(changes, updated_at=time.time())
            db.execute('UPDATE connections SET value=? WHERE repo=?', (canonical_json(current), repository))
        return current

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
                    if self._app_bound(value):
                        value['grant_epoch'] = value.get('grant_epoch', 0) + 1
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
                and (c['phase'] in {'initial','updates'} or c.get('grant_pending') is True)],
                key=lambda c:c.get('scheduled_at',0))

    def admit_command(self, payload):
        """Keep signed App commands inside the active installation grant.

        A signature authenticates the delivery, not a still-active connection.
        Plain manual signed webhooks retain their legacy admission only when
        neither local configuration, payload nor saved connection is App-bound.
        This check performs no acquisition, scheduling, provider call or write.
        """
        repository = payload.get('repository')
        if not isinstance(repository, dict):
            raise ValueError('Command repository identity is missing')
        source = parse_github_reference(repository.get('full_name', ''))
        if source.issue_number is not None or repository.get('private') is not False:
            raise ValueError('Commands require a public repository')
        name = source.repository.lower()
        connection = self.connection(name)
        if connection is not None and not isinstance(connection, dict):
            raise ValueError('Command connection identity is invalid')
        configured = {key: os.environ.get(key, '') for key in ('GITHUB_APP_ID', 'GITHUB_INSTALLATION_ID')}
        app_bound = (any(configured.values()) or 'installation' in payload or
                     connection is not None and any(connection.get(key) is not None
                                                    for key in ('app_id', 'installation_id')))
        if not app_bound:
            return
        if not any(configured.values()):
            raise ValueError('GitHub App command requires local App or installation configuration')
        identities = {}
        for key, value in configured.items():
            if value:
                if not value.isascii() or not value.isdigit() or len(value) > 20:
                    raise ValueError('GitHub App command configuration is invalid')
                identities[key] = _positive(int(value))
        if (connection is None or connection.get('repository') != name
                or connection.get('paused') is not False
                or connection.get('grant_pending') is True
                or connection.get('phase') not in {'initial', 'updates', 'ready'}):
            raise ValueError('GitHub App command repository is not actively connected')
        identity = repository_identity(repository)
        if identity['repository_id'] != _positive(connection.get('repository_id')):
            raise ValueError('Command global repository ID differs from the connection')
        installation = payload.get('installation')
        if not isinstance(installation, dict):
            raise ValueError('GitHub App command installation identity is missing')
        installation_id = _positive(installation.get('id'))
        if installation_id != _positive(connection.get('installation_id')):
            raise ValueError('Command installation differs from the connected installation')
        if ('GITHUB_INSTALLATION_ID' in identities
                and installation_id != identities['GITHUB_INSTALLATION_ID']):
            raise ValueError('Command installation differs from local configuration')
        stored_app = connection.get('app_id')
        if stored_app is not None:
            _positive(stored_app)
        if ('GITHUB_APP_ID' in identities
                and (stored_app is None or stored_app != identities['GITHUB_APP_ID'])):
            raise ValueError('Command App differs from the configured connection')
        if 'app_id' in installation:
            payload_app = _positive(installation['app_id'])
            if stored_app is not None and payload_app != stored_app:
                raise ValueError('Command App differs from the connected App')

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
            installation = payload.get('installation')
            if not isinstance(installation, dict):
                raise ValueError('GitHub App installation identity is missing')
            install_id = _positive(installation.get('id'))
            configured = os.environ.get('GITHUB_INSTALLATION_ID', '')
            app = os.environ.get('GITHUB_APP_ID', '')
            payload_app = installation.get('app_id')
            if payload_app is not None:
                _positive(payload_app)
            known_app = next((c.get('app_id') for c in self.connections() if c['installation_id']==install_id and c.get('app_id')), None)
            for value in (configured, app):
                if value and (not value.isascii() or not value.isdigit() or len(value) > 19 or int(value) <= 0):
                    raise ValueError('GitHub App installation is not configured locally')
            allowed = (bool(configured or app)
                       and (not configured or int(configured) == install_id)
                       and (not app or (payload_app if payload_app is not None else known_app) == int(app))
                       and (known_app is None or payload_app in {None, known_app}))
            if not allowed:
                raise ValueError('GitHub App installation is not configured locally')
            action = payload.get('action')
            revoke_installation = event == 'installation' and action in {'deleted', 'suspend'}
            added = payload.get('repositories', []) if event == 'installation' and action == 'created' else payload.get('repositories_added', []) if action == 'added' else []
            # Installation revocation is authoritative for every saved binding;
            # it never depends on completeness or shape of a repository array.
            removed = payload.get('repositories_removed', []) if action == 'removed' else []
            if not isinstance(added, list) or not isinstance(removed, list) or len(added)+len(removed) > 100:
                raise ValueError('Installation repository list exceeds bounds')
            for item in added:
                if not isinstance(item, dict):
                    raise ValueError('Invalid installation repository identity')
                if item.get('private') is True:
                    continue
                identity = repository_identity(item)
                existing = self.connection(identity['repository'])
                self.connect(identity['repository'], revision=existing['revision'] if existing else 'HEAD',
                             repository_id=identity['repository_id'], installation_id=install_id,
                             app_id=payload_app or known_app)
                if existing and existing.get('paused') and existing.get('error') != 'installation_removed':
                    self.change(identity['repository'], paused=True, error=existing.get('error'), phase=existing['phase'],
                                initial_started=existing.get('initial_started', False))
                repositories.append(identity['repository'])
            for item in removed:
                if not isinstance(item, dict):
                    raise ValueError('Invalid installation repository identity')
                # Revocation remains meaningful if a previously public repo was
                # made private. No private contents are ever acquired here.
                identity = repository_identity({**item, 'private': False})
                existing = self.connection(identity['repository'])
                if existing and existing['installation_id'] == install_id and existing['repository_id'] == identity['repository_id']:
                    self._grant_changed(identity['repository'])
            self._invalidate(install_id)
            if revoke_installation:
                for connection in self.connections():
                    if connection['installation_id'] == install_id:
                        self._grant_changed(connection['repository'])
            elif event == 'installation_repositories':
                # GitHub can report all -> selected with an empty removed list.
                # Never infer retained access from omission: close admission now,
                # reconcile every known connection on the shared worker later.
                for connection in self.connections():
                    if connection['installation_id'] == install_id and not connection['paused']:
                        self._pending(connection)
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
                if not current or current['paused'] or current.get('grant_pending') is True:
                    raise InterruptedError('Automatic sync paused')
            try:
                reconciled = False
                if connection.get('grant_pending') is True:
                    self._authorize(repository, fresh_grant=True, allow_pending=True)
                    # Reconcile only the exact grant generation we checked. A
                    # newer delivery stays pending instead of being cleared.
                    with self.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        current = json.loads(db.execute('SELECT value FROM connections WHERE repo=?', (repository,)).fetchone()[0])
                        if current['paused'] or self._binding(current) != self._binding(connection):
                            raise ValueError('GitHub App grant changed during reconciliation')
                        current.update(grant_pending=False, error=None, updated_at=time.time())
                        db.execute('UPDATE connections SET value=? WHERE repo=?', (canonical_json(current), repository))
                    connection = current
                    reconciled = True
                reader = self.reader(repository, fresh_grant=not reconciled)
                check()
                if connection['phase'] == 'initial':
                    refresh = index.status()['scan_complete'] and not connection.get('initial_started')
                    status = index.sync(revision=connection['revision'], max_requests=20, refresh=refresh,
                                        expected_repository_id=connection['repository_id'], check_cancelled=check,
                                        reader=reader)
                    phase = 'updates' if status['scan_complete'] and status['pending_updates'] else 'ready' if status['scan_complete'] else 'initial'
                elif connection['phase'] == 'updates':
                    if index.status()['github_repository_id'] != connection['repository_id']:
                        raise ValueError('Connected repository identity changed')
                    status = process_updates(index, revision=connection['revision'], max_requests=20, check_cancelled=check,
                                             reader=reader)
                    phase = 'updates' if status['pending_updates'] else 'ready'
                else:
                    status = index.status()
                    phase = 'updates' if status['pending_updates'] else 'ready'
                # A delivery can arrive while the batch finishes. Check its inbox under registry lock.
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if phase == 'ready' and index.status()['pending_updates']:
                        phase = 'updates'
                    current = json.loads(db.execute('SELECT value FROM connections WHERE repo=?', (repository,)).fetchone()[0])
                    if current['paused'] or current.get('grant_pending') is True:
                        return status  # Preserve a revocation/pending event racing the batch.
                    current.update(phase=phase, repository_id=status['github_repository_id'], initial_started=True,
                                   failures=0, next_attempt=0, error=None, progress={k:v for k,v in status.items() if k != 'tasks'})
                    db.execute('UPDATE connections SET value=? WHERE repo=?', (canonical_json(current),repository))
                return status
            except InterruptedError:
                self._batch_failure(repository, connection, paused=True, error='paused')
                raise
            except SyncServerStopping:
                raise
            except GitHubAppError as error:
                transient = error.http_status in {429, 500, 502, 503, 504}
                self._batch_failure(repository, connection, error='github_app_' + error.code, paused=not transient,
                            next_attempt=time.time()+60, failures=connection['failures']+1)
                return None
            except GitHubReadError as error:
                transient = error.retryable
                self._batch_failure(repository, connection, error=f'github_http_{error.status}', paused=not transient,
                            next_attempt=time.time()+max(error.retry_after,60*2**min(connection['failures'],6)),
                            failures=connection['failures']+1)
                return None
            except (OSError, TimeoutError):
                self._batch_failure(repository, connection, error='network_or_local_io', next_attempt=time.time()+60,
                            failures=connection['failures']+1)
                return None
            except Exception:
                self._batch_failure(repository, connection, error='identity_or_integrity_check_failed', paused=True)
                raise
