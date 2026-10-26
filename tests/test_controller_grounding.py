"""Closed product common-judge driver, using native receipts and synthetic HTTP."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3

import pytest

from issueintel.evaluation import controller_grounding as driver
from issueintel.evaluation import controller_study as registry
from issueintel.investigation.providers import GroqProvider
from issueintel.runtime.study_budget import StudyBudgetStop
from test_common_grounding import source_rows, sealed


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')
    return path


@pytest.fixture
def parent(tmp_path):
    root = tmp_path / 'frozen'; root.mkdir()
    configs = root / 'configs'; configs.mkdir()
    (configs / 'runtime.toml').write_text('''provider = "groq"
model = "openai/gpt-oss-120b"
max_output_tokens = 4096
price_file = "configs/prices.json"
ledger = "data/runtime/budget.sqlite"
tokens_unlimited_authorized = true
usd_unlimited_authorized = true
''')
    save(configs / 'author_decisions.json', {'model': 'openai/gpt-oss-120b',
        'scope': 'necessary_issueintel_rebuild_work', 'tokens_unlimited_authorized': True,
        'usd_unlimited_authorized': True})
    save(configs / 'prices.json', {'checked_at': datetime.now(timezone.utc).date().isoformat(),
        'currency': 'USD', 'source': 'https://console.groq.com/docs/models',
        'models': {'openai/gpt-oss-120b': {'input_per_million': '0.15',
            'output_per_million': '0.60', 'context_tokens': 131072, 'max_output_tokens': 4096}}})
    cases, results = source_rows(distinct=True)
    spec = sealed({'schema': 'controller_study_registration_v1', 'study_id': 'a' * 64,
        'cases': cases, 'protocol': {'arms': ['rules', 'jev']},
        'source_fingerprint': sealed({'schema': 'controller_study_source_fingerprint_v1', 'files': {}}),
        'runtime': sealed({'schema': 'controller_study_runtime_v1', 'synthetic': True})})
    for row in results:
        row['registration_sha256'] = spec['sha256']
        row['sha256'] = registry.digest({k: v for k, v in row.items() if k != 'sha256'})
    inference = sealed({'schema': 'controller_study_inference_seal_v1', 'study_id': spec['study_id'],
        'registration_sha256': spec['sha256'], 'gold_read': False, 'quality_claim': False,
        'arm_results': {row['unit_id'] + ':' + row['arm_id']: row['sha256'] for row in results}})
    state = {'registration': spec, 'seal': inference, 'results': results,
        'budget_snapshot': {'locked': False, 'calls': []}, 'inventory': {}, 'native_replay_verified': True}
    loader_calls = []
    def loader(actual_root, identifier):
        assert Path(actual_root) == root and identifier == spec['study_id']
        loader_calls.append(identifier)
        return deepcopy(state)
    return root, state, loader, loader_calls


def authorize(root, plan, *, limit=1, changes=None):
    now = datetime.now(timezone.utc)
    value = {'schema': driver.AUTH_SCHEMA, 'scope': driver.SCOPE,
        'study_id': plan['study_id'], 'plan_sha256': plan['sha256'],
        'parent_study_id': plan['parent_study_id'],
        'parent_registration_sha256': plan['parent_registration_sha256'],
        'parent_inference_seal_sha256': plan['parent_inference_seal_sha256'],
        'parent_source_fingerprint_sha256': plan['parent_source_fingerprint']['sha256'],
        'parent_runtime_sha256': plan['parent_runtime']['sha256'],
        'adapter_sources_sha256': registry.digest(plan['adapter_sources']),
        'case_ids': plan['case_ids'], 'unit_ids': [row['unit_id'] for row in plan['units']],
        'authorized_case_count': limit, 'phase': 'first_case_pilot' if limit == 1 else 'registered_matrix',
        'max_new_judge_calls': len(driver._selected(plan, limit)), **driver._CAPS,
        'execution_authorized': True, 'groq_spending_authorized': True,
        'author_request': 'Synthetic finite common-judge phase authority.',
        'date': now.date().isoformat(), 'expires_at': (now + timedelta(hours=12)).isoformat(),
        **{key: False for key in driver._FALSE_FLAGS}}
    value.update(changes or {})
    return save(root / ('artifacts/quality-authority-' + str(limit) + '.json'), value)


@pytest.fixture
def http(monkeypatch):
    requests, constructors = [], []
    settings = {'error': False, 'missing_usage': False, 'invalid_response': False}
    class HTTP(BytesIO):
        status = 200
        headers = {'content-type': 'application/json'}
    def transport(request, timeout):
        body = json.loads(request.data)
        requests.append(body)
        if settings['error']:
            raise OSError('synthetic transport error')
        payload = json.loads(body['messages'][1]['content'])
        result = {'reviews': [{'hypothesis_id': row['id'], 'assessment': 'supported',
            'reason': 'Synthetic assessment of visible evidence',
            'evidence_ids': [payload['evidence'][0]['id']]} for row in payload['hypotheses']]}
        if settings['invalid_response']:
            result = {'reviews': []}
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]}
        if not settings['missing_usage']:
            response['usage'] = {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}
        return HTTP(json.dumps(response).encode())
    def factory(config, **kwargs):
        constructors.append(config)
        assert kwargs['call_context'] is None
        directory = config.ledger_file.parents[1]
        # Owned joint call and full unit intent must exist before the native client.
        intents = list((directory / 'units').glob('*/intent.json'))
        assert intents
        return GroqProvider(config, transport=transport, **kwargs)
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-groq-only')
    return requests, constructors, settings, factory


def test_plan_pins_fresh_units_parent_and_single_owner_without_model(parent):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    assert len(plan['units']) == 10 and len(driver._selected(plan, 1)) == 2
    assert plan['parent_inventory'] == state['inventory']
    assert plan['combined_caps']['max_combined_micro_usd'] == 1935360
    assert plan['fresh_common_judge'] and not plan['reuses_in_treatment_critic']
    assert driver.load_plan(root, plan['study_id'], parent_loader=loader)[0] == plan
    assert driver.create_plan(root, state['registration']['study_id'], parent_loader=loader) == plan
    directory = driver.grounding_directory(root, plan['study_id'])
    assert not (directory / 'budget').exists() and not (directory / 'native').exists()


@pytest.mark.parametrize('change', [
    {'groq_spending_authorized': False}, {'jev_spending_authorized': True},
    {'max_combined_groq_calls': 91}, {'max_combined_micro_usd': 1935361},
    {'max_new_judge_calls': 3}, {'phase': 'registered_matrix'}, {'gold_read': True},
    {'expires_at': '2000-01-01T00:00:00+00:00'}, {'adapter_sources_sha256': 'f' * 64},
])
def test_bad_authority_denies_before_credentials_or_provider(parent, change):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    path = authorize(root, plan, changes=change)
    with pytest.raises((ValueError, OSError)):
        driver.run(root, plan['study_id'], path, parent_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('dotenv before authority'),
            provider_factory=lambda *a, **k: pytest.fail('provider before authority'))


def test_native_receipted_pilot_matrix_and_repeated_score(parent, http):
    root, state, loader, _ = parent
    requests, constructors, _, factory = http
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    pilot = driver.run(root, plan['study_id'], authorize(root, plan), parent_loader=loader,
                       provider_factory=factory)
    assert pilot['reviewed_units'] == 2 and not pilot['inference_sealed']
    assert len(requests) == len(constructors) == 2
    assert driver.inspect(root, plan['study_id'], parent_loader=loader)['owned_calls'] == 2
    with pytest.raises((ValueError, OSError)):
        driver.score(root, plan['study_id'], parent_loader=loader)
    matrix = driver.run(root, plan['study_id'], authorize(root, plan, limit=5), limit=5,
                        parent_loader=loader, provider_factory=factory)
    assert matrix['reviewed_units'] == 10 and matrix['inference_sealed']
    assert len(requests) == len(constructors) == 10
    score = driver.score(root, plan['study_id'], parent_loader=loader)
    assert score['assessment']['case_clusters'] == 5
    assert score['assessment']['completed_units'] == 10
    assert score['assessment']['independent_human'] is False
    assert driver.score(root, plan['study_id'], parent_loader=loader) == score
    repeated = driver.run(root, plan['study_id'], authorize(root, plan, limit=5), limit=5,
                          parent_loader=loader, provider_factory=factory)
    assert repeated['inference_sealed'] and len(requests) == 10


def test_actual_parent_loader_requires_exact_frozen_package_root(parent):
    root, state, _, _ = parent
    with pytest.raises(StudyBudgetStop, match='exact frozen parent'):
        driver.create_plan(root, state['registration']['study_id'])


def test_full_matrix_requires_first_case_common_pilot(parent):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    with pytest.raises(StudyBudgetStop, match='first-case'):
        driver.run(root, plan['study_id'], authorize(root, plan, limit=5), limit=5,
            parent_loader=loader, load_environment=True,
            env_loader=lambda *a: pytest.fail('credentials before common pilot'))


def test_closed_transport_failure_is_retained_and_never_retried(parent, http):
    root, state, loader, _ = parent
    requests, _, settings, factory = http
    settings['error'] = True
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    path = authorize(root, plan)
    progress = driver.run(root, plan['study_id'], path, parent_loader=loader, provider_factory=factory)
    assert progress['failed_units'] == 2 and len(requests) == 2
    driver.run(root, plan['study_id'], path, parent_loader=loader, provider_factory=factory)
    assert len(requests) == 2
    with pytest.raises(StudyBudgetStop, match='first-case'):
        driver.run(root, plan['study_id'], authorize(root, plan, limit=5), limit=5,
                   parent_loader=loader, provider_factory=factory)


def test_missing_usage_keeps_native_bound(parent, http):
    root, state, loader, _ = parent
    _, _, settings, factory = http
    settings['missing_usage'] = True
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    progress = driver.run(root, plan['study_id'], authorize(root, plan), parent_loader=loader,
                          provider_factory=factory)
    assert progress['reviewed_units'] == 2
    _, directory, _ = driver.load_plan(root, plan['study_id'], parent_loader=loader)
    _, snapshot = driver._snapshot(directory, plan)
    assert all(row['receipt']['status'] == 'missing_usage_bound' and
        row['receipt']['micro_usd_charged_or_reserved'] == row['micro_usd_bound'] for row in snapshot['calls'])


def test_unknown_constructor_attempt_blocks_restart_before_credentials(parent):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    path = authorize(root, plan)
    def unavailable(*args, **kwargs):
        raise OSError('synthetic constructor failure')
    progress = driver.run(root, plan['study_id'], path, parent_loader=loader, provider_factory=unavailable)
    assert progress['unknown_work'] and progress['owned_calls'] == 1
    with pytest.raises(StudyBudgetStop, match='interrupted'):
        driver.run(root, plan['study_id'], path, parent_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('unknown call cannot read credentials'))


def test_parent_change_denies_saved_child_without_reset(parent):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    state['inventory']['changed'] = {'bytes': 1, 'sha256': 'b' * 64}
    with pytest.raises(StudyBudgetStop, match='changed'):
        driver.load_plan(root, plan['study_id'], parent_loader=loader)
    with pytest.raises(StudyBudgetStop, match='different'):
        driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)


def test_unknown_inventory_and_orphan_native_receipt_are_rejected(parent, http):
    root, state, loader, _ = parent
    _, constructors, _, factory = http
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    driver.run(root, plan['study_id'], authorize(root, plan), parent_loader=loader, provider_factory=factory)
    directory = driver.grounding_directory(root, plan['study_id'])
    (directory / 'unexpected.json').write_text('{}')
    with pytest.raises(StudyBudgetStop, match='Unknown'):
        driver.inspect(root, plan['study_id'], parent_loader=loader)
    (directory / 'unexpected.json').unlink()
    config = constructors[0]
    orphan = config.ledger().reserve(config.price(), 1000, 2048)
    with pytest.raises(StudyBudgetStop, match='orphan'):
        driver.inspect(root, plan['study_id'], parent_loader=loader)
    assert config.ledger().receipt(orphan)['status'] == 'reserved'


def reseal(value):
    return registry.seal({key: item for key, item in value.items() if key != 'sha256'})


def completed_pilot(parent, http):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    driver.run(root, plan['study_id'], authorize(root, plan), parent_loader=loader,
               provider_factory=http[3])
    return root, plan, loader, driver.grounding_directory(root, plan['study_id'])


def test_deleted_phase_authority_cannot_be_reconstructed_from_paid_receipts(parent, http):
    root, plan, loader, directory = completed_pilot(parent, http)
    for path in (directory / 'execution-intents').glob('*.json'):
        path.unlink()
    before = len(http[0])
    with pytest.raises((ValueError, OSError)):
        driver.run(root, plan['study_id'], authorize(root, plan), parent_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('Missing historical authority before env'),
            provider_factory=http[3])
    assert len(http[0]) == before


@pytest.mark.parametrize('change', [
    {'gold_read': True}, {'groq_spending_authorized': False},
    {'max_new_judge_calls': 3}, {'phase': 'registered_matrix'},
])
def test_rehashed_retained_authority_still_requires_exact_scope(parent, http, change):
    root, plan, loader, directory = completed_pilot(parent, http)
    path = next((directory / 'execution-intents').glob('*.json'))
    phase = json.loads(path.read_bytes())
    phase['authority'].update(change)
    phase['authority_sha256'] = registry.digest(phase['authority'])
    phase = reseal(phase)
    path.unlink()
    save(path.with_name(phase['sha256'] + '.json'), phase)
    with pytest.raises((ValueError, OSError)):
        driver.inspect(root, plan['study_id'], parent_loader=loader)


def test_paid_owner_cannot_be_retargeted_to_another_self_sealed_authority(parent, http):
    root, plan, loader, directory = completed_pilot(parent, http)
    path = next((directory / 'execution-intents').glob('*.json'))
    original = json.loads(path.read_bytes())
    replacement = deepcopy(original)
    replacement['authority']['author_request'] = 'A different declaration cannot take ownership of past calls.'
    replacement['authority_sha256'] = registry.digest(replacement['authority'])
    replacement = reseal(replacement)
    save(path.with_name(replacement['sha256'] + '.json'), replacement)
    for child in (directory / 'units').iterdir():
        for name in ('intent.json', 'outcome.json'):
            record = json.loads((child / name).read_bytes())
            record['execution_intent_sha256'] = replacement['sha256']
            save(child / name, reseal(record))
    with pytest.raises(StudyBudgetStop, match='owner'):
        driver.inspect(root, plan['study_id'], parent_loader=loader)


@pytest.mark.parametrize('mutation', ['delete-gate', 'rewrite-gate', 'delete-matrix-authority'])
def test_retained_matrix_gate_and_authority_are_required_independently_of_full_seal(parent, http, mutation):
    root, plan, loader, directory = completed_pilot(parent, http)
    driver.run(root, plan['study_id'], authorize(root, plan, limit=5), limit=5,
               parent_loader=loader, provider_factory=http[3])
    if mutation == 'delete-gate':
        (directory / 'pilot-gate.json').unlink()
    elif mutation == 'rewrite-gate':
        gate = json.loads((directory / 'pilot-gate.json').read_bytes())
        gate['units'] = {}
        save(directory / 'pilot-gate.json', reseal(gate))
    else:
        for path in (directory / 'execution-intents').glob('*.json'):
            if json.loads(path.read_bytes())['requested_cases'] == 5:
                path.unlink()
    _, snapshot = driver._snapshot(directory, plan)
    with pytest.raises((ValueError, OSError)):
        driver._records(root, directory, plan, snapshot)


def test_expired_live_authority_does_not_invalidate_its_historical_scope(parent, http):
    root, plan, loader, directory = completed_pilot(parent, http)
    phase_path = next((directory / 'execution-intents').glob('*.json'))
    phase = json.loads(phase_path.read_bytes())
    expiry = datetime.fromisoformat(phase['authority']['expires_at'])
    future = expiry + timedelta(days=1)
    assert driver._authority_value(plan, phase['authority'], limit=1,
        clock=lambda: future, active=False) == phase['authority']
    with pytest.raises(ValueError, match='unexpired'):
        driver._authority_value(plan, phase['authority'], limit=1, clock=lambda: future)
    assert driver.inspect(root, plan['study_id'], parent_loader=loader)['reviewed_units'] == 2


def test_unknown_file_after_first_closed_unit_blocks_next_paid_constructor(parent, http, monkeypatch):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    directory = driver.grounding_directory(root, plan['study_id'])
    native_write = registry.write_new
    added = []
    def publish(workspace, path, value):
        result = native_write(workspace, path, value)
        if Path(path).name == 'outcome.json' and not added:
            (directory / 'unexpected.json').write_text('{}')
            added.append(True)
        return result
    monkeypatch.setattr(registry, 'write_new', publish)
    with pytest.raises(StudyBudgetStop, match='Unknown'):
        driver.run(root, plan['study_id'], authorize(root, plan), parent_loader=loader,
                   provider_factory=http[3])
    assert added and len(http[0]) == len(http[1]) == 1


def test_unavailable_first_case_cannot_open_matrix_without_a_common_pilot(parent, http):
    root, state, loader, _ = parent
    for row in state['results']:
        if row['unit_id'] == state['registration']['cases'][0]['unit_id']:
            row.update(status='unavailable', pack=None, failure={'kind': 'synthetic'})
            row['sha256'] = reseal(row)['sha256']
    state['seal']['arm_results'] = {row['unit_id'] + ':' + row['arm_id']: row['sha256'] for row in state['results']}
    state['seal'] = reseal(state['seal'])
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    assert driver._selected(plan, 1) == [] and len(plan['units']) == 8
    with pytest.raises(StudyBudgetStop, match='first-case'):
        driver.run(root, plan['study_id'], authorize(root, plan, limit=5), limit=5,
            parent_loader=loader, load_environment=True,
            env_loader=lambda *a: pytest.fail('No implicit replacement pilot'), provider_factory=http[3])
    assert not http[0] and not http[1]


def test_sqlite_wal_is_rejected_before_any_read_only_auxiliary_change(tmp_path):
    path = tmp_path / 'wal.sqlite'
    db = sqlite3.connect(path)
    try:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        db.execute('CREATE TABLE records(value TEXT)'); db.commit()
        before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
        with pytest.raises(StudyBudgetStop, match='closed checkpointed'):
            driver._read_only(path)
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    finally:
        db.close()


def test_parent_change_after_phase_intent_denies_before_credentials(parent, monkeypatch):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    native_write = registry.write_new
    def publish(workspace, path, value):
        result = native_write(workspace, path, value)
        if Path(path).parent.name == 'execution-intents':
            state['inventory']['new-parent-file'] = {'bytes': 1, 'sha256': 'f' * 64}
        return result
    monkeypatch.setattr(registry, 'write_new', publish)
    with pytest.raises(StudyBudgetStop, match='changed'):
        driver.run(root, plan['study_id'], authorize(root, plan), parent_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('Source changed before dotenv'),
            provider_factory=lambda *a, **k: pytest.fail('Source changed before provider'))
    assert not (driver.grounding_directory(root, plan['study_id']) / 'budget').exists()


def test_factory_cannot_change_native_reasoning_config_before_http(parent, http):
    root, state, loader, _ = parent
    plan = driver.create_plan(root, state['registration']['study_id'], parent_loader=loader)
    def changed(config, **kwargs):
        return http[3](replace(config, reasoning_effort='medium'), **kwargs)
    result = driver.run(root, plan['study_id'], authorize(root, plan),
        parent_loader=loader, provider_factory=changed)
    assert result['unknown_work'] and result['owned_calls'] == 1
    assert len(http[1]) == 1 and not http[0]
