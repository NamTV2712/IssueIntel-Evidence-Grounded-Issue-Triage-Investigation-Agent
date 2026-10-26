"""Synthetic phase authority and complete native controller-study workflow."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from issueintel.evaluation import controller_study as registration
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.providers import GroqProvider
from issueintel.runtime.typesafe import TypesafeClient
from issueintel.runtime.study_budget import StudyBudgetLedger
from test_controller_study import sources, planned_repository, save
from test_controller_study_execution import HTTP
from test_planned_pipeline import PlannedModel


@pytest.fixture
def registered(sources):
    root, selected, selection, loader = sources
    # Native Jev requests deliberately omit job/case identifiers. Distinct source
    # text and model statements make these five native synthetic requests differ
    # without adding a nonce or changing the production model projection.
    for index, row in enumerate(selection['cases'], 1):
        path = root / row['pack']['path']
        pack = json.loads(path.read_bytes())
        context = loader(root, row, pack)
        case = {**context['case'], 'problem_statement':
                f'parser.parse_list crashes for selected synthetic issue {index}'}
        pack = investigate(case, context['snapshot'].repository,
            snapshot=context['snapshot'], graph=context['graph'])
        row['pack']['sha256'] = save(path, pack)
        job_path = root / ('artifacts/demo/jobs/' + row['source_job_id'] + '.json')
        job = json.loads(job_path.read_bytes())
        job['result']['pack.json']['sha256'] = row['pack']['sha256']
        save(job_path, job)
    save(selected, selection)
    spec = registration.register_study(root, selected, source_loader=loader)
    return root, spec, loader


def authority(spec, *, matrix=False, now=None):
    now = now or datetime.now(timezone.utc)
    return {'schema': 'controller_study_authorization_v1',
        'study_id': spec['study_id'], 'registration_sha256': spec['sha256'],
        'source_fingerprint_sha256': spec['source_fingerprint']['sha256'],
        'runtime_sha256': spec['runtime']['sha256'],
        'budget_policy_sha256': registration.digest(spec['budget_policy']),
        'unit_ids': [row['unit_id'] for row in spec['cases']],
        'authorized_case_count': 5 if matrix else 1,
        'phase': 'registered_matrix' if matrix else 'first_case_pilot',
        'max_groq_attempts': 90 if matrix else 18,
        'max_typesafe_attempts': 20 if matrix else 4,
        'max_joint_micro_usd': 1935360 if matrix else 387072,
        'max_typesafe_micro_usd': 53760 if matrix else 10752,
        'execution_authorized': True, 'groq_spending_authorized': True,
        'typesafe_spending_authorized': True,
        'author_request': 'Explicit synthetic test authority for the registered phase only.',
        'date': now.date().isoformat(), 'expires_at': (now + timedelta(hours=12)).isoformat(),
        'gold_read': False, 'holdout_access_authorized': False,
        'live_access_authorized': False, 'new_freeze_authorized': False,
        'bulk_annotation_restart_authorized': False}


def save_authority(root, spec, *, matrix=False, now=None, changes=None):
    value = authority(spec, matrix=matrix, now=now)
    value.update(changes or {})
    path = root / ('artifacts/matrix-authority.json' if matrix else 'artifacts/pilot-authority.json')
    save(path, value)
    return path


@pytest.fixture
def native_http(monkeypatch):
    models, requests, constructors = {}, [], []
    settings = {'jev_error': False, 'groq_error': False, 'unknown_usage': False}
    def groq_http(request, timeout):
        body = json.loads(request.data); requests.append(('groq', body))
        if settings['groq_error']:
            raise OSError('synthetic transport failure')
        payload = json.loads(body['messages'][1]['content'])
        if 'case' in payload:
            case_id = payload['case']['issue_url']
            model = models.setdefault(case_id, PlannedModel())
            content = model(body['messages'], body['max_completion_tokens'])
            for index, row in enumerate(content.get('hypotheses', []), 1):
                row['id'] = f'H{index}'
                row['statement'] = case_id + ': ' + row['statement']
        else:
            content = PlannedModel()(body['messages'], body['max_completion_tokens'])
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}]}
        if not settings['unknown_usage']:
            response['usage'] = {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}
        return HTTP(json.dumps(response).encode())
    def typesafe_http(request, timeout):
        body = json.loads(request.data); requests.append(('typesafe', body))
        if settings['jev_error']:
            raise OSError('synthetic service failure')
        allowed = list(body['questions']['next_action']['criteria'])
        action = 'retrieve' if 'retrieve' in allowed else 'verify'
        return HTTP(json.dumps({'model': 'jev-1.13.0', 'answers': {'next_action': {
            'type': 'choice', 'choice': action, 'confidence': 1,
            'probabilities': {name: int(name == action) for name in allowed}}},
            'usage': {'input_tokens': 1000, 'output_tokens': 35}}).encode())
    def construct(name, native):
        def factory(config, **kwargs):
            constructors.append(name)
            return native(config, **kwargs)
        return factory
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-groq-key')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic-typesafe-key')
    return {'factories': {'groq': construct('groq', GroqProvider),
        'typesafe': construct('typesafe', TypesafeClient),
        'groq_transport': groq_http, 'typesafe_transport': typesafe_http},
        'requests': requests, 'constructors': constructors, 'settings': settings}


def api():
    from issueintel.evaluation import controller_study_run
    return controller_study_run


def test_inventory_excludes_only_exact_control_file_bytes(tmp_path, monkeypatch):
    lock = tmp_path / 'execution.lock'; lock.write_bytes(b'1')
    (tmp_path / 'nested').mkdir()
    nested = tmp_path / 'nested/execution.lock'; nested.write_bytes(b'nested control bytes')
    native_read = registration.read_bytes
    def checked_read(path, **kwargs):
        assert path != lock, 'A held Windows lock cannot be read'
        return native_read(path, **kwargs)
    monkeypatch.setattr(registration, 'read_bytes', checked_read)
    files = registration.inventory(tmp_path, exclude_names={'execution.lock'})
    assert files['execution.lock'] == {'bytes': 1}
    assert files['nested/execution.lock']['sha256'] == sha256(nested.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='byte bound'):
        registration.inventory(tmp_path, exclude_names={'execution.lock'}, maximum_bytes=1)
    with pytest.raises(ValueError, match='entry bound'):
        registration.inventory(tmp_path, exclude_names={'execution.lock'}, maximum_files=1)
    import os
    alias = tmp_path / 'alias'; os.link(lock, alias)
    with pytest.raises(ValueError, match='hardlink'):
        registration.inventory(tmp_path, exclude_names={'execution.lock', 'alias'})


def saved_results(root, spec):
    directory = registration.study_directory(root, spec['study_id'])
    return [registration.read_json(root, path) for path in (directory / 'arms').glob('*.json')]


def test_exact_phase_authority_binds_all_registered_inputs(registered):
    root, spec, _ = registered
    now = datetime.now(timezone.utc)
    path = save_authority(root, spec, now=now)
    approved = api().verify_authorization(root, spec, path, limit=1, clock=lambda: now)
    assert approved is not None


@pytest.mark.parametrize('change', [
    {'study_id': 'f' * 64}, {'registration_sha256': 'f' * 64},
    {'source_fingerprint_sha256': 'f' * 64}, {'runtime_sha256': 'f' * 64},
    {'budget_policy_sha256': 'f' * 64}, {'unit_ids': ['case-1']},
    {'authorized_case_count': 5}, {'phase': 'registered_matrix'},
    {'max_groq_attempts': 19}, {'max_typesafe_attempts': 5},
    {'max_joint_micro_usd': 387073}, {'max_typesafe_micro_usd': 10753},
    {'max_typesafe_attempts': True}, {'max_groq_attempts': 0},
    {'execution_authorized': False}, {'groq_spending_authorized': False},
    {'typesafe_spending_authorized': False}, {'gold_read': True},
    {'holdout_access_authorized': True}, {'live_access_authorized': True},
    {'new_freeze_authorized': True}, {'bulk_annotation_restart_authorized': True},
    {'author_request': ''}, {'expires_at': '2000-01-01T00:00:00+00:00'},
    {'expires_at': '2099-01-01T00:00:00+00:00'}, {'extra_approval': True},
])
def test_invalid_phase_authority_denies_before_environment_or_provider(registered, change):
    root, spec, loader = registered
    path = save_authority(root, spec, changes=change)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('unexpected')
        pytest.fail('Authority must deny before environment or provider')
    with pytest.raises((ValueError, OSError)):
        api().run_study(root, spec['study_id'], path, limit=1, source_loader=loader,
            provider_factories={'groq': forbidden, 'typesafe': forbidden},
            load_environment=True, env_loader=forbidden)
    assert calls == []


def test_missing_typesafe_approval_is_not_inferred_from_key_or_groq(registered):
    root, spec, loader = registered
    value = authority(spec); value.pop('typesafe_spending_authorized')
    path = root / 'artifacts/missing-typesafe-authority.json'; save(path, value)
    with pytest.raises((ValueError, OSError)):
        api().run_study(root, spec['study_id'], path, source_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('dotenv before Typesafe approval'))
    directory = registration.study_directory(root, spec['study_id'])
    assert not (directory / 'native').exists()
    assert not (directory / 'budget').exists()


def test_matrix_requires_successful_first_case_pilot_before_environment(registered):
    root, spec, loader = registered
    path = save_authority(root, spec, matrix=True)
    with pytest.raises((ValueError, OSError)):
        api().run_study(root, spec['study_id'], path, limit=5, source_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('dotenv before pilot gate'))


def test_pilot_native_pair_preserves_full_seed_and_closed_receipts(registered, native_http):
    root, spec, loader = registered
    path = save_authority(root, spec)
    progress = api().run_study(root, spec['study_id'], path, limit=1,
        source_loader=loader, provider_factories=native_http['factories'])
    assert progress['terminal_arms'] == progress['completed_arms'] == 2
    assert progress['selected_cases'] == 5 and progress['inference_sealed'] is False
    assert native_http['requests']
    assert any(name == 'typesafe' for name, _ in native_http['requests'])
    inspected = api().inspect_run(root, spec['study_id'], source_loader=loader)
    assert inspected['terminal_arms'] == 2 and inspected['selected_cases'] == 5
    assert inspected['provider_constructed'] is False
    results = {row['arm_id']: row for row in saved_results(root, spec)}
    assert set(results) == {'rules', 'jev'}
    rules, jev = results['rules']['pack'], results['jev']['pack']
    assert rules['workflow_plan']['seed_annotation'] == jev['workflow_plan']['seed_annotation']
    assert rules['workflow_plan']['seed_annotation']['provenance']['created_at'] == jev['workflow_plan']['seed_annotation']['provenance']['created_at']
    # This graph adds evidence in two rounds; the third retrieval is empty.
    # Seed + two refinements + verifier are four logical Groq calls per arm.
    assert rules['workflow_plan']['provider_calls'] == jev['workflow_plan']['provider_calls'] == 4
    directory = registration.study_directory(root, spec['study_id'])
    snapshot = StudyBudgetLedger(directory / 'budget', spec['budget_policy']).inspect()
    assert snapshot['owned_attempts'] == 8
    assert all(row['outcome'] == 'completed' and row['receipt']['status'] == 'settled' for row in snapshot['calls'])
    assert all(len(row['references']) == 2 for row in snapshot['calls'] if row['provider'] == 'groq')
    before = len(native_http['requests'])
    # Reopening may return the same progress or explicitly refuse already-owned
    # work; either behavior must preserve the no-retry contract.
    try:
        api().run_study(root, spec['study_id'], path, limit=1,
            source_loader=loader, provider_factories=native_http['factories'])
    except (ValueError, OSError):
        pass
    assert len(native_http['requests']) == before
    with pytest.raises((ValueError, OSError)):
        api().score_study(root, spec['study_id'], source_loader=loader)


def test_fallback_only_pilot_cannot_open_matrix(registered, native_http):
    root, spec, loader = registered
    native_http['settings']['jev_error'] = True
    pilot = save_authority(root, spec)
    api().run_study(root, spec['study_id'], pilot, limit=1,
        source_loader=loader, provider_factories=native_http['factories'])
    before = len(native_http['requests'])
    matrix = save_authority(root, spec, matrix=True)
    with pytest.raises((ValueError, OSError)):
        api().run_study(root, spec['study_id'], matrix, limit=5, source_loader=loader,
            provider_factories=native_http['factories'], load_environment=True,
            env_loader=lambda *a: pytest.fail('dotenv before actual Jev pilot gate'))
    assert len(native_http['requests']) == before


def test_full_five_case_native_matrix_seal_and_gold_free_score(registered, native_http):
    root, spec, loader = registered
    pilot = save_authority(root, spec)
    api().run_study(root, spec['study_id'], pilot, limit=1,
        source_loader=loader, provider_factories=native_http['factories'])
    matrix = save_authority(root, spec, matrix=True)
    api().run_study(root, spec['study_id'], matrix, limit=5,
        source_loader=loader, provider_factories=native_http['factories'])
    calls = list(native_http['requests'])
    scored = api().score_study(root, spec['study_id'], source_loader=loader)
    assert scored['selected_case_clusters'] == 5
    assert scored['selected_arm_rows'] == 10
    assert scored['paired_completed_subset']['case_cluster_count'] == 5
    assert scored['independent_human_validation'] is False
    assert scored['semantic_truth_computed'] is False
    assert scored['action_accuracy_computed'] is False
    assert scored['developer_benefit_computed'] is False
    assert {row['unit_id'] for row in scored['cells']} == {row['unit_id'] for row in spec['cases']}
    assert all(row['status'] == 'completed' for row in scored['cells'])
    rows = saved_results(root, spec)
    for index, selected in enumerate(spec['cases']):
        pair = {row['arm_id']: row for row in rows if row['unit_id'] == selected['unit_id']}
        assert pair['rules']['pack']['workflow_plan']['seed_annotation'] == pair['jev']['pack']['workflow_plan']['seed_annotation']
        assert pair['rules']['preparation_measurement_id'] == pair['jev']['preparation_measurement_id']
        assert {row['preparation_owner_arm_id'] for row in pair.values()} == {'rules' if index % 2 == 0 else 'jev'}
    assert {name: sum(provider == name for provider, _ in calls) for name in ('groq', 'typesafe')} == {'groq': 20, 'typesafe': 20}
    assert native_http['requests'] == calls
    repeated = api().score_study(root, spec['study_id'], source_loader=loader)
    assert repeated == scored and native_http['requests'] == calls
