"""Owned Jev decision-prefix/tail replay; synthetic clients only."""
from copy import deepcopy

import pytest

from issueintel.investigation.controller import Action,DecisionState
from issueintel.investigation.planner import choose_action
from issueintel.investigation.typesafe_controller import (TypesafePlannedController,
    validate_controller_decisions)
from issueintel.investigation.typesafe_recovery import (RecoveringTypesafeController,
    TypesafeRecoveryBudgetExhausted,TypesafeRecoveryError)
from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import MODEL
from test_planned_pipeline import planned_repository
from test_typesafe_controller import FakeClient,run_controlled,reseal


ORIGIN='a'*24
CHILD='b'*24


class BoundaryStop(OSError):pass


class DecisionTape:
    def __init__(self,entries=None,*,fail_commit=False):
        self.context={'controller':{'name':'jev','model':MODEL,'origin_job_id':ORIGIN}}
        self.context_sha=digest(self.context)
        self.saved=deepcopy(entries or []);self.completed=[];self.fail_commit=fail_commit

    def run(self,stage,inputs,produce):
        index=len(self.completed)
        if index<len(self.saved):
            value=deepcopy(self.saved[index])
            if value['stage']!=stage or value['inputs']!=digest(inputs):raise ValueError('changed prefix')
            self.completed.append(value);return deepcopy(value['payload'])
        payload=produce()
        if self.fail_commit:raise BoundaryStop('Synthetic failure after durable decision')
        self.completed.append({'stage':stage,'inputs':digest(inputs),'payload':deepcopy(payload)})
        return deepcopy(payload)


@pytest.fixture
def decision_case(planned_repository):
    pack,_,_=run_controlled(planned_repository)
    plan=pack['workflow_plan'];known={row['id']:row for row in pack['evidence']}
    current=[known[identifier] for identifier in plan['seed_evidence_ids']]
    annotation=plan['seed_annotation'];states=[]
    for index,step in enumerate(plan['rounds'],1):
        state=DecisionState(sum(row['kind']=='code' for row in current),len(annotation['hypotheses']),
            tuple(pack['intake']['missing_information']),plan['limits']['max_retrieval_rounds']-index+1)
        states.append((deepcopy(current),deepcopy(annotation),state,(Action.RETRIEVE,Action.VERIFY,Action.STOP)))
        current += [known[identifier] for identifier in step['added_ids']]
        if step['output_annotation'] is not None:annotation=step['output_annotation']
    states.append((deepcopy(current),deepcopy(annotation),DecisionState(
        sum(row['kind']=='code' for row in current),len(annotation['hypotheses']),
        tuple(pack['intake']['missing_information']),0),(Action.VERIFY,Action.STOP)))
    assert len(states)==4
    return pack,states


def controller(tape,*,owner=ORIGIN,prior=0,saved=None,client=None,attempts=None,decisions=None):
    client=client or FakeClient()
    delegate=TypesafePlannedController(client,owner,on_decision=(decisions.append if decisions is not None else None))
    wrapped=RecoveringTypesafeController(delegate,tape,origin_job_id=ORIGIN,owner_job_id=owner,
        prior_attempts=prior,saved_records=saved,on_attempt=(attempts.append if attempts is not None else None))
    return wrapped,client


def decide_at(wrapped,pack,view):
    evidence,annotation,state,allowed=view
    wrapped.bind_context(pack['case'],pack['snapshot'],evidence,annotation)
    return wrapped.decide(state,allowed)


def test_initial_controller_captures_four_owned_attempts_and_exports_v2(decision_case):
    pack,states=decision_case;tape=DecisionTape();attempts=[];decisions=[]
    wrapped,client=controller(tape,attempts=attempts,decisions=decisions)
    for view in states:decide_at(wrapped,pack,view)
    sidecar=wrapped.export_decisions(pack)
    assert len(client.calls)==len(attempts)==len(decisions)==4
    assert [a['controller_calls'] for a in attempts]==[1,2,3,4]
    assert [a['decision_index'] for a in attempts]==[1,2,3,4]
    assert all(a['owner_job_id']==ORIGIN and a['context_sha256']==tape.context_sha for a in attempts)
    assert sidecar['schema']=='typesafe_controller_v2' and sidecar['decision_owners']==[ORIGIN]*4
    assert sidecar['recovery']['inherited_calls']==0 and sidecar['recovery']['new_calls']==4
    assert sidecar['records']==decisions
    assert validate_controller_decisions(dict(pack,controller_decisions=sidecar))==sidecar


def test_cached_prefix_and_saved_tail_replay_without_client_or_old_observer(decision_case):
    pack,states=decision_case;source=DecisionTape();old,old_client=controller(source)
    for view in states:decide_at(old,pack,view)
    # The first record was checkpointed; three durable decisions are a saved tail.
    replay=DecisionTape(source.completed[:1]);attempts=[];decisions=[]
    wrapped,client=controller(replay,owner=CHILD,prior=4,saved=old.records,
        attempts=attempts,decisions=decisions)
    client.decide=lambda *a:pytest.fail('Completed Jev decision called again')
    for view in states:decide_at(wrapped,pack,view)
    sidecar=wrapped.export_decisions(pack)
    assert client.calls==attempts==decisions==[]
    assert old_client.calls and wrapped.records==old.records
    assert sidecar['decision_owners']==[ORIGIN]*4
    assert sidecar['recovery']['inherited_calls']==4 and sidecar['recovery']['new_calls']==0
    assert source.completed==replay.completed


def test_failure_after_durable_decision_before_tape_commit_reuses_exact_tail(decision_case):
    pack,states=decision_case;source=DecisionTape(fail_commit=True);saved=[];attempts=[]
    old,client=controller(source,attempts=attempts,decisions=saved)
    with pytest.raises(BoundaryStop):decide_at(old,pack,states[0])
    assert len(client.calls)==len(saved)==len(attempts)==1 and not source.completed
    recovered_tape=DecisionTape();new_attempts=[];new_decisions=[]
    recovered,new_client=controller(recovered_tape,owner=CHILD,prior=1,saved=saved,
        attempts=new_attempts,decisions=new_decisions)
    for view in states:decide_at(recovered,pack,view)
    sidecar=recovered.export_decisions(pack)
    assert len(new_client.calls)==len(new_attempts)==len(new_decisions)==3
    assert [a['controller_calls'] for a in new_attempts]==[2,3,4]
    assert [a['decision_index'] for a in new_attempts]==[2,3,4]
    assert sidecar['records'][0]==saved[0]
    assert sidecar['decision_owners']==[ORIGIN,CHILD,CHILD,CHILD]
    assert sidecar['recovery']['new_calls']==3


def test_missing_decision_attempt_debit_is_not_reset_or_converted_to_rules(decision_case):
    pack,states=decision_case;wrapped,client=controller(DecisionTape(),owner=CHILD,prior=4,saved=[])
    evidence,annotation,state,_=states[0]
    wrapped.bind_context(pack['case'],pack['snapshot'],evidence,annotation)
    with pytest.raises(TypesafeRecoveryBudgetExhausted) as raised:
        choose_action(wrapped,state.evidence_count,state.hypotheses_count,
            state.missing_information,state.rounds_remaining)
    assert raised.value.used==raised.value.limit==4 and client.calls==[]


def test_interrupted_missing_decision_uses_remaining_attempt_not_another_full_budget(decision_case):
    pack,states=decision_case;attempts=[]
    wrapped,client=controller(DecisionTape(),owner=CHILD,prior=3,saved=[],attempts=attempts)
    decide_at(wrapped,pack,states[0])
    assert attempts[0]['controller_calls']==4 and attempts[0]['decision_index']==1
    with pytest.raises(TypesafeRecoveryBudgetExhausted):decide_at(wrapped,pack,states[1])
    assert len(client.calls)==1


@pytest.mark.parametrize('mutation',['state','owner','allowed','receipt','index'])
def test_saved_decision_tamper_stops_without_fallback_or_provider(decision_case,mutation):
    pack,states=decision_case;saved=deepcopy(pack['controller_decisions']['records'][:1])
    if mutation=='state':saved[0]['state']['issue']['text']='Different issue'
    elif mutation=='owner':saved[0]['binding']['job_id']=CHILD
    elif mutation=='allowed':saved[0]['allowed']=['stop']
    elif mutation=='receipt':saved[0]['receipt']['status']='reserved'
    else:saved[0]['binding']['decision_index']=2
    wrapped,client=controller(DecisionTape(),owner=CHILD,prior=1,saved=saved)
    evidence,annotation,state,_=states[0]
    wrapped.bind_context(pack['case'],pack['snapshot'],evidence,annotation)
    with pytest.raises(TypesafeRecoveryError):
        choose_action(wrapped,state.evidence_count,state.hypotheses_count,
            state.missing_information,state.rounds_remaining)
    assert client.calls==[]


def test_preview_boundary_stops_before_constructed_lazy_client_is_used(decision_case):
    class NeedCall(Exception):pass
    pack,states=decision_case;events=[]
    def attempt(value):events.append(value);raise NeedCall()
    wrapped,client=controller(DecisionTape())
    wrapped.on_attempt=attempt
    with pytest.raises(NeedCall):decide_at(wrapped,pack,states[0])
    assert len(events)==1 and events[0]['stage']=='planned_decision_1' and not client.calls


def test_attempt_observer_failure_halts_before_client_without_rule_fallback(decision_case):
    pack,states=decision_case;wrapped,client=controller(DecisionTape())
    def failed(_):raise ValueError('Synthetic persistence failure')
    wrapped.on_attempt=failed
    with pytest.raises(TypesafeRecoveryError):decide_at(wrapped,pack,states[0])
    assert not client.calls


def test_unconsumed_saved_tail_cannot_export_a_shorter_rewritten_path(decision_case):
    pack,states=decision_case;wrapped,_=controller(DecisionTape(),owner=CHILD,prior=4,
        saved=pack['controller_decisions']['records'])
    decide_at(wrapped,pack,states[0])
    with pytest.raises(TypesafeRecoveryError):wrapped.export_decisions(pack)


@pytest.mark.parametrize('mutate',[
    lambda s:s['decision_owners'].__setitem__(0,CHILD),
    lambda s:s['recovery'].update(inherited_calls=True),
    lambda s:s['recovery'].update(new_calls=5),
    lambda s:s['recovery'].update(automatic_retry=True),
    lambda s:s['recovery'].update(origin_job_id=CHILD),
    lambda s:s['recovery'].update(owner_job_id=CHILD),
])
def test_v2_owner_and_attempt_telemetry_tamper_fails_after_reseal(decision_case,mutate):
    pack,states=decision_case;wrapped,_=controller(DecisionTape())
    for view in states:decide_at(wrapped,pack,view)
    sidecar=wrapped.export_decisions(pack);mutate(sidecar);reseal(sidecar)
    with pytest.raises(ValueError):validate_controller_decisions(dict(pack,controller_decisions=sidecar))
