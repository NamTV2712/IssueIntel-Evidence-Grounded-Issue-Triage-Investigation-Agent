"""Owned, explicitly confirmed product-stage recovery. Never automatic retries."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import uuid

from issueintel.runtime.io import digest,canonical_json
from .pipeline import investigate,export_pack
from .stage_recovery import SCHEMA,StageTape,policy_sha256 as core_policy


MODES={'example','local','github'}
TERMINAL={'failed','cancelled','interrupted'}


def job_policy_sha256():
    package=Path(__file__).resolve().parents[1]
    names=('investigation/stage_jobs.py','investigation/workbench.py','investigation/finalization.py',
           'investigation/dossier.py','investigation/planned_continuations.py')
    return digest({'core':core_policy(),**{name:sha256((package/name).read_bytes()).hexdigest() for name in names}})


def bind_original(request,context):
    from .workbench import validate_request
    from .resume import validate_product_case
    from issueintel.integrations.github import github_input
    from .finalization import _snapshot_descriptor
    if (digest(validate_request(request))!=digest(request) or request['mode'] not in MODES
            or request.get('controller','rules')!='rules'):
        raise ValueError('Stage recovery requires canonical original product authority')
    case=context['case'];snapshot=context['snapshot'];options=context['options']
    validate_product_case(case,snapshot['protocol']);_snapshot_descriptor(snapshot)
    if any(k in case for k in ('split','instance_id')):raise ValueError('Benchmark metadata cannot enter product recovery')
    planned=request['depth']=='planned'
    expected={'provider':request['provider'],'model':'none' if request['provider']=='none' else 'openai/gpt-oss-120b',
        'top_k':4 if request['depth']=='quick' else 12,'refinement_rounds':int(request['depth']=='deep'),
        'resumable':not planned,'planned_workflow':planned,'independent_critic':True,
        'planner_policy':'bounded_planner_v3' if planned else 'bounded_planner_v1'}
    if (context['schema']!=SCHEMA or any(digest(options[k])!=digest(v) for k,v in expected.items())
            or set(options)!=set(expected)|{'repository_context','max_output_tokens'}
            or type(options['max_output_tokens']) is not int or not 0<options['max_output_tokens']<=65536):
        raise ValueError('Saved stage recipe differs from its original request')
    if request['mode']=='local':
        expected_case={'case_id':'local:'+digest(request)[:16],'repository':request['repository'].strip(),
            'revision':request['revision'].strip(),'problem_statement':request['issue_text'],'issue_created_at':None}
        if digest(case)!=digest(expected_case):raise ValueError('Local stage query differs from its owner')
    elif request['mode']=='example':
        if case!={'case_id':'synthetic:parse-list','repository':'synthetic/example','revision':snapshot['commit_sha'],
                'problem_statement':'parser.parse_list fails on None input. Inspect how missing values are handled.','issue_created_at':None}:
            raise ValueError('Example recovery identity changed')
    else:
        wanted=github_input(request['issue_url']);saved=github_input(case['issue_url'])
        if (wanted.repository.casefold()!=saved.repository.casefold() or wanted.issue_number!=saved.issue_number
                or case['case_id']!=saved.issue_url or case['repository'].casefold()!=saved.repository.casefold()
                or case['revision']!=request['revision']):raise ValueError('GitHub stage identity changed')


class NeedCall(Exception):pass


class ExecutionOwned(ValueError):pass


class StageRecoveryJobs:
    def __init__(self,host):self.host=host

    def lease(self,identifier,*,create=False):
        self.host._job_path(identifier)
        path=self.host.directory/'runs'/identifier/'execution-lease.sqlite'
        if create:path.parent.mkdir(parents=True,exist_ok=True)
        elif not path.is_file():raise ValueError('Missing durable execution lease')
        db=sqlite3.connect(path.as_uri()+('?mode=rwc' if create else '?mode=rw'),uri=True,timeout=0,check_same_thread=False)
        try:
            if create:db.execute('CREATE TABLE IF NOT EXISTS owner (id INTEGER)')
            db.execute('BEGIN IMMEDIATE')
        except sqlite3.Error as error:
            db.close()
            if getattr(error,'sqlite_errorcode',0)&255 in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}:
                raise ExecutionOwned('Source execution is still owned by another worker') from None
            raise ValueError('Cannot determine durable execution ownership') from None
        return db

    def externally_owned(self,identifier):
        self.host._job_path(identifier)
        if not (self.host.directory/'runs'/identifier/'execution-lease.sqlite').is_file():return False
        try:lease=self.lease(identifier)
        except ExecutionOwned:return True
        lease.close();return False

    def _db(self):
        db=sqlite3.connect(self.host.directory/'stage_recovery.sqlite',timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents (source_job TEXT PRIMARY KEY, binding_sha TEXT NOT NULL, '
            'target_job TEXT NOT NULL UNIQUE, binding BLOB NOT NULL, requires_api INTEGER NOT NULL)')
        return db

    def _response(self,identifier,request):
        return dict(self.host.job(identifier),stage_recovery_source={
            'source_job_id':request['source_job_id'],'binding_sha256':request['binding_sha256']})

    def source(self,identifier,*,admission=False,owns_execution_lease=False):
        host=self.host;row=json.loads(host._job_path(identifier).read_bytes());request=row.get('request')
        # Admission already holds the lease. Do not probe that same lock and
        # mistake our own reader for a live source worker after a crash.
        if owns_execution_lease:
            stopped=row.get('status') in TERMINAL or row.get('status') in {'queued','running'} and identifier not in host.active
        else:stopped=host.job(identifier)['status'] in TERMINAL
        if (row.get('id')!=identifier or not isinstance(request,dict) or request.get('mode') not in MODES
                or any(row.get(k)!=request.get(k) for k in ('mode','provider','depth'))
                or not stopped or request.get('controller','rules')!='rules'
                or not re.fullmatch('[a-f0-9]{64}',row.get('recovery_job_policy_sha256',''))):
            raise ValueError('No owned stopped product journal is available')
        refs=row.get('stage_checkpoints') or {}
        if 'execution_input' not in refs:raise ValueError('Legacy audit checkpoints are not recovery journals')
        # Existing paid verifier/export recovery owns the late Quick/Deep path.
        if request['provider']=='groq' and request['depth']!='planned' and 'verification_input' in refs:
            raise ValueError('Use the existing verifier or export continuation')
        records=[];inventory=[]
        for stage in refs:
            raw=host.stage_bytes(identifier,stage);record=json.loads(raw)
            if not isinstance(record.get('recovery'),dict) or record.get('stage')!=stage:
                raise ValueError('Mixed legacy or changed recovery checkpoint')
            records.append(record);inventory.append({'stage':stage,'sha256':sha256(raw).hexdigest()})
        records.sort(key=lambda r:r['recovery']['index']);inventory.sort(key=lambda r:next(
            v['recovery']['index'] for v in records if v['stage']==r['stage']))
        context=records[0]['payload']['context'];bind_original(request,context)
        context_sha=digest(context);attempts=row.get('pipeline_attempts')
        limit=0 if request['provider']=='none' else 9 if request['depth']=='planned' else 6 if request['depth']=='deep' else 4
        if (not isinstance(attempts,list) or len(attempts)>limit or row.get('pipeline_context_sha256')!=context_sha):
            raise ValueError('Missing or changed durable provider-attempt accounting')
        for index,attempt in enumerate(attempts,1):
            if (set(attempt)!={'schema','context_sha256','stage','provider_calls','max_provider_calls'}
                    or attempt['schema']!='product_pipeline_call_attempt_v1' or attempt['context_sha256']!=context_sha
                    or type(attempt['provider_calls']) is not int or attempt['provider_calls']!=index
                    or attempt['max_provider_calls']!=limit
                    or attempt['stage'] not in {'seed_generation','refined_generation','verification',
                        'planned_generation_1','planned_generation_2','planned_generation_3'}):
                raise ValueError('Changed durable provider attempt')
        if row.get('usage_tracking') is True and len(row.get('usage_receipts',{}))>len(attempts):
            raise ValueError('Captured receipts exceed the recorded provider attempts')
        previous=None;last_calls=0
        for index,record in enumerate(records):
            proof=record['recovery']
            if (record['case_id']!=context['case']['case_id'] or record['query_sha256']!=sha256(context['case']['problem_statement'].encode()).hexdigest()
                    or record['commit_sha']!=context['snapshot']['commit_sha'] or record['snapshot_sha256']!=context['snapshot']['snapshot_sha256']
                    or proof['schema']!=SCHEMA or proof['context_sha256']!=context_sha or proof['index']!=index
                    or proof['previous_record_canonical_sha256']!=previous or type(proof['provider_calls']) is not int
                    or not last_calls<=proof['provider_calls']<=len(attempts)):
                raise ValueError('Recovery stage sequence/source/accounting changed')
            previous=digest(record);last_calls=proof['provider_calls']
        binding={'schema':'product_stage_recovery_source_v1','source_job_id':identifier,
            'source_request_sha256':digest(request),'context_sha256':context_sha,
            'stages':inventory,'attempts_sha256':digest(attempts),'prior_provider_calls':len(attempts),
            'usage_sha256':digest({'tracking':row.get('usage_tracking'),'receipts':row.get('usage_receipts',{})}),
            'job_policy_sha256':row['recovery_job_policy_sha256'],'core_policy_sha256':context['policy_sha256'],
            'provider':request['provider'],'depth':request['depth'],'commit_sha':context['snapshot']['commit_sha'],
            'snapshot_sha256':context['snapshot']['snapshot_sha256']}
        binding=dict(binding,sha256=digest(binding))
        if admission and (binding['job_policy_sha256']!=job_policy_sha256() or context['policy_sha256']!=core_policy()):
            raise ValueError('Recovery admission policy changed')
        return row,records,context,binding

    def _invoke(self,context,records,prior,*,complete,on_stage,on_attempt):
        options=deepcopy(context['options'])
        return investigate(context['case'],context['snapshot']['repository'],**options,complete_json=complete,
            recoverable=True,recovery_records=records,prior_provider_calls=prior,on_stage=on_stage,on_recovery_attempt=on_attempt)

    def admit(self,identifier):
        lease=self.lease(identifier)
        try:
            row,records,context,binding=self.source(identifier,admission=True,owns_execution_lease=True)
            pending=[]
            def attempt(value):pending.append(value['stage']);raise NeedCall()
            def never(*a,**k):raise AssertionError('Preview must not invoke a provider')
            def simulated(stage,record):return {'path':'preview/'+stage+'.json','sha256':digest(record)}
            try:self._invoke(context,records,binding['prior_provider_calls'],complete=never if binding['provider']=='groq' else None,
                on_stage=simulated,on_attempt=attempt)
            except NeedCall:pass
            return row,records,context,binding,bool(pending),pending[0] if pending else 'export'
        finally:lease.close()

    def preview(self,identifier):
        if not self.host.capacity.acquire(blocking=False):raise RuntimeError('Queue is full; wait for current work')
        try:
            try:row,records,context,binding,paid,next_stage=self.admit(identifier)
            except (ValueError,KeyError,TypeError,AttributeError,OSError,sqlite3.Error):
                return {'available':False,'source_job_id':identifier,'reason':'no_valid_owned_recovery_journal','model_calls':0}
            return {'schema':'product_stage_recovery_preview_v1','available':True,'source_job_id':identifier,
                'binding_sha256':binding['sha256'],'context_sha256':binding['context_sha256'],
                'commit_sha':binding['commit_sha'],'provider':binding['provider'],'depth':binding['depth'],
                'completed_stages':[r['stage'] for r in records],'next_stage':next_stage,
                'prior_provider_calls':binding['prior_provider_calls'],'max_provider_calls':
                    0 if binding['provider']=='none' else 9 if binding['depth']=='planned' else 6 if binding['depth']=='deep' else 4,
                'requires_api':paid,'automatic_retry':False,'model_calls':0}
        finally:self.host.capacity.release()

    def _intent(self,identifier,request):
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT binding_sha,target_job,binding,requires_api FROM intents WHERE source_job=?',(request['source_job_id'],)).fetchone()
            db.commit()
        finally:db.close()
        if row is None or row[:2]!=(request['binding_sha256'],identifier):raise ValueError('Missing durable stage recovery intent')
        saved=json.loads(self.host._job_path(identifier).read_bytes())
        binding=json.loads(row[2])
        if (saved.get('id')!=identifier or saved.get('request')!=request or request.get('mode')!='stage_resume'
                or any(saved.get(k)!=request.get(k) for k in ('mode','provider','depth'))
                or request!={'mode':'stage_resume','provider':binding['provider'],'depth':binding['depth'],
                    'source_job_id':binding['source_job_id'],'binding_sha256':binding['sha256'],'requires_api':bool(row[3])}
                or self.source(request['source_job_id'])[3]!=binding):
            raise ValueError('Saved recovery execution authority changed')
        return binding

    def submit(self,identifier,request):
        if (not isinstance(request,dict) or set(request)!={'binding_sha256','confirmation'}
                or not isinstance(request['binding_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',request['binding_sha256'])
                or not isinstance(request['confirmation'],str)
                or request['confirmation'] not in {'resume_product_stages_with_api_cost','resume_product_stages_without_api'}):
            raise ValueError('Explicit preview-bound stage recovery confirmation is required')
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT binding_sha,target_job,requires_api FROM intents WHERE source_job=?',(identifier,)).fetchone()
            if existing:
                if existing[0]!=request['binding_sha256'] or request['confirmation']!=('resume_product_stages_with_api_cost' if existing[2] else 'resume_product_stages_without_api'):
                    raise ValueError('Recovery intent differs from this preview')
                db.commit();old=json.loads(self.host._job_path(existing[1]).read_bytes())['request']
                self._intent(existing[1],old);return self._response(existing[1],old)
            if not self.host.capacity.acquire(blocking=False):raise RuntimeError('Queue is full; wait for current work')
            try:_,_,_,binding,paid,_=self.admit(identifier)
            finally:self.host.capacity.release()
            if (binding['sha256']!=request['binding_sha256'] or request['confirmation']!=
                    ('resume_product_stages_with_api_cost' if paid else 'resume_product_stages_without_api')):
                raise ValueError('Source or cost consent changed; inspect the recovery preview again')
            target=uuid.uuid4().hex[:24]
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?)',(identifier,binding['sha256'],target,canonical_json(binding).encode(),int(paid)))
            row=self.host._enqueue({'mode':'stage_resume','provider':binding['provider'],'depth':binding['depth'],
                'source_job_id':identifier,'binding_sha256':binding['sha256'],'requires_api':paid},self._run,identifier=target)
            db.commit();return self._response(row['id'],json.loads(self.host._job_path(row['id']).read_bytes())['request'])
        except BaseException:db.rollback();raise
        finally:db.close()

    def _run(self,row):
        host=self.host;lease=None
        try:
            lease=host._take_execution_lease(row['id']) or self.lease(row['id'],create=True)
            host._check_cancelled(row['id'])
            request=row['request'];binding=self._intent(row['id'],request)
            def guard():
                self._intent(row['id'],request)
                current=self.admit(request['source_job_id'])
                if current[3]!=binding or current[4]!=request['requires_api']:raise ValueError('Recovery source changed during execution')
                host._check_cancelled(row['id'])
                return current
            _,records,context,_,_,_=guard()
            host._save(row,status='running',stage='replay_stages',pipeline_attempts=[],pipeline_context_sha256=binding['context_sha256'])
            if not request['requires_api']:host._save(row,usage_tracking=True,usage_receipts={})
            provider=None
            if request['requires_api']:
                from issueintel.runtime.config import RuntimeConfig
                config=RuntimeConfig.load(host.root)
                if config.model!=context['options']['model'] or config.max_output_tokens<context['options']['max_output_tokens']:
                    raise ValueError('Recovery provider/output configuration differs from its source')
                call_context=({'category':'ARTIFACT_GENERATION_COST','reasoning_effort':config.reasoning_effort,
                    'stage':'local_demo_stage_recovery','diagnostic':False} if config.reasoning_effort in {'medium','high'} else None)
                provider=host.provider_factory(config,call_context=call_context)
                if provider.model!=context['options']['model']:raise ValueError('Recovery transport model changed')
                if hasattr(provider,'on_receipt'):
                    def capture(value):
                        row.setdefault('usage_receipts',{})[value['reservation_id']]=dict(value,stage=row['pipeline_attempts'][-1]['stage'])
                        host._save(row,usage_tracking=True,usage_receipts=row['usage_receipts'])
                    provider.on_receipt=capture;host._save(row,usage_tracking=True,usage_receipts={})
            def complete(messages,max_output_tokens):
                guard();result=provider(messages,max_output_tokens);guard();return result
            if provider is not None and hasattr(provider,'complete_structured'):
                def structured(messages,*,max_output_tokens,schema):
                    guard();result=provider.complete_structured(messages,max_output_tokens=max_output_tokens,schema=schema);guard();return result
                complete.complete_structured=structured
            def attempt(value):
                guard()
                if not request['requires_api'] or value['provider_calls']!=binding['prior_provider_calls']+len(row['pipeline_attempts'])+1:
                    raise ValueError('Recovery attempt authority changed')
                host._save(row,pipeline_attempts=row['pipeline_attempts']+[deepcopy(value)])
            from .stages import StageCheckpoints
            store=StageCheckpoints(host.root,host.directory/'runs'/row['id']/'stages')
            def save(stage,record):
                host._check_cancelled(row['id']);ref=store(stage,record)
                host._save(row,stage=stage,stage_checkpoints=dict(store.receipts));return ref
            # A no-API completion still uses a failing stub for a paid source;
            # an unexpected call cannot instantiate a provider or spend.
            def no_api(*a,**k):raise ValueError('Recovery preview did not authorize an API call')
            model_call=complete if provider is not None else no_api if binding['provider']=='groq' else None
            pack=self._invoke(context,records,binding['prior_provider_calls'],complete=model_call,on_stage=save,on_attempt=attempt)
            guard();pack['recovered_from']={'schema':'product_stage_resume_v1','source':binding,'automatic_retry':False}
            self.validate_child(row['id'],pack,request)
            directory=host.directory/'runs'/row['id'];export_pack(pack,directory);guard()
            refs={name:{'path':(directory/name).relative_to(host.root).as_posix(),'sha256':sha256((directory/name).read_bytes()).hexdigest()}
                for name in ('pack.json','report.md')}
            host._save(row,status='completed',stage='completed',result=refs,run_id=pack['run_id'],case_id=pack['case']['case_id'],
                strict_issue_time_verified=False,stop_reason=pack['stop_reason'])
        except InterruptedError:host._save(row,status='cancelled',stage='cancelled')
        except Exception as error:host._save(row,status='failed',stage='failed',error=f'Stage recovery failed ({type(error).__name__}); no automatic retry.')
        finally:
            if lease is not None:lease.close()
            with host.lock:host.active.discard(row['id']);host.cancelled.discard(row['id'])
            host.capacity.release()

    def validate_child(self,identifier,pack,request):
        binding=self._intent(identifier,request);_,records,context,_=self.source(request['source_job_id'])
        value=pack.get('stage_recovery') or {};link=pack.get('recovered_from') or {}
        limit=0 if binding['provider']=='none' else 9 if binding['depth']=='planned' else 6 if binding['depth']=='deep' else 4
        if (link!={'schema':'product_stage_resume_v1','source':binding,'automatic_retry':False}
                or digest(pack['case'])!=digest(context['case']) or digest(pack['snapshot'])!=digest(context['snapshot'])
                or value.get('context_sha256')!=binding['context_sha256'] or value.get('policy_sha256')!=binding['core_policy_sha256']
                or set(value)!={'schema','context_sha256','policy_sha256','reused_stages','inherited_provider_calls',
                    'new_provider_calls','max_provider_calls','call_count_kind','automatic_retry','billing_verified'}
                or value.get('schema')!=SCHEMA or value.get('max_provider_calls')!=limit
                or value.get('call_count_kind')!='attempt_upper_bound' or value.get('automatic_retry') is not False
                or value.get('billing_verified') is not False or type(value.get('new_provider_calls')) is not int
                or not 0<=value['new_provider_calls']<=limit-binding['prior_provider_calls']
                or value.get('inherited_provider_calls')!=binding['prior_provider_calls']
                or value.get('reused_stages')!=[{'stage':r['stage'],'record_canonical_sha256':digest(r),
                    'recorded_provider_calls':r['recovery']['provider_calls']} for r in records]
                or any(pack['leakage_audit'].get(k) is not False for k in
                    ('strict_issue_time_verified','gold_in_inference','snapshot_issue_time_verified','original_query_verified'))
                or any(k in pack for k in ('followup','continued_from','resumed_from','finalized_from','context_selection','controller_decisions'))):
            raise ValueError('Recovered child differs from its owned stage source')
        row=json.loads(self.host._job_path(identifier).read_bytes())
        attempts=row.get('pipeline_attempts',[])
        if (value.get('new_provider_calls')!=len(attempts)
                or pack.get('stage_checkpoints',{})!=row.get('stage_checkpoints',{})):
            raise ValueError('Recovered child call accounting differs from durable attempts')
        for index,attempt in enumerate(attempts,binding['prior_provider_calls']+1):
            if (attempt['context_sha256']!=binding['context_sha256'] or attempt['provider_calls']!=index
                    or attempt['max_provider_calls']!=limit):raise ValueError('Recovered provider attempt changed')
        new=[json.loads(self.host.stage_bytes(identifier,stage)) for stage in row.get('stage_checkpoints',{})]
        new.sort(key=lambda record:record['recovery']['index'])
        previous=digest(records[-1]);last_calls=binding['prior_provider_calls']
        for index,record in enumerate(new,len(records)):
            proof=record['recovery']
            if (proof['context_sha256']!=binding['context_sha256'] or proof['index']!=index
                    or proof['previous_record_canonical_sha256']!=previous
                    or not last_calls<=proof['provider_calls']<=binding['prior_provider_calls']+len(attempts)):
                raise ValueError('Recovered stage tail differs from its source prefix')
            previous=digest(record);last_calls=proof['provider_calls']
        full=records+new
        case=context['case'];text=case['problem_statement']
        issue={'id':'issue_'+digest(text)[:20],'kind':'issue','locator':case.get('issue_url') or case['case_id'],
            'text':text,'sha256':sha256(text.encode()).hexdigest(),'revision':None,'available_at':case.get('issue_created_at'),
            'provenance':{'kind':'input','edit_history':'unknown'}}
        if [r for r in pack['evidence'] if r['kind']=='issue']!=[issue]:raise ValueError('Recovered issue provenance changed')
        generated=[record for record in full if record['stage'] in {'seed_generation','refined_generation',
            'planned_generation_1','planned_generation_2','planned_generation_3'}]
        if generated:
            latest=generated[-1]
            if (pack.get('annotation')!=latest['payload']['annotation'] or latest['recovery']['input_sha256']!=
                    digest({'case_sha256':digest(pack['case']),'evidence_sha256':digest(pack['evidence'])})):
                raise ValueError('Recovered generation differs from recorded final inputs/output')
        elif pack.get('annotation') is not None:raise ValueError('Unexpected recovered generation')
        verified=[record for record in full if record['stage']=='verification']
        if verified:
            if pack.get('verification')!=verified[-1]['payload']['verification']:raise ValueError('Recovered verifier output changed')
        elif pack.get('verification') is not None:raise ValueError('Unexpected recovered verifier output')
        return pack
