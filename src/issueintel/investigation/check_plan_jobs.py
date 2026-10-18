"""Explicit, receipted check proposals over immutable product investigations.

One durable intent per source pack prevents ambiguous submissions, restarts or
failed reads from creating replacement paid attempts. Reading/exporting a saved
plan validates its authority without reacquiring Git or constructing a provider.
"""
import hashlib
import json
import re
import sqlite3
import uuid

from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest, write_json
from .question_jobs import replay_snapshot
from .check_plans import build_source, generate_plan, validate_plan, render_markdown


CONFIRMATION = 'generate_check_plan_with_api_cost'
_REQUEST_KEYS = {'source_job_id', 'source_pack_sha256', 'confirmation'}


def _request(request, *, saved=False):
    keys = _REQUEST_KEYS | ({'mode', 'provider', 'depth'} if saved else set())
    if (not isinstance(request, dict) or set(request) != keys
            or request.get('confirmation') != CONFIRMATION
            or not isinstance(request.get('source_job_id'), str)
            or not re.fullmatch(r'[a-f0-9]{24}', request['source_job_id'])
            or not isinstance(request.get('source_pack_sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', request['source_pack_sha256'])
            or (saved and (request['mode'] != 'check_plan' or request['provider'] != 'groq'
                           or request['depth'] != 'checks'))):
        raise ValueError('Explicit source-bound check plan generation is required')
    return request


class CheckPlanJobs:
    def __init__(self, workbench):
        self.host = workbench

    def _db(self):
        db = sqlite3.connect(self.host.directory/'check_plans.sqlite', timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents (source_job TEXT NOT NULL, source_sha TEXT NOT NULL, target_job TEXT NOT NULL UNIQUE, PRIMARY KEY(source_job,source_sha))')
        return db

    def source(self, identifier, expected=None):
        pack, raw, _ = self.host._load_pack(identifier)
        self.host._clarification_product(identifier, pack)
        sha = hashlib.sha256(raw).hexdigest()
        if expected is not None and sha != expected:
            raise ValueError('Check plan source changed')
        return pack, build_source(pack, identifier, sha)

    def _intent(self, request, target):
        _request(request, saved=True)
        db = self._db()
        try:
            # A worker waits for the submitting transaction before checking.
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',
                (request['source_job_id'], request['source_pack_sha256'])).fetchone()
            db.commit()
        finally:
            db.close()
        if saved != (target,):
            raise ValueError('Missing durable check plan generation intent')

    def submit(self, request):
        _request(request)
        self.source(request['source_job_id'], request['source_pack_sha256'])
        db = self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',
                (request['source_job_id'], request['source_pack_sha256'])).fetchone()
            if saved:
                return self.host.job(saved[0])
            target = uuid.uuid4().hex[:24]
            db.execute('INSERT INTO intents VALUES (?,?,?)',
                (request['source_job_id'], request['source_pack_sha256'], target))
            job = self.host._enqueue(dict(request, mode='check_plan', provider='groq', depth='checks'),
                self._run, identifier=target)
            db.commit()
            return job
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def history(self, identifier):
        pack, raw, _ = self.host._load_pack(identifier)
        sha = hashlib.sha256(raw).hexdigest()
        try:
            self.host._clarification_product(identifier, pack)
            build_source(pack, identifier, sha)
            available, reason = True, None
        except (ValueError, KeyError, TypeError):
            available, reason = False, 'requires_product_report_with_competing_hypotheses'
        db = self._db()
        try:
            rows = db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',
                (identifier, sha)).fetchall()
        finally:
            db.close()
        return {'source_job_id': identifier, 'source_pack_sha256': sha,
                'available': available, 'reason': reason,
                'plan_jobs': [self.host.job(row[0]) for row in rows]}

    def _raw(self, identifier):
        row = self.host.job(identifier)
        if row.get('mode') != 'check_plan' or row['status'] != 'completed':
            raise ValueError('Check plan generation has not completed')
        ref = row['check_plan_result']
        expected = (self.host.directory/'check_plan_runs'/identifier/'check-plan.json').resolve()
        path = (self.host.root/ref['path']).resolve()
        if path != expected or not path.is_relative_to(self.host.directory.resolve()):
            raise ValueError('Check plan export path changed')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('Check plan export integrity changed')
        return raw

    def bundle(self, identifier):
        raw = self._raw(identifier)
        request = json.loads(self.host._job_path(identifier).read_bytes())['request']
        self._intent(request, identifier)
        _, source = self.source(request['source_job_id'], request['source_pack_sha256'])
        return validate_plan(json.loads(raw), source)

    def export(self, identifier, format='json'):
        if format not in {'json', 'md'}:
            raise ValueError('Choose JSON or Markdown check plan')
        bundle = self.bundle(identifier)
        # Serialize the value that was validated, avoiding a second disk read.
        content = (json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2)+'\n'
                   if format == 'json' else render_markdown(bundle))
        return content.encode('utf-8'), bundle['source']['report_sha256'], bundle['sha256']

    def _run(self, row):
        host = self.host
        try:
            host._check_cancelled(row['id'])
            saved = json.loads(host._job_path(row['id']).read_bytes())
            if (saved['request'] != row['request'] or saved['mode'] != 'check_plan'
                    or saved['provider'] != 'groq' or saved['depth'] != 'checks'):
                raise ValueError('Check plan job authority changed')
            self._intent(saved['request'], row['id'])
            host._save(row, status='running', stage='prepare_check_plan')
            pack, source = self.source(row['request']['source_job_id'], row['request']['source_pack_sha256'])
            replay_snapshot(pack)
            host._check_cancelled(row['id'])
            config = RuntimeConfig.load(host.root)
            if config.model != 'openai/gpt-oss-120b':
                raise ValueError('Check plan provider has no authorization')
            context = ({'category': 'ARTIFACT_GENERATION_COST', 'reasoning_effort': config.reasoning_effort,
                        'stage': 'local_demo_check_plan', 'diagnostic': False}
                       if config.reasoning_effort in {'medium', 'high'} else None)
            provider = host.provider_factory(config, call_context=context)
            if provider.model != config.model:
                raise ValueError('Check plan generation model changed')
            if hasattr(provider, 'on_receipt'):
                def capture(receipt):
                    row.setdefault('usage_receipts', {})[receipt['reservation_id']] = dict(receipt, stage='check_plan')
                    host._save(row, usage_tracking=True, usage_receipts=row['usage_receipts'])
                provider.on_receipt = capture
                host._save(row, usage_tracking=True, usage_receipts={})

            def admit_call():
                host._check_cancelled(row['id'])
                saved = json.loads(host._job_path(row['id']).read_bytes())
                if (saved['request'] != row['request'] or saved['mode'] != 'check_plan'
                        or saved['provider'] != 'groq' or saved['depth'] != 'checks'):
                    raise ValueError('Check plan job authority changed')
                self._intent(saved['request'], row['id'])
                _, current = self.source(row['request']['source_job_id'], row['request']['source_pack_sha256'])
                if digest(current) != digest(source):
                    raise ValueError('Check plan source changed during generation')

            def complete(messages, max_output_tokens):
                admit_call()
                answer = provider(messages, max_output_tokens)
                host._check_cancelled(row['id'])
                return answer
            if hasattr(provider, 'complete_structured'):
                def structured(messages, *, max_output_tokens, schema):
                    admit_call()
                    answer = provider.complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
                    host._check_cancelled(row['id'])
                    return answer
                complete.complete_structured = structured
            host._save(row, stage='generate_check_plan')
            bundle = generate_plan(source, complete, model=provider.model,
                                   max_output_tokens=min(config.max_output_tokens, 8192))
            # Source may have changed while the model request was in flight.
            admit_call()
            validate_plan(bundle, source)
            path = host.directory/'check_plan_runs'/row['id']/'check-plan.json'
            with host.lock:
                host._check_cancelled(row['id'])
                if path.exists():
                    raise ValueError('Check plan artifacts must not be overwritten')
                write_json(path, bundle)
                host._save(row, status='completed', stage='completed', check_plan_result={
                    'path': path.relative_to(host.root).as_posix(),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
                    source_job_id=source['source_job_id'], source_pack_sha256=source['source_pack_sha256'])
        except InterruptedError:
            host._save(row, status='cancelled', stage='cancelled',
                       error='Cancelled; an in-flight API call may already have been charged.')
        except Exception as error:
            host._save(row, status='failed', stage='failed',
                       error=f'Check plan generation failed ({type(error).__name__}); no automatic retry.')
        finally:
            with host.lock:
                host.active.discard(row['id'])
                host.cancelled.discard(row['id'])
            host.capacity.release()
