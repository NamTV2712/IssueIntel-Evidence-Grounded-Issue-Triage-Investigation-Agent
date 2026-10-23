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
from .stage_recovery import SCHEMA,StageTape,RecoveryBudgetExhausted,recorded_call_limit,policy_sha256 as core_policy
from .fork_origins import MODES as FORK_MODES


MODES={'example','local','github','planned_continuation'}|FORK_MODES
TERMINAL={'failed','cancelled','interrupted'}


def job_policy_sha256():
    package=Path(__file__).resolve().parents[1]
    names=('investigation/stage_jobs.py','investigation/workbench.py','investigation/finalization.py',
           'investigation/dossier.py','investigation/planned_continuations.py','investigation/fork_origins.py',
           'investigation/memory_followups.py','investigation/check_followups.py','investigation/memory_selection.py',
           'investigation/product_memory.py','investigation/adaptive_clarifications.py','investigation/question_jobs.py',
           'investigation/controller_accounting.py','investigation/artifact_accounting.py',
           'investigation/batch_jobs.py','investigation/product_batches.py')
    return digest({'core':core_policy(),**{name:sha256((package/name).read_bytes()).hexdigest() for name in names}})


def bind_original(request,context):
    from .workbench import validate_request
    from .resume import validate_product_case
    from issueintel.integrations.github import github_input
    from .finalization import _snapshot_descriptor
    continuation=request.get('mode')=='planned_continuation'
    fork=request.get('mode') in FORK_MODES
    if continuation:
        from .planned_continuations import validate_request as validate_continuation
        canonical=validate_continuation(request,saved=True)
    elif fork:
        from .fork_origins import canonical_request
        canonical=canonical_request(request)
    else:canonical=validate_request(request)
    if (digest(canonical)!=digest(request) or request['mode'] not in MODES
            or request.get('controller','rules') not in {'rules','jev'}):
        raise ValueError('Stage recovery requires canonical original product authority')
    case=context['case'];snapshot=context['snapshot'];options=context['options']
    if fork:
        from .fork_origins import validate_case
        validate_case(case,snapshot['protocol'],context['fork'])
    else:validate_product_case(case,snapshot['protocol'])
    _snapshot_descriptor(snapshot)
    if any(k in case for k in ('split','instance_id')):raise ValueError('Benchmark metadata cannot enter product recovery')
    planned=request['depth']=='planned'
    expected={'provider':request['provider'],'model':'none' if request['provider']=='none' else 'openai/gpt-oss-120b',
        'top_k':4 if request['depth']=='quick' else 12,'refinement_rounds':int(request['depth']=='deep'),
        'resumable':not planned and not fork,'planned_workflow':planned,'independent_critic':True,
        'planner_policy':('bounded_planner_v2' if request.get('controller')=='jev' else 'bounded_planner_v3') if planned else 'bounded_planner_v1'}
    if (context['schema']!=SCHEMA or any(digest(options[k])!=digest(v) for k,v in expected.items())
            or set(options)!=set(expected)|{'repository_context','max_output_tokens'}
            or type(options['max_output_tokens']) is not int or not 0<options['max_output_tokens']<=65536):
        raise ValueError('Saved stage recipe differs from its original request')
    if bool(context.get('controller'))!=(request.get('controller')=='jev'):
        raise ValueError('Saved controller authority changed')
    if context.get('controller'):
        authority=context['controller']
        if (authority!={'name':'jev','model':'jev-1.13.0','origin_job_id':authority.get('origin_job_id')}
                or not re.fullmatch('[a-f0-9]{24}',authority['origin_job_id'])
                or not planned or continuation or fork or context.get('call_budget_policy')!='profile_and_seed_route_v1'):
            raise ValueError('Invalid Jev recovery authority')
    if fork:
        if context['fork']['origin']['request']!=request or 'continuation' in context:
            raise ValueError('Fork recovery authority changed')
    elif 'fork' in context:raise ValueError('Original recovery cannot claim fork authority')
    elif continuation:
        authority=context['continuation']
        if authority['origin']['request']!=request or context.get('call_budget_policy')!='continuation_root_budget_v1':
            raise ValueError('Continuation recovery authority changed')
    elif 'continuation' in context:raise ValueError('Original recovery cannot inherit a continuation budget')
    elif request['mode']=='local':
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


def source_binding(segments,inventory,context,policy):
    """Versioned immutable ownership projection; never copy prior stage files."""
    last=segments[-1];request=last['request'];root=segments[0]['request']
    attempts=[a for s in segments for a in s['attempts']]
    jev=bool(context.get('controller'));continuation=root['mode']=='planned_continuation';fork=root['mode'] in FORK_MODES;many=len(segments)>1 or continuation or fork or jev
    binding={'schema':'product_stage_recovery_source_v5' if jev else 'product_stage_recovery_source_v4' if fork else 'product_stage_recovery_source_v3' if continuation else 'product_stage_recovery_source_v2' if many else 'product_stage_recovery_source_v1',
        'source_job_id':last['job_id'],'source_request_sha256':digest(request),'context_sha256':digest(context),
        'stages':inventory if many else [{k:v for k,v in ref.items() if k!='owner_job_id'} for ref in inventory],
        'attempts_sha256':digest(attempts),'prior_provider_calls':len(attempts),
        'usage_sha256':digest([dict(job_id=s['job_id'],**s['usage']) for s in segments] if many else last['usage']),
        'job_policy_sha256':policy,'core_policy_sha256':context['policy_sha256'],
        'provider':root['provider'],'depth':root['depth'],'commit_sha':context['snapshot']['commit_sha'],
        'snapshot_sha256':context['snapshot']['snapshot_sha256']}
    if jev:
        controller=[dict(job_id=s['job_id'],**s['controller']) for s in segments]
        binding.update(controller='jev',controller_segments_sha256=digest(controller),
            prior_controller_calls=sum(len(s['attempts']) for s in controller),max_controller_calls=4)
    if many:
        binding.update(root_job_id=segments[0]['job_id'],source_chain=[{
            'job_id':s['job_id'],'request_sha256':digest(s['request']),'attempts_sha256':digest(s['attempts']),
            'usage_sha256':digest(s['usage']),'source_binding_sha256':(s['input_binding'] or {}).get('sha256')} for s in segments])
    return dict(binding,sha256=digest(binding))


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
        return self._source_state(identifier,admission=admission,owns_execution_lease=owns_execution_lease)[:4]

    def accounting_segments(self,identifier):
        return self._source_state(identifier)[4]

    def _source_state(self,identifier,*,admission=False,owns_execution_lease=False):
        host=self.host;chain=[];seen=set();current=identifier
        for _ in range(8):
            if current in seen:raise ValueError('Cyclic stage recovery ancestry')
            seen.add(current);row=json.loads(host._job_path(current).read_bytes());request=row.get('request')
            if owns_execution_lease and current==identifier:
                stopped=row.get('status') in TERMINAL or row.get('status') in {'queued','running'} and current not in host.active
            else:stopped=host.job(current)['status'] in TERMINAL
            if (row.get('id')!=current or not isinstance(request,dict) or request.get('mode') not in MODES|{'stage_resume'}
                    or any(row.get(k)!=request.get(k) for k in ('mode','provider','depth'))
                    or not stopped or request.get('controller','rules') not in {'rules','jev'}):
                raise ValueError('No owned stopped product journal is available')
            input_binding=self._saved_intent(current,request) if request['mode']=='stage_resume' else None
            chain.append((row,input_binding))
            if input_binding is None:break
            current=request['source_job_id']
        else:raise ValueError('Stage recovery ancestry exceeds eight jobs')
        chain.reverse();original=chain[0][0];root_request=original['request']
        policy=original.get('recovery_job_policy_sha256','')
        if not re.fullmatch('[a-f0-9]{64}',policy):raise ValueError('Missing original recovery admission policy')
        if 'execution_input' not in (original.get('stage_checkpoints') or {}):
            raise ValueError('Legacy audit checkpoints are not recovery journals')
        # Existing verifier/export channels own late original Quick/Deep jobs;
        # a failed recovered child has no competing legacy recovery channel.
        if (len(chain)==1 and root_request['mode'] in {'local','github','example'} and root_request['provider']=='groq' and root_request['depth']!='planned'
                and 'verification_input' in original['stage_checkpoints']):
            raise ValueError('Use the existing verifier or export continuation')
        records=[];inventory=[];segments=[];attempts=[];receipt_ids=set();context=None;binding=None
        previous=None;last_calls=0
        limit=0 if root_request['provider']=='none' else 9 if root_request['depth']=='planned' else 6 if root_request['depth']=='deep' else 4
        for row,input_binding in chain:
            if input_binding is not None and input_binding!=binding:raise ValueError('Ancestor recovery intent/source changed')
            identifier_here=row['id'];request=row['request'];local=[]
            for stage in row.get('stage_checkpoints') or {}:
                raw=host.stage_bytes(identifier_here,stage);record=json.loads(raw)
                if not isinstance(record.get('recovery'),dict) or record.get('stage')!=stage:
                    raise ValueError('Mixed legacy or changed recovery checkpoint')
                local.append((record,{'stage':stage,'sha256':sha256(raw).hexdigest(),'owner_job_id':identifier_here}))
            local.sort(key=lambda item:item[0]['recovery']['index'])
            if context is None:
                if not local or local[0][0]['stage']!='execution_input':raise ValueError('Missing recovery header')
                context=local[0][0]['payload']['context'];bind_original(root_request,context)
                if root_request['mode']=='planned_continuation':
                    parent,_,case,_,link=host.planned_continuations.input(root_request,original['id'],admission=admission)
                    from .planned_waiting import _inherit,continued_context
                    expected={'origin':{'job_id':original['id'],'request':root_request},'link':link,'inherited':_inherit(parent)}
                    if (context['continuation']!=expected or context['case']!=case
                            or context['snapshot']!=parent['snapshot']
                            or context['options']['repository_context']!=continued_context(parent,case)):
                        raise ValueError('Continuation journal differs from its source-bound answers')
                elif root_request['mode'] in FORK_MODES:
                    from .fork_origins import prepare
                    prepared=prepare(host,original,admission=admission,recorded=context['fork'],context=context['options']['repository_context'])
                    if (context['case']!=prepared['case'] or context['snapshot']!=prepared['snapshot']
                            or context['options']['repository_context']!=prepared['repository_context']):
                        raise ValueError('Fork journal differs from its source-bound inputs')
            context_sha=digest(context);local_attempts=row.get('pipeline_attempts')
            if (not isinstance(local_attempts,list) or len(attempts)+len(local_attempts)>limit
                    or row.get('pipeline_context_sha256')!=context_sha
                    or input_binding is not None and request['requires_api'] is False and local_attempts):
                raise ValueError('Missing or changed durable provider-attempt accounting')
            for index,attempt in enumerate(local_attempts,len(attempts)+1):
                if (set(attempt)!={'schema','context_sha256','stage','provider_calls','max_provider_calls'}
                        or attempt['schema']!='product_pipeline_call_attempt_v1' or attempt['context_sha256']!=context_sha
                        or type(attempt['provider_calls']) is not int or attempt['provider_calls']!=index
                        or attempt['max_provider_calls']!=limit or attempt['stage'] not in {
                            'seed_generation','refined_generation','verification','planned_generation_1','planned_generation_2','planned_generation_3'}):
                    raise ValueError('Changed durable provider attempt')
            attempts.extend(local_attempts)
            usage={'tracking':row.get('usage_tracking'),'receipts':row.get('usage_receipts',{})}
            if (not isinstance(usage['receipts'],dict) or receipt_ids&set(usage['receipts'])
                    or usage['tracking'] is True and len(usage['receipts'])>len(local_attempts)):
                raise ValueError('Recovery receipt ownership or attempt count changed')
            receipt_ids.update(usage['receipts'])
            for record,ref in local:
                proof=record['recovery']
                if (record['case_id']!=context['case']['case_id'] or record['query_sha256']!=sha256(context['case']['problem_statement'].encode()).hexdigest()
                        or record['commit_sha']!=context['snapshot']['commit_sha'] or record['snapshot_sha256']!=context['snapshot']['snapshot_sha256']
                        or len(records)>=22 or proof['schema']!=SCHEMA or proof['context_sha256']!=context_sha or proof['index']!=len(records)
                        or proof['previous_record_canonical_sha256']!=previous or type(proof['provider_calls']) is not int
                        or not last_calls<=proof['provider_calls']<=len(attempts)):
                    raise ValueError('Recovery stage sequence/source/accounting changed')
                records.append(record);inventory.append(ref);previous=digest(record);last_calls=proof['provider_calls']
            segments.append({'job_id':identifier_here,'request':request,'attempts':local_attempts,'usage':usage,
                'input_binding':input_binding,'stages':[{'stage':ref['stage'],'sha256':ref['sha256']} for _,ref in local]})
            if context.get('controller'):
                segments[-1]['controller']={'attempts':row.get('controller_attempts'),'decisions':row.get('typesafe_decisions'),'receipts':row.get('typesafe_receipts')}
            binding=source_binding(segments,inventory,context,policy)
        if context.get('controller'):
            from .controller_accounting import segments as controller_segments
            controller,_,_,_=controller_segments([r for r,_ in chain],context)
            if receipt_ids & {identifier for s in controller for identifier in s['receipts']}:
                raise ValueError('Providers cannot share receipt ownership')
        if admission and (binding['job_policy_sha256']!=job_policy_sha256() or context['policy_sha256']!=core_policy()):
            raise ValueError('Recovery admission policy changed')
        return chain[-1][0],records,context,binding,deepcopy(segments)

    def _invoke(self,context,records,prior,*,complete,on_stage,on_attempt,controller=None,recovery_controller=None):
        options=deepcopy(context['options'])
        extra={}
        if context.get('continuation'):
            origin=context['continuation']['origin']
            parent,_,case,_,link=self.host.planned_continuations.input(origin['request'],origin['job_id'],admission=True)
            if case!=context['case'] or link!=context['continuation']['link']:raise ValueError('Continuation input changed')
            options['repository_context']=None
            extra={'planned_parent':parent,'planned_continuation':link,'recovery_origin':origin}
        if context.get('fork'):
            from .fork_origins import prepare
            origin=context['fork']['origin'];row=json.loads(self.host._job_path(origin['job_id']).read_bytes())
            prepared=prepare(self.host,row,recorded=context['fork'])
            extra={'recovery_fork':context['fork'],'snapshot':prepared['extra']['snapshot']}
        if context.get('controller'):
            if controller is None:raise ValueError('Missing owned Typesafe replay controller')
            extra.update(controller=controller,recovery_controller=recovery_controller)
        pack=investigate(context['case'],context['snapshot']['repository'],**options,complete_json=complete,
            recoverable=True,recovery_records=records,prior_provider_calls=prior,on_stage=on_stage,on_recovery_attempt=on_attempt,**extra)
        if context.get('fork'):
            from .fork_origins import attach
            attach(pack,context['fork'])
        return pack

    @staticmethod
    def _budget_preview(context):
        if not context.get('continuation'):return {}
        inherited=context['continuation']['inherited']
        return {'budget_scope':'planned_continuation_remaining','inherited_root_provider_calls':inherited['counts']['provider_calls'],
                'root_max_provider_calls':inherited['limits']['max_provider_calls']}

    def _controller_args(self,context,segments,owner,*,on_attempt,on_receipt=None,on_decision=None,guard=None):
        if not context.get('controller'):return {}
        from types import SimpleNamespace
        from .typesafe_controller import TypesafePlannedController
        host=self.host
        class LazyClient:
            config=SimpleNamespace(model='jev-1.13.0')
            client=None
            def decide(self,state,allowed,binding):
                if guard:guard()
                if self.client is None:
                    from issueintel.runtime.typesafe import TypesafeConfig,TypesafeClient
                    self.client=(host.typesafe_factory or TypesafeClient)(TypesafeConfig.load(host.root),on_receipt=on_receipt)
                value=self.client.decide(state,allowed,binding)
                if guard:guard()
                return value
            @property
            def last_receipt(self):return getattr(self.client,'last_receipt',None)
        saved=[r for s in segments for r in s['controller']['decisions']]
        return {'controller':TypesafePlannedController(LazyClient(),owner,on_decision=on_decision),
            'recovery_controller':{'origin_job_id':context['controller']['origin_job_id'],'owner_job_id':owner,
                'prior_attempts':sum(len(s['controller']['attempts']) for s in segments),
                'saved_records':saved,'prior_receipts':[r for s in segments for r in s['controller']['receipts'].values()],
                'on_attempt':on_attempt}}

    def admit(self,identifier):
        self.host.dossiers._origin(identifier,max_hops=10)
        lease=self.lease(identifier)
        try:
            row,records,context,binding,segments=self._source_state(identifier,admission=True,owns_execution_lease=True)
            pending=[]
            def attempt(value):pending.append(value['stage']);raise NeedCall()
            def never(*a,**k):raise AssertionError('Preview must not invoke a provider')
            def simulated(stage,record):return {'path':'preview/'+stage+'.json','sha256':digest(record)}
            controller=self._controller_args(context,segments,'0'*24,on_attempt=attempt)
            try:self._invoke(context,records,binding['prior_provider_calls'],complete=never if binding['provider']=='groq' else None,
                on_stage=simulated,on_attempt=attempt,**controller)
            except NeedCall:pass
            if context.get('controller') and pending and not pending[0].startswith('planned_decision_') and pending[0]!='verification' and binding['prior_controller_calls']>=4:
                from .typesafe_recovery import TypesafeRecoveryBudgetExhausted
                raise TypesafeRecoveryBudgetExhausted(binding['prior_controller_calls'])
            paid=bool(pending);next_stage=pending[0] if pending else 'export';controller_preview={}
            if context.get('controller'):
                orphan_count=sum(len(s['controller']['receipts'])-sum(r['receipt'] is not None for r in s['controller']['decisions']) for s in segments)
                may_use_typesafe=(len([r for s in segments for r in s['controller']['decisions']])<4
                    and binding['prior_controller_calls']<4 and next_stage!='verification')
                controller_preview={'controller':'jev','prior_controller_calls':binding['prior_controller_calls'],'max_controller_calls':4,
                    'orphan_terminal_receipts':orphan_count,'next_provider':('typesafe' if next_stage.startswith('planned_decision_') else 'groq') if paid else None,
                    'api_providers':(['groq']+(['typesafe'] if may_use_typesafe else [])) if paid else []}
            return row,records,context,binding,paid,next_stage,controller_preview
        finally:lease.close()

    def preview(self,identifier):
        if not self.host.capacity.acquire(blocking=False):raise RuntimeError('Queue is full; wait for current work')
        try:
            from .typesafe_recovery import TypesafeRecoveryBudgetExhausted
            from .controller_accounting import UnsettledControllerReceipt
            try:row,records,context,binding,paid,next_stage,controller=self.admit(identifier)
            except TypesafeRecoveryBudgetExhausted as error:
                return {'available':False,'source_job_id':identifier,'reason':'original_controller_budget_exhausted',
                    'prior_controller_calls':error.used,'max_controller_calls':error.limit,'automatic_retry':False,'model_calls':0}
            except UnsettledControllerReceipt:
                return {'available':False,'source_job_id':identifier,'reason':'controller_receipt_unsettled','automatic_retry':False,'model_calls':0}
            except RecoveryBudgetExhausted as error:
                context=self.source(identifier)[2]
                return {'available':False,'source_job_id':identifier,'reason':'original_call_budget_exhausted',
                    'prior_provider_calls':error.used,'max_provider_calls':error.limit,'automatic_retry':False,'model_calls':0,**self._budget_preview(context)}
            except (ValueError,KeyError,TypeError,AttributeError,OSError,sqlite3.Error):
                return {'available':False,'source_job_id':identifier,'reason':'no_valid_owned_recovery_journal','model_calls':0}
            return {'schema':'product_stage_recovery_preview_v1','available':True,'source_job_id':identifier,
                'binding_sha256':binding['sha256'],'context_sha256':binding['context_sha256'],
                'commit_sha':binding['commit_sha'],'provider':binding['provider'],'depth':binding['depth'],
                'root_job_id':binding.get('root_job_id',identifier),
                'source_job_ids':[s['job_id'] for s in binding.get('source_chain',[])] or [identifier],
                'completed_stages':[r['stage'] for r in records],'next_stage':next_stage,
                'prior_provider_calls':binding['prior_provider_calls'],'max_provider_calls':recorded_call_limit(context,records),
                'requires_api':paid,'automatic_retry':False,'model_calls':0,**self._budget_preview(context),**controller}
        finally:self.host.capacity.release()

    def _saved_intent(self,identifier,request,*,wait_for_dispatch=False):
        db=(self._db() if wait_for_dispatch else sqlite3.connect(
            (self.host.directory/'stage_recovery.sqlite').as_uri()+'?mode=ro',uri=True,timeout=30))
        try:
            if wait_for_dispatch:db.execute('BEGIN IMMEDIATE')
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
                or type(request['requires_api']) is not bool
                or binding.get('sha256')!=digest({k:v for k,v in binding.items() if k!='sha256'})):
            raise ValueError('Saved recovery execution authority changed')
        return binding

    def _intent(self,identifier,request,*,wait_for_dispatch=False):
        binding=self._saved_intent(identifier,request,wait_for_dispatch=wait_for_dispatch)
        if self.source(request['source_job_id'])[3]!=binding:raise ValueError('Saved recovery source changed')
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
            try:_,_,_,binding,paid,_,_=self.admit(identifier)
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
            request=row['request'];binding=self._intent(row['id'],request,wait_for_dispatch=True)
            def guard():
                self._intent(row['id'],request)
                current=self.admit(request['source_job_id'])
                if current[3]!=binding or current[4]!=request['requires_api']:raise ValueError('Recovery source changed during execution')
                host._check_cancelled(row['id'])
                return current
            _,records,context,_,_,_,_=guard()
            host._save(row,status='running',stage='replay_stages',pipeline_attempts=[],pipeline_context_sha256=binding['context_sha256'])
            if context.get('controller'):host._save(row,controller_attempts=[],typesafe_decisions=[],typesafe_receipts={})
            if not request['requires_api']:host._save(row,usage_tracking=True,usage_receipts={})
            provider=None
            def get_provider():
                nonlocal provider
                if provider is not None:return provider
                if not request['requires_api']:raise ValueError('Preview did not authorize an API call')
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
                return provider
            def complete(messages,max_output_tokens):
                guard();result=get_provider()(messages,max_output_tokens);guard();return result
            def structured(messages,*,max_output_tokens,schema):
                guard();transport=get_provider()
                result=(transport.complete_structured(messages,max_output_tokens=max_output_tokens,schema=schema)
                    if hasattr(transport,'complete_structured') else transport(messages,max_output_tokens))
                guard();return result
            complete.complete_structured=structured
            def attempt(value):
                guard()
                if not request['requires_api'] or value['provider_calls']!=binding['prior_provider_calls']+len(row['pipeline_attempts'])+1:
                    raise ValueError('Recovery attempt authority changed')
                host._save(row,pipeline_attempts=row['pipeline_attempts']+[deepcopy(value)])
            def controller_attempt(value):
                guard()
                if (not request['requires_api'] or value['owner_job_id']!=row['id']
                        or value['controller_calls']!=binding['prior_controller_calls']+len(row['controller_attempts'])+1):
                    raise ValueError('Recovery controller attempt authority changed')
                host._save(row,controller_attempts=row['controller_attempts']+[deepcopy(value)])
            def controller_receipt(receipt):
                row['typesafe_receipts'][receipt['reservation_id']]=deepcopy(receipt)
                host._save(row,typesafe_receipts=row['typesafe_receipts']);guard()
            def controller_decision(record):
                guard();row['typesafe_decisions'].append(deepcopy(record))
                host._save(row,typesafe_decisions=row['typesafe_decisions']);guard()
            controller=self._controller_args(context,self.accounting_segments(request['source_job_id']),row['id'],
                on_attempt=controller_attempt,on_receipt=controller_receipt,on_decision=controller_decision,guard=guard)
            from .stages import StageCheckpoints
            store=StageCheckpoints(host.root,host.directory/'runs'/row['id']/'stages')
            def save(stage,record):
                host._check_cancelled(row['id']);ref=store(stage,record)
                host._save(row,stage=stage,stage_checkpoints=dict(store.receipts));return ref
            # A no-API completion still uses a failing stub for a paid source;
            # an unexpected call cannot instantiate a provider or spend.
            def no_api(*a,**k):raise ValueError('Recovery preview did not authorize an API call')
            model_call=complete if request['requires_api'] else no_api if binding['provider']=='groq' else None
            pack=self._invoke(context,records,binding['prior_provider_calls'],complete=model_call,on_stage=save,on_attempt=attempt,**controller)
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
                    'new_provider_calls','max_provider_calls','call_count_kind','automatic_retry','billing_verified'}|
                    ({'effective_max_provider_calls'} if 'effective_max_provider_calls' in value else set())
                or value.get('schema')!=SCHEMA or value.get('max_provider_calls')!=limit
                or value.get('call_count_kind')!='attempt_upper_bound' or value.get('automatic_retry') is not False
                or value.get('billing_verified') is not False or type(value.get('new_provider_calls')) is not int
                or not 0<=value['new_provider_calls']<=limit-binding['prior_provider_calls']
                or value.get('inherited_provider_calls')!=binding['prior_provider_calls']
                or value.get('reused_stages')!=[{'stage':r['stage'],'record_canonical_sha256':digest(r),
                    'recorded_provider_calls':r['recovery']['provider_calls']} for r in records]
                or any(pack['leakage_audit'].get(k) is not False for k in
                    ('strict_issue_time_verified','gold_in_inference','snapshot_issue_time_verified','original_query_verified'))
                or any(k in pack for k in ('resumed_from','finalized_from'))
                or bool(context.get('controller'))!=bool(pack.get('controller_decisions'))
                or bool(context.get('continuation'))!=bool(pack.get('continued_from'))
                or not context.get('fork') and any(k in pack for k in ('followup','context_selection'))):
            raise ValueError('Recovered child differs from its owned stage source')
        if context.get('continuation'):
            origin=context['continuation']['origin']
            self.host.planned_continuations.validate_child(origin['job_id'],pack,origin['request'])
        if context.get('fork'):
            from .fork_origins import validate_output
            validate_output(pack,context['fork'])
        row=json.loads(self.host._job_path(identifier).read_bytes())
        if context.get('controller'):
            from .controller_accounting import segments as controller_segments
            from .typesafe_controller import validate_controller_decisions
            rows=[json.loads(self.host._job_path(s['job_id']).read_bytes()) for s in binding['source_chain']]+[row]
            _,decisions,controller_calls,_=controller_segments(rows,context)
            sidecar=validate_controller_decisions(pack)
            if (sidecar['schema']!='typesafe_controller_v2' or sidecar['job_id']!=context['controller']['origin_job_id']
                    or sidecar['records']!=decisions or sidecar['recovery']!={
                        'schema':'typesafe_stage_recovery_v1','owner_job_id':identifier,
                        'origin_job_id':context['controller']['origin_job_id'],'inherited_calls':binding['prior_controller_calls'],
                        'new_calls':len(row['controller_attempts']),'max_calls':4,'automatic_retry':False,'billing_verified':False}
                    or controller_calls!=binding['prior_controller_calls']+len(row['controller_attempts'])):
                raise ValueError('Recovered controller differs from durable owner accounting')
            groq_ids={key for r in rows for key in r.get('usage_receipts',{})}
            if groq_ids & {key for r in rows for key in r.get('typesafe_receipts',{})}:
                raise ValueError('Providers cannot share receipt ownership')
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
        effective=recorded_call_limit(context,full)
        if (value.get('effective_max_provider_calls',limit)!=effective
                or ('effective_max_provider_calls' in value)!=(effective<limit)
                or binding['prior_provider_calls']+len(attempts)>effective):
            raise ValueError('Recovered calls differ from the original effective route bound')
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
