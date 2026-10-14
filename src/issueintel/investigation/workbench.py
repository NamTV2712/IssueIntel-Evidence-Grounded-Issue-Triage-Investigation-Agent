"""Local product jobs calling the shared read-only investigation pipeline."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import subprocess
import threading
import uuid

from issueintel.benchmarks.dev_context import accepted_context, public_catalog
from issueintel.repository.acquire import acquire_repository
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest, write_json
from .pipeline import investigate, export_pack
from .providers import GroqProvider, ProviderError
from issueintel.runtime.budget import BudgetDenied


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_request(request):
    allowed = {'mode', 'case_id', 'repository', 'revision', 'issue_text', 'provider', 'depth'}
    if not isinstance(request, dict) or set(request)-allowed:
        raise ValueError('Unsupported investigation fields')
    mode = request.get('mode', 'local')
    provider = request.get('provider', 'none')
    if request.get('depth', 'quick') not in {'quick', 'deep'}:
        raise ValueError('Choose quick or deep investigation')
    if mode not in {'local', 'main_dev', 'example'} or provider not in {'none', 'groq'}:
        raise ValueError('Choose a supported mode and provider')
    if mode == 'main_dev':
        if set(request) & {'repository', 'revision', 'issue_text'}:
            raise ValueError('A Main Dev query and revision cannot be overridden')
        if not isinstance(request.get('case_id'), str) or len(request['case_id']) > 200:
            raise ValueError('Select a Main Dev case')
    elif mode == 'example':
        if set(request) & {'repository', 'revision', 'issue_text', 'case_id'}:
            raise ValueError('The example has fixed synthetic input')
    else:
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
        paths = sorted(self.jobs.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]
        return [self.job(p.stem) for p in paths]

    def submit(self, request):
        request = validate_request(request)
        if not self.capacity.acquire(blocking=False):
            raise RuntimeError('The investigation queue is full')
        identifier = uuid.uuid4().hex[:24]
        row = {'id': identifier, 'status': 'queued', 'stage': 'queued', 'created_at': now(),
               'mode': request['mode'], 'provider': request['provider'], 'depth': request['depth'], 'request': request}
        with self.lock:
            write_json(self._job_path(identifier), row)
            self.active.add(identifier)
        try:
            self.executor.submit(self._run, row)
        except BaseException:
            with self.lock:
                self.active.discard(identifier)
            self.capacity.release()
            raise
        return self.job(identifier)

    def _save(self, row, **changes):
        row.update(changes, updated_at=now())
        with self.lock:
            write_json(self._job_path(row['id']), row)

    def cancel(self, identifier):
        self.job(identifier)
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
            if request['mode'] == 'main_dev':
                case, snapshot, query = accepted_context(self.root, request['case_id'])
                local = Path(snapshot.repository)
                extra = dict(snapshot=snapshot, query_receipt=query, protocol='issue_time')
            elif request['mode'] == 'example':
                case, local = self._example()
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
            self._save(row, stage='investigate')
            pack = investigate(case, local, complete_json=complete if provider else None, provider=request['provider'],
                model=provider.model if provider else 'none',
                max_output_tokens=provider.config.max_output_tokens if provider else 2048,
                top_k=4 if request['depth'] == 'quick' else 12, **extra)
            self._check_cancelled(row['id'])
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

    def close(self):
        self.executor.shutdown(wait=True)
