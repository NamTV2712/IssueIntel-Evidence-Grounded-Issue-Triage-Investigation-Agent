"""Synthetic planned workflow checks; no real provider or benchmark data."""
from copy import deepcopy
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from issueintel.investigation.controller import Action
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.planner import seal_plan, validate_planned, planned_run_id, routing
from issueintel.investigation.stages import StageCheckpoints
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from issueintel.retrieval import retrieve_evidence
from issueintel.retrieval.focused import expand_for_hypotheses
from issueintel.runtime.budget import BudgetDenied


@pytest.fixture
def planned_repository(tmp_path):
    path=tmp_path/'repository';path.mkdir()
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=','-C',str(path),*args]).decode().strip()
    git('init','-q')
    sources={'parser.py':'def parse_list(items):\n    return [item.strip() for item in items]\n',
             'fallback.py':'def recover_none(sentinel):\n    return [] if sentinel is None else sentinel\n',
             'formatting.py':'def render_label(display):\n    return str(display)\n',
             'validation.py':'def validate_flag(validity):\n    return bool(validity)\n',
             'syntax_gap.py':'def incomplete(\n'}
    for name,source in sources.items():(path/name).write_text(source,encoding='utf-8')
    git('add','.')
    git('-c','user.name=Synthetic','-c','user.email=test@example.invalid',
        '-c','commit.gpgsign=false','commit','-qm','Synthetic planned fixture')
    case={'case_id':'synthetic:planned','repository':'synthetic/repository','revision':git('rev-parse','HEAD'),
          'problem_statement':'parser.parse_list crashes'}
    return case,path


class PlannedModel:
    def __init__(self,focuses=None,fail_generation=None):
        self.focuses=focuses or ['fallback recover_none sentinel','formatting render_label display',
                                'validation validate_flag validity','parser parse_list items']
        self.calls=[];self.generations=0;self.fail_generation=fail_generation

    def __call__(self,messages,max_output_tokens):
        payload=json.loads(messages[1]['content']);self.calls.append(payload)
        if 'case' in payload:
            self.generations+=1
            if self.generations==self.fail_generation:
                raise BudgetDenied('Synthetic budget denial; no retry')
            focus=self.focuses[min(self.generations-1,len(self.focuses)-1)]
            eid=next(row['id'] for row in payload['evidence'] if row['kind']=='code')
            return {'hypotheses':[{'id':f'h{index}','statement':f'{focus} condition {index}',
                    'support_ids':[eid],'counter_ids':[],'counter_status':'not_found',
                    'falsification_test':f'Inspect {focus} condition {index}'} for index in range(3)],
                    'abstention':{'label':'uncertain','reason':'No runtime reproduction'}}
        return {'reviews':[{'hypothesis_id':row['id'],'assessment':'uncertain',
                'reason':'Not executed','evidence_ids':row['support_ids']} for row in payload['hypotheses']]}


def run_planned(planned_repository,model=None,**kwargs):
    case,path=planned_repository
    return investigate(case,path,top_k=1,planned_workflow=True,complete_json=model,**kwargs)


def test_three_observed_gap_rounds_follow_changed_queries_then_one_critic(planned_repository):
    model=PlannedModel();pack=run_planned(planned_repository,model)
    plan=validate_planned(pack)
    assert plan['routing']['kind']=='observable_heuristic'
    assert plan['routing']['max_retrieval_rounds']==3 and 'snapshot_gaps' in plan['routing']['reason_codes']
    assert len(plan['rounds'])==3 and all(row['added_ids'] for row in plan['rounds'])
    assert model.generations==4 and len(model.calls)==5
    assert plan['generation_passes']==4 and plan['verifier_passes']==1 and plan['provider_calls']==5
    assert plan['stop_reason']==pack['stop_reason']=='round_limit'
    assert plan['limits']['max_provider_calls']==9
    assert all(row['revision']==pack['snapshot']['commit_sha'] for row in pack['evidence'] if row['kind']=='code')
    inventories=[{row['id'] for row in call['evidence']} for call in model.calls]
    assert all(before<after for before,after in zip(inventories[:3],inventories[1:4]))
    assert inventories[-2]==inventories[-1]
    assert all(row['correctness']=='unverified' for row in pack['annotation']['hypotheses'])
    assert 'refinement' not in pack and pack['leakage_audit']['strict_issue_time_verified'] is False


def test_repeated_queries_skip_search_and_generation_but_verify_once(planned_repository,monkeypatch):
    model=PlannedModel(focuses=['fallback recover_none sentinel'])
    import issueintel.retrieval.focused as focused
    original=focused.retrieve_evidence;search_calls=[]
    def searched(*args,**kwargs):search_calls.append(args[0]);return original(*args,**kwargs)
    monkeypatch.setattr(focused,'retrieve_evidence',searched)
    pack=run_planned(planned_repository,model);plan=validate_planned(pack)
    assert len(plan['rounds'])==2 and len(search_calls)==3
    assert plan['rounds'][-1]['search']['queries']==[]
    assert all(row['executed'] is False for row in plan['rounds'][-1]['search']['skipped_queries'])
    assert plan['stop_reason']=='no_new_queries' and model.generations==2 and len(model.calls)==3
    assert plan['rounds'][-1]['output_annotation_sha256'] is None


def test_no_new_evidence_does_not_regenerate(planned_repository):
    model=PlannedModel(focuses=['parser parse_list items'])
    pack=run_planned(planned_repository,model)
    assert pack['workflow_plan']['stop_reason']=='no_new_evidence'
    assert model.generations==1 and len(model.calls)==2
    validate_planned(pack)


def test_invalid_controller_falls_back_to_bounded_rules(planned_repository):
    controller=SimpleNamespace(name='invalid',decide=lambda *args:'run_shell')
    pack=run_planned(planned_repository,PlannedModel(),controller=controller)
    assert all(row['fallback'] for row in pack['workflow_plan']['rounds'])
    assert pack['workflow_plan']['final_decision']['fallback'] is True
    validate_planned(pack)


def test_jev_internal_fallback_is_declared_in_every_saved_decision(planned_repository):
    from issueintel.investigation.controller import JevController
    controller=JevController(lambda _: {'action':'run_shell'})
    pack=run_planned(planned_repository,PlannedModel(),controller=controller)
    plan=validate_planned(pack)
    assert all(row['fallback'] and row['controller']=='jev' for row in plan['rounds'])
    assert plan['final_decision']['fallback'] is True


@pytest.mark.parametrize('action',[Action.VERIFY,Action.STOP])
def test_controller_can_terminate_without_extra_search(planned_repository,action):
    model=PlannedModel();controller=SimpleNamespace(name='selected',decide=lambda *args:action)
    pack=run_planned(planned_repository,model,controller=controller)
    plan=validate_planned(pack)
    assert len(plan['rounds'])==1 and plan['rounds'][0]['search'] is None
    assert model.generations==1 and len(model.calls)==(2 if action==Action.VERIFY else 1)
    assert plan['stop_reason']==('controller_skipped_retrieval' if action==Action.VERIFY else 'controller_stopped')


def test_no_critic_option_has_no_fake_model_pass(planned_repository):
    model=PlannedModel()
    pack=run_planned(planned_repository,model,independent_critic=False)
    assert pack['workflow_plan']['verifier_passes']==0
    assert pack['verification']['kind']=='structural' and len(model.calls)==4
    validate_planned(pack)


def test_offline_plan_has_zero_calls_and_bound_sources(planned_repository,tmp_path):
    checkpoints=StageCheckpoints(tmp_path,tmp_path/'artifacts/stages')
    pack=run_planned(planned_repository,on_stage=checkpoints)
    plan=validate_planned(pack)
    assert plan['seed_annotation'] is None and plan['rounds']==[]
    assert plan['generation_passes']==plan['verifier_passes']==plan['provider_calls']==0
    assert plan['stop_reason']=='provider_not_configured'
    assert set(checkpoints.receipts)=={'planned_input','planned_complete'}
    assert pack['annotation'] is None and pack['verification'] is None


def test_insufficient_evidence_plan_does_not_call_provider(planned_repository):
    case,path=planned_repository
    case=dict(case,problem_statement='unfindablezqxword')
    pack=investigate(case,path,planned_workflow=True,complete_json=lambda *a,**k:pytest.fail('called'))
    assert validate_planned(pack)['stop_reason']=='insufficient_evidence'


def test_numbered_retrieval_receipt_survives_generation_budget_denial(planned_repository,tmp_path):
    checkpoints=StageCheckpoints(tmp_path,tmp_path/'artifacts/stages')
    model=PlannedModel(fail_generation=3)
    with pytest.raises(BudgetDenied):
        run_planned(planned_repository,model,on_stage=checkpoints)
    assert model.generations==3 and len(model.calls)==3
    assert set(checkpoints.receipts)=={'planned_input','seed_generation','planned_retrieval_1',
                                        'planned_generation_1','planned_retrieval_2'}
    for ref in checkpoints.receipts.values():
        raw=(tmp_path/ref['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==ref['sha256']
        record=json.loads(raw)
        assert record['planned_workflow'] is True and record['automatic_retry'] is False


def test_successful_checkpoints_are_numbered_and_no_stage_is_overwritten(planned_repository,tmp_path):
    checkpoints=StageCheckpoints(tmp_path,tmp_path/'artifacts/stages')
    pack=run_planned(planned_repository,PlannedModel(),on_stage=checkpoints)
    assert set(checkpoints.receipts)=={'planned_input','seed_generation','planned_retrieval_1',
        'planned_retrieval_2','planned_retrieval_3','planned_generation_1','planned_generation_2',
        'planned_generation_3','verification','planned_complete'}
    record=json.loads((tmp_path/checkpoints.receipts['planned_complete']['path']).read_bytes())
    assert record['payload']['workflow_plan']==pack['workflow_plan']
    with pytest.raises(ValueError,match='repeated'):
        checkpoints('planned_retrieval_1',{})


def test_schema_repair_slots_are_included_in_maximum_call_bound(planned_repository):
    class RepairModel(PlannedModel):
        attempts=0
        def __call__(self,messages,max_output_tokens):
            payload=json.loads(messages[1]['content'])
            if 'case' in payload:
                self.attempts+=1
                if self.attempts%2:
                    self.calls.append(payload)
                    return {'hypotheses':[],'abstention':{'label':'uncertain','reason':'Malformed synthetic response'}}
            return super().__call__(messages,max_output_tokens)
    model=RepairModel();pack=run_planned(planned_repository,model);plan=validate_planned(pack)
    assert len(model.calls)==plan['provider_calls']==plan['limits']['max_provider_calls']==9
    assert plan['generation_passes']==4 and plan['verifier_passes']==1
    assert plan['seed_annotation']['provenance']['repair_count']==1
    assert all(row['output_annotation']['provenance']['repair_count']==1 for row in plan['rounds'])


def test_route_uses_only_frozen_seed_observations(planned_repository):
    case,path=planned_repository;snapshot=build_snapshot(path,case['revision']);graph=build_graph(snapshot)
    evidence=retrieve_evidence('parser parse_list fallback recover_none formatting render_label',snapshot,graph,top_k=3)
    descriptor=snapshot.to_dict();graph_descriptor=graph.to_dict();graph_descriptor['parse_errors']=[]
    assert routing(evidence[:1],descriptor,graph_descriptor,None)['max_retrieval_rounds']==1
    assert routing(evidence[:2],descriptor,graph_descriptor,None)['max_retrieval_rounds']==2
    hypotheses={'hypotheses':[{'counter_status':'not_found'} for _ in range(3)]}
    assert routing(evidence[:1],descriptor,graph_descriptor,hypotheses)['max_retrieval_rounds']==2
    assert routing(evidence[:3],descriptor,graph_descriptor,hypotheses)['max_retrieval_rounds']==3
    descriptor['skipped']=[{'path':'omitted.py','reason':'file_limit'}]
    route=routing(evidence[:1],descriptor,graph_descriptor,None)
    assert route['max_retrieval_rounds']==3 and route['reason_codes']==['snapshot_gaps']


@pytest.mark.parametrize('change,option',[
    ({'split':'main_dev'},{}),({'split':None},{}),({'instance_id':'django__django-1'},{}),
    ({'case_id':'django__django-1'},{}),({'case_id':'https://github.com/a/b/pull/1'},{}),
    ({},{'protocol':'issue_time'}),({},{'refinement_rounds':1}),
    ({},{'prepared_evidence':[]}),({},{'resumable':True}),
])
def test_planned_boundary_denies_before_io_or_provider(planned_repository,monkeypatch,change,option):
    case,path=planned_repository
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',lambda *a,**k:pytest.fail('Snapshot IO'))
    with pytest.raises(ValueError,match='Planned'):
        investigate(dict(case,**change),path,planned_workflow=True,
                    complete_json=lambda *a,**k:pytest.fail('Provider called'),**option)


def test_cli_local_id_and_canonical_issue_are_product_scoped(planned_repository):
    case,path=planned_repository
    for changed in [dict(case,case_id='local'),dict(case,case_id='local:sample'),
                    dict(case,case_id='https://github.com/example/repository/issues/1',
                         issue_url='https://github.com/example/repository/issues/1',repository='example/repository')]:
        pack=investigate(changed,path,planned_workflow=True)
        validate_planned(pack)
    with pytest.raises(ValueError,match='canonical'):
        investigate(dict(case,case_id='https://github.com/example/repository/issues/1',
                         issue_url='https://github.com/other/repository/issues/1'),path,planned_workflow=True)


def test_legacy_modes_keep_default_metadata_and_call_count(planned_repository):
    case,path=planned_repository
    for rounds,expected in [(0,2),(1,3)]:
        model=PlannedModel()
        pack=investigate(case,path,top_k=1,refinement_rounds=rounds,complete_json=model)
        assert len(model.calls)==expected and 'workflow_plan' not in pack
        assert ('refinement' in pack)==bool(rounds)


@pytest.mark.parametrize('history',[(),('original',)])
def test_focused_default_trace_stays_legacy_and_explicit_history_deduplicates(planned_repository,history):
    case,path=planned_repository;snapshot=build_snapshot(path,case['revision']);graph=build_graph(snapshot)
    evidence=retrieve_evidence(case['problem_statement'],snapshot,graph,top_k=1)
    eid=evidence[0]['id'];prefix='fallback recover_none sentinel '+'x'*650
    hypotheses=[{'id':f'h{i}','statement':prefix+str(i),'support_ids':[eid],
                 'counter_ids':[],'counter_status':'not_found','falsification_test':'inspect sentinel'} for i in range(3)]
    _,legacy=expand_for_hypotheses(hypotheses,evidence,snapshot,graph)
    assert 'excluded_query_sha256s' not in legacy and 'skipped_queries' not in legacy
    exclusions={hashlib.sha256(case['problem_statement'].encode()).hexdigest()} if history else ()
    _,planned=expand_for_hypotheses(hypotheses,evidence,snapshot,graph,exclude_query_sha256s=exclusions)
    assert len(planned['queries'])==1 and len(planned['skipped_queries'])==2
    assert planned['excluded_query_sha256s']==sorted(exclusions)


def _reseal(pack):
    pack['workflow_plan']=seal_plan(pack['workflow_plan'])
    pack['run_id']=planned_run_id(pack['case'],pack['snapshot']['snapshot_sha256'],pack['evidence'],
                                 pack['annotation'],pack['workflow_plan'],pack.get('repository_context'))


@pytest.mark.parametrize('mutation',[
    lambda p:p['workflow_plan']['source'].update(query_sha256='0'*64),
    lambda p:p['workflow_plan']['limits'].update(max_retrieval_rounds=50),
    lambda p:p['workflow_plan'].update(generation_passes=0),
    lambda p:p['workflow_plan']['routing']['signals'].update(code_files=40),
    lambda p:p['workflow_plan']['rounds'][0].update(input_annotation_sha256='0'*64),
    lambda p:p['workflow_plan']['rounds'][0].update(input_evidence_ids=[]),
    lambda p:p['workflow_plan']['rounds'][0]['search']['queries'][0].update(query='invented query'),
    lambda p:p['workflow_plan']['rounds'][0]['search'].update(excluded_query_sha256s=[]),
    lambda p:p['workflow_plan']['rounds'][0].update(output_annotation_sha256='0'*64),
    lambda p:p['workflow_plan']['rounds'][0]['search']['queries'][0].update(added_ids=[]),
    lambda p:p['workflow_plan'].update(stop_reason='verified_fix'),
    lambda p:p['workflow_plan'].update(provider_calls=100),
])
def test_validator_rejects_resealed_linkage_policy_and_budget_tampering(planned_repository,mutation):
    pack=run_planned(planned_repository,PlannedModel());mutation(pack);_reseal(pack)
    with pytest.raises(ValueError):validate_planned(pack)


def test_seal_and_run_id_cannot_be_silently_replaced(planned_repository):
    pack=run_planned(planned_repository)
    changed=deepcopy(pack);changed['workflow_plan']['sha256']='0'*64
    with pytest.raises(ValueError,match='integrity'):validate_planned(changed)
    changed=deepcopy(pack);changed['run_id']='0'*24
    with pytest.raises(ValueError,match='identity'):validate_planned(changed)
