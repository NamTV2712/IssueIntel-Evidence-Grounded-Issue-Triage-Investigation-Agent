"""Synthetic frozen native pipeline and append-only continuation journal tests."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from issueintel.benchmarks import component_ablation as registration
from issueintel.benchmarks import component_ablation_run as native
from issueintel.benchmarks import component_continuation as core
from issueintel.benchmarks import component_continuation_run as continuation
from issueintel.investigation.providers import ProviderError
from issueintel.repository.snapshot import SourceFile
from issueintel.runtime.io import digest

from test_component_ablation import workspace, registered, authorization, write
from test_component_ablation_run import runtime


API = (registration, native)


def continuation_authorization(root, plan, spec, *, count=1):
    _, base_auth = authorization(root, spec, count=count)
    value = {'schema': core.AUTHORIZATION_SCHEMA, 'plan_sha256': plan['sha256'],
        'registration_sha256': spec['sha256'], 'code_fingerprint_sha256': plan['code_fingerprint_sha256'],
        'protocol_sha256': plan['protocol_sha256'], 'adapter_sources': plan['adapter_sources'],
        'case_ids': plan['case_ids'], 'provider': 'groq', 'model': spec['runtime']['model'],
        'reasoning_effort': spec['runtime']['reasoning_effort'], 'max_output_tokens': 2048,
        'max_provider_calls_per_arm': 5, 'max_provider_calls_total': count * 25,
        'authorized_case_count': count, 'phase': 'first_case_pilot' if count == 1 else 'registered_matrix',
        'author_request': 'Synthetic manual transport continuation only', 'date': '2026-10-24',
        'execution_authorized': True, 'manual_transport_attempt_authorized': True,
        'gold_scoring_authorized': False, 'base_authorization_path': base_auth.relative_to(root).as_posix(),
        'base_authorization_sha256': sha256(base_auth.read_bytes()).hexdigest(),
        **{flag: False for flag in registration.ACCESS_FLAGS}}
    return write(root / 'artifacts/continuation-auth.json', value)


@pytest.fixture
def failed_pilot(workspace, runtime):
    root, contexts, ledger, calls, provider = runtime
    selected = registration._selected_rows(workspace[1])[0]
    case, snapshot, receipt = contexts[selected['case_id']]
    text = 'def sentinel(unavailable):\n    return unavailable if unavailable is not None else []\n'
    snapshot = replace(snapshot, files=snapshot.files + (SourceFile('sentinel.py', text, sha256(text.encode()).hexdigest()),))
    unsealed = asdict(snapshot); unsealed.pop('snapshot_sha256')
    contexts[selected['case_id']] = (case, replace(snapshot, snapshot_sha256=digest(unsealed)), receipt)
    spec, base_directory, _ = registered(workspace)
    _, auth = authorization(root, spec)

    class ClosedTransport(provider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            payload = json.loads(messages[1]['content'])
            if 'case' not in payload or not any(row.get('locator', '').startswith('sentinel.py:') for row in payload['evidence']):
                return super().complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
            rid = f'{len(calls) + 1:032x}'
            record = {'reservation_id': rid, 'created_at': '2026-10-24T00:00:00Z',
                'model': self.config.model, 'input_bound': 10000, 'output_bound': max_output_tokens,
                'tokens_charged_or_reserved': 12048, 'micro_usd_charged_or_reserved': 100,
                'status': 'reserved', 'price_date': '2026-10-24',
                'observed_input_tokens': None, 'observed_output_tokens': None}
            ledger.rows[rid] = deepcopy(record); self.on_receipt(record)
            calls.append({'failed_transport': True, 'messages': deepcopy(messages), 'schema': deepcopy(schema)})
            record['status'] = 'failed_bound'
            ledger.rows[rid] = deepcopy(record); self.on_receipt(record)
            raise ProviderError('Synthetic closed request failure', diagnostics={
                'failure_phase': 'request', 'transport_error_type': 'RemoteDisconnected',
                'transport_reason_type': 'RemoteDisconnected'})

    result = native.run_component_ablation(root, spec['sha256'], auth, provider_factory=ClosedTransport)
    assert result['completed_arms'] == 4 and result['failed_arms'] == 1
    original_inventory = native._inventory(root, base_directory)
    adapter_paths = [Path(continuation.__file__).with_name(name + '.py') for name in
                     ('component_continuation', 'component_continuation_run', 'component_continuation_score')]
    plan = core.create_plan(root, spec['sha256'], adapter_paths=adapter_paths, base_api=API)
    core.save_plan(root, plan, base_api=API)
    auth = continuation_authorization(root, plan, spec)
    return root, spec, base_directory, plan, auth, ledger, calls, provider, original_inventory


def run(fixture, *, limit=1, provider=None):
    root, _, _, plan, auth, _, _, default_provider, _ = fixture
    return continuation.run_component_continuation(root, plan['sha256'], auth, limit=limit,
        provider_factory=provider or default_provider, base_api=API)


def test_recovery_reuses_native_payloads_exact_request_and_preserves_original(failed_pilot):
    root, spec, base_directory, plan, _, _, calls, _, original_inventory = failed_pilot
    before = len(calls)
    result = run(failed_pilot)
    assert result['completed_arms'] == 5 and not result['inference_sealed']
    assert len(calls) == before + 2  # New refined generation and its own critic only.
    assert native._inventory(root, base_directory) == original_inventory
    view = core.load_effective_view(root, plan, base_api=API)
    recovered = next(row for row in view['results'] if row['arm_id'] == 'plus_counter_search')
    assert recovered['status'] == 'completed'
    assert recovered['audit_call_ids'] == [plan['target']['call_id']]
    assert len(recovered['call_ids']) <= 5
    retry = next(row for row in view['overlay_calls'] if row['stage_key'] == plan['target']['stage_key'])
    original = next(row for row in view['base_calls'] if row['call_id'] == plan['target']['call_id'])
    assert retry['call_id'] != original['call_id'] and retry['request'] == original['request']
    generation = next(row for row in view['overlay_stages'] if row['stage_key'] == plan['target']['stage_key'])
    assert generation['payload']['annotation']['provenance']['repair_count'] == 0
    original_annotation = next(row for row in view['base_stages'] if row['binding']['stage'] == 'seed_generation')['payload']
    seed_ref = next(ref for ref in recovered['stage_refs'] if ref['stage_key'] == digest(native._stage_binding(
        spec, spec['cases'][0], 'seed_generation', next(stage['binding']['inputs'] for stage in view['base_stages']
        if stage['binding']['stage'] == 'seed_generation'))))
    assert seed_ref['cache_hit']
    assert next(row for row in view['execution_stages'].values() if row['stage_key'] == seed_ref['stage_key'])['payload'] == original_annotation
    assert recovered['pipeline_latency_seconds'] == view['audit_arms'][0]['pipeline_latency_seconds']
    assert recovered['continuation_overhead']['pipeline_latency_seconds'] >= 0
    again = run(failed_pilot)
    assert again['completed_arms'] == 5 and len(calls) == before + 2


def test_matrix_cannot_expand_before_effective_pilot_and_denies_before_environment(failed_pilot, monkeypatch):
    root, spec, _, plan, auth, _, calls, _, _ = failed_pilot
    continuation_authorization(root, plan, spec, count=25)
    monkeypatch.setattr(native.RuntimeConfig, 'ledger', lambda *args: pytest.fail('ledger opened before pilot gate'))
    with pytest.raises(ValueError, match='five effective'):
        run(failed_pilot, limit=25)
    assert len(calls) == plan['limits']['total_calls_before']


def test_full125_native_effective_seal_and_readonly_reopen(failed_pilot):
    root, spec, base_directory, plan, _, _, calls, _, original_inventory = failed_pilot
    run(failed_pilot)
    continuation_authorization(root, plan, spec, count=25)
    result = run(failed_pilot, limit=25)
    assert result['completed_arms'] == 125 and result['inference_sealed']
    view = continuation.load_sealed_continuation(root, plan['sha256'], base_api=API)
    assert len(view['results']) == 125 and len(view['calls']) == len(calls)
    assert len(view['audit_arms']) == 1 and view['audit_arms'][0]['status'] == 'failed'
    assert view['seal']['gold_read'] is False
    assert native._inventory(root, base_directory) == original_inventory
    assert len({rid for call in view['calls'] for rid in call['receipts']}) == len(calls)


def test_crash_after_native_stage_publication_reuses_timestamp_without_new_generation(failed_pilot, monkeypatch):
    root, _, _, plan, _, _, calls, _, _ = failed_pilot
    original = continuation._write
    def crash(root, path, value, **kwargs):
        if Path(path).parent.name == 'arms':
            raise KeyboardInterrupt('Synthetic process death before arm publication')
        return original(root, path, value, **kwargs)
    monkeypatch.setattr(continuation, '_write', crash)
    with pytest.raises(KeyboardInterrupt):
        run(failed_pilot)
    view = core.load_effective_view(root, plan, base_api=API)
    refined = next(row for row in view['overlay_stages'] if row['binding']['stage'] == 'refined_generation')
    annotation, count = deepcopy(refined['payload']['annotation']), len(calls)
    monkeypatch.setattr(continuation, '_write', original)
    assert run(failed_pilot)['completed_arms'] == 5 and len(calls) == count
    effective = core.load_effective_view(root, plan, base_api=API)
    recovered = next(row for row in effective['results'] if row['arm_id'] == 'plus_counter_search')
    assert recovered['pack']['annotation'] == annotation


def test_returned_response_without_native_payload_stops_without_retry(failed_pilot, monkeypatch):
    _, _, _, plan, _, _, calls, _, _ = failed_pilot
    original = continuation._write
    def crash(root, path, value, **kwargs):
        if value.get('schema') == continuation.STAGE_SCHEMA and value.get('status') == 'completed' and value['binding']['stage'] == 'refined_generation':
            raise KeyboardInterrupt('Synthetic process death after HTTP before native stage commit')
        return original(root, path, value, **kwargs)
    monkeypatch.setattr(continuation, '_write', crash)
    with pytest.raises(KeyboardInterrupt):
        run(failed_pilot)
    count = len(calls)
    monkeypatch.setattr(continuation, '_write', original)
    with pytest.raises(ValueError, match='Interrupted native stage'):
        run(failed_pilot)
    assert len(calls) == count == plan['limits']['total_calls_before'] + 1


def test_new_closed_transport_failure_is_terminal_and_never_paid_retried(failed_pilot):
    root, _, _, plan, _, ledger, calls, provider, _ = failed_pilot
    class FailsAgain(provider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            # Publish a normal closed response receipt then raise a closed transport
            # failure to exercise continuation terminal ownership with fake receipts.
            super().complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
            rid = f'{len(calls):032x}'
            ledger.rows[rid].update(status='failed_bound', observed_input_tokens=None, observed_output_tokens=None)
            self.on_receipt(deepcopy(ledger.rows[rid]))
            raise ProviderError('Synthetic second transport failure', diagnostics={
                'failure_phase': 'request', 'transport_error_type': 'RemoteDisconnected'})
    result = run(failed_pilot, provider=FailsAgain)
    assert result['failed_arms'] == 1 and result['stop_reason']
    count = len(calls)
    repeated = run(failed_pilot)
    assert repeated['failed_arms'] == 1 and len(calls) == count
    view = core.load_effective_view(root, plan, base_api=API)
    assert len(view['overlay_calls']) == 1


def test_unknown_new_receipt_denies_restart_without_another_attempt(failed_pilot):
    _, _, _, _, _, ledger, calls, provider, _ = failed_pilot
    class Unknown(provider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            rid = f'{len(calls) + 1:032x}'
            receipt = {'reservation_id': rid, 'created_at': '2026-10-24T00:00:00Z',
                'model': self.config.model, 'input_bound': 10000, 'output_bound': max_output_tokens,
                'tokens_charged_or_reserved': 12048, 'micro_usd_charged_or_reserved': 100,
                'status': 'reserved', 'price_date': '2026-10-24',
                'observed_input_tokens': None, 'observed_output_tokens': None}
            ledger.rows[rid] = deepcopy(receipt); self.on_receipt(receipt)
            calls.append({'unknown': True})
            raise ProviderError('Synthetic unknown outcome')
    result = run(failed_pilot, provider=Unknown)
    assert result['stop_reason']
    count = len(calls)
    with pytest.raises(ValueError, match='Paid outcome remains unknown'):
        run(failed_pilot)
    assert len(calls) == count


def test_request_order_adapter_preserves_only_semantically_identical_user_json():
    request = {'messages': [{'role': 'system', 'content': 'fixed instructions'},
                           {'role': 'user', 'content': '{"case":{"b":2,"a":1},"items":[1,2]}'}], 'schema': {'a': 1}}
    ordered = deepcopy(request)
    ordered['messages'][1]['content'] = '{"items":[1,2],"case":{"a":1,"b":2}}'
    assert continuation._same_native_request(request, ordered)
    ordered['messages'][0]['content'] += '!'
    assert not continuation._same_native_request(request, ordered)
    ordered = deepcopy(request); ordered['messages'][1]['content'] = '{"case":{"b":2,"a":1},"items":[2,1]}'
    assert not continuation._same_native_request(request, ordered)


def test_native_origin_guard_rejects_other_checkout(tmp_path):
    with pytest.raises(ValueError, match='frozen root'):
        continuation._native_origins(tmp_path, API)


def test_other_adapter_files_cannot_authorize_current_executor_before_environment(failed_pilot, monkeypatch):
    root, spec, _, _, _, _, calls, _, _ = failed_pilot
    alternate = root / 'separately-approved-adapter'
    alternate.mkdir()
    paths = []
    for name in sorted(core.ADAPTER_NAMES):
        path = alternate / name
        path.write_bytes(Path(continuation.__file__).with_name(name).read_bytes())
        paths.append(path)
    plan = core.create_plan(root, spec['sha256'], adapter_paths=paths, base_api=API)
    core.save_plan(root, plan, base_api=API)
    auth = continuation_authorization(root, plan, spec)
    before = len(calls)
    monkeypatch.setattr(native.RuntimeConfig, 'ledger', lambda *args: pytest.fail('ledger before adapter binding'))
    from issueintel.runtime import io
    monkeypatch.setattr(io, 'load_local_env', lambda *args: pytest.fail('credentials before adapter binding'))
    with pytest.raises(ValueError, match='Executing adapter'):
        continuation.run_component_continuation(root, plan['sha256'], auth,
            provider_factory=failed_pilot[7], base_api=API, load_environment=True)
    assert len(calls) == before


def test_loaded_adapter_location_must_match_approved_files(tmp_path, monkeypatch):
    paths = [Path(continuation.__file__).with_name(name) for name in sorted(core.ADAPTER_NAMES)]
    plan = {'adapter_sources': core._adapter_sources(paths)}
    continuation._adapter_origins(plan)
    scorer = continuation._extension('component_continuation_score')
    other = tmp_path / 'component_continuation_score.py'
    other.write_bytes(Path(scorer.__file__).read_bytes())
    monkeypatch.setattr(scorer, '__file__', str(other))
    with pytest.raises(ValueError, match='Executing adapter'):
        continuation._adapter_origins(plan)
