"""Exact Jev decision replay with immutable owners and inherited attempt bounds.

The owning job service authenticates receipts and source ancestry. This adapter
replays each completed decision at its exact public state, including a durable
decision tail saved before its next pipeline checkpoint. It never re-publishes
old decisions or transfers their receipts to a new execution owner.
"""
from copy import deepcopy
import re

from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import MODEL,build_request,validate_receipt
from .controller import Action
from .typesafe_controller import (MAX_DECISIONS, RECOVERY_SCHEMA,
    TypesafePlannedController, _allowed, _binding, _record, _state,
    validate_controller_decisions)


class TypesafeRecoveryError(OSError):
    """Invalid recovery authority must stop rather than fall back to rules."""


class TypesafeRecoveryBudgetExhausted(TypesafeRecoveryError):
    def __init__(self,used,limit=MAX_DECISIONS):
        super().__init__('Original Typesafe controller attempt budget exhausted')
        self.used=used;self.limit=limit


class RecoveringTypesafeController:
    name='jev'

    def __init__(self,delegate,tape,*,origin_job_id,owner_job_id,
                 prior_attempts=0,saved_records=None,on_attempt=None,prior_receipts=None):
        if (not isinstance(delegate,TypesafePlannedController)
                or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{24}',v)
                       for v in (origin_job_id,owner_job_id))
                or delegate.job_id!=owner_job_id
                or type(prior_attempts) is not int or not 0<=prior_attempts<=MAX_DECISIONS
                or on_attempt is not None and not callable(on_attempt)
                or tape.context.get('controller')!={'name':'jev','model':MODEL,'origin_job_id':origin_job_id}
                or not callable(getattr(tape,'run',None))):
            raise ValueError('Typesafe recovery requires an exact owned controller recipe')
        if saved_records is not None and (not isinstance(saved_records,list)
                or len(saved_records)>MAX_DECISIONS or len(saved_records)>prior_attempts):
            raise ValueError('Invalid saved Typesafe decision prefix')
        self.saved_records=deepcopy(saved_records or [])
        if prior_receipts is not None and (not isinstance(prior_receipts,list) or len(prior_receipts)>prior_attempts):
            raise ValueError('Invalid prior Typesafe receipt inventory')
        self.prior_receipts=deepcopy(prior_receipts or [])
        self.explicit_saved=saved_records is not None
        for index,record in enumerate(self.saved_records,1):
            if (not isinstance(record,dict) or type(record.get('decision_index')) is not int
                    or record['decision_index']!=index):
                raise ValueError('Saved Typesafe decision prefix is not consecutive')
        self.delegate=delegate;self.tape=tape
        self.origin_job_id=origin_job_id;self.owner_job_id=owner_job_id
        self.inherited=prior_attempts;self.calls=prior_attempts;self.on_attempt=on_attempt
        self.records=[];self.last_fallback=False

    def bind_context(self,case,snapshot,evidence,annotation):
        self.delegate.bind_context(case,snapshot,evidence,annotation)

    def _validate(self,record,state,allowed,index):
        owner=record['binding']['job_id']
        if not isinstance(owner,str) or not re.fullmatch('[a-f0-9]{24}',owner):
            raise ValueError('Invalid saved Typesafe decision owner')
        _record(record,self.delegate.context,state,allowed,
                {'controller':'jev','action':record['action'],'fallback':record['fallback']},owner,index)
        return record

    def decide(self,state,allowed):
        try:return self._decide(state,allowed)
        except (KeyError,TypeError,ValueError,RuntimeError,AttributeError):
            # choose_action catches these types as ordinary controller failures.
            # A changed recovery source must instead halt the investigation.
            raise TypesafeRecoveryError('Typesafe recovery state or decision source changed') from None

    def _decide(self,state,allowed):
        index=len(self.records)+1
        if index>MAX_DECISIONS:raise TypesafeRecoveryBudgetExhausted(self.calls)
        inputs={'decision_index':index,
            'origin_binding':_binding(self.delegate.context,self.origin_job_id,index),
            'state':_state(self.delegate.context,state),'allowed':_allowed(state,allowed)}
        for receipt in self.prior_receipts:
            if receipt['binding']['decision_index']==index:
                binding=_binding(self.delegate.context,receipt['binding']['job_id'],index)
                validate_receipt(receipt,binding,digest(build_request(inputs['state'],inputs['allowed'],MODEL)))
        stage='planned_decision_'+str(index);produced=False
        def produce():
            nonlocal produced
            produced=True
            if index<=len(self.saved_records):
                record=deepcopy(self.saved_records[index-1])
                self._validate(record,state,allowed,index)
                return {'record':record}
            if self.calls>=MAX_DECISIONS:raise TypesafeRecoveryBudgetExhausted(self.calls)
            self.calls+=1
            attempt={'schema':'product_typesafe_call_attempt_v1','context_sha256':self.tape.context_sha,
                'stage':stage,'controller_calls':self.calls,'max_controller_calls':MAX_DECISIONS,
                'decision_index':index,'owner_job_id':self.owner_job_id}
            if self.on_attempt is not None:self.on_attempt(deepcopy(attempt))
            self.delegate.records=deepcopy(self.records)
            self.delegate.decide(state,allowed)
            if len(self.delegate.records)!=index:raise ValueError('Typesafe delegate decision count changed')
            record=deepcopy(self.delegate.records[-1])
            self._validate(record,state,allowed,index)
            return {'record':record}
        value=self.tape.run(stage,inputs,produce)
        if not isinstance(value,dict) or set(value)!={'record'}:raise ValueError('Changed Typesafe decision stage')
        record=self._validate(value['record'],state,allowed,index)
        if index<=len(self.saved_records):
            if digest(record)!=digest(self.saved_records[index-1]):raise ValueError('Typesafe decision differs from durable prefix')
        elif not produced and self.explicit_saved:
            raise ValueError('Completed Typesafe stage has no matching durable decision')
        self.records.append(deepcopy(record));self.delegate.records=deepcopy(self.records)
        self.last_fallback=record['fallback']
        return Action(record['action'])

    def export_decisions(self,pack):
        if len(self.records)<len(self.saved_records):
            raise TypesafeRecoveryError('Typesafe recovery left completed decisions unused')
        plan=pack['workflow_plan']
        sidecar={'schema':RECOVERY_SCHEMA,'provider':'typesafe','model':MODEL,
            'source':deepcopy(plan['source']),'workflow_plan_sha256':plan['sha256'],
            'job_id':self.origin_job_id,'max_decisions':MAX_DECISIONS,'records':deepcopy(self.records),
            'decision_owners':[record['binding']['job_id'] for record in self.records],
            'recovery':{'schema':'typesafe_stage_recovery_v1','owner_job_id':self.owner_job_id,
                'origin_job_id':self.origin_job_id,'inherited_calls':self.inherited,
                'new_calls':self.calls-self.inherited,'max_calls':MAX_DECISIONS,
                'automatic_retry':False,'billing_verified':False}}
        sidecar['sha256']=digest(sidecar)
        validate_controller_decisions(dict(pack,controller_decisions=sidecar))
        return sidecar
