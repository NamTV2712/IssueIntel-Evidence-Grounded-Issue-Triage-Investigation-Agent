"""Explicit, receipted adaptive question jobs over immutable product reports."""
import hashlib
import json
import re
import sqlite3
import uuid

from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest, write_json
from .comparison import compare_packs
from .pipeline import validate_prepared_evidence
from .adaptive_clarifications import build_source, generate_questions, validate_bundle


def replay_snapshot(pack):
    """Replay real Git bytes before constructing a provider, never execute code."""
    compare_packs(pack,pack)
    snapshot=build_snapshot(pack['snapshot']['repository'],pack['snapshot']['commit_sha'])
    descriptor=snapshot.to_dict()
    descriptor['files']=[{key:value for key,value in file.to_dict().items() if key!='text'} for file in snapshot.files]
    if digest(descriptor)!=digest(pack['snapshot']):
        raise ValueError('Question source snapshot no longer replays')
    validate_prepared_evidence(pack['case'],[e for e in pack['evidence'] if e['kind']!='repository_context'],snapshot)
    return snapshot


class QuestionJobs:
    def __init__(self,workbench):
        self.host=workbench

    def _db(self):
        db=sqlite3.connect(self.host.directory/'adaptive_questions.sqlite',timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents (source_job TEXT NOT NULL, source_sha TEXT NOT NULL, target_job TEXT NOT NULL UNIQUE, PRIMARY KEY(source_job,source_sha))')
        return db

    def source(self,identifier,expected=None):
        pack,raw,depth=self.host._load_pack(identifier)
        self.host._clarification_product(identifier,pack)
        sha=hashlib.sha256(raw).hexdigest()
        if depth>=8 or (expected is not None and sha!=expected):
            raise ValueError('Question source changed or generation limit reached')
        return pack,build_source(pack,identifier,sha)

    def submit(self,request):
        if (not isinstance(request,dict) or set(request)!={'source_job_id','source_pack_sha256','confirmation'}
                or request['confirmation']!='generate_questions_with_api_cost'
                or not isinstance(request['source_job_id'],str) or not re.fullmatch(r'[a-f0-9]{24}',request['source_job_id'])
                or not isinstance(request['source_pack_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',request['source_pack_sha256'])):
            raise ValueError('Explicit source-bound question generation is required')
        self.source(request['source_job_id'],request['source_pack_sha256'])
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            saved=db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',
                             (request['source_job_id'],request['source_pack_sha256'])).fetchone()
            if saved:return self.host.job(saved[0])
            target=uuid.uuid4().hex[:24]
            db.execute('INSERT INTO intents VALUES (?,?,?)',(request['source_job_id'],request['source_pack_sha256'],target))
            job=self.host._enqueue(dict(request,mode='adaptive_questions',provider='groq',depth='questions'),self._run,identifier=target)
            db.commit()
            return job
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def history(self,identifier):
        pack,raw,depth=self.host._load_pack(identifier)
        sha=hashlib.sha256(raw).hexdigest()
        try:
            self.host._clarification_product(identifier,pack)
            if depth>=8:raise ValueError('Generation limit reached')
            build_source(pack,identifier,sha)
            available=True;reason=None
        except (ValueError,KeyError,TypeError):
            available=False;reason='requires_product_report_with_competing_hypotheses'
        db=self._db()
        try:
            rows=db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',(identifier,sha)).fetchall()
        finally:db.close()
        return {'source_job_id':identifier,'source_pack_sha256':sha,'available':available,'reason':reason,
                'question_jobs':[self.host.job(row[0]) for row in rows]}

    def _raw(self,identifier):
        row=self.host.job(identifier)
        if row.get('mode')!='adaptive_questions' or row['status']!='completed':
            raise ValueError('Question generation has not completed')
        ref=row['question_result']
        expected=(self.host.directory/'question_runs'/identifier/'questions.json').resolve()
        path=(self.host.root/ref['path']).resolve()
        if path!=expected or not path.is_relative_to(self.host.directory.resolve()):
            raise ValueError('Question export path changed')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:
            raise ValueError('Question export integrity changed')
        return raw

    def bundle(self,identifier,*,expected_source=None):
        raw=self._raw(identifier)
        request=json.loads(self.host._job_path(identifier).read_bytes())['request']
        if (set(request)!={'source_job_id','source_pack_sha256','confirmation','mode','provider','depth'}
                or request['mode']!='adaptive_questions' or request['provider']!='groq' or request['depth']!='questions'
                or request['confirmation']!='generate_questions_with_api_cost'):
            raise ValueError('Question job authority changed')
        if expected_source is None:
            _,expected_source=self.source(request['source_job_id'],request['source_pack_sha256'])
        if (request['source_job_id']!=expected_source['source_job_id']
                or request['source_pack_sha256']!=expected_source['source_pack_sha256']):
            raise ValueError('Question bundle belongs to another source report')
        db=self._db()
        try:
            intent=db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',
                (request['source_job_id'],request['source_pack_sha256'])).fetchone()
        finally:db.close()
        if intent!=(identifier,):raise ValueError('Question generation intent changed')
        return validate_bundle(json.loads(raw),expected_source)

    def read_bytes(self,identifier):
        self.bundle(identifier)
        return self._raw(identifier)

    def _run(self,row):
        host=self.host
        try:
            host._check_cancelled(row['id'])
            db=self._db()
            try:
                db.execute('BEGIN IMMEDIATE')
                intent=db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',
                                 (row['request']['source_job_id'],row['request']['source_pack_sha256'])).fetchone()
                db.commit()
            finally:db.close()
            if intent!=(row['id'],):raise ValueError('Missing durable question generation intent')
            host._save(row,status='running',stage='prepare_questions')
            pack,source=self.source(row['request']['source_job_id'],row['request']['source_pack_sha256'])
            replay_snapshot(pack)
            host._check_cancelled(row['id'])
            config=RuntimeConfig.load(host.root)
            if config.model!='openai/gpt-oss-120b':raise ValueError('Question provider has no authorization')
            context=({'category':'ARTIFACT_GENERATION_COST','reasoning_effort':config.reasoning_effort,
                      'stage':'local_demo_adaptive_questions','diagnostic':False}
                     if config.reasoning_effort in {'medium','high'} else None)
            provider=host.provider_factory(config,call_context=context)
            if provider.model!=config.model:raise ValueError('Question generation model changed')
            if hasattr(provider,'on_receipt'):
                def capture(receipt):
                    row.setdefault('usage_receipts',{})[receipt['reservation_id']]=dict(receipt,stage='adaptive_questions')
                    host._save(row,usage_tracking=True,usage_receipts=row['usage_receipts'])
                provider.on_receipt=capture
                host._save(row,usage_tracking=True,usage_receipts={})
            def complete(messages,max_output_tokens):
                host._check_cancelled(row['id']);answer=provider(messages,max_output_tokens)
                host._check_cancelled(row['id']);return answer
            if hasattr(provider,'complete_structured'):
                def structured(messages,*,max_output_tokens,schema):
                    host._check_cancelled(row['id'])
                    answer=provider.complete_structured(messages,max_output_tokens=max_output_tokens,schema=schema)
                    host._check_cancelled(row['id']);return answer
                complete.complete_structured=structured
            host._save(row,stage='adaptive_questions')
            bundle=generate_questions(source,complete,model=provider.model,max_output_tokens=min(config.max_output_tokens,8192))
            host._check_cancelled(row['id'])
            path=host.directory/'question_runs'/row['id']/'questions.json'
            if path.exists():raise ValueError('Question artifacts must not be overwritten')
            write_json(path,bundle)
            host._save(row,status='completed',stage='completed',question_result={
                'path':path.relative_to(host.root).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
                source_job_id=source['source_job_id'],source_pack_sha256=source['source_pack_sha256'])
        except InterruptedError:
            host._save(row,status='cancelled',stage='cancelled',error='Cancelled; an in-flight API call may already have been charged.')
        except Exception as error:
            host._save(row,status='failed',stage='failed',error=f'Question generation failed ({type(error).__name__}); no automatic retry.')
        finally:
            with host.lock:
                host.active.discard(row['id']);host.cancelled.discard(row['id'])
            host.capacity.release()
