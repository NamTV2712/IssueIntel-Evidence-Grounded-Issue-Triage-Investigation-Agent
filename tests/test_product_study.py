"""Synthetic paired Planned stages; no credentials, providers or benchmark IO."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from issueintel.investigation.controller import RuleController
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.planner import validate_planned
from issueintel.investigation.product_study import (
    RECIPE, make_policy, source_hashes, stage_binding, validate_case, validate_policy)
from issueintel.investigation.typesafe_controller import TypesafePlannedController, validate_controller_decisions
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.io import digest
from test_planned_pipeline import planned_repository, PlannedModel
from test_typesafe_controller import FakeClient


MODEL = 'openai/gpt-oss-120b'


@pytest.fixture
def product_source(planned_repository):
    original, path = planned_repository
    case = {**original, 'case_id':'https://github.com/example/study/issues/7',
            'issue_url':'https://github.com/example/study/issues/7',
            'repository':'example/study', 'issue_numbers':[7]}
    snapshot = build_snapshot(path, case['revision'])
    graph = build_graph(snapshot)
    policy = make_policy(case,snapshot,graph,study_id='a'*64,
                         source_sha256='b'*64,runtime_sha256='c'*64)
    return case,path,snapshot,graph,policy


class Cache:
    def __init__(self):
        self.values = {}
        self.executed, self.reused, self.bindings = [], [], []

    def __call__(self, stage, binding, produce):
        key = digest(binding)
        self.bindings.append(deepcopy(binding))
        if key in self.values:
            self.reused.append(stage)
            return deepcopy(self.values[key])
        value = produce()
        self.executed.append(stage)
        self.values[key] = deepcopy(value)
        return value


def run_study(source, completion=None, executor=None, controller=None, **changes):
    case,path,snapshot,graph,policy = source
    options = dict(snapshot=snapshot,graph=graph,complete_json=completion or PlannedModel(),
        provider='groq',model=MODEL,top_k=12,independent_critic=True,max_output_tokens=4096,
        planned_workflow=True,planner_policy='bounded_planner_v2',
        controller=controller or RuleController(),product_study_policy=policy,
        product_study_stage_executor=executor or Cache())
    options.update(changes)
    return investigate(case,path,**options)


def test_pair_reuses_native_full_payload_and_logical_calls(product_source):
    cache = Cache()
    first_model, second_model = PlannedModel(), PlannedModel()
    first = run_study(product_source,first_model,cache)
    client = FakeClient()
    second = run_study(product_source,second_model,cache,
                       TypesafePlannedController(client,'d'*24))
    validate_planned(first)
    validate_planned(second)
    validate_controller_decisions(second)
    assert len(first_model.calls) > 0 and second_model.calls == []
    assert len(client.calls) == 4
    assert first['workflow_plan']['seed_annotation'] == second['workflow_plan']['seed_annotation']
    assert first['annotation']['provenance']['created_at'] == second['annotation']['provenance']['created_at']
    assert first['verification'] == second['verification']
    assert first['workflow_plan']['provider_calls'] == second['workflow_plan']['provider_calls'] == len(first_model.calls)
    assert first['workflow_plan']['limits'] == second['workflow_plan']['limits']
    assert {'initial_retrieval','seed_generation','verification'} <= set(cache.reused)
    assert all(row['schema']=='product_controller_study_stage_binding_v1' for row in cache.bindings)
    assert not any('controller' in row for row in cache.bindings)


def test_shared_repaired_seed_preserves_logical_failed_attempt(product_source):
    class RepairModel(PlannedModel):
        def __call__(self,messages,max_output_tokens):
            if not self.calls:
                self.calls.append({'invalid_response':True})
                return {'hypotheses':[],'abstention':{'label':'uncertain','reason':'Malformed synthetic generation'}}
            return super().__call__(messages,max_output_tokens)
    cache = Cache()
    first_model, other_model = RepairModel(), PlannedModel()
    first = run_study(product_source,first_model,cache)
    other = run_study(product_source,other_model,cache)
    seed = first['workflow_plan']['seed_annotation']
    assert seed['provenance']['repair_count'] == 1
    assert seed == other['workflow_plan']['seed_annotation']
    assert not other_model.calls
    assert first['workflow_plan']['provider_calls'] == other['workflow_plan']['provider_calls'] == len(first_model.calls)
    validate_planned(other)


@pytest.mark.parametrize('change', [
    {'protocol':'issue_time'}, {'provider':'none'}, {'model':'other'}, {'top_k':1},
    {'top_k':True}, {'independent_critic':False}, {'max_output_tokens':2048},
    {'planned_workflow':False}, {'planner_policy':'bounded_planner_v3'},
    {'refinement_rounds':1}, {'resumable':True}, {'recoverable':True},
    {'prepared_evidence':[]}, {'repository_context':{}}, {'historical_sources':[]},
    {'query_receipt':{}}, {'availability':{}}, {'planned_parent':{}},
    {'experiment_policy':{}}, {'experiment_stage_executor':lambda *a:None},
    {'controller':type('ForeignController',(),{'name':'rules','decide':lambda *a:None})()},
    {'product_study_stage_executor':None}, {'complete_json':None},
])
def test_invalid_recipe_rejected_before_source_or_provider_io(product_source,monkeypatch,change):
    import issueintel.investigation.pipeline as pipeline
    model = PlannedModel()
    monkeypatch.setattr(pipeline,'build_snapshot',lambda *a,**k:pytest.fail('unexpected source IO'))
    with pytest.raises(ValueError):
        run_study(product_source,model,snapshot=None,**change)
    assert model.calls == []


@pytest.mark.parametrize('change', [
    {'case_id':'verified:example__study-7'}, {'case_id':'synthetic:study'},
    {'case_id':'local:study'}, {'split':'main_dev'}, {'instance_id':'example__study-7'},
    {'parent_case_id':'https://github.com/example/study/issues/7'},
    {'patch':'restricted patch'}, {'issue_url':'https://github.com/example/study/issues/8'},
    {'repository':'other/study'}, {'repository':[]}, {'issue_url':[]},
    {'revision':'main'}, {'issue_numbers':[True]}, {'issue_created_at':{'non_json'}},
])
def test_only_original_canonical_public_product_identity(product_source,change):
    case = {**product_source[0],**change}
    with pytest.raises(ValueError):
        validate_case(case)


def test_policy_reseal_cannot_change_recipe_or_hide_code_changes(product_source,monkeypatch):
    policy = deepcopy(product_source[-1])
    policy['recipe']['independent_critic'] = 1
    policy['sha256'] = digest({key:value for key,value in policy.items() if key!='sha256'})
    with pytest.raises(ValueError):validate_policy(policy)
    import issueintel.investigation.product_study as study
    hashes = source_hashes()
    monkeypatch.setattr(study,'source_hashes',lambda:{**hashes,'investigation/verifier.py':'f'*64})
    with pytest.raises(ValueError):validate_policy(product_source[-1])


@pytest.mark.parametrize('target',['initial_retrieval','planned_retrieval_1'])
def test_injected_retrieval_rejected_before_later_model_call(product_source,target):
    model = PlannedModel()
    def injected(stage,binding,produce):
        value = produce()
        if stage == target:
            if stage == 'initial_retrieval':value['evidence'][0]['provenance']['score_is_probability'] = 0
            else:value['search']['max_queries'] = True
        return value
    with pytest.raises(ValueError,match='deterministic replay'):
        run_study(product_source,model,injected)
    assert len(model.calls) == (0 if target=='initial_retrieval' else 1)


def test_cached_annotation_cannot_change_evidence_inventory(product_source):
    def injected(stage,binding,produce):
        value = produce()
        if stage == 'seed_generation':value['annotation']['evidence_ids'] = ['fabricated']
        return value
    model = PlannedModel()
    with pytest.raises(ValueError):run_study(product_source,model,injected)
    assert len(model.calls) == 1


def test_shared_generation_round_cannot_use_boolean_as_integer(product_source):
    def injected(stage,binding,produce):
        value = produce()
        if stage == 'planned_generation_1':value['round'] = True
        return value
    model = PlannedModel()
    with pytest.raises(ValueError,match='JSON types'):
        run_study(product_source,model,injected)
    assert len(model.calls) == 2


@pytest.mark.parametrize('mutation', ['extra_review_field','human_flag','bias_flag','structural_field'])
def test_cached_critic_uses_exact_wire_and_native_contract(product_source,mutation):
    def injected(stage,binding,produce):
        value = produce()
        if stage == 'verification':
            review = value['verification']
            if mutation=='extra_review_field':review['reviews'][0]['not_requested'] = 'fabricated'
            elif mutation=='human_flag':review['independent_human'] = 0
            elif mutation=='bias_flag':review['same_model_bias_possible'] = 1
            else:review['reports'][0]['citation_integrity'] = 'false_confirmation'
        return value
    with pytest.raises(ValueError):run_study(product_source,PlannedModel(),injected)


def test_each_provider_call_checks_policy_before_schema_repair(product_source,monkeypatch):
    import issueintel.investigation.product_study as study
    hashes = source_hashes()
    class MutatingModel(PlannedModel):
        def __call__(self,messages,max_output_tokens):
            self.calls.append({'first_call':True})
            monkeypatch.setattr(study,'source_hashes',lambda:{**hashes,'annotations/generator.py':'f'*64})
            return {'hypotheses':[],'abstention':{'label':'uncertain','reason':'Synthetic invalid output'}}
    model = MutatingModel()
    with pytest.raises(ValueError,match='source code'):
        run_study(product_source,model)
    assert len(model.calls) == 1


def test_source_graph_change_invalidates_shared_stage(product_source):
    case,path,snapshot,graph,policy = product_source
    from dataclasses import replace
    other = replace(graph,parser_version=graph.parser_version+'-different')
    model = PlannedModel()
    with pytest.raises(ValueError,match='fixed graph'):
        run_study((case,path,snapshot,other,policy),model)
    assert model.calls == []


def test_binding_changes_with_public_inputs_but_not_controller(product_source):
    policy = product_source[-1]
    first = stage_binding(policy,'seed_generation',{'case_sha256':'d'*64,'evidence_sha256':'e'*64})
    changed = stage_binding(policy,'seed_generation',{'case_sha256':'d'*64,'evidence_sha256':'f'*64})
    assert digest(first) != digest(changed)
    assert first['recipe_sha256'] == digest(RECIPE)
    assert not any(name in first for name in ('arm_id','controller','receipts','gold'))
    with pytest.raises(ValueError):stage_binding(policy,'arbitrary_paid_stage',{})


def test_default_native_planned_output_unchanged_without_study(product_source,monkeypatch):
    import issueintel.annotations.generator as generator
    class FixedDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,25,tzinfo=timezone.utc)
    monkeypatch.setattr(generator,'datetime',FixedDateTime)
    first = run_study(product_source,PlannedModel())
    case,path,snapshot,graph,_ = product_source
    ordinary = investigate(case,path,snapshot=snapshot,graph=graph,complete_json=PlannedModel(),
        provider='groq',model=MODEL,top_k=12,independent_critic=True,max_output_tokens=4096,
        planned_workflow=True,planner_policy='bounded_planner_v2',controller=RuleController())
    assert first == ordinary
