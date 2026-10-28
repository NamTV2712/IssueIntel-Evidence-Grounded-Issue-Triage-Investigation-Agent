"""A recovered outcome joins inference without rewriting or rebilling history."""
from copy import deepcopy

import pytest

from issueintel.benchmarks import component_successor as successor
from issueintel.benchmarks import component_response_recovery as recovery
from issueintel.benchmarks import component_continuation as core
from issueintel.benchmarks import component_ablation_run as native

from test_component_ablation import workspace
from test_component_ablation_run import runtime
from test_component_continuation_run import failed_pilot, run
from test_component_response_recovery import interrupted_verifier, prepare, API


@pytest.fixture
def saved_recovery(interrupted_verifier):
    fixture, before = interrupted_verifier
    value = prepare(fixture)
    recovery.save_response_recovery(fixture[0], value, base_api=API, parent_core=core)
    return fixture, before, value


def options():
    return {'base_api': API, 'parent_core': core, 'recovery_adapter': recovery}


def test_admission_preserves_calls_parent_and_unknown_time(saved_recovery, monkeypatch):
    fixture, before, candidate = saved_recovery
    root, _, _, parent_plan, _, _, physical, _, _ = fixture
    count = len(physical)
    import issueintel.runtime.io as io
    monkeypatch.setattr(io, 'load_local_env', lambda *a: pytest.fail('credentials opened'))
    monkeypatch.setattr(native.RuntimeConfig, 'ledger', lambda *a: pytest.fail('ledger opened'))
    value = successor.prepare_successor(root, candidate['sha256'], **options())
    path = successor.save_successor(root, value, **options())
    assert successor.save_successor(root, value, **options()) == path
    view = successor.load_successor_view(root, value['sha256'], **options())
    assert view['parent_view']['results'] == before['results']
    assert view['parent_view']['files'] == before['files']
    assert view['base_files'] == before['base_files']
    assert view['calls'] == before['calls'] and len(physical) == count
    assert view['results'][:-1] == before['results']
    arm, stage = value['recovered_arm'], value['recovered_stage']
    assert arm['pack'] == candidate['pack']
    assert arm['schema'] == successor.ARM_SCHEMA and stage['schema'] == successor.STAGE_SCHEMA
    assert arm['pipeline_latency_seconds'] is arm['preparation_seconds'] is None
    assert arm['preparation_measurement_id'] is stage['latency_seconds'] is None
    old = next(row for row in before['stages'] if row['status'] == 'intent')
    assert old in view['audit_stages'] and old in view['stages']
    assert view['execution_stages'][old['stage_key']] == stage
    assert stage['call_ids'] == old['call_ids']
    assert arm['audit_stage_refs'][0]['sha256'] == old['sha256']
    assert view['progress']['terminal_arms'] == len(before['results']) + 1
    assert view['progress']['physical_provider_calls'] == len(before['calls'])
    assert view['progress']['original_timing_unknown_arms'] == 1
    assert view['progress']['new_provider_calls'] == 0
    assert view['progress']['inference_sealed'] is view['progress']['execution_authorized'] is False
    # Every existing failed/unknown-bound receipt remains an original physical
    # owner; recovery adds only references, never a duplicate receipt or charge.
    receipts_before = {rid: rec for call in before['calls'] for rid, rec in call['receipts'].items()}
    receipts_after = {rid: rec for call in view['calls'] for rid, rec in call['receipts'].items()}
    assert receipts_before == receipts_after
    api_before = core.load_effective_view(root, parent_plan, base_api=API)
    assert api_before['results'] == before['results']  # Old view deliberately stays unchanged.


@pytest.mark.parametrize('field', ['timing', 'cost', 'authority', 'pack', 'refs', 'limits', 'parent_results'])
def test_forged_successor_cannot_be_published_even_with_new_hash(saved_recovery, field):
    fixture, _, candidate = saved_recovery
    root = fixture[0]
    value = successor.prepare_successor(root, candidate['sha256'], **options())
    value = deepcopy(value)
    if field == 'timing':
        value['recovered_arm']['pipeline_latency_seconds'] = 0
    elif field == 'cost':
        value['parent_calls'] = {}
    elif field == 'authority':
        value['execution_authorized'] = True
    elif field == 'pack':
        value['recovered_arm']['pack']['verification'] = {}
    elif field == 'refs':
        value['recovered_arm']['audit_stage_refs'] = []
    elif field == 'limits':
        value['limits']['max_provider_calls_total'] = 626
    else:
        value['parent_results'] = {}
    value = core.seal({key: item for key, item in value.items() if key != 'sha256'})
    with pytest.raises(ValueError, match='does not replay'):
        successor.save_successor(root, value, **options())


def test_saved_reopen_replays_parent_and_recovery_not_just_seals(saved_recovery):
    fixture, _, candidate = saved_recovery
    root = fixture[0]
    value = successor.prepare_successor(root, candidate['sha256'], **options())
    path = successor.save_successor(root, value, **options())
    forged = core.seal({**{k: v for k, v in value.items() if k != 'sha256'}, 'quality_claim': True})
    forged_path = successor._path(root, forged['sha256'], core)
    core.write_new(root, forged_path, forged)
    with pytest.raises(ValueError, match='differs from native'):
        successor.load_successor_view(root, forged['sha256'], **options())
    assert path.exists()
    (root/'src/issueintel/synthetic.py').write_text('VERSION=99')
    with pytest.raises(ValueError):
        successor.load_successor_view(root, value['sha256'], **options())


def test_parent_grows_after_candidate_successor_is_denied(saved_recovery):
    fixture, _, candidate = saved_recovery
    root, _, _, parent_plan, *_ = fixture
    # A new immutable parent intent is enough to invalidate the captured
    # recovery inventory; admission cannot silently absorb concurrent work.
    core.write_new(root, core.overlay_directory(root, parent_plan)/'new-audit.json',
                   core.seal({'schema': 'synthetic_new_parent_event'}))
    with pytest.raises(ValueError, match='does not replay'):
        successor.prepare_successor(root, candidate['sha256'], **options())


def test_old_runner_stays_closed_after_new_view_admission(saved_recovery):
    fixture, _, candidate = saved_recovery
    value = successor.prepare_successor(fixture[0], candidate['sha256'], **options())
    successor.save_successor(fixture[0], value, **options())
    calls = len(fixture[6])
    with pytest.raises(ValueError, match='Interrupted native stage'):
        run(fixture, limit=25)
    assert len(fixture[6]) == calls
