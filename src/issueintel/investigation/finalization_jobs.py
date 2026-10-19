"""Explicit local export recovery from two immutable, already paid checkpoints."""
import hashlib
import json
import sqlite3
import uuid

from issueintel.runtime.io import digest
from .finalization import build_finalized_pack, bind_source_request, replay_checkpoints
from .resume import resume_policy
from .pipeline import export_pack


_REQUEST = {'mode','provider','depth','source_job_id','source_request_sha256',
            'verification_input_sha256','verification_sha256','policy_sha256'}


class FinalizationJobs:
    def __init__(self, host):
        self.host = host

    def _db(self):
        db = sqlite3.connect(self.host.directory/'finalizations.sqlite', timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents (source_job TEXT PRIMARY KEY, request_sha TEXT NOT NULL, '
                   'input_sha TEXT NOT NULL, verification_sha TEXT NOT NULL, target_job TEXT NOT NULL UNIQUE)')
        return db

    def _source(self, identifier, *, admission):
        host = self.host
        row = host.job(identifier)
        saved = json.loads(host._job_path(identifier).read_bytes())
        request = saved['request']
        if (type(request) is not dict or saved['id'] != identifier or row['mode'] != request.get('mode')
                or row['provider'] != request.get('provider') or row['depth'] != request.get('depth')
                or row['mode'] not in {'local','github','example'} or row['provider'] != 'groq'
                or row['depth'] not in {'quick','deep'} or row['status'] not in {'failed','interrupted','cancelled'}
                or not {'verification_input','verification'} <= set(row.get('stage_checkpoints',{}))):
            raise ValueError('No public export finalization is available')
        input_raw = host.stage_bytes(identifier,'verification_input')
        verification_raw = host.stage_bytes(identifier,'verification')
        inputs, verified = json.loads(input_raw), json.loads(verification_raw)
        prepared, _ = replay_checkpoints(inputs, verified, expected_policy=resume_policy() if admission else None)
        bind_source_request(request, prepared, refinement_rounds=inputs['refinement_rounds'])
        binding = {'source_job_id':identifier,'source_request_sha256':digest(request),
                   'verification_input_sha256':hashlib.sha256(input_raw).hexdigest(),
                   'verification_sha256':hashlib.sha256(verification_raw).hexdigest(),
                   'policy_sha256':inputs['payload']['policy_sha256']}
        return inputs, verified, binding

    def preview(self, identifier):
        try:
            inputs, verified, binding = self._source(identifier, admission=True)
        except (ValueError,KeyError,TypeError,AttributeError,OSError):
            return {'source_job_id':identifier,'available':False,'reason':'no_valid_product_export_checkpoints'}
        return {'source_job_id':identifier,'available':True,
                'verification_input_sha256':binding['verification_input_sha256'],
                'verification_sha256':binding['verification_sha256'],
                'model':inputs['model'],'commit_sha':inputs['commit_sha'],'model_calls':0,'no_api_required':True,
                'generation_reused':True,'verification_reused':True,'full_git_replay_performed':False,
                'automatic_retry':False}

    def _assert_intent(self, target, binding):
        db = self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            intent = db.execute('SELECT request_sha,input_sha,verification_sha,target_job FROM intents WHERE source_job=?',
                                (binding['source_job_id'],)).fetchone()
            db.commit()
        finally:
            db.close()
        expected = (binding['source_request_sha256'],binding['verification_input_sha256'],binding['verification_sha256'],target)
        if intent != expected:
            raise ValueError('Finalization source or durable intent changed')

    def submit(self, identifier, request):
        if (type(request) is not dict or set(request) != {'verification_input_sha256','verification_sha256','confirmation'}
                or request['confirmation'] != 'finalize_saved_checkpoints_without_api'):
            raise ValueError('Explicit checkpoint-bound finalization is required')
        inputs, verified, binding = self._source(identifier, admission=True)
        if any(request[key] != binding[key] for key in ('verification_input_sha256','verification_sha256')):
            raise ValueError('Finalization checkpoints changed')
        db = self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT request_sha,input_sha,verification_sha,target_job FROM intents WHERE source_job=?',
                                  (identifier,)).fetchone()
            if existing:
                if existing[:3] != (binding['source_request_sha256'],binding['verification_input_sha256'],binding['verification_sha256']):
                    raise ValueError('Finalization source changed')
                return self.host.job(existing[3])
            target = uuid.uuid4().hex[:24]
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?)', (identifier,binding['source_request_sha256'],
                       binding['verification_input_sha256'],binding['verification_sha256'],target))
            job = self.host._enqueue(dict(binding,mode='finalization',provider='none',depth='export'), self._run, identifier=target)
            db.commit()
            return job
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _bound_pack(self, request, *, admission):
        if (type(request) is not dict or set(request) != _REQUEST or request['mode'] != 'finalization'
                or request['provider'] != 'none' or request['depth'] != 'export'):
            raise ValueError('Finalization job authority changed')
        inputs, verified, binding = self._source(request['source_job_id'], admission=admission)
        if digest(binding) != digest({key:request[key] for key in binding}):
            raise ValueError('Finalization source request or checkpoints changed')
        return build_finalized_pack(inputs,verified, **{key:binding[key] for key in binding if key != 'policy_sha256'},
                                    expected_policy=resume_policy() if admission else None)

    def validate_child(self, identifier, pack, request):
        self._assert_child_authority(identifier, request)
        expected = self._bound_pack(request, admission=False)
        self._assert_intent(identifier, request)
        if digest(pack) != digest(expected):
            raise ValueError('Finalized child differs from its immutable saved stages')
        return pack

    def _assert_child_authority(self, identifier, request):
        saved = json.loads(self.host._job_path(identifier).read_bytes())
        if (saved.get('id') != identifier or saved.get('mode') != 'finalization'
                or saved.get('provider') != 'none' or saved.get('depth') != 'export'
                or digest(saved.get('request')) != digest(request)):
            raise ValueError('Saved finalization child authority changed')

    def _run(self, row):
        host = self.host
        try:
            host._check_cancelled(row['id'])
            self._assert_intent(row['id'],row['request'])  # Wait for dispatch transaction.
            self._assert_child_authority(row['id'],row['request'])
            host._save(row,status='running',stage='replay_saved_checkpoints')
            pack = self._bound_pack(row['request'], admission=True)
            host._check_cancelled(row['id'])
            directory = host.directory/'runs'/row['id']
            if directory.exists():
                raise ValueError('Finalized child exports must not be overwritten')
            self._assert_child_authority(row['id'],row['request'])
            export_pack(pack,directory)
            host._check_cancelled(row['id'])
            # Recheck exact saved source immediately before publishing the child.
            self.validate_child(row['id'],pack,row['request'])
            refs = {name:{'path':(directory/name).relative_to(host.root).as_posix(),
                          'sha256':hashlib.sha256((directory/name).read_bytes()).hexdigest()}
                    for name in ('pack.json','report.md')}
            host._save(row,status='completed',stage='completed',result=refs,run_id=pack['run_id'],
                       case_id=pack['case']['case_id'],strict_issue_time_verified=False,
                       stop_reason=pack['stop_reason'],model_calls=0,no_api_required=True)
        except InterruptedError:
            host._save(row,status='cancelled',stage='cancelled')
        except Exception as error:
            host._save(row,status='failed',stage='failed',error=f'Export finalization failed ({type(error).__name__}); no API call or automatic retry.')
        finally:
            with host.lock:
                host.active.discard(row['id'])
                host.cancelled.discard(row['id'])
            host.capacity.release()
