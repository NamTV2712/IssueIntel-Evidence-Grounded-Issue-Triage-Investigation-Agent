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


def policy_sha256():
    package=Path(__file__).resolve().parents[1]
    names=('investigation/stage_recovery.py','investigation/stages.py','investigation/pipeline.py','investigation/resume.py',
           'investigation/controller.py','investigation/planner.py','investigation/planned_waiting.py',
           'investigation/intake.py','investigation/verifier.py','repository/snapshot.py','repository/graph.py',
           'retrieval/__init__.py','retrieval/search.py','retrieval/focused.py','retrieval/gap_directed.py','retrieval/repository_context.py',
           'investigation/clarifications.py','runtime/io.py',
           'annotations/generator.py','annotations/models.py','annotations/public_inputs.py','annotations/structured.py',
           'investigation/providers.py','runtime/config.py','runtime/budget.py')
    return digest({name:sha256((package/name).read_bytes()).hexdigest() for name in names})


def saved_commit(records):
    """Locate a declared full commit before Git; full binding follows in Tape."""
    try:
        if not isinstance(records,list) or not 1<=len(records)<=18:
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
    def __init__(self,case,snapshot,graph,options,*,on_stage,records=None,prior_provider_calls=None,on_attempt=None):
        from .resume import validate_product_case
        validate_product_case(case,snapshot.protocol)
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
            if cached!=payload:raise ValueError('Recovered deterministic stage differs from its source')
            return
        record=dict(self.base,stage=stage,payload=deepcopy(payload),recovery={
            'schema':SCHEMA,'context_sha256':self.context_sha,'index':len(self.completed),
            'previous_record_canonical_sha256':digest(self.completed[-1]) if self.completed else None,
            'input_sha256':digest(inputs),'provider_calls':self.calls})
        self.on_stage(stage,record)
        self.completed.append(record)

    def run(self,stage,inputs,produce):
        cached=self._cached(stage,inputs)
        if cached is not None:return cached
        self.running_stage=stage
        try:payload=produce()
        finally:self.running_stage=None
        self.commit(stage,payload,inputs=inputs)
        return deepcopy(payload)

    def wrap(self,complete):
        if hasattr(complete,'model') and complete.model!=self.base['model']:
            raise ValueError('Recovery transport model differs from its saved recipe')
        def call(fn,*args,**kwargs):
            self.guard()
            if self.calls>=self.limit:raise ValueError('Original product provider-call budget exhausted')
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
        if len(self.completed)<len(self.records):raise ValueError('Recovery left completed source stages unused')
        pack['stage_recovery']={'schema':SCHEMA,'context_sha256':self.context_sha,
            'policy_sha256':self.context['policy_sha256'],'reused_stages':deepcopy(self.reused),
            'inherited_provider_calls':self.inherited,'new_provider_calls':self.calls-self.inherited,
            'max_provider_calls':self.limit,'call_count_kind':'attempt_upper_bound','automatic_retry':False,'billing_verified':False}
        return pack
