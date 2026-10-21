"""A bounded product-stage journal; admission belongs to the owning interface.

Saved prefixes are data, never programs. Replay checks source/recipe and stage
inputs, skips completed operations and preserves their original records. This
module does not grant retry permission, authenticate a job or attribute billing.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from issueintel.runtime.io import digest


SCHEMA='product_pipeline_recovery_v1'
BUDGET_POLICY='profile_and_seed_route_v1'
CONTINUATION_BUDGET_POLICY='continuation_root_budget_v1'


class RecoveryBudgetExhausted(ValueError):
    def __init__(self,used,limit):
        super().__init__('Original product provider-call budget exhausted')
        self.used=used;self.limit=limit


def recorded_call_limit(context,records):
    """Recorded policy controls new attempts; old saved contexts stay readable."""
    options=context['options']
    profile=0 if options['provider']=='none' else 9 if options['planned_workflow'] else 6 if options['refinement_rounds'] else 4
    policy=context.get('call_budget_policy')
    if policy is None:return profile
    if policy==CONTINUATION_BUDGET_POLICY:
        inherited=context['continuation']['inherited']
        maximum=inherited['limits']['max_provider_calls'];used=inherited['counts']['provider_calls']
        if (not options['planned_workflow'] or options['provider']!='groq'
                or type(maximum) is not int or maximum not in {5,7,9}
                or type(used) is not int or not 0<=used<=maximum):
            raise ValueError('Invalid inherited continuation recovery budget')
        return min(profile,maximum-used)
    if policy!=BUDGET_POLICY or not options['planned_workflow']:raise ValueError('Unknown recovery call budget policy')
    seed=next((r for r in records if r['stage']=='seed_generation'),None)
    if seed is None or profile==0:return profile
    entry=next((r for r in records if r['stage']=='planned_input'),None)
    if entry is None:raise ValueError('Missing original Planned budget input')
    count=context['call_budget_inputs']['parse_error_count']
    if type(count) is not int or not 0<=count<=len(context['snapshot']['files']):
        raise ValueError('Invalid recorded Planned routing input')
    from .planner import routing,limits
    route=routing(entry['payload']['evidence'],context['snapshot'],{'parse_errors':range(count)},seed['payload']['annotation'])
    return min(profile,limits(route['max_retrieval_rounds'])['max_provider_calls'])


def policy_sha256():
    package=Path(__file__).resolve().parents[1]
    names=('investigation/stage_recovery.py','investigation/stages.py','investigation/pipeline.py','investigation/resume.py',
           'investigation/controller.py','investigation/planner.py','investigation/planned_waiting.py',
           'investigation/intake.py','investigation/verifier.py','repository/snapshot.py','repository/graph.py',
           'retrieval/__init__.py','retrieval/search.py','retrieval/focused.py','retrieval/gap_directed.py','retrieval/repository_context.py',
           'investigation/clarifications.py','investigation/fork_origins.py','runtime/io.py',
           'investigation/typesafe_controller.py','investigation/typesafe_recovery.py','runtime/typesafe.py',
           'annotations/generator.py','annotations/models.py','annotations/public_inputs.py','annotations/structured.py',
           'investigation/providers.py','runtime/config.py','runtime/budget.py')
    return digest({name:sha256((package/name).read_bytes()).hexdigest() for name in names})


def saved_commit(records):
    """Locate a declared full commit before Git; full binding follows in Tape."""
    try:
        if not isinstance(records,list) or not 1<=len(records)<=22:
            raise ValueError('Recovery requires a bounded completed stage prefix')
        header=records[0]
        commit=header['payload']['context']['snapshot']['commit_sha']
        if (header['stage']!='execution_input' or not isinstance(commit,str)
                or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}',commit)):
            raise ValueError('Recovery needs its original pinned commit')
        return commit
    except (KeyError,TypeError,IndexError):
        raise ValueError('Invalid recovery journal header') from None


class StageTape:
    def __init__(self,case,snapshot,graph,options,*,on_stage,records=None,prior_provider_calls=None,on_attempt=None,continuation=None,fork=None,controller=None):
        from .resume import validate_product_case
        if fork is None:validate_product_case(case,snapshot.protocol)
        else:
            from .fork_origins import validate_case
            validate_case(case,snapshot.protocol,fork)
        if ('instance_id' in case or not callable(on_stage) or not isinstance(options['provider'],str)
                or options['provider'] not in {'none','groq'}
                or options['model']!=('openai/gpt-oss-120b' if options['provider']=='groq' else 'none') or type(options['top_k']) is not int
                or not 1<=options['top_k']<=50 or type(options['max_output_tokens']) is not int
                or not 0<options['max_output_tokens']<=65536
                or on_attempt is not None and not callable(on_attempt)):
            raise ValueError('Recovery requires an explicit bounded original product recipe')
        descriptor=snapshot.to_dict()
        descriptor['files']=[{k:v for k,v in file.to_dict().items() if k!='text'} for file in snapshot.files]
        self.context={'schema':SCHEMA,'case':deepcopy(case),'snapshot':descriptor,
            'graph_sha256':digest(graph.to_dict()),'options':deepcopy(options),'policy_sha256':policy_sha256()}
        if fork is not None:self.context['fork']=deepcopy(fork)
        if controller is not None:
            if (set(controller)!={'name','model','origin_job_id'} or controller['name']!='jev'
                    or controller['model']!='jev-1.13.0' or not re.fullmatch('[a-f0-9]{24}',controller['origin_job_id'])
                    or not options['planned_workflow'] or options['planner_policy']!='bounded_planner_v2'
                    or fork is not None or continuation is not None):
                raise ValueError('Invalid original Typesafe recovery authority')
            self.context['controller']=deepcopy(controller)
        if continuation is not None:
            self.context.update(call_budget_policy=CONTINUATION_BUDGET_POLICY,continuation=deepcopy(continuation))
        elif options['planned_workflow']:
            self.context.update(call_budget_policy=BUDGET_POLICY,call_budget_inputs={'parse_error_count':len(graph.parse_errors)})
        self.context_sha=digest(self.context)
        self.base={'schema':'product_investigation_stage_v1','case_id':case['case_id'],
            'query_sha256':sha256(case['problem_statement'].encode()).hexdigest(),
            'snapshot_sha256':snapshot.snapshot_sha256,'commit_sha':snapshot.commit_sha,
            'provider':options['provider'],'model':options['model'],'refinement_rounds':options['refinement_rounds'],
            'evaluation_performed':False,'automatic_retry':False}
        if options['planned_workflow']:self.base['planned_workflow']=True
        self.records=deepcopy(records or []);self.completed=[];self.reused=[];self.on_stage=on_stage
        self.on_attempt=on_attempt;self.running_stage=None
        self.limit=0 if options['provider']=='none' else 9 if options['planned_workflow'] else 6 if options['refinement_rounds'] else 4
        if records is not None:saved_commit(records)
        if (records is None and prior_provider_calls not in (None,0)
                or records is not None and (type(prior_provider_calls) is not int or prior_provider_calls<0)
                or prior_provider_calls is not None and prior_provider_calls>self.limit):
            raise ValueError('Recovery needs known inherited call attempts within the original bound')
        self.inherited=prior_provider_calls or 0;self.calls=self.inherited
        previous=None;last_calls=0
        from .stages import STAGES
        for index,record in enumerate(self.records):
            try:
                proof=record['recovery']
                if (set(record)!=set(self.base)|{'stage','payload','recovery'}
                        or record['stage'] not in STAGES or not isinstance(record['payload'],dict)
                        or record['automatic_retry'] is not False or record['evaluation_performed'] is not False
                        or any(record[k]!=v for k,v in self.base.items())
                        or set(proof)!={'schema','context_sha256','index','previous_record_canonical_sha256','input_sha256','provider_calls'}
                        or proof['schema']!=SCHEMA or proof['context_sha256']!=self.context_sha
                        or proof['index']!=index or type(proof['index']) is not int
                        or proof['previous_record_canonical_sha256']!=previous
                        or not re.fullmatch('[a-f0-9]{64}',proof['input_sha256'])
                        or type(proof['provider_calls']) is not int
                        or not last_calls<=proof['provider_calls']<=self.inherited):
                    raise ValueError('Recovery source, sequence, recipe or call binding changed')
                previous=digest(record);last_calls=proof['provider_calls']
            except (KeyError,TypeError,AttributeError):
                raise ValueError('Invalid recovery stage structure') from None
        self.commit('execution_input',{'context':self.context})

    def guard(self):
        if policy_sha256()!=self.context['policy_sha256']:
            raise ValueError('Recovery policy changed')

    def _cached(self,stage,inputs):
        self.guard();index=len(self.completed)
        if index>=len(self.records):return None
        record=self.records[index]
        if record['stage']!=stage or record['recovery']['input_sha256']!=digest(inputs):
            raise ValueError('Recovery journal is not the exact completed execution prefix')
        self.completed.append(record)
        self.reused.append({'stage':stage,'record_canonical_sha256':digest(record),
            'recorded_provider_calls':record['recovery']['provider_calls']})
        return deepcopy(record['payload'])

    def commit(self,stage,payload,*,inputs=None):
        inputs=payload if inputs is None else inputs
        cached=self._cached(stage,inputs)
        if cached is not None:
            if digest(cached)!=digest(payload):raise ValueError('Recovered deterministic stage differs from its source')
            return
        record=dict(self.base,stage=stage,payload=deepcopy(payload),recovery={
            'schema':SCHEMA,'context_sha256':self.context_sha,'index':len(self.completed),
            'previous_record_canonical_sha256':digest(self.completed[-1]) if self.completed else None,
            'input_sha256':digest(inputs),'provider_calls':self.calls})
        self.on_stage(stage,record)
        self.completed.append(record)

    def run(self,stage,inputs,produce):
        cached=self._cached(stage,inputs)
        if cached is not None:
            self.check_call_limit();return cached
        self.running_stage=stage
        try:payload=produce()
        finally:self.running_stage=None
        self.commit(stage,payload,inputs=inputs)
        self.check_call_limit()
        return deepcopy(payload)

    def check_call_limit(self):
        limit=recorded_call_limit(self.context,self.completed)
        if self.calls>limit:raise RecoveryBudgetExhausted(self.calls,limit)
        return limit

    def wrap(self,complete):
        if hasattr(complete,'model') and complete.model!=self.base['model']:
            raise ValueError('Recovery transport model differs from its saved recipe')
        def call(fn,*args,**kwargs):
            self.guard()
            limit=self.check_call_limit()
            if self.calls>=limit:raise RecoveryBudgetExhausted(self.calls,limit)
            self.calls+=1  # Count attempted calls, including transport/schema failures.
            if self.on_attempt is not None:
                self.on_attempt({'schema':'product_pipeline_call_attempt_v1','context_sha256':self.context_sha,
                    'stage':self.running_stage,'provider_calls':self.calls,'max_provider_calls':self.limit})
            self.guard()
            answer=fn(*args,**kwargs);self.guard();return answer
        def wrapped(messages,max_output_tokens):return call(complete,messages,max_output_tokens)
        if hasattr(complete,'complete_structured'):
            def structured(messages,*,max_output_tokens,schema):
                return call(complete.complete_structured,messages,max_output_tokens=max_output_tokens,schema=schema)
            wrapped.complete_structured=structured
        if hasattr(complete,'complete_json'):
            def json_mode(messages,*,max_output_tokens):
                return call(complete.complete_json,messages,max_output_tokens=max_output_tokens)
            wrapped.complete_json=json_mode
        return wrapped

    def finish(self,pack):
        self.guard()
        effective=self.check_call_limit()
        if len(self.completed)<len(self.records):raise ValueError('Recovery left completed source stages unused')
        pack['stage_recovery']={'schema':SCHEMA,'context_sha256':self.context_sha,
            'policy_sha256':self.context['policy_sha256'],'reused_stages':deepcopy(self.reused),
            'inherited_provider_calls':self.inherited,'new_provider_calls':self.calls-self.inherited,
            'max_provider_calls':self.limit,'call_count_kind':'attempt_upper_bound','automatic_retry':False,'billing_verified':False}
        if effective<self.limit:pack['stage_recovery']['effective_max_provider_calls']=effective
        return pack
