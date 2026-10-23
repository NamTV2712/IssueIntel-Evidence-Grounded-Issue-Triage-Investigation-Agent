"""Durable, explicitly approved batches of ordinary product investigations.

The scheduler records a fixed child identity before dispatch and records that a
child has started before entering the shared Workbench pipeline.  Started work
is never automatically retried.  Opening saved batches does not start a worker.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import quote
from urllib.request import Request

from issueintel.runtime.io import canonical_json, digest, write_json
from . import product_batches as contract


TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


class _Connection(sqlite3.Connection):
    def __exit__(self, *arguments):
        try:
            return super().__exit__(*arguments)
        finally:
            self.close()


class BatchConflict(ValueError):
    """An author-chosen batch key or captured approval has changed."""


def now():
    return datetime.now(timezone.utc).isoformat()


def policy_sha256():
    from .stage_jobs import job_policy_sha256
    directory = Path(__file__).parent
    return digest({'schema': 'product_batch_policy_v1', 'job_policy': job_policy_sha256(),
                   'batch_jobs': sha256((directory/'batch_jobs.py').read_bytes()).hexdigest(),
                   'contracts': sha256((directory/'product_batches.py').read_bytes()).hexdigest(),
                   'concurrency': 1, 'automatic_started_retry': False})


class BatchJobs:
    def __init__(self, host):
        self.host = host
        self.path = host.directory/'product_batches.sqlite'
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.thread_lock = threading.Lock()
        self.enabled = set()
        # Test seams use synthetic input; the defaults perform ordinary public
        # read-only acquisition only after a user explicitly previews a manifest.
        self.issue_fetcher = None
        self.revision_resolver = None

    def _db(self, *, write=False):
        if not write and not self.path.is_file():
            return None
        db = sqlite3.connect(self.path if write else self.path.as_uri()+'?mode=ro',
                             uri=not write, timeout=30, factory=_Connection)
        db.row_factory = sqlite3.Row
        if write:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS previews (
                    sha TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, run_key TEXT NOT NULL UNIQUE,
                    manifest_sha TEXT NOT NULL, preview_sha TEXT NOT NULL,
                    manifest TEXT NOT NULL, preview TEXT NOT NULL,
                    approved_api INTEGER NOT NULL, desired TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items (
                    batch_id TEXT NOT NULL, ordinal INTEGER NOT NULL, item_key TEXT NOT NULL,
                    child_id TEXT NOT NULL UNIQUE, phase TEXT NOT NULL,
                    cancelled INTEGER NOT NULL DEFAULT 0, outcome TEXT, error TEXT,
                    PRIMARY KEY(batch_id,item_key));
            ''')
        return db

    @staticmethod
    def _identifier(identifier):
        if type(identifier) is not str or re.fullmatch('[a-f0-9]{24}', identifier) is None:
            raise ValueError('Invalid product batch ID')

    def _records(self, identifier, db):
        self._identifier(identifier)
        batch = db.execute('SELECT * FROM batches WHERE id=?', (identifier,)).fetchone()
        if batch is None:
            raise FileNotFoundError('No saved product batch')
        preview = contract.validate_preview(json.loads(batch['preview']))
        manifest = contract.validate_manifest(json.loads(batch['manifest']))
        if (batch['manifest_sha'] != digest(manifest) or preview['manifest'] != manifest
                or batch['preview_sha'] != preview['sha256']
                or contract.batch_id(batch['run_key']) != identifier
                or batch['run_key'] != manifest['run_key']):
            raise ValueError('Saved batch approval binding changed')
        if (type(batch['approved_api']) is not int or batch['approved_api'] not in {0,1}
                or preview['requires_api'] and batch['approved_api'] != 1
                or batch['desired'] not in {'running','paused','cancelled'}):
            raise ValueError('Saved batch approval or desired state changed')
        items = db.execute('SELECT * FROM items WHERE batch_id=? ORDER BY ordinal', (identifier,)).fetchall()
        if len(items) != len(manifest['items']):
            raise ValueError('Saved batch child inventory changed')
        for ordinal, (item, original) in enumerate(zip(items, manifest['items'])):
            if (item['ordinal'] != ordinal or item['item_key'] != original['key']
                    or item['child_id'] != contract.stable_child_id(batch['run_key'], original['key'])
                    or item['phase'] not in {'pending', 'prepared', 'started', 'terminal'}
                    or type(item['cancelled']) is not int or item['cancelled'] not in {0,1}
                    or item['phase']=='terminal' and item['outcome'] not in TERMINAL):
                raise ValueError('Saved batch child identity changed')
        return batch, preview, items

    def _remote_revision(self, reference, revision):
        if self.revision_resolver is not None:
            result = self.revision_resolver(reference, revision)
        else:
            from issueintel.repository.acquire import parse_github_reference, open_github_request
            source = parse_github_reference(reference)
            headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'IssueIntel-readonly'}
            token = os.environ.get('GITHUB_TOKEN', '')
            if any(ord(char) < 32 for char in token):
                raise ValueError('Invalid GitHub credential header')
            if token:
                headers['Authorization'] = 'Bearer '+token
            url = 'https://api.github.com/repos/'+source.repository+'/commits/'+quote(revision, safe='')
            with open_github_request(Request(url, headers=headers, method='GET'), timeout=30) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('GitHub commit response exceeds the size bound')
            result = json.loads(raw)['sha']
        if type(result) is not str or re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', result) is None:
            raise ValueError('Require a resolved full Git commit object ID')
        return result

    def _capture(self, request):
        from . import workbench
        from issueintel.repository.snapshot import _git
        mode = request['mode']
        provenance = None
        requested = None if mode == 'example' else request['revision']
        if mode == 'example':
            case, local = self.host._example()
            revision = case['revision']
            resolved = deepcopy(request)
        else:
            if mode == 'github':
                issue = (self.issue_fetcher or workbench.fetch_github_issue)(request['issue_url'])
                repository = issue['repository']
                provenance = deepcopy(issue['provenance'])
                # The canonical manifest has lower-case GitHub identity.
                repository = repository.lower()
                provenance['source_url'] = ('https://api.github.com/repos/'+repository
                                             +'/issues/'+str(issue['number']))
                revision = self._remote_revision(repository, requested)
                case = {'case_id': request['issue_url'], 'issue_url': request['issue_url'],
                        'repository': repository, 'revision': revision,
                        'problem_statement': issue['problem_statement'], 'issue_created_at': issue['created_at']}
            else:
                repository = request['repository']
                path = Path(repository)
                revision = (_git(path.resolve(), 'rev-parse', '--verify', '--end-of-options', requested+'^{commit}')
                            .decode('ascii').strip() if path.is_dir()
                            else self._remote_revision(repository, requested))
            resolved = dict(request, revision=revision)
            if mode == 'local':
                case = {'case_id': 'local:'+digest(resolved)[:16], 'repository': repository,
                        'revision': revision, 'problem_statement': request['issue_text'], 'issue_created_at': None}
            local = Path(repository).resolve()
            if mode == 'github' or not Path(repository).is_dir():
                from issueintel.repository.acquire import parse_github_reference
                identity = parse_github_reference(repository)
                local = (self.host.root/'data/repos'/
                         (identity.owner.lower()+'__'+identity.repo.lower()+'.git')).resolve()
        source = contract.seal_source({'schema': contract.SOURCE_SCHEMA, 'mode': mode,
            'repository_path': str(Path(local).resolve()), 'requested_revision': requested,
            'revision': revision, 'case': case, 'issue_provenance': provenance}, request=resolved)
        return resolved, source

    def preview(self, manifest):
        manifest = contract.validate_manifest(manifest)
        rows = []
        for item in manifest['items']:
            try:
                request, source = self._capture(item['request'])
                rows.append(dict(key=item['key'], resolved_request=request, source=source, error=None))
            except Exception as error:
                # Do not reflect a network response, local path or credential in errors.
                rows.append(dict(key=item['key'], resolved_request=None, source=None,
                    error={'code': 'source_unavailable', 'message': 'Source capture failed ('+type(error).__name__+').'}))
        preview = contract.build_preview(manifest, rows, policy_sha256=policy_sha256())
        with self._db(write=True) as db:
            db.execute('INSERT OR IGNORE INTO previews VALUES (?,?,?)',
                       (preview['sha256'], canonical_json(preview), now()))
        return preview

    def submit(self, payload):
        if type(payload) is not dict or set(payload) != {'manifest', 'preview', 'confirmed', 'allow_api_calls'}:
            raise ValueError('Confirm an exact captured batch preview')
        manifest = contract.validate_manifest(payload['manifest'])
        preview = contract.validate_preview(payload['preview'])
        if (payload['confirmed'] is not True or type(payload['allow_api_calls']) is not bool
                or preview['manifest'] != manifest or not preview['can_start']):
            raise ValueError('Batch source, policy or explicit API approval is missing')
        identifier = contract.batch_id(manifest['run_key'])
        existing_intent = False
        with self._db(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            captured = db.execute('SELECT value FROM previews WHERE sha=?', (preview['sha256'],)).fetchone()
            if captured is None or captured['value'] != canonical_json(preview):
                raise ValueError('The exact preview must have been captured by this local server')
            existing = db.execute('SELECT * FROM batches WHERE id=? OR run_key=?',
                                  (identifier, manifest['run_key'])).fetchone()
            if existing is not None:
                if (existing['manifest_sha'] != digest(manifest) or existing['preview_sha'] != preview['sha256']
                        or existing['manifest'] != canonical_json(manifest)):
                    raise BatchConflict('This run key already owns a different manifest or captured source')
                existing_intent = True
            else:
                if (preview['policy_sha256'] != policy_sha256()
                        or preview['requires_api'] and not payload['allow_api_calls']):
                    raise ValueError('Batch policy or explicit API approval is missing')
                timestamp = now()
                db.execute('INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (identifier, manifest['run_key'], digest(manifest), preview['sha256'],
                     canonical_json(manifest), canonical_json(preview), int(payload['allow_api_calls']),
                     'running', timestamp, timestamp, ''))
                for ordinal, item in enumerate(manifest['items']):
                    db.execute('INSERT INTO items VALUES (?,?,?,?,?,?,?,?)',
                        (identifier, ordinal, item['key'], contract.stable_child_id(manifest['run_key'], item['key']),
                         'pending', 0, None, None))
        if not existing_intent:
            self._start(identifier)
        return self.read(identifier)

    def _binding(self, batch, preview, item):
        source = preview['items'][item['ordinal']]
        return {'schema': 'product_batch_child_v1', 'batch_id': batch['id'], 'item_key': item['item_key'],
                'manifest_sha256': batch['manifest_sha'], 'preview_sha256': batch['preview_sha'],
                'request_sha256': digest(source['resolved_request']), 'source_sha256': source['source_sha256'],
                'policy_sha256': preview['policy_sha256']}

    def _raw_child(self, batch, preview, item):
        path = self.host._job_path(item['child_id'])
        if not path.is_file():
            return None
        if path.stat().st_size > 8*1024*1024:
            raise ValueError('Saved batch child exceeds the read bound')
        row = json.loads(path.read_bytes())
        expected = preview['items'][item['ordinal']]['resolved_request']
        if (row.get('id') != item['child_id'] or row.get('request') != expected
                or row.get('batch') != self._binding(batch, preview, item)
                or any(row.get(key) != expected[key] for key in ('mode', 'provider', 'depth'))):
            raise ValueError('Saved batch child source binding changed')
        return row

    @staticmethod
    def _pristine(row):
        return (row is not None and row.get('status') == 'queued' and row.get('stage') == 'queued'
                and type(row.get('batch')) is dict
                and not set(row)-{'id','status','stage','created_at','mode','provider','depth','request','batch'})

    def _owned(self, identifier):
        return identifier in self.host.active or self.host.stage_recoveries.externally_owned(identifier)

    def _project(self, batch, preview, item):
        row = None
        integrity_unavailable = False
        try:
            row = self._raw_child(batch, preview, item)
            if item['phase'] == 'terminal':
                status = (item['outcome'] if row is not None and row.get('status')==item['outcome']
                          or row is None and item['outcome']=='cancelled' else 'interrupted')
            elif item['phase'] == 'pending' and row is None:
                status = 'cancelled' if item['cancelled'] else 'pending'
            elif item['phase'] in {'pending','prepared'} and self._pristine(row):
                status = 'cancelled' if item['cancelled'] and not self._owned(item['child_id']) else 'queued'
            elif row is not None and row.get('status') in TERMINAL:
                status = row['status']
            elif row is not None and row.get('status') in {'queued','running'}:
                if self._owned(item['child_id']):
                    status = row['status']
                else:
                    # Publication precedes release of the execution lease. The
                    # first file read may race that publication; re-read after
                    # proving no owner rather than misreport a finished child.
                    row = self._raw_child(batch,preview,item)
                    status = row['status'] if row is not None and row.get('status') in TERMINAL else 'interrupted'
            else:
                status = 'interrupted'
        except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError):
            status = 'interrupted'
            row = None
            integrity_unavailable = True
            try:
                if self._owned(item['child_id']):
                    status = 'queued' if item['phase'] in {'pending','prepared'} else 'running'
            except (ValueError,OSError,sqlite3.Error):
                pass
        source = preview['items'][item['ordinal']]
        request = source['resolved_request']
        error = item['error'] or (row or {}).get('error')
        if status == 'interrupted':
            error = 'Source or execution outcome unavailable; started work is never automatically retried.'
        elif integrity_unavailable:
            error = 'Source integrity is unavailable while a worker still owns execution; no retry is authorized.'
        return {'key': item['item_key'], 'job_id': item['child_id'],
                'job_available': row is not None,
                'request_sha256': source['request_sha256'], 'source_sha256': source['source_sha256'],
                'mode': request['mode'], 'provider': request['provider'], 'depth': request['depth'],
                'revision': source['source']['revision'], 'status': status,
                'stage': 'source_integrity_unavailable' if integrity_unavailable else (row or {}).get('stage', status),
                'error': error[:2000] if isinstance(error,str) else None}

    def read(self, identifier):
        db = self._db()
        if db is None:
            raise FileNotFoundError('No saved product batch')
        try:
            db.execute('BEGIN')
            batch, preview, records = self._records(identifier, db)
            items = [self._project(batch, preview, item) for item in records]
            counts = {status: sum(item['status'] == status for item in items)
                      for status in ('pending','queued','running','completed','failed','cancelled','interrupted')}
            counts['total'] = len(items)
            result = {'schema': contract.READ_SCHEMA, 'id': identifier, 'run_key': batch['run_key'],
                'manifest_sha256': batch['manifest_sha'], 'preview_sha256': batch['preview_sha'],
                'policy_sha256': preview['policy_sha256'], 'created_at': batch['created_at'],
                'updated_at': batch['updated_at'], 'desired_state': batch['desired'],
                'status': contract.batch_status(items, batch['desired']), 'requires_api': preview['requires_api'],
                'concurrency': 1, 'model_calls': 0, 'counts': counts, 'items': items}
            return contract.validate_read(dict(result, sha256=digest(result)))
        finally:
            db.close()

    def history(self):
        db = self._db()
        if db is None:
            return {'schema': 'product_batch_history_v1', 'batches': [], 'errors': []}
        try:
            ids = [row['id'] for row in db.execute('SELECT id FROM batches ORDER BY created_at DESC LIMIT 50')]
        finally:
            db.close()
        batches=[];errors=[]
        for identifier in ids:
            try:
                batches.append(self.read(identifier))
            except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):
                errors.append({'id':identifier,'code':'source_unavailable'})
        return {'schema': 'product_batch_history_v1', 'batches': batches, 'errors': errors}

    def action(self, identifier, action, payload, *, key=None):
        if action not in {'pause','continue','cancel'} or key is not None and action != 'cancel':
            raise ValueError('Choose a supported product batch action')
        allowed = {'manifest_sha256','preview_sha256'} | ({'allow_api_calls'} if action == 'continue' else set())
        if type(payload) is not dict or set(payload)-allowed or not {'manifest_sha256','preview_sha256'} <= set(payload):
            raise ValueError('Confirm the exact saved batch binding')
        with self._db(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            batch, preview, items = self._records(identifier, db)
            if payload['manifest_sha256'] != batch['manifest_sha'] or payload['preview_sha256'] != batch['preview_sha']:
                raise BatchConflict('The selected batch source changed')
            if action == 'continue':
                if key is not None or batch['desired'] == 'cancelled':
                    raise ValueError('A cancelled batch cannot be continued')
                if preview['policy_sha256'] != policy_sha256():
                    raise ValueError('The saved batch policy changed; no work was restarted')
                if preview['requires_api'] and payload.get('allow_api_calls') is not True:
                    raise ValueError('Continue requires explicit approval for the remaining API calls')
                if 'allow_api_calls' in payload and type(payload['allow_api_calls']) is not bool:
                    raise ValueError('Choose an explicit API consent')
            if key is not None and key not in {item['item_key'] for item in items}:
                raise ValueError('No matching product batch item')
            if key is None:
                desired = {'pause':'paused','continue':'running','cancel':'cancelled'}[action]
                db.execute('UPDATE batches SET desired=?,updated_at=? WHERE id=?', (desired,now(),identifier))
            if action == 'cancel':
                for item in items:
                    if key is None or item['item_key'] == key:
                        db.execute('UPDATE items SET cancelled=1 WHERE batch_id=? AND item_key=?',
                                   (identifier,item['item_key']))
        # Cancellation flags are durable before touching the process-local set.
        if action == 'cancel':
            for item in items:
                if (key is None or item['item_key'] == key) and item['child_id'] in self.host.active:
                    with self.host.lock:
                        self.host.cancelled.add(item['child_id'])
        if action in {'continue','cancel'}:
            self._start(identifier if action=='continue' else None)
        self.wake.set()
        return self.read(identifier)

    def is_cancelled(self, identifier):
        db = self._db()
        if db is None:
            return False
        try:
            row = db.execute('SELECT i.cancelled,b.desired FROM items i JOIN batches b ON b.id=i.batch_id '
                             'WHERE i.child_id=?', (identifier,)).fetchone()
            return row is not None and (bool(row['cancelled']) or row['desired'] == 'cancelled')
        finally:
            db.close()

    def prepared(self, identifier, row):
        """Only a durably bound, untouched child may remain queued without a worker."""
        if not self._pristine(row):
            return False
        db = self._db()
        if db is None:
            return False
        try:
            item = db.execute('SELECT * FROM items WHERE child_id=?', (identifier,)).fetchone()
            if item is None or item['phase'] != 'prepared' or item['cancelled']:
                return False
            batch, preview, _ = self._records(item['batch_id'], db)
            return self._raw_child(batch, preview, item) == row
        except (ValueError, OSError):
            return False
        finally:
            db.close()

    def input(self, row, *, acquire=True):
        """Read the server-captured body; never fetch a newer issue on dispatch."""
        binding = row.get('batch')
        if type(binding) is not dict:
            raise ValueError('Missing batch source authority')
        db = self._db()
        if db is None:
            raise ValueError('Missing durable batch source')
        try:
            batch, preview, items = self._records(binding['batch_id'], db)
            item = next((value for value in items if value['child_id'] == row['id']), None)
            if (item is None or item['phase'] != 'started' or item['cancelled'] or batch['desired'] == 'cancelled'
                    or self._raw_child(batch, preview, item) is None
                    or binding != self._binding(batch, preview, item)
                    or preview['policy_sha256'] != policy_sha256()):
                raise ValueError('Batch source, cancellation or policy authority changed')
            source = preview['items'][item['ordinal']]['source']
            if row['request'] != preview['items'][item['ordinal']]['resolved_request']:
                raise ValueError('The batch child request changed')
        finally:
            db.close()
        local = Path(source['repository_path'])
        if acquire:
            from . import workbench
            from issueintel.repository.snapshot import _git
            remote = (source['mode']=='github' or source['mode']=='local'
                      and Path(source['case']['repository']).resolve()!=local.resolve())
            if remote:
                # The cache directory may exist while this particular object
                # is absent. Native acquisition reuses cached full SHAs and
                # fetches only the pinned missing object, never a moving ref.
                acquired = workbench.acquire_repository(source['case']['repository'], source['revision'],
                                                       self.host.root/'data/repos')
                if Path(acquired.path).resolve() != local.resolve():
                    raise ValueError('Acquired repository differs from the captured source')
            elif not local.is_dir():
                raise ValueError('The captured local repository is unavailable')
            commit = _git(local, 'rev-parse', '--verify', '--end-of-options', source['revision']+'^{commit}').decode().strip()
            if commit != source['revision']:
                raise ValueError('Captured commit is no longer available')
        return deepcopy(source['case']), local

    def _start(self, identifier=None):
        with self.thread_lock:
            if self.stop.is_set():
                raise RuntimeError('The batch dispatcher is closed')
            if identifier is not None:
                self.enabled.add(identifier)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name='issueintel-batch-scheduler', daemon=True)
                self.thread.start()
        self.wake.set()

    def _loop(self):
        while not self.stop.is_set():
            try:
                self.dispatch_once()
            except (ValueError, OSError, sqlite3.Error, RuntimeError):
                # Intent and child identity survive lock pressure and restarts.
                pass
            self.wake.wait(.1)
            self.wake.clear()

    def dispatch_once(self):
        """Advance at most one child, never consume a worker waiting for a slot."""
        selected = None
        with self._db(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            ids = [row['id'] for row in db.execute('SELECT id FROM batches ORDER BY scheduled_at,created_at')]
            occupied = False
            candidates = []
            for identifier in ids:
                try:
                    batch, preview, items = self._records(identifier, db)
                except (ValueError,OSError,KeyError,TypeError):
                    # A damaged historical approval must not starve unrelated
                    # valid work. A live (or unknowable) owner still blocks the
                    # global outstanding-child limit; never repair its hashes.
                    occupied = occupied or self._invalid_batch_owned(identifier,db)
                    continue
                for item in items:
                    projected = self._project(batch, preview, item)
                    try:
                        owned=self._owned(item['child_id'])
                    except (ValueError,OSError,sqlite3.Error):
                        owned=True  # Unknown lease authority cannot free a slot.
                    if owned:
                        occupied=True
                        continue
                    if item['phase'] == 'terminal':
                        continue
                    if item['cancelled'] or batch['desired'] == 'cancelled':
                        if item['phase'] != 'started':
                            self._terminal(db,batch,item,'cancelled',None)
                            continue
                    if item['phase'] == 'started' or projected['status'] in TERMINAL:
                        if projected['status'] in TERMINAL:
                            self._terminal(db,batch,item,projected['status'],projected['error'])
                        else:
                            occupied = True
                        continue
                    if item['phase'] == 'prepared' and self._owned(item['child_id']):
                        occupied = True
                    if batch['desired'] == 'running' and not item['cancelled'] and identifier in self.enabled:
                        candidates.append((batch,preview,item))
            if occupied or not candidates:
                return False
            # Prefer an already prepared identity, otherwise round-robin batches.
            batch, preview, item = next((candidate for candidate in candidates if candidate[2]['phase']=='prepared'),candidates[0])
            if preview['policy_sha256'] != policy_sha256():
                db.execute('UPDATE batches SET desired=?,updated_at=? WHERE id=?',('paused',now(),batch['id']))
                return False
            request = preview['items'][item['ordinal']]['resolved_request']
            if item['phase'] == 'pending':
                existing = self._raw_child(batch,preview,item)
                if existing is not None and not self._pristine(existing):
                    self._terminal(db,batch,item,'interrupted','A fixed child identity is already occupied; no replacement was created.')
                    return False
                row = existing or self.host._prepare_batch_job(request,item['child_id'],self._binding(batch,preview,item))
                db.execute('UPDATE items SET phase=? WHERE batch_id=? AND item_key=?',('prepared',batch['id'],item['item_key']))
            else:
                row = self._raw_child(batch,preview,item)
                if not self._pristine(row):
                    self._terminal(db,batch,item,'interrupted','Prepared child is no longer pristine; no automatic retry.')
                    return False
            db.execute('UPDATE batches SET scheduled_at=?,updated_at=? WHERE id=?',(now(),now(),batch['id']))
            selected = row
        try:
            self.host._dispatch_prepared(selected,self._claim_and_run)
        except RuntimeError:
            return False
        return True

    def _invalid_batch_owned(self,identifier,db):
        try:
            rows=db.execute('SELECT child_id FROM items WHERE batch_id=?',(identifier,)).fetchall()
            children={row['child_id'] for row in rows}
            saved=db.execute('SELECT manifest FROM batches WHERE id=?',(identifier,)).fetchone()
            try:
                manifest=contract.validate_manifest(json.loads(saved['manifest']))
                children.update(contract.stable_child_id(manifest['run_key'],item['key']) for item in manifest['items'])
            except (ValueError,TypeError,KeyError):
                pass
            if not children or len(children)>contract.MAX_ITEMS:
                return True  # Missing/unbounded inventory cannot prove no owner.
            for child in children:
                self._identifier(child)
                if self._owned(child):return True
            return False
        except (ValueError,OSError,sqlite3.Error,TypeError):
            return True

    def _terminal(self,db,batch,item,status,error):
        if status == 'cancelled' and item['phase'] in {'pending','prepared'}:
            # This state has not claimed execution. A queued callback will see
            # the durable cancellation before its started compare-and-swap.
            try:
                row=json.loads(self.host._job_path(item['child_id']).read_bytes())
                if self._pristine(row):
                    self.host._save(row,status='cancelled',stage='cancelled',error='Cancelled before execution; no model call was started.')
            except (OSError,ValueError):
                pass
        db.execute('UPDATE items SET phase=?,outcome=?,error=? WHERE batch_id=? AND item_key=?',
                   ('terminal',status,error,batch['id'],item['item_key']))
        db.execute('UPDATE batches SET updated_at=? WHERE id=?',(now(),batch['id']))

    def _claim_and_run(self,row):
        run = False
        try:
            with self._db(write=True) as db:
                db.execute('BEGIN IMMEDIATE')
                binding = row['batch']
                batch,preview,items = self._records(binding['batch_id'],db)
                item = next(value for value in items if value['child_id']==row['id'])
                if (item['phase']=='prepared' and batch['desired']=='running' and not item['cancelled']
                        and self._raw_child(batch,preview,item)==row and self._pristine(row)
                        and preview['policy_sha256']==policy_sha256()):
                    db.execute('UPDATE items SET phase=? WHERE batch_id=? AND item_key=? AND phase=?',
                               ('started',batch['id'],item['item_key'],'prepared'))
                    run = True
            if run:
                self.host._run(row)  # Shared pipeline owns lease/capacity cleanup.
        finally:
            if not run:
                self.host._release_prepared(row['id'])
            self.wake.set()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(5)
