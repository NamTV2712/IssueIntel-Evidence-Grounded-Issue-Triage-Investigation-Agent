"""Saved verifier responses recover offline without changing frozen journals."""
from copy import deepcopy
from pathlib import Path

import pytest

from issueintel.benchmarks import component_ablation as registration
from issueintel.benchmarks import component_ablation_run as native
from issueintel.benchmarks import component_continuation as core
from issueintel.benchmarks import component_continuation_run as runner
from issueintel.benchmarks import component_response_recovery as recovery
from issueintel.runtime.io import digest

from test_component_ablation import workspace
from test_component_ablation_run import runtime
from test_component_continuation_run import failed_pilot, run, continuation_authorization


API = (registration, native)


@pytest.fixture
def interrupted_verifier(failed_pilot, monkeypatch):
    root, spec, _, plan, _, _, _, _, _ = failed_pilot
    run(failed_pilot)
    continuation_authorization(root, plan, spec, count=25)
    target_case = spec['cases'][1]['case_id']
    original = runner._write

    def crash(root, path, value, **kwargs):
        if (value.get('schema') == runner.STAGE_SCHEMA and value.get('status') == 'completed'
                and value['binding']['case_id'] == target_case
                and value['binding']['stage'] == 'verification'
                and value['binding']['inputs'].get('independent_critic') is True):
            raise KeyboardInterrupt('Synthetic exit after settled response, before payload publication')
        return original(root, path, value, **kwargs)

    monkeypatch.setattr(runner, '_write', crash)
    with pytest.raises(KeyboardInterrupt):
        run(failed_pilot, limit=25)
    monkeypatch.setattr(runner, '_write', original)
    view = core.load_effective_view(root, plan, base_api=API)
    assert len([stage for stage in view['stages'] if stage['status'] == 'intent']) == 1
    return failed_pilot, view


def prepare(fixture):
    root, _, _, plan, *_ = fixture
    return recovery.prepare_response_recovery(root, plan['sha256'], base_api=API, parent_core=core)


def test_response_replays_native_verifier_without_calls_or_original_mutation(interrupted_verifier, monkeypatch):
    fixture, before = interrupted_verifier
    root, _, _, plan, _, _, calls, _, _ = fixture
    count = len(calls)
    monkeypatch.setattr(native.RuntimeConfig, 'ledger', lambda *a: pytest.fail('ledger opened'))
    import issueintel.runtime.io as io
    monkeypatch.setattr(io, 'load_local_env', lambda *a: pytest.fail('credentials opened'))
    value = prepare(fixture)
    assert value['schema'] == recovery.SCHEMA
    assert value['status'] == 'candidate_not_admitted_to_inference'
    assert value['execution_authorized'] is value['parent_mutated'] is value['gold_read'] is False
    assert value['provider_calls'] == 0 and len(calls) == count
    assert value['timing'] == {
        'original_stage_latency_seconds': None, 'original_arm_pipeline_seconds': None,
        'original_arm_preparation_seconds': None, 'original_arm_preparation_measurement_id': None,
        'status': 'not_recorded_before_interruption'}
    target = next(stage for stage in before['stages'] if stage['status'] == 'intent')
    assert value['original_stage']['sha256'] == target['sha256']
    assert digest(value['pack']['annotation']) == target['binding']['inputs']['annotation_sha256']
    assert digest(value['pack']['evidence']) == target['binding']['inputs']['evidence_sha256']
    assert value['pack']['verification'] == value['verification_payload']['verification']
    assert value['pack']['verification']['kind'] == 'llm_separate_pass'
    assert value['pack']['leakage_audit']['strict_issue_time_verified'] is True
    assert value['original_call']['reservation_ids']
    after = core.load_effective_view(root, plan, base_api=API)
    assert before['files'] == after['files'] and before['base_files'] == after['base_files']
    assert recovery.prepare_response_recovery(root, plan['sha256'], base_api=API, parent_core=core) == value


def test_publication_replays_and_reopen_does_not_admit_paid_continuation(interrupted_verifier):
    fixture, _ = interrupted_verifier
    root, _, _, _, _, _, calls, _, _ = fixture
    value = prepare(fixture)
    path = recovery.save_response_recovery(root, value, base_api=API, parent_core=core)
    assert path == root/'artifacts/experiments/component_response_recovery'/value['sha256']/'recovery.json'
    assert recovery.save_response_recovery(root, value, base_api=API, parent_core=core) == path
    assert recovery.load_response_recovery(root, value['sha256'], base_api=API, parent_core=core) == value
    count = len(calls)
    with pytest.raises(ValueError, match='Interrupted native stage'):
        run(fixture, limit=25)
    assert len(calls) == count


@pytest.mark.parametrize('field,value', [
    ('execution_authorized', True), ('provider_calls', 1), ('quality_claim', True),
    ('timing', {'original_stage_latency_seconds': 0}), ('pack_sha256', 'f'*64),
])
def test_rehashed_invented_outcome_or_time_is_not_publishable(interrupted_verifier, field, value):
    fixture, _ = interrupted_verifier
    original = prepare(fixture)
    forged = core.seal({**{k:v for k,v in original.items() if k != 'sha256'}, field:value})
    with pytest.raises(ValueError, match='does not replay'):
        recovery.save_response_recovery(fixture[0], forged, base_api=API, parent_core=core)


def test_changed_registered_source_denies_candidate(interrupted_verifier):
    fixture, _ = interrupted_verifier
    root = fixture[0]
    (root/'src/issueintel/synthetic.py').write_text('VERSION=99')
    with pytest.raises(ValueError):
        prepare(fixture)


@pytest.mark.parametrize('mutation', ['missing_response', 'unsettled', 'unknown_usage', 'generation', 'multiple_intents'])
def test_only_single_settled_verifier_response_is_recoverable(interrupted_verifier, mutation):
    fixture, original = interrupted_verifier
    view = deepcopy(original)
    stage, call = recovery._target(view)
    if mutation == 'missing_response':
        call['status'] = 'intent'
    elif mutation == 'unsettled':
        next(iter(call['receipts'].values()))['status'] = 'reserved'
    elif mutation == 'unknown_usage':
        next(iter(call['receipts'].values()))['status'] = 'missing_usage_bound'
    elif mutation == 'generation':
        stage['binding']['stage'] = 'seed_generation'
    else:
        view['stages'].append(deepcopy(stage))
    with pytest.raises(ValueError):
        recovery._target(view)


def test_stored_critique_requires_exact_request_and_single_consumption():
    request = {'messages':[{'role':'system','content':'exact system'},
                           {'role':'user','content':'{"a":1,"b":[2,3]}'}],
               'schema':{'type':'object'},'max_output_tokens':2048,
               'model':'openai/gpt-oss-120b','reasoning_effort':'medium'}
    call = {'request':request,'response':{'reviews':[]}}
    stored = recovery._StoredCritique(call, core)
    messages = deepcopy(request['messages'])
    messages[1]['content'] = '{ "b": [2,3], "a": 1 }'
    assert stored.complete_structured(messages,max_output_tokens=2048,schema={'type':'object'}) == call['response']
    with pytest.raises(ValueError):
        stored.complete_structured(messages,max_output_tokens=2048,schema={'type':'object'})
    for changed in ['{"a":1,"b":[3,2]}','{"a":1,"a":1,"b":[2,3]}',
                    '{"a":NaN,"b":[2,3]}','{"a":true,"b":[2,3]}','{"a":1.0,"b":[2,3]}']:
        messages[1]['content'] = changed
        with pytest.raises(ValueError):
            recovery._StoredCritique(call,core).complete_structured(messages,max_output_tokens=2048,schema={'type':'object'})
