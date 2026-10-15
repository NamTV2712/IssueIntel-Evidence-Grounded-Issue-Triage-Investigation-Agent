"""Local product jobs calling the shared read-only investigation pipeline."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sqlite3
import threading
import time
import uuid

from issueintel.benchmarks.dev_context import accepted_context, public_catalog
from issueintel.repository.acquire import acquire_repository, fetch_github_issue
from issueintel.integrations.github import github_input, comment_preview, post_reviewed_comment, signed_command
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest, write_json
from issueintel.retrieval.related import rank_related
from .pipeline import investigate, export_pack
from .comparison import compare_packs
from .intake import inspect_issue
from .plan import PlanStore
from .stages import StageCheckpoints, STAGES
from .providers import GroqProvider, ProviderError
from issueintel.runtime.budget import BudgetDenied


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_request(request):
    allowed = {'mode', 'case_id', 'repository', 'revision', 'issue_text', 'provider', 'depth', 'issue_url', 'parent_job_id', 'additional_context'}
    if not isinstance(request, dict) or set(request)-allowed:
        raise ValueError('Unsupported investigation fields')
    mode = request.get('mode', 'local')
    provider = request.get('provider', 'none')
    if request.get('depth', 'quick') not in {'quick', 'deep'}:
        raise ValueError('Choose quick or deep investigation')
    if mode not in {'local', 'main_dev', 'example', 'github', 'followup'} or provider not in {'none', 'groq'}:
        raise ValueError('Choose a supported mode and provider')
    if mode != 'followup' and set(request) & {'parent_job_id', 'additional_context'}:
        raise ValueError('Follow-up fields require a follow-up job')
    if mode == 'followup':
        if set(request)-{'mode', 'provider', 'depth', 'parent_job_id', 'additional_context'}:
            raise ValueError('A follow-up cannot override its source or revision')
        if not isinstance(request.get('parent_job_id'), str) or not re.fullmatch(r'[a-f0-9]{24}', request['parent_job_id']):
            raise ValueError('Choose a completed parent investigation')
        text = request.get('additional_context')
        if not isinstance(text, str) or not text.strip() or len(text)>20000 or '\x00' in text:
            raise ValueError('Provide bounded additional context')
    elif mode == 'main_dev':
        if set(request) & {'repository', 'revision', 'issue_text', 'issue_url'}:
            raise ValueError('A Main Dev query and revision cannot be overridden')
        if not isinstance(request.get('case_id'), str) or len(request['case_id']) > 200:
            raise ValueError('Select a Main Dev case')
    elif mode == 'example':
        if set(request) & {'repository', 'revision', 'issue_text', 'case_id', 'issue_url'}:
            raise ValueError('The example has fixed synthetic input')
    elif mode == 'github':
        if set(request) & {'repository', 'issue_text', 'case_id'}:
            raise ValueError('GitHub input cannot override issue identity or text')
        github_input(request.get('issue_url', ''))
        revision = request.get('revision', 'HEAD')
        if not isinstance(revision, str) or not revision.strip() or len(revision) > 200 or any(ord(c) < 32 for c in revision):
            raise ValueError('Choose a concrete Git revision or HEAD')
        request = dict(request, revision=revision)
    else:
        if 'issue_url' in request:
            raise ValueError('A local issue cannot impersonate a GitHub target')
        if 'case_id' in request:
            raise ValueError('Custom input cannot impersonate a benchmark case')
        for key, maximum in [('repository', 2000), ('revision', 200), ('issue_text', 100000)]:
            value = request.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
                raise ValueError(f'Invalid or missing {key}')
        if any(ord(c) < 32 for c in request['revision']):
            raise ValueError('Invalid revision')
    return dict(request, mode=mode, provider=provider, depth=request.get('depth', 'quick'))


class Workbench:
    def __init__(self, root, *, provider_factory=GroqProvider):
        self.root = Path(root).resolve()
        self.directory = self.root/'artifacts/demo'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.jobs = self.directory/'jobs'
        self.jobs.mkdir(exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='issueintel-demo')
        self.capacity = threading.BoundedSemaphore(4)
        self.provider_factory = provider_factory
        self.lock = threading.RLock()
        self.active = set()
        self.cancelled = set()
        self.plans = PlanStore(self.directory/'plans.sqlite')
        from issueintel.integrations.github_sync import GitHubSync
        self.github_sync = GitHubSync(self.root)
        # Reconcile changes that might have occurred while the local server was off.
        for connection in self.github_sync.connections():
            if not connection['paused'] and connection['phase']=='ready':
                self.github_sync.change(connection['repository'], phase='initial', initial_started=False)
        self.sync_stop = threading.Event()
        self.sync_thread = threading.Thread(target=self._auto_sync_loop, name='issueintel-sync-scheduler', daemon=True)
        self.sync_thread.start()

    def _auto_sync_loop(self):
        while not self.sync_stop.is_set():
            try:
                for connection in self.github_sync.due():
                    with self.lock:
                        if connection.get('job_id') in self.active:
                            continue
                        request = {'mode':'repository', 'provider':'none', 'depth':'index', 'automatic':True,
                                   'repository':connection['repository'], 'revision':connection['revision']}
                        job = self._enqueue(request, self._run_automatic_sync)
                        self.github_sync.change(connection['repository'], job_id=job['id'], scheduled_at=time.time())
                    break  # Fair batches share the bounded queue with investigations.
            except (RuntimeError, ValueError, OSError, sqlite3.DatabaseError):
                pass  # Queue/lock pressure must not discard durable pending work.
            self.sync_stop.wait(1)

    def _run_automatic_sync(self, row):
        from issueintel.integrations.github_sync import SyncServerStopping
        repository = row['request']['repository']
        try:
            self._save(row, status='running', stage='repository_sync')
            def check():
                if self.sync_stop.is_set():
                    raise SyncServerStopping()
                self._check_cancelled(row['id'])
            report = self.github_sync.batch(repository, check_cancelled=check)
            self._save(row, status='completed', stage='completed',
                       sync_report={k:v for k,v in report.items() if k != 'tasks'} if report else None,
                       connection=self.github_sync.connection(repository))
        except InterruptedError:
            self._save(row, status='cancelled', stage='cancelled')
        except SyncServerStopping:
            self._save(row, status='interrupted', stage='interrupted')
        except Exception as error:
            self._save(row, status='failed', stage='failed', error=f'Automatic sync stopped ({type(error).__name__}).')
        finally:
            with self.lock:
                self.active.discard(row['id']); self.cancelled.discard(row['id'])
            self.capacity.release()

    def connect_repository(self, request):
        if not isinstance(request, dict) or set(request)-{'repository','revision'} or 'repository' not in request:
            raise ValueError('Invalid automatic connection input')
        return self.github_sync.connect(request['repository'], revision=request.get('revision','HEAD'))

    def repository_connection(self, request, *, pause=False):
        from issueintel.integrations.github_index import RepositoryIndex
        allowed = {'repository','paused'} if pause else {'repository'}
        if not isinstance(request, dict) or set(request)-allowed or 'repository' not in request:
            raise ValueError('Invalid repository connection input')
        source = RepositoryIndex(self.root, request['repository'])
        if pause:
            if type(request.get('paused')) is not bool:
                raise ValueError('Choose a pause state')
            if request['paused']:
                connection = self.github_sync.change(source.repository, paused=True, next_attempt=0)
            else:
                current = self.github_sync.connection(source.repository)
                if not current:
                    raise ValueError('Repository has not been connected')
                connection = self.github_sync.connect(source.repository, revision=current['revision'])
        else:
            connection = self.github_sync.connection(source.repository)
        return {'connection':connection, 'index':source.status(),
                'webhook_configured':bool(os.environ.get('GITHUB_WEBHOOK_SECRET'))}

    def catalog(self):
        return public_catalog(self.root)

    def _job_path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{24}', identifier):
            raise ValueError('Invalid job ID')
        return self.jobs/(identifier+'.json')

    def job(self, identifier):
        with self.lock:
            row = json.loads(self._job_path(identifier).read_bytes())
            if row['status'] in {'queued', 'running'} and identifier not in self.active:
                row = dict(row, status='interrupted', stage='interrupted',
                           error='Interrupted before completion; no automatic model retry.')
            return {k: v for k, v in row.items() if k != 'request'}

    def history(self):
        return self._history_mode(False, 50)

    def _history_mode(self, repository, limit):
        paths = sorted(self.jobs.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        rows = []
        for path in paths:
            row = self.job(path.stem)
            if row.get('mode') == 'version_comparison':
                continue
            if (row.get('mode') == 'repository') == repository:
                rows.append(row)
                if len(rows) >= limit:
                    break
        return rows

    def submit(self, request):
        request = validate_request(request)
        return self._enqueue(request, self._run)

    def _enqueue(self, request, callback):
        if not self.capacity.acquire(blocking=False):
            raise RuntimeError('The investigation queue is full')
        identifier = uuid.uuid4().hex[:24]
        row = {'id': identifier, 'status': 'queued', 'stage': 'queued', 'created_at': now(),
               'mode': request['mode'], 'provider': request['provider'], 'depth': request['depth'], 'request': request}
        if request['mode'] == 'repository':
            row['repository'] = request['repository']
        with self.lock:
            write_json(self._job_path(identifier), row)
            self.active.add(identifier)
        try:
            self.executor.submit(callback, row)
        except BaseException:
            with self.lock:
                self.active.discard(identifier)
            self.capacity.release()
            raise
        return self.job(identifier)

    def repository_history(self):
        return self._history_mode(True, 20)

    def submit_versions(self, request):
        from issueintel.investigation.versions import product_version_source
        from issueintel.repository.acquire import _validate_revision
        if not isinstance(request, dict) or set(request) != {'job_id', 'revision'}:
            raise ValueError('Choose a product investigation and target revision')
        revision = _validate_revision(request['revision'])
        pack, raw, _ = self._load_pack(request['job_id'])
        product_version_source(pack)  # Reject benchmark/follow-up before Git or queue.
        return self._enqueue({'mode':'version_comparison', 'provider':'none', 'depth':'versions',
                              'source_job_id':request['job_id'], 'source_pack_sha256':hashlib.sha256(raw).hexdigest(),
                              'revision':revision}, self._run_versions)

    def versions_history(self, identifier):
        self._load_pack(identifier)
        rows = []
        for path in sorted(self.jobs.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True):
            row = json.loads(path.read_bytes())
            if row.get('mode') == 'version_comparison' and row.get('request',{}).get('source_job_id') == identifier:
                rows.append(self.job(row['id']))
                if len(rows) == 20:
                    break
        return rows

    def versions_bytes(self, identifier):
        row = self.job(identifier)
        if row['mode'] != 'version_comparison' or row['status'] != 'completed':
            raise ValueError('Version comparison is not complete')
        ref = row['result']['versions.json']
        expected = (self.directory/'runs'/identifier/'versions.json').resolve()
        path = (self.root/ref['path']).resolve()
        if path != expected or not path.is_relative_to(self.directory.resolve()):
            raise ValueError('Version export path changed')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('Version export integrity changed')
        result = json.loads(raw)
        _, source_raw, _ = self._load_pack(result['source']['job_id'])
        if hashlib.sha256(source_raw).hexdigest() != result['source']['pack_sha256']:
            raise ValueError('Version comparison source changed')
        return raw

    def _run_versions(self, row):
        from issueintel.investigation.versions import compare_versions, product_version_source
        from issueintel.repository.snapshot import build_snapshot
        try:
            self._check_cancelled(row['id'])
            self._save(row, status='running', stage='compare_versions')
            request = row['request']
            pack, raw, _ = self._load_pack(request['source_job_id'])
            product_version_source(pack)
            if hashlib.sha256(raw).hexdigest() != request['source_pack_sha256']:
                raise ValueError('Source pack changed while queued')
            local = Path(pack['snapshot']['repository'])
            before = build_snapshot(local, pack['snapshot']['commit_sha'])
            compare_versions(pack, before, before)  # Replay source bytes before any target fetch.
            self._check_cancelled(row['id'])
            # Only the original explicitly remote input may fetch a named ref.
            source_request = json.loads(self._job_path(request['source_job_id']).read_bytes())['request']
            if source_request['mode'] == 'github' or (source_request['mode'] == 'local' and not Path(source_request['repository']).is_dir()):
                acquired = acquire_repository(pack['case']['repository'], request['revision'], self.root/'data/repos')
                if acquired.path.resolve() != local.resolve():
                    raise ValueError('Target acquisition differs from source repository')
                revision = acquired.commit_sha
            else:
                revision = request['revision']
            self._check_cancelled(row['id'])
            after = build_snapshot(local, revision)
            result = compare_versions(pack, before, after)
            result['source'] = {'job_id':request['source_job_id'], 'pack_sha256':request['source_pack_sha256']}
            result['requested_revision'] = request['revision']
            result.pop('sha256')
            result['sha256'] = digest(result)
            self._check_cancelled(row['id'])
            path = self.directory/'runs'/row['id']/'versions.json'
            write_json(path, result)
            self._save(row, status='completed', stage='completed',
                       source_job_id=request['source_job_id'], target_commit=after.commit_sha,
                       result={'versions.json':{'path':path.relative_to(self.root).as_posix(),
                                               'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}})
        except InterruptedError:
            self._save(row, status='cancelled', stage='cancelled')
        except Exception as error:
            self._save(row, status='failed', stage='failed', error=f'Version comparison failed ({type(error).__name__}).')
        finally:
            with self.lock:
                self.active.discard(row['id']); self.cancelled.discard(row['id'])
            self.capacity.release()

    def repository_read(self, action, request):
        from issueintel.integrations.github_index import RepositoryIndex
        fields = {'status': {'repository'}, 'search': {'repository','query','code','limit'},
                  'item': {'repository','key'}}
        if action not in fields or not isinstance(request, dict) or set(request)-fields[action] or 'repository' not in request:
            raise ValueError('Invalid repository index input')
        index = RepositoryIndex(self.root, request['repository'])
        if action == 'status':
            return index.status()
        if action == 'item':
            return index.item(request.get('key'))
        if type(request.get('code', False)) is not bool:
            raise ValueError('Choose text or code search')
        search = index.search_code if request.get('code') else index.search
        return search(request.get('query'), limit=request.get('limit', 10))

    def submit_repository(self, request):
        from issueintel.repository.acquire import parse_github_reference, _validate_revision
        if (not isinstance(request, dict) or set(request)-{'repository','revision','max_requests','refresh'}
                or 'repository' not in request):
            raise ValueError('Invalid repository sync input')
        source = parse_github_reference(request['repository'])
        if source.issue_number is not None:
            raise ValueError('Select a repository, not an issue')
        revision = _validate_revision(request.get('revision', 'HEAD'))
        maximum, refresh = request.get('max_requests', 50), request.get('refresh', False)
        if type(maximum) is not int or not 1 <= maximum <= 500 or type(refresh) is not bool:
            raise ValueError('Invalid repository sync limits')
        return self._enqueue({'mode':'repository','provider':'none','depth':'index',
                              'repository':source.repository.lower(), 'revision':revision,
                              'max_requests':maximum, 'refresh':refresh}, self._run_repository)

    def _run_repository(self, row):
        from issueintel.integrations.github_index import RepositoryIndex
        request = row['request']
        index = None
        try:
            self._check_cancelled(row['id'])
            self._save(row, status='running', stage='repository_sync', repository=request['repository'])
            index = RepositoryIndex(self.root, request['repository'])
            def progress(report):
                self._save(row, sync_report={k:v for k,v in report.items() if k != 'tasks'})
            report = index.sync(revision=request['revision'], max_requests=request['max_requests'],
                                refresh=request['refresh'], on_progress=progress,
                                check_cancelled=lambda: self._check_cancelled(row['id']))
            self._save(row, status='completed', stage='completed',
                       sync_report={k:v for k,v in report.items() if k != 'tasks'})
        except InterruptedError:
            self._save(row, status='cancelled', stage='cancelled',
                       error='Repository sync paused; committed pages retained. Resume explicitly.')
        except Exception as error:
            self._save(row, status='failed', stage='failed',
                       error=f'Repository sync failed ({type(error).__name__}); committed pages retained, no automatic retry.')
        finally:
            with self.lock:
                self.active.discard(row['id'])
                self.cancelled.discard(row['id'])
            self.capacity.release()

    def _save(self, row, **changes):
        row.update(changes, updated_at=now())
        with self.lock:
            write_json(self._job_path(row['id']), row)

    def cancel(self, identifier):
        row = self.job(identifier)
        if row.get('connection') or (row['mode']=='repository' and row.get('repository') and self.github_sync.connection(row['repository'])):
            self.github_sync.change(row['repository'], paused=True)
        with self.lock:
            if identifier in self.active:
                self.cancelled.add(identifier)
        return self.job(identifier)

    def _check_cancelled(self, identifier):
        with self.lock:
            if identifier in self.cancelled:
                raise InterruptedError('Cancelled')

    def _example(self):
        path = self.directory/'example_repo'
        if not (path/'.git').is_dir():
            path.mkdir(exist_ok=True)
            (path/'parser.py').write_text('def parse_list(items):\n    return [item.strip() for item in items]\n', encoding='utf-8')
            (path/'formatting.py').write_text('def render(value):\n    return str(value)\n', encoding='utf-8')
            def git(*args):
                return subprocess.check_output(['git', '-c', 'core.hooksPath=', '-c', 'init.templateDir=',
                    '-C', str(path), *args], stderr=subprocess.PIPE, timeout=30).decode().strip()
            git('init', '-q')
            git('add', 'parser.py', 'formatting.py')
            git('-c', 'user.name=IssueIntel Example', '-c', 'user.email=example@issueintel.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic local example')
        revision = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], timeout=10).decode().strip()
        case = {'case_id': 'synthetic:parse-list', 'repository': 'synthetic/example', 'revision': revision,
                'problem_statement': 'parser.parse_list fails on None input. Inspect how missing values are handled.',
                'issue_created_at': None}
        return case, path

    def _run(self, row):
        try:
            self._check_cancelled(row['id'])
            self._save(row, status='running', stage='prepare_snapshot')
            request = row['request']
            extra = {}
            followup = None
            if request['mode'] == 'followup':
                parent, raw, depth = self._load_pack(request['parent_job_id'])
                if depth >= 8:
                    raise ValueError('Follow-up depth is bounded to eight generations')
                additional = request['additional_context']
                text = parent['case']['problem_statement']+'\n\n[User follow-up context; unverified]\n'+additional
                if len(text)>100000:
                    raise ValueError('Combined query exceeds the public input bound')
                case = {'case_id':'followup:'+row['id'], 'repository':parent['case']['repository'],
                        'revision':parent['snapshot']['commit_sha'], 'problem_statement':text,
                        'issue_created_at':None}
                if parent['case'].get('issue_url'):
                    case['issue_url'] = parent['case']['issue_url']
                local = Path(parent['snapshot']['repository'])
                followup = {'schema':'investigation_followup_v1', 'parent_job_id':request['parent_job_id'],
                            'parent_pack_sha256':hashlib.sha256(raw).hexdigest(),
                            'source_case_id':(parent.get('followup') or {}).get('source_case_id',parent['case']['case_id']),
                            'additional_context':additional, 'context_sha256':hashlib.sha256(additional.encode()).hexdigest(),
                            'created_at':now(), 'historical_query_verified':False, 'depth':depth+1}
            elif request['mode'] == 'main_dev':
                case, snapshot, query = accepted_context(self.root, request['case_id'])
                local = Path(snapshot.repository)
                extra = dict(snapshot=snapshot, query_receipt=query, protocol='issue_time')
            elif request['mode'] == 'example':
                case, local = self._example()
            elif request['mode'] == 'github':
                issue = fetch_github_issue(request['issue_url'])
                case = {'case_id':issue['url'], 'issue_url':issue['url'], 'repository':issue['repository'],
                        'revision':request['revision'], 'problem_statement':issue['problem_statement'],
                        'issue_created_at':issue['created_at']}
                local = acquire_repository(issue['repository'], case['revision'], self.root/'data/repos').path
                connection=self.github_sync.connection(issue['repository'])
                if connection and not connection['paused']:
                    from issueintel.integrations.github_index import RepositoryIndex
                    from issueintel.retrieval.repository_context import collect_context
                    extra['repository_context']=collect_context(RepositoryIndex(self.root,issue['repository']),
                                                                issue['url'],issue['problem_statement'])
            else:
                repository = request['repository'].strip()
                case = {'case_id': 'local:'+digest(request)[:16], 'repository': repository,
                        'revision': request['revision'].strip(), 'problem_statement': request['issue_text'],
                        'issue_created_at': None}
                local = Path(repository)
                if not local.is_dir():
                    local = acquire_repository(repository, case['revision'], self.root/'data/repos').path
            # No provider exists until the public/Main Dev boundary has replayed.
            config = RuntimeConfig.load(self.root) if request['provider'] == 'groq' else None
            context = ({'category': 'ARTIFACT_GENERATION_COST', 'reasoning_effort': config.reasoning_effort,
                        'stage': 'local_demo_investigation', 'diagnostic': False}
                       if config and config.reasoning_effort in {'medium', 'high'} else None)
            provider = (self.provider_factory(config, call_context=context)
                if request['provider'] == 'groq' else None)
            def complete(messages, max_output_tokens):
                self._check_cancelled(row['id'])
                answer = provider(messages, max_output_tokens)
                self._check_cancelled(row['id'])
                return answer
            def structured(messages, *, max_output_tokens, schema):
                self._check_cancelled(row['id'])
                answer = provider.complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
                self._check_cancelled(row['id'])
                return answer
            if provider and hasattr(provider, 'complete_structured'):
                complete.complete_structured = structured
            stage_store=StageCheckpoints(self.root,self.directory/'runs'/row['id']/'stages')
            def save_stage(stage,record):
                self._check_cancelled(row['id'])
                ref=stage_store(stage,record)
                self._save(row,stage=stage,stage_checkpoints=dict(stage_store.receipts))
                return ref
            self._save(row, stage='investigate')
            pack = investigate(case, local, complete_json=complete if provider else None, provider=request['provider'],
                model=provider.model if provider else 'none',
                max_output_tokens=provider.config.max_output_tokens if provider else 2048,
                top_k=4 if request['depth'] == 'quick' else 12,
                refinement_rounds=1 if request['depth']=='deep' else 0,on_stage=save_stage,**extra)
            self._check_cancelled(row['id'])
            if followup:
                pack['followup'] = followup
            directory = self.directory/'runs'/row['id']
            export_pack(pack, directory)
            refs = {name: {'path': (directory/name).relative_to(self.root).as_posix(),
                         'sha256': hashlib.sha256((directory/name).read_bytes()).hexdigest()}
                    for name in ['pack.json', 'report.md']}
            self._save(row, status='completed', stage='completed', run_id=pack['run_id'],
                       case_id=case['case_id'], strict_issue_time_verified=pack['leakage_audit']['strict_issue_time_verified'],
                       result=refs, stop_reason=pack['stop_reason'])
        except InterruptedError:
            self._save(row, status='cancelled', stage='cancelled', error='Cancelled. An in-flight API call may already have been charged.')
        except (BudgetDenied, ProviderError) as error:
            self._save(row, status='failed', stage='failed', error=str(error)+'; no automatic retry.')
        except Exception as error:
            # Source text, response bodies, paths and credentials stay out of errors.
            self._save(row, status='failed', stage='failed', error=f'Investigation failed ({type(error).__name__}). Check input and local data integrity.')
        finally:
            with self.lock:
                self.active.discard(row['id'])
                self.cancelled.discard(row['id'])
            self.capacity.release()

    def result_bytes(self, identifier, name='pack.json'):
        if name not in {'pack.json', 'report.md'}:
            raise ValueError('Unsupported export')
        _, raw, _ = self._load_pack(identifier)
        return raw if name == 'pack.json' else self._export_bytes(identifier, name)

    def _export_bytes(self, identifier, name='pack.json'):
        if name not in {'pack.json', 'report.md'}:
            raise ValueError('Unsupported export')
        row = self.job(identifier)
        if row['status'] != 'completed':
            raise ValueError('Investigation is not complete')
        ref = row['result'][name]
        expected = (self.directory/'runs'/identifier/name).resolve()
        path = (self.root/ref['path']).resolve()
        if path != expected or not path.is_relative_to(self.directory.resolve()):
            raise ValueError('Export path changed')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('Export integrity changed')
        return raw

    def stage_bytes(self,identifier,stage):
        if stage not in STAGES:
            raise ValueError('Unsupported investigation stage')
        row=self.job(identifier)
        ref=row.get('stage_checkpoints',{}).get(stage)
        if not ref:
            raise FileNotFoundError('No completed checkpoint for this stage')
        expected=(self.directory/'runs'/identifier/'stages'/(stage+'.json')).resolve()
        path=(self.root/ref['path']).resolve()
        if path!=expected or not path.is_relative_to(self.directory.resolve()):
            raise ValueError('Stage checkpoint path changed')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:
            raise ValueError('Stage checkpoint integrity changed')
        return raw

    def _load_pack(self, identifier, seen=None):
        seen = set() if seen is None else seen
        if identifier in seen or len(seen)>8:
            raise ValueError('Invalid follow-up lineage')
        seen.add(identifier)
        raw = self._export_bytes(identifier)
        pack = json.loads(raw)
        link = pack.get('followup')
        if not link:
            return pack, raw, 0
        parent, parent_raw, depth = self._load_pack(link['parent_job_id'], seen)
        additional = link['additional_context']
        if (link.get('schema') != 'investigation_followup_v1' or link['depth'] != depth+1
                or hashlib.sha256(parent_raw).hexdigest() != link['parent_pack_sha256']
                or hashlib.sha256(additional.encode()).hexdigest() != link['context_sha256']
                or link['source_case_id'] != (parent.get('followup') or {}).get('source_case_id',parent['case']['case_id'])
                or pack['case']['problem_statement'] != parent['case']['problem_statement']+'\n\n[User follow-up context; unverified]\n'+additional
                or pack['case']['repository'] != parent['case']['repository']
                or pack['snapshot']['commit_sha'] != parent['snapshot']['commit_sha']
                or pack['snapshot']['protocol'] != 'benchmark_base'
                or pack['leakage_audit']['strict_issue_time_verified'] is not False):
            raise ValueError('Follow-up source binding changed')
        return pack, raw, depth+1

    def intake(self, identifier):
        pack, raw, _ = self._load_pack(identifier)
        return dict(inspect_issue(pack['case']['problem_statement']),
                    source_pack_sha256=hashlib.sha256(raw).hexdigest(), job_id=identifier)

    def plan(self,identifier,request=None):
        pack, raw, _ = self._load_pack(identifier)
        args=(identifier,hashlib.sha256(raw).hexdigest(),pack['recommended_steps'])
        return self.plans.load(*args) if request is None else self.plans.update(*args,request)

    def _source_case(self, pack):
        # The entire chain was replayed by _load_pack before this traversal.
        while pack.get('followup'):
            pack = json.loads(self._export_bytes(pack['followup']['parent_job_id']))
        return pack['case']

    @staticmethod
    def _issue_identity(case):
        return (case['repository'], hashlib.sha256(case['problem_statement'].encode()).hexdigest()
                if case['case_id'].startswith('local:') else case['case_id'])

    def related(self, request):
        if not isinstance(request,dict) or set(request)!={'job_id'}:
            raise ValueError('Choose a saved investigation')
        selected, raw, _ = self._load_pack(request['job_id'])
        source = self._source_case(selected)
        seen = {self._issue_identity(source)}
        documents=[]; scanned=0; skipped=0
        for row in self.history():
            if row['status']!='completed' or row['id']==request['job_id']:
                continue
            scanned+=1
            try:
                candidate, candidate_raw, _ = self._load_pack(row['id'])
                original = self._source_case(candidate)
                identity = self._issue_identity(original)
                if original['repository'] != source['repository'] or identity in seen:
                    continue
                # Use the original issue query, never appended follow-up context
                # as if it were another independently reported issue.
                root_pack = candidate
                root_raw = candidate_raw
                root_id = row['id']
                while root_pack.get('followup'):
                    root_id = root_pack['followup']['parent_job_id']
                    root_raw = self._export_bytes(root_id)
                    root_pack = json.loads(root_raw)
                text=original['problem_statement']
                documents.append({'job_id':root_id,'case_id':original['case_id'],
                    'repository':original['repository'],'text':text,
                    'query_sha256':hashlib.sha256(text.encode()).hexdigest(),
                    'pack_sha256':hashlib.sha256(root_raw).hexdigest()})
                seen.add(identity)
            except (ValueError,KeyError,TypeError,OSError):
                skipped+=1
        return {'schema':'local_related_issues_v1','job_id':request['job_id'],
                'source_pack_sha256':hashlib.sha256(raw).hexdigest(),
                'repository':source['repository'],
                'scope':'recent_50_local_jobs_same_repository',
                'scanned_completed':scanned,'distinct_other_issues':len(documents),
                'skipped_integrity':skipped,'minimum_shared_terms':2,
                'results':rank_related(selected['case']['problem_statement'],documents),
                'included_in_inference':False,'historical_availability_for_cutoff':'unverified',
                'duplicate_confirmed':False,'evaluation_performed':False}

    def compare(self, request):
        if not isinstance(request, dict) or set(request) != {'before_id', 'after_id'}:
            raise ValueError('Choose two completed investigations')
        if request['before_id'] == request['after_id']:
            raise ValueError('Choose two different investigations')
        loaded = [self._load_pack(request[key]) for key in ('before_id', 'after_id')]
        raw = [value[1] for value in loaded]
        views = [json.loads(value) for value in raw]
        # Identity comes only from replayed local lineage, not a client override.
        for view in views:
            if view.get('followup'):
                view['case']['case_id'] = view['followup']['source_case_id']
        result = compare_packs(*views)
        result['sources'] = {side: {'job_id': request[side+'_id'],
                                  'pack_sha256': hashlib.sha256(value).hexdigest()}
                             for side, value in zip(('before', 'after'), raw)}
        return result

    def preview_comment(self, identifier):
        if self.job(identifier)['mode'] != 'github':
            raise ValueError('Only an explicit GitHub investigation can prepare a comment')
        pack = json.loads(self.result_bytes(identifier))
        preview = dict(comment_preview(pack), job_id=identifier,
                       pack_sha256=hashlib.sha256(self.result_bytes(identifier)).hexdigest())
        path = self.directory/'runs'/identifier/'github_preview.json'
        with self.lock:
            write_json(path, preview)
        return preview

    def publish_comment(self, identifier, approval):
        if not isinstance(approval, dict) or set(approval) != {'approved_sha256', 'confirmation'} or approval['confirmation'] != 'publish_reviewed_comment':
            raise ValueError('Exact reviewed comment approval is required')
        if self.job(identifier)['mode'] != 'github':
            raise ValueError('Only an explicit GitHub investigation can publish')
        key = os.environ.get('GITHUB_TOKEN', '')
        if not key or any(ord(c) < 32 for c in key):
            raise ValueError('A local GitHub token is required')
        directory = self.directory/'runs'/identifier
        with self.lock:
            preview = json.loads((directory/'github_preview.json').read_bytes())
            if preview.get('job_id') != identifier or preview.get('pack_sha256') != hashlib.sha256(self.result_bytes(identifier)).hexdigest():
                raise ValueError('Reviewed source pack changed')
            actual = hashlib.sha256(preview['body'].encode()).hexdigest()
            if actual != approval['approved_sha256'] or actual != preview['body_sha256']:
                raise ValueError('Reviewed comment changed')
            intent_path = directory/'github_publish_intent.json'
            if intent_path.exists():
                raise ValueError('Publication already attempted; inspect the receipt before any retry')
            write_json(intent_path, {'job_id':identifier, 'body_sha256':actual, 'issue_url':preview['issue_url'],
                                     'started_at':now(), 'automatic_retry':False})
        try:
            receipt = post_reviewed_comment(preview, approved_sha256=approval['approved_sha256'])
        except Exception as error:
            write_json(directory/'github_publish_failure.json', {'error_type':type(error).__name__,
                'recorded_at':now(), 'publication_outcome':'unknown', 'automatic_retry':False})
            raise ValueError('Publication did not complete; inspect GitHub and the intent before retrying') from None
        write_json(directory/'github_publish_receipt.json', receipt)
        return receipt

    def receive_webhook(self, raw, *, signature, event, delivery, secret):
        from issueintel.integrations.repository_updates import authenticate
        payload = authenticate(raw, signature, delivery, secret)
        comment = payload.get('comment', {})
        if not (event == 'issue_comment' and isinstance(comment.get('body'), str)
                and comment['body'].startswith('/issueintel investigate')):
            return self.github_sync.receive(raw, signature=signature, event=event, delivery=delivery, secret=secret)
        request = signed_command(raw, signature=signature, event=event, delivery=delivery, secret=secret)
        directory = self.directory/'github_deliveries'; directory.mkdir(exist_ok=True)
        path = directory/(delivery+'.json')
        fingerprint = hashlib.sha256(raw).hexdigest()
        with self.lock:
            if path.exists():
                previous = json.loads(path.read_bytes())
                if previous['payload_sha256'] != fingerprint or not previous.get('job_id'):
                    raise ValueError('Delivery identity changed or needs recovery')
                identifier = previous['job_id']
            else:
                write_json(path, {'payload_sha256':fingerprint, 'recorded_at':now(), 'job_id':None})
                identifier = None
        if identifier:
            return self.job(identifier)
        job = self.submit(request)
        with self.lock:
            write_json(path, {'payload_sha256':fingerprint, 'recorded_at':now(), 'job_id':job['id']})
        return job

    def close(self):
        self.sync_stop.set()
        self.sync_thread.join(5)
        self.executor.shutdown(wait=True)
