"""Synthetic continuation admission and immutable overlay contracts; no HTTP."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import pytest

from issueintel.benchmarks import component_ablation as registration
from issueintel.benchmarks import component_ablation_run as native
from issueintel.benchmarks import component_continuation as continuation
from issueintel.investigation.providers import ProviderError
from issueintel.repository.snapshot import SourceFile
from issueintel.runtime.io import digest

from test_component_ablation import workspace, authorization, write
from test_component_ablation_run import runtime, setup, records


@pytest.fixture
def failed_pilot(workspace, runtime, tmp_path):
    root, contexts, ledger, calls, provider = runtime
    selected = registration._selected_rows(workspace[1])[0]
    case, snapshot, receipt = contexts[selected['case_id']]
    text = 'def sentinel(unavailable):\n    return unavailable if unavailable is not None else []\n'
    updated = replace(snapshot, files=snapshot.files + (SourceFile('sentinel.py', text, sha256(text.encode()).hexdigest()),))
    unsealed = asdict(updated); unsealed.pop('snapshot_sha256')
    contexts[selected['case_id']] = (case, replace(updated, snapshot_sha256=digest(unsealed)), receipt)
    spec, directory, auth = setup(workspace)

    class Disconnect(provider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            answer = super().complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
            payload = json.loads(messages[1]['content'])
            if 'case' in payload and any(e.get('locator', '').startswith('sentinel.py:') for e in payload['evidence']):
                rid = f'{len(calls):032x}'
                record = ledger.rows[rid]
                record.update(status='failed_bound', observed_input_tokens=None, observed_output_tokens=None,
                              tokens_charged_or_reserved=record['input_bound'] + record['output_bound'])
                self.on_receipt(record)
                raise ProviderError('Synthetic disconnect', diagnostics={
                    'failure_phase': 'request', 'transport_error_type': 'RemoteDisconnected',
                    'transport_reason_type': 'RemoteDisconnected'})
            return answer

    result = native.run_component_ablation(root, spec['sha256'], auth, provider_factory=Disconnect)
    assert result['completed_arms'] == 4 and result['failed_arms'] == 1
    # Adapter code lives outside the registered source/config inventory.
    adapter_dir = root / 'adapter'; adapter_dir.mkdir()
    paths = []
    for name in sorted(continuation.ADAPTER_NAMES):
        path = adapter_dir / name
        path.write_text('# synthetic pinned orchestration\n')
        paths.append(path)
    plan = continuation.create_plan(root, spec['sha256'], adapter_paths=paths)
    return root, spec, directory, auth, ledger, calls, paths, plan


def continuation_auth(fixture, *, count=1):
    root, spec, _, _, _, _, _, plan = fixture
    _, base_auth = authorization(root, spec, count=count)
    value = {'schema': continuation.AUTHORIZATION_SCHEMA, 'plan_sha256': plan['sha256'],
        'registration_sha256': spec['sha256'], 'code_fingerprint_sha256': plan['code_fingerprint_sha256'],
        'protocol_sha256': plan['protocol_sha256'], 'adapter_sources': plan['adapter_sources'],
        'case_ids': plan['case_ids'], 'provider': 'groq', 'model': 'openai/gpt-oss-120b',
        'reasoning_effort': 'medium', 'max_output_tokens': 2048, 'max_provider_calls_per_arm': 5,
        'max_provider_calls_total': count * 25, 'authorized_case_count': count,
        'phase': 'first_case_pilot' if count == 1 else 'registered_matrix',
        'author_request': 'Synthetic explicit single transport continuation', 'date': '2026-10-24',
        'execution_authorized': True, 'manual_transport_attempt_authorized': True,
        'gold_scoring_authorized': False, 'base_authorization_path': base_auth.relative_to(root).as_posix(),
        'base_authorization_sha256': sha256(base_auth.read_bytes()).hexdigest(),
        **{flag: False for flag in registration.ACCESS_FLAGS}}
    path = write(root / 'artifacts/continuation-authorization.json', value)
    return value, path


def test_preview_pins_original_failures_and_never_reads_environment_or_ledger(failed_pilot, monkeypatch):
    root, _, directory, _, _, _, paths, plan = failed_pilot
    before = native._inventory(root, directory)
    (root / '.env').write_text('DO_NOT_READ=secret')
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path.name == '.env':
            pytest.fail('environment read by preview')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    monkeypatch.setattr(native.RuntimeConfig, 'ledger', lambda *a: pytest.fail('ledger read by preview'))
    again = continuation.create_plan(root, plan['registration_sha256'], adapter_paths=paths)
    assert again == plan
    assert plan['limits']['referenced_calls_before'] == 2
    assert plan['limits']['max_new_calls_for_target'] == 3
    assert plan['limits']['max_provider_calls_total'] == 625
    assert len(plan['completed_arms']) == 4
    assert not plan['execution_authorized'] and not plan['gold_read']
    assert native._inventory(root, directory) == before


def test_plan_publication_is_idempotent_and_global_hash_lookup_is_exact(failed_pilot):
    root, _, directory, _, _, _, _, plan = failed_pilot
    before = native._inventory(root, directory)
    path = continuation.save_plan(root, plan)
    assert path == root / 'artifacts/experiments/component_continuation' / plan['sha256'] / 'plan.json'
    assert continuation.save_plan(root, plan) == path
    loaded, overlay = continuation.load_plan(root, plan['sha256'])
    assert loaded == plan and overlay == path.parent
    assert native._inventory(root, directory) == before
    changed = deepcopy(plan); changed['quality_claim'] = True
    changed = continuation.seal({key: value for key, value in changed.items() if key != 'sha256'})
    with pytest.raises(ValueError, match='Immutable'):
        continuation.write_new(root, path, changed)


@pytest.mark.parametrize('full_replay', [True, False])
@pytest.mark.parametrize('change', ['source', 'adapter', 'inventory', 'bound', 'runtime'])
def test_changed_source_adapter_inventory_or_bound_denies_plan(failed_pilot, change, full_replay):
    root, _, directory, _, _, _, paths, plan = failed_pilot
    if change == 'source':
        (root / 'src/issueintel/synthetic.py').write_text('VERSION=9')
    elif change == 'adapter':
        paths[0].write_text('# changed adapter')
    elif change == 'inventory':
        path = next((directory / 'cases').glob('*/calls/*.json'))
        value = native._read(root, path); value['latency_seconds'] += 1
        native._write(root, path, native._seal({k: v for k, v in value.items() if k != 'sha256'}))
    elif change == 'runtime':
        path = root / 'configs/runtime.toml'
        path.write_text(path.read_text() + '\nreasoning_effort = "high"\n')
    else:
        plan = deepcopy(plan); plan['limits']['max_provider_calls_total'] = 626
        plan = continuation.seal({k: v for k, v in plan.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        continuation.validate_plan(root, plan, full_replay=full_replay)


def test_native_matrix_authorization_is_still_required(failed_pilot):
    root, spec, _, _, _, _, _, plan = failed_pilot
    _, path = continuation_auth(failed_pilot)
    assert continuation.verify_authorization(root, plan, path, requested_cases=1)['authorized_case_count'] == 1
    with pytest.raises(ValueError):
        continuation.verify_authorization(root, plan, path, requested_cases=25)
    _, path = continuation_auth(failed_pilot, count=25)
    assert continuation.verify_authorization(root, plan, path, requested_cases=25)['authorized_case_count'] == 25
    # A matching continuation declaration cannot upgrade the referenced native pilot.
    value = json.loads(path.read_text())
    _, base_path = authorization(root, spec, count=1)
    value['base_authorization_sha256'] = sha256(base_path.read_bytes()).hexdigest()
    write(path, value)
    with pytest.raises(ValueError):
        continuation.verify_authorization(root, plan, path, requested_cases=25)


@pytest.mark.parametrize('field,value', [('manual_transport_attempt_authorized', False),
    ('gold_scoring_authorized', True), ('max_provider_calls_per_arm', 6),
    ('holdout_access_authorized', True), ('authorized_case_count', True), ('date', 'invalid')])
def test_exact_continuation_authority_rejects_widened_or_ambiguous_fields(failed_pilot, field, value):
    root, _, _, _, _, _, _, plan = failed_pilot
    declared, path = continuation_auth(failed_pilot)
    declared[field] = value; write(path, declared)
    with pytest.raises(ValueError):
        continuation.verify_authorization(root, plan, path, requested_cases=1)


@pytest.mark.parametrize('mutation', ['returned', 'unknown', 'output', 'response_read'])
def test_non_transport_or_unresolved_paid_failure_is_not_retry_authority(failed_pilot, mutation):
    root, spec, directory, _, _, _, paths, plan = failed_pilot
    state = continuation.validate_plan(root, plan)
    call = deepcopy(state['target_call']); stage = deepcopy(state['target_stage']); arm = deepcopy(state['target_arm'])
    if mutation == 'returned':
        call.update(status='returned', response={'not': 'a full native annotation'}, failure=None)
        call['response_sha256'] = digest(call['response'])
    elif mutation == 'unknown':
        next(iter(call['receipts'].values()))['status'] = 'reserved'
    elif mutation == 'output':
        stage['terminal_output_failure'] = True
    else:
        call['failure']['failure_phase'] = 'response_read'
    call = native._seal({k: v for k, v in call.items() if k != 'sha256'})
    stage = native._seal({k: v for k, v in stage.items() if k != 'sha256'})
    for ref in arm['stage_refs']:
        if ref['stage_key'] == stage['stage_key']:
            ref['sha256'] = stage['sha256']
    arm = native._seal({k: v for k, v in arm.items() if k != 'sha256'})
    case_dir = directory / 'cases' / digest(arm['case_id'])
    native._write(root, case_dir / 'calls' / (call['call_id'] + '.json'), call)
    native._write(root, case_dir / 'stages' / (stage['stage_key'] + '.json'), stage)
    native._write(root, case_dir / 'arms' / (arm['arm_id'] + '.json'), arm)
    with pytest.raises(ValueError):
        continuation.create_plan(root, spec['sha256'], adapter_paths=paths)


def test_empty_overlay_retains_original_cost_and_does_not_complete_failed_arm(failed_pilot):
    root, _, _, _, _, _, _, plan = failed_pilot
    view = continuation.load_effective_view(root, plan)
    assert len(view['results']) == 5 and len(view['base_arms']) == 5
    assert sum(row['status'] == 'completed' for row in view['results']) == 4
    assert view['audit_calls'][0]['call_id'] == plan['target']['call_id']
    assert view['audit_calls'][0] in view['calls']
    assert next(iter(view['audit_calls'][0]['receipts'].values()))['status'] == 'failed_bound'


def overlay_failed_attempt(fixture):
    root, _, _, _, _, _, _, plan = fixture
    state = continuation.validate_plan(root, plan)
    call = deepcopy(state['target_call'])
    call.update(schema=continuation.CALL_SCHEMA, attempt_id=plan['sha256'], ordinal=0,
                native_request_sha256=call['request_sha256'])
    call['call_id'] = digest([plan['sha256'], plan['sha256'], call['stage_key'], 0, call['request_sha256']])
    call = continuation.seal({key: value for key, value in call.items() if key != 'sha256'})
    stage = deepcopy(state['target_stage'])
    stage.update(schema=continuation.STAGE_SCHEMA, attempt_id=plan['sha256'], call_ids=[call['call_id']])
    stage = continuation.seal({key: value for key, value in stage.items() if key != 'sha256'})
    return call, stage


def test_new_attempt_has_distinct_identity_and_exact_original_request(failed_pilot):
    _, spec, _, _, _, _, _, plan = failed_pilot
    call, stage = overlay_failed_attempt(failed_pilot)
    assert call['call_id'] != plan['target']['call_id']
    assert stage['stage_key'] == plan['target']['stage_key']
    assert continuation.validate_call(call, plan, spec) == call
    call['request']['messages'][0]['content'] += ' changed'
    call['request_sha256'] = digest(call['request'])
    call['call_id'] = digest([plan['sha256'], plan['sha256'], call['stage_key'], 0, call['request_sha256']])
    call = continuation.seal({key: value for key, value in call.items() if key != 'sha256'})
    with pytest.raises(ValueError, match='original frozen request'):
        continuation.validate_call(call, plan, spec)


def test_new_attempt_cannot_reuse_old_reservation_or_hide_orphan_call(failed_pilot):
    root, _, _, _, _, _, _, plan = failed_pilot
    call, stage = overlay_failed_attempt(failed_pilot)
    directory = continuation.overlay_directory(root, plan) / 'cases' / digest(plan['target']['case_id'])
    continuation.write_new(root, directory / 'calls' / (call['call_id'] + '.json'), call)
    with pytest.raises(ValueError, match='orphan'):
        continuation.load_effective_view(root, plan)
    continuation.write_new(root, directory / 'stages' / (stage['stage_key'] + '.json'), stage)
    with pytest.raises(ValueError, match='multiple paid owners'):
        continuation.load_effective_view(root, plan)


def test_overlay_cannot_replace_an_original_completed_arm(failed_pilot):
    root, _, _, _, _, _, _, plan = failed_pilot
    state = continuation.validate_plan(root, plan)
    arm = deepcopy(next(arm for arm in state['arms'] if arm['status'] == 'completed'))
    arm.update(schema=continuation.ARM_SCHEMA, attempt_id=plan['sha256'], audit_call_ids=[],
               audit_stage_refs=[], audit_arm_refs=[], continuation_overhead={})
    arm = continuation.seal({key: value for key, value in arm.items() if key != 'sha256'})
    path = continuation.overlay_directory(root, plan) / 'cases' / digest(arm['case_id']) / 'arms' / (arm['arm_id'] + '.json')
    continuation.write_new(root, path, arm)
    with pytest.raises(ValueError, match='original completed cell'):
        continuation.load_effective_view(root, plan)


def test_private_io_denies_escape_and_replacing_closed_owned_journal(tmp_path):
    root = tmp_path
    value = continuation.seal({'status': 'completed', 'payload': 'synthetic'})
    with pytest.raises(ValueError):
        continuation.write_new(root, '../escape.json', value)
    with pytest.raises(ValueError):
        continuation.write(root, 'artifacts/base/calls/a.json', value)
    path = root / 'artifacts/experiments/component_continuation' / ('a' * 64) / 'cases/c/calls/a.json'
    continuation.write(root, path, value)
    with pytest.raises(ValueError, match='Closed'):
        continuation.write(root, path, continuation.seal({'status': 'failed', 'payload': 'changed'}))


def test_private_io_denies_symbolic_link(tmp_path):
    root = tmp_path
    path = root / 'artifacts/target/record.json'
    continuation.write_new(root, path, continuation.seal({'payload': 'synthetic'}))
    link = root / 'artifacts/link'
    try:
        link.symlink_to(path.parent, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation unavailable on this platform')
    with pytest.raises(ValueError, match='Links'):
        continuation.read(root, link / path.name)


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction guard')
def test_private_io_denies_actual_windows_junction(tmp_path):
    (tmp_path / 'artifacts').mkdir()
    target = tmp_path / 'shared'; target.mkdir()
    canary = target / 'record.json'
    canary.write_text('do not read or change', encoding='utf-8')
    junction = tmp_path / 'artifacts/junction'
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
        'New-Item -ItemType Junction -Path $env:ISSUEINTEL_TEST_JUNCTION -Value $env:ISSUEINTEL_TEST_JUNCTION_TARGET | Out-Null'],
        env={**os.environ, 'ISSUEINTEL_TEST_JUNCTION': str(junction),
             'ISSUEINTEL_TEST_JUNCTION_TARGET': str(target)}, capture_output=True, check=True)
    try:
        with pytest.raises(ValueError, match='Links'):
            continuation.read(tmp_path, junction / canary.name)
        assert canary.read_text(encoding='utf-8') == 'do not read or change'
    finally:
        assert junction.absolute().is_relative_to(tmp_path.absolute())
        os.rmdir(junction)  # Remove this named junction only; never traverse its target.
