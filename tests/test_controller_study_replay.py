"""Cache-only replay against actual synthetic native study journals."""
from copy import deepcopy

import pytest

from issueintel.evaluation import controller_study as registration
from issueintel.evaluation import controller_study_replay as replay
from issueintel.evaluation.controller_study_execution import StudyExecution
from issueintel.investigation.controller import RuleController
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.planner import validate_planned
from issueintel.investigation.typesafe_controller import TypesafePlannedController
from issueintel.runtime.study_budget import StudyBudgetLedger
from test_controller_study_run import registered, native_http
from test_controller_study import sources, planned_repository
from test_planned_pipeline import PlannedModel


@pytest.fixture
def completed_pair(registered, native_http, request):
    root, spec, loader = registered
    directory = registration.study_directory(root, spec['study_id'])
    budget = StudyBudgetLedger(directory / 'budget', spec['budget_policy'])
    configs = registration.runtime_configs(root, spec)
    selected = spec['cases'][0]
    unit = selected['unit_id']
    context = registration.load_case(root, spec, unit, source_loader=loader)
    native_http['settings']['jev_error'] = getattr(request, 'param', False)
    rows = []
    for arm in ('rules', 'jev'):
        ex = StudyExecution(root, directory, unit, arm, selected['product_policy'],
            *configs, budget, lambda: None, provider_factories=native_http['factories'])
        controller = (RuleController() if arm == 'rules' else TypesafePlannedController(
            ex.typesafe_client, ex.job_id, on_decision=ex.on_decision))
        pack = investigate(context['case'], context['snapshot'].repository,
            snapshot=context['snapshot'], graph=context['graph'], protocol='benchmark_base',
            complete_json=ex.groq, provider='groq', model='openai/gpt-oss-120b',
            top_k=12, independent_critic=True, max_output_tokens=4096,
            refinement_rounds=0, planned_workflow=True, planner_policy='bounded_planner_v2',
            controller=controller, product_study_policy=selected['product_policy'],
            product_study_stage_executor=ex.stage)
        rows.append(registration.seal({'schema': 'controller_study_arm_v1',
            'study_id': spec['study_id'], 'registration_sha256': spec['sha256'],
            'unit_id': unit, 'arm_id': arm, 'source': selected['context'],
            'status': 'completed', 'pack': pack, 'failure': None,
            'stage_refs': ex.stage_refs, 'call_ids': ex.call_ids,
            'owned_call_ids': ex.owned_call_ids, 'stop_reason': pack['stop_reason']}))
    return root, spec, rows, {unit: ex.inspect()}, loader, native_http


def check(fixture):
    root, spec, rows, views, loader, _ = fixture
    return replay.replay_results(root, spec, rows, views, source_loader=loader)


def reseal(value):
    value['sha256'] = registration.digest({key: item for key, item in value.items() if key != 'sha256'})


@pytest.mark.parametrize('completed_pair', [False, True], indirect=True, ids=['selected', 'closed-fallback'])
def test_actual_rules_and_jev_replay_without_env_provider_or_budget_mutation(completed_pair, monkeypatch):
    root, spec, rows, views, loader, http = completed_pair
    request_count, constructors = len(http['requests']), list(http['constructors'])
    directory = registration.study_directory(root, spec['study_id'])
    before = registration.inventory(root, directory)
    def forbidden(*args, **kwargs):
        pytest.fail('Saved pack replay cannot open credentials, provider, ledger or target runtime')
    import issueintel.runtime.io as runtime_io
    import issueintel.investigation.providers as providers
    import issueintel.runtime.typesafe as typesafe
    import issueintel.runtime.study_budget as study_budget
    monkeypatch.setattr(runtime_io, 'load_local_env', forbidden)
    monkeypatch.setattr(providers, 'GroqProvider', forbidden)
    monkeypatch.setattr(typesafe, 'TypesafeClient', forbidden)
    monkeypatch.setattr(study_budget, 'StudyBudgetLedger', forbidden)
    assert check(completed_pair) is True
    assert len(http['requests']) == request_count and http['constructors'] == constructors
    assert registration.inventory(root, directory) == before


def test_another_structurally_valid_native_pack_cannot_replace_recorded_output(completed_pair):
    root, spec, rows, _, loader, _ = completed_pair
    selected = spec['cases'][0]
    context = registration.load_case(root, spec, selected['unit_id'], source_loader=loader)
    model = PlannedModel()
    def synthetic_generation(messages, max_output_tokens):
        content = model(messages, max_output_tokens)
        for index, row in enumerate(content.get('hypotheses', []), 1):
            row['id'] = f'H{index}'
            row['statement'] = 'Different but structurally admissible: ' + row['statement']
        return content
    replacement = investigate(context['case'], context['snapshot'].repository,
        snapshot=context['snapshot'], graph=context['graph'], protocol='benchmark_base',
        complete_json=synthetic_generation, provider='groq', model='openai/gpt-oss-120b',
        top_k=12, independent_critic=True, max_output_tokens=4096,
        refinement_rounds=0, planned_workflow=True, planner_policy='bounded_planner_v2',
        controller=RuleController())
    validate_planned(replacement)
    assert replacement != rows[0]['pack']
    rows[0]['pack'] = replacement
    reseal(rows[0])
    # Native structural validation alone accepts this alternative output. The
    # recorded full annotations and trajectory must still reject substitution.
    with pytest.raises(replay.ControllerReplayError, match='full native stage replay'):
        check(completed_pair)


@pytest.mark.parametrize('arm', ['rules', 'jev'])
def test_rehashed_completed_pack_cannot_change_hypothesis(completed_pair, arm):
    row = next(row for row in completed_pair[2] if row['arm_id'] == arm)
    row['pack']['annotation']['hypotheses'][0]['statement'] = 'A replacement statement with valid citation shape.'
    reseal(row)
    with pytest.raises((ValueError, OSError)):
        check(completed_pair)


@pytest.mark.parametrize('mutation', ['missing', 'binding', 'payload', 'reference', 'status'])
def test_native_completed_stage_required_exactly(completed_pair, mutation):
    view = next(iter(completed_pair[3].values()))
    stage = next(row for row in view['stages'] if row['kind'] == 'seed_generation')
    if mutation == 'missing':
        view['stages'].remove(stage)
    elif mutation == 'binding':
        stage['binding']['inputs']['synthetic_injected_key'] = True
        stage['key'] = registration.digest(stage['binding'])
    elif mutation == 'payload':
        stage['payload']['synthetic_injected_key'] = True
        stage['payload_sha256'] = registration.digest(stage['payload'])
    elif mutation == 'reference':
        stage['references'].remove(completed_pair[2][0]['unit_id'] + ':rules')
    else:
        stage['status'] = 'pending'
    with pytest.raises((ValueError, OSError)):
        check(completed_pair)


@pytest.mark.parametrize('mutation', ['missing', 'wrong-record', 'wrong-request', 'wrong-owner', 'wrong-receipt', 'wrong-response'])
def test_jev_native_decisions_are_bound_to_actual_owned_call(completed_pair, mutation):
    view = next(iter(completed_pair[3].values()))
    decision = view['decisions'][0]
    call = next(row for row in view['calls'] if row['call_id'] == decision['call_id'])
    if mutation == 'missing':
        view['decisions'].remove(decision)
    elif mutation == 'wrong-record':
        decision['record']['state']['decision']['rounds_remaining'] = 0
    elif mutation == 'wrong-request':
        call['request_sha256'] = 'f' * 64
    elif mutation == 'wrong-owner':
        call['arm_id'] = completed_pair[2][0]['unit_id'] + ':rules'
    elif mutation == 'wrong-receipt':
        call['receipts'][-1]['reservation_id'] = 'f' * 32
    else:
        call['result']['response']['answers']['next_action']['choice'] = 'stop'
    with pytest.raises((ValueError, OSError)):
        check(completed_pair)


@pytest.mark.parametrize('mutation', ['missing-ref', 'extra-ref', 'duplicate-stage', 'duplicate-result', 'wrong-context'])
def test_exact_trajectory_and_selection_are_required(completed_pair, mutation):
    _, _, rows, views, _, _ = completed_pair
    view = next(iter(views.values()))
    if mutation == 'missing-ref':
        rows[0]['stage_refs'].pop(); reseal(rows[0])
    elif mutation == 'extra-ref':
        rows[0]['stage_refs'].append(deepcopy(rows[0]['stage_refs'][0])); reseal(rows[0])
    elif mutation == 'duplicate-stage':
        view['stages'].append(deepcopy(view['stages'][0]))
    elif mutation == 'duplicate-result':
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]['source']['case_sha256'] = 'f' * 64; reseal(rows[0])
    with pytest.raises((ValueError, OSError)):
        check(completed_pair)
