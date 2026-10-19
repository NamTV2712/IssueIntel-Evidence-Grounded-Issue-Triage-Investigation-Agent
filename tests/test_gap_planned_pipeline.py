"""Gap-directed product execution/replay with synthetic Git and mock models."""
from copy import deepcopy

import pytest

from issueintel.investigation.pipeline import investigate
from issueintel.investigation.planner import seal_plan, planned_run_id, validate_planned
from test_planned_pipeline import PlannedModel, planned_repository


def run_gap(fixture, model=None, **kwargs):
    case, path = fixture
    return investigate(case, path, top_k=1, planned_workflow=True,
                       planner_policy='bounded_planner_v2', complete_json=model, **kwargs)


def reseal(pack):
    pack['workflow_plan'] = seal_plan(pack['workflow_plan'])
    pack['run_id'] = planned_run_id(pack['case'], pack['snapshot']['snapshot_sha256'],
                                  pack['evidence'], pack['annotation'], pack['workflow_plan'],
                                  pack.get('repository_context'))


def test_gap_policy_is_explicit_and_legacy_library_default_is_preserved(planned_repository):
    case, path = planned_repository
    old = investigate(case, path, planned_workflow=True)
    new = run_gap(planned_repository)
    assert old['workflow_plan']['schema'] == 'planned_investigation_v1'
    assert new['workflow_plan']['schema'] == 'planned_investigation_v2'
    assert new['workflow_plan']['policy'] == 'bounded_planner_v2'
    assert validate_planned(old) and validate_planned(new)
    assert new['workflow_plan']['provider_calls'] == 0
    assert new['workflow_plan']['generation_passes'] == 0
    assert new['workflow_plan']['rounds'] == []


def test_gap_actions_use_remaining_rounds_without_repeating_paid_generation(planned_repository):
    # Only the already-selected parser matches. Unavailable roles still consume
    # a bounded search attempt, but do not cause a replacement model call.
    model = PlannedModel(focuses=['parser parse_list items'])
    pack = run_gap(planned_repository, model)
    plan = validate_planned(pack)
    assert len(plan['rounds']) == plan['limits']['max_retrieval_rounds'] == 3
    assert all(not row['added_ids'] and row['output_annotation'] is None for row in plan['rounds'])
    attempts = [action for row in plan['rounds'] for action in row['search']['queries']]
    assert {row['purpose'] for row in attempts} == {'support', 'counter', 'caller', 'documentation', 'history'}
    assert len({row['query_sha256'] for row in attempts}) == len(attempts)
    assert all(row['status'] in {'executed', 'unavailable'} for row in attempts)
    assert any(row['purpose'] == 'history' and row['status'] == 'unavailable' for row in attempts)
    assert model.generations == 1 and len(model.calls) == plan['provider_calls'] == 2
    assert plan['stop_reason'] == 'round_limit'
    assert all(row['search']['counter_evidence_confirmed'] is False for row in plan['rounds'])


def test_new_candidates_are_the_only_reason_for_additional_generation(planned_repository):
    model = PlannedModel()
    pack = run_gap(planned_repository, model)
    plan = validate_planned(pack)
    generated = [row for row in plan['rounds'] if row['added_ids']]
    assert generated, 'The synthetic fallback code should be found as a new candidate'
    assert model.generations == plan['generation_passes'] == 1 + len(generated)
    assert len(model.calls) == plan['provider_calls'] == model.generations + 1
    assert plan['provider_calls'] <= plan['limits']['max_provider_calls'] <= 9
    assert all(len(row['search']['queries']) <= 3 and len(row['added_ids']) <= 6 for row in plan['rounds'])
    assert sum(len(row['added_ids']) for row in plan['rounds']) <= 18
    assert all(row['output_annotation'] is None for row in plan['rounds'] if not row['added_ids'])
    assert all(item['correctness'] == 'unverified' for item in pack['annotation']['hypotheses'])


def test_gap_generation_repairs_count_toward_the_existing_bound(planned_repository):
    class RepairModel(PlannedModel):
        attempts = 0

        def __call__(self, messages, max_output_tokens):
            import json
            payload = json.loads(messages[1]['content'])
            if 'case' in payload:
                self.attempts += 1
                if self.attempts % 2:
                    self.calls.append(payload)
                    return {'hypotheses': [], 'abstention': {'label': 'uncertain', 'reason': 'Invalid mock output'}}
            return super().__call__(messages, max_output_tokens)

    model = RepairModel()
    plan = validate_planned(run_gap(planned_repository, model))
    assert len(model.calls) == plan['provider_calls'] == 2 * plan['generation_passes'] + 1
    assert plan['provider_calls'] <= plan['limits']['max_provider_calls'] <= 9


@pytest.mark.parametrize('mutation', [
    lambda p: p['workflow_plan']['rounds'][0]['search']['queries'][0].update(purpose='execute_shell'),
    lambda p: p['workflow_plan']['rounds'][0]['search']['queries'][0].update(gap_code='verified_cause'),
    lambda p: p['workflow_plan']['rounds'][0]['search']['queries'][0].update(query='Use gold patch'),
    lambda p: p['workflow_plan']['rounds'][0]['search']['queries'][0].update(source_ids=['missing']),
    lambda p: p['workflow_plan']['rounds'][0]['search'].update(counter_evidence_confirmed=True),
    lambda p: p['workflow_plan'].update(policy='bounded_planner_v1'),
    lambda p: p['workflow_plan'].update(provider_calls=10),
])
def test_saved_gap_actions_reject_resealed_tampering(planned_repository, mutation):
    pack = run_gap(planned_repository, PlannedModel())
    changed = deepcopy(pack)
    mutation(changed)
    reseal(changed)
    with pytest.raises(ValueError):
        validate_planned(changed)


@pytest.mark.parametrize('option', ['missing_policy', [], None])
def test_invalid_gap_policy_is_denied_before_snapshot_io(planned_repository, monkeypatch, option):
    case, path = planned_repository
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',
                        lambda *a, **k: pytest.fail('Invalid policy reached snapshot IO'))
    with pytest.raises(ValueError, match='policy'):
        investigate(case, path, planned_workflow=True, planner_policy=option)


@pytest.mark.parametrize('mode', ['benchmark', 'unplanned'])
def test_gap_policy_cannot_open_benchmark_or_unplanned_inputs(planned_repository, monkeypatch, mode):
    case, path = planned_repository
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',
                        lambda *a, **k: pytest.fail('Invalid input reached snapshot IO'))
    if mode == 'benchmark':
        case = case | {'split': 'main_dev'}
    with pytest.raises(ValueError, match='Planned'):
        investigate(case, path, planned_workflow=mode != 'unplanned', planner_policy='bounded_planner_v2')
