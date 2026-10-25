"""Synthetic continuation metrics and restricted-boundary tests; no real data."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueintel.benchmarks import component_ablation_score as frozen
from issueintel.benchmarks import component_continuation_score as scoring
from issueintel.investigation.verifier import verify_hypotheses
from issueintel.runtime.io import digest


def seal(value):
    return {**value, 'sha256': digest(value)}


def pack(cid, *, counter=False):
    text = 'synthetic code'
    evidence = [{'id': 'code1', 'kind': 'code', 'locator': 'hit.py:1-2', 'text': text,
                 'sha256': sha256(text.encode()).hexdigest(), 'revision': 'a' * 40}]
    annotation = {'hypotheses': [{'id': 'h1', 'statement': 'A synthetic mechanism',
        'support_ids': ['code1'], 'counter_ids': ['code1'] if counter else [],
        'counter_status': 'found' if counter else 'not_found',
        'falsification_test': 'Inspect synthetic behavior'}], 'provenance': {'repair_count': 0}}
    audit = seal({'selected_sources': 1, 'eligible_sources': 1, 'included_sources': 1, 'enabled': True})
    result = {'case': {'case_id': cid, 'split': 'main_dev'}, 'evidence': evidence,
        'annotation': annotation, 'verification': verify_hypotheses(annotation, evidence),
        'historical_source_audit': audit}
    if counter:
        result['refinement'] = {'search': {'policy': 'falsification_focused_retrieval_v1',
            'added_ids': ['code1'], 'counter_evidence_confirmed': False}}
    return result


def call(cid, arm, name, *, failed=False, stage_key=None, ordinal=0):
    key = digest([cid, arm, name])
    rid = key[:32]
    receipt = {'reservation_id': rid, 'model': 'openai/gpt-oss-120b', 'input_bound': 100,
        'output_bound': 200, 'tokens_charged_or_reserved': 300 if failed else 30,
        'micro_usd_charged_or_reserved': 40 if failed else 4,
        'status': 'failed_bound' if failed else 'settled',
        'observed_input_tokens': None if failed else 10, 'observed_output_tokens': None if failed else 20}
    return {'call_id': key, 'case_id': cid, 'owner_arm_id': arm,
        'status': 'failed' if failed else 'returned', 'receipts': {rid: receipt},
        'latency_seconds': 0.3 if failed else 0.2, 'stage_key': stage_key or digest([cid, name]),
        'ordinal': ordinal}


@pytest.fixture
def matrix(tmp_path):
    spec = seal({'cases': [{'case_id': f'verified:synthetic__{index}', 'history_included_sources': 1}
                          for index in range(25)],
        'arms': [{'arm_id': arm} for arm in scoring.ARMS],
        'limits': {'max_provider_calls_per_arm': 5, 'max_provider_calls_total': 625}})
    plan = seal({'registration_sha256': spec['sha256'],
        'adapter_sources': [{'path': '/synthetic/adapter.py', 'sha256': 'a' * 64}]})
    rows, calls, targets, stages = [], [], {}, []
    for case in spec['cases']:
        cid = case['case_id']
        paid = call(cid, scoring.ARMS[0], 'seed')
        calls.append(paid)
        stages.append(seal({'stage_key': paid['stage_key'], 'status': 'completed',
            'binding': {'stage': 'seed_generation'}, 'payload': {'annotation': {'provenance': {'repair_count': 0}}}}))
        targets[cid] = {'files': ['hit.py', 'outside.py'], 'available_files': ['hit.py'], 'patch_sha256': 'b' * 64}
        for arm in scoring.ARMS:
            rows.append(seal({'case_id': cid, 'arm_id': arm, 'status': 'completed',
                'pack': pack(cid, counter=arm == scoring.ARMS[-1]), 'call_ids': [paid['call_id']],
                'owned_call_ids': [paid['call_id']] if arm == scoring.ARMS[0] else [],
                'pipeline_latency_seconds': 0.5, 'preparation_seconds': 0.1,
                'preparation_measurement_id': digest([cid, 'prepare']),
                'preparation_owner_arm_id': scoring.ARMS[0]}))
    cid = spec['cases'][0]['case_id']
    stage_key = digest([cid, 'refined'])
    failed = call(cid, scoring.ARMS[-1], 'failed transport', failed=True, stage_key=stage_key)
    retry = call(cid, scoring.ARMS[-1], 'explicit retry', stage_key=stage_key)
    calls.extend([failed, retry])
    original = deepcopy(rows[:5])
    old = original[-1]
    old.update(status='failed', pack=None, pipeline_latency_seconds=0.7,
        call_ids=[calls[0]['call_id'], failed['call_id']], owned_call_ids=[failed['call_id']])
    old.pop('sha256')
    original[-1] = seal(old)
    recovered = rows[4]
    recovered.update(call_ids=[calls[0]['call_id'], retry['call_id'], failed['call_id']],
        owned_call_ids=[retry['call_id'], failed['call_id']],
        **{field: original[-1][field] for field in ('pipeline_latency_seconds', 'preparation_seconds',
            'preparation_measurement_id', 'preparation_owner_arm_id')},
        continuation_overhead={'pipeline_latency_seconds': 0.8, 'preparation_seconds': 0.4,
            'preparation_measurement_id': digest([cid, 'retry preparation']),
            'preparation_owner_arm_id': scoring.ARMS[-1]})
    recovered.pop('sha256')
    rows[4] = seal(recovered)
    for index in range(5, len(rows)):
        rows[index] = seal({**{key: value for key, value in rows[index].items() if key != 'sha256'},
                            'continuation_overhead': {}})
    stages += [seal({'stage_key': stage_key, 'status': 'failed', 'binding': {'stage': 'refined_generation'},
                     'payload': None, 'latency_seconds': 0.4}),
               seal({'stage_key': stage_key, 'status': 'completed', 'binding': {'stage': 'refined_generation'},
                     'payload': {'annotation': {'provenance': {'repair_count': 0}}}, 'latency_seconds': 0.6})]
    effective_seal = seal({'gold_read': False, 'quality_claim': False, 'holdout_or_live_access': False})
    state = {'registration': spec, 'plan': plan, 'seal': effective_seal, 'results': rows, 'calls': calls,
        'base_calls': [calls[0], failed], 'overlay_calls': calls[1:25] + [retry],
        'base_arms': original, 'audit_arms': [original[-1]], 'stages': stages,
        'base_stages': [stages[0], stages[-2]], 'overlay_stages': stages[1:25] + [stages[-1]],
        'directory': tmp_path / 'artifacts/experiments/component_continuation' / plan['sha256']}
    return state, targets


def test_preserves_all_patch_files_and_all25_denominators(matrix):
    state, targets = matrix
    report = scoring.summarize_component_continuation(state, targets)
    assert report['schema'] == scoring.SCHEMA
    assert len(report['cases']) == 125
    for aggregate in report['aggregates'].values():
        assert aggregate['selected_cases'] == aggregate['completed_cases'] == 25
        assert aggregate['retrieval_localization']['recall_at_k']['mean'] == 0.5
        assert aggregate['gold_file_denominator'] == 'all_patch_files_including_unavailable'
    assert report['cases'][4]['unavailable_gold_files'] == ['outside.py']
    assert report['semantic_quality'] == 'unreviewed'
    assert report['independent_human'] is False
    assert report['holdout_or_live_access'] is False


def test_recovery_does_not_complete_original_or_change_inputs(matrix):
    state, targets = matrix
    before = deepcopy(state)
    report = scoring.summarize_component_continuation(state, targets)
    audit = report['continuation_attempt_audit']
    assert audit['original_status_counts'] == {'completed': 4, 'failed': 1}
    assert audit['effective_status_counts'] == {'completed': 125}
    assert audit['recovered_original_failed_arms'] == 1
    detail = report['cases'][4]['attempt_audit']
    assert detail['original_status'] == 'failed'
    assert detail['effective_status'] == 'completed'
    assert detail['original_pipeline_latency_seconds'] == 0.7
    assert detail['continuation_pipeline_latency_seconds'] == 0.8
    assert report['aggregates']['plus_counter_search']['original_attempt_status_counts'] == {'failed': 1, 'not_attempted': 24}
    assert state == before


def test_old_failed_new_and_shared_receipts_are_counted_once(matrix):
    state, targets = matrix
    report = scoring.summarize_component_continuation(state, targets)
    audit = report['continuation_attempt_audit']
    assert report['global_unique_provider_calls'] == 27
    assert report['global_unique_usage']['tokens_charged_or_reserved'] == 1080
    assert report['global_unique_usage']['micro_usd_charged_or_reserved'] == 144
    assert report['global_unique_usage']['unsettled_or_unknown_micro_usd_bound'] == 40
    assert audit['original_unique_provider_calls'] == 2
    assert audit['continuation_unique_provider_calls'] == 25
    assert audit['original_failed_bound_usage']['micro_usd_charged_or_reserved'] == 40
    assert audit['original_failed_provider_active_time']['observed_seconds'] == 0.3
    assert audit['explicit_transport_retry_calls'] == 1
    assert audit['native_schema_repair_calls'] == 0
    assert audit['transport_retries_are_schema_repairs'] is False
    assert audit['provider_invoice_verified'] is False
    assert report['aggregates']['plus_counter_search']['explicit_transport_retry_calls'] == 1
    assert audit['original_native_stage_active_time']['observed_seconds'] == 0.4
    assert audit['continuation_native_stage_active_time']['observed_seconds'] == 0.6


def test_preparation_and_pipeline_attempts_are_preserved_separately(matrix):
    report = scoring.summarize_component_continuation(*matrix)
    audit = report['continuation_attempt_audit']
    assert report['global_unique_preparation_latency']['observed_seconds'] == pytest.approx(2.9)
    assert report['native_preparation_metric_projection']['observed_seconds'] == pytest.approx(2.5)
    assert audit['original_unique_preparation_time']['observed_seconds'] == 0.1
    assert audit['continuation_added_unique_preparation_time']['observed_seconds'] == pytest.approx(2.8)
    assert audit['all_attempt_unique_preparation_time']['observed_seconds'] == pytest.approx(2.9)
    assert audit['original_pipeline_active_time']['observed_seconds'] == pytest.approx(2.7)
    assert audit['continuation_pipeline_active_time']['observed_seconds'] == pytest.approx(60.8)
    assert audit['all_attempt_pipeline_active_time']['observed_seconds'] == pytest.approx(63.5)
    assert report['cases'][4]['preparation_seconds'] == 0.1
    assert report['cases'][4]['attempt_audit']['effective_preparation']['preparation_seconds'] == 0.1
    assert report['cases'][4]['attempt_audit']['continuation_preparation']['preparation_seconds'] == 0.4


@pytest.mark.parametrize('mutation', [
    lambda row: row.pop('continuation_overhead'),
    lambda row: row.update(pipeline_latency_seconds=0.8),
    lambda row: row['continuation_overhead'].update(extra=True),
    lambda row: row['continuation_overhead'].update(pipeline_latency_seconds=float('nan')),
    lambda row: row['continuation_overhead'].update(preparation_seconds=-1),
    lambda row: row['continuation_overhead'].update(preparation_owner_arm_id=scoring.ARMS[0]),
    lambda row: row['continuation_overhead'].update(preparation_measurement_id='invalid'),
])
def test_recovered_measurements_require_separate_valid_overhead(matrix, mutation):
    state, targets = matrix
    mutation(state['results'][4])
    with pytest.raises(ValueError):
        scoring.summarize_component_continuation(state, targets)


def test_fresh_matrix_arm_cannot_hide_extra_measurements_in_overhead(matrix):
    state, targets = matrix
    state['results'][5]['continuation_overhead'] = deepcopy(state['results'][4]['continuation_overhead'])
    with pytest.raises(ValueError, match='Fresh matrix timing'):
        scoring.summarize_component_continuation(state, targets)


def test_standard_native_metrics_are_reused_without_new_judge(monkeypatch, matrix):
    state, targets = matrix
    actual = frozen.summarize_component_ablation
    invocations = []
    def summarize(*args):
        invocations.append(args)
        return actual(*args)
    monkeypatch.setattr(frozen, 'summarize_component_ablation', summarize)
    scoring.summarize_component_continuation(state, targets)
    assert len(invocations) == 1
    assert invocations[0][0] is state['registration']
    assert invocations[0][3] is state['calls']
    assert invocations[0][2] is targets


@pytest.mark.parametrize('mutation', [
    lambda state: state['results'].pop(),
    lambda state: state['results'][0].update(status='failed', pack=None),
    lambda state: state['calls'][0].update(status='intent'),
    lambda state: state['overlay_calls'].append(deepcopy(state['base_calls'][0])),
    lambda state: state['results'][4].update(call_ids=['c' * 64] * 6),
    lambda state: state['base_arms'][0].update(status='completed', pack=None),
    lambda state: state['audit_arms'][0].update(status='completed'),
    lambda state: state['stages'][0].update(status='intent'),
    lambda state: state['results'][5].update(preparation_seconds=float('nan')),
])
def test_incomplete_or_inconsistent_attempts_reject(matrix, mutation):
    state, targets = matrix
    mutation(state)
    with pytest.raises((ValueError, TypeError, KeyError)):
        scoring.summarize_component_continuation(state, targets)


def authorization(state):
    return {'schema': scoring.AUTHORIZATION_SCHEMA, 'scope': scoring.SCOPE,
        'plan_sha256': state['plan']['sha256'], 'registration_sha256': state['registration']['sha256'],
        'effective_inference_seal_sha256': state['seal']['sha256'],
        'adapter_sources_sha256': digest(state['plan']['adapter_sources']),
        'selected_case_ids': [row['case_id'] for row in state['registration']['cases']],
        'split': 'main_dev', 'gold_scoring_authorized': True, 'new_provider_calls_authorized': False,
        'holdout_or_live_access': False, 'declared_at': '2026-10-24T00:00:00Z', 'declared_by': 'synthetic author'}


def wrapper(monkeypatch, tmp_path, matrix):
    from issueintel.benchmarks import dev_context
    state, targets = matrix
    path = tmp_path / 'artifacts/score-authorization.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(authorization(state)), encoding='utf-8')
    order = []
    def load(*args, **kwargs):
        order.append('replay')
        return deepcopy(state)
    def context(root, cid):
        order.append('public')
        return {'split': 'main_dev'}, SimpleNamespace(protocol='issue_time', files=[SimpleNamespace(path='hit.py')]), {}
    def gold(*args):
        order.append('gold')
        return deepcopy(targets), {'sha256': 'd' * 64}
    monkeypatch.setattr(scoring, '_extension', lambda name: SimpleNamespace(load_sealed_continuation=load))
    monkeypatch.setattr(dev_context, 'accepted_context', context)
    monkeypatch.setattr(frozen, '_gold_targets', gold)
    return state, path, order


def test_exact_separate_gold_permission_after125_replay(monkeypatch, tmp_path, matrix):
    state, path, order = wrapper(monkeypatch, tmp_path, matrix)
    result = scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)
    assert order[:2] == ['replay', 'public']
    assert order.index('gold') == 27
    assert order[-1] == 'replay'
    report = json.loads(Path(result['report_json']).read_bytes())
    assert report['sha256'] == digest({key: row for key, row in report.items() if key != 'sha256'})
    assert report['plan_sha256'] == state['plan']['sha256']
    assert report['inference_seal_sha256'] == state['seal']['sha256']
    assert report['inference_gold_used'] is False
    assert report['scoring_gold_used'] is True
    assert result == scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)


@pytest.mark.parametrize('mutation', [
    lambda auth: auth.update(gold_scoring_authorized=False),
    lambda auth: auth.update(effective_inference_seal_sha256='0' * 64),
    lambda auth: auth.update(plan_sha256='0' * 64),
    lambda auth: auth.update(registration_sha256='0' * 64),
    lambda auth: auth.update(adapter_sources_sha256='0' * 64),
    lambda auth: auth.update(selected_case_ids=list(reversed(auth['selected_case_ids']))),
    lambda auth: auth.update(split='main_test'),
    lambda auth: auth.update(new_provider_calls_authorized=True),
    lambda auth: auth.update(holdout_or_live_access=True),
    lambda auth: auth.update(declared_at='2026-10-24'),
    lambda auth: auth.update(declared_by=''),
])
def test_gold_not_opened_with_missing_or_mismatched_permission(monkeypatch, tmp_path, matrix, mutation):
    state, path, order = wrapper(monkeypatch, tmp_path, matrix)
    auth = authorization(state)
    mutation(auth)
    path.write_text(json.dumps(auth), encoding='utf-8')
    with pytest.raises((PermissionError, ValueError)):
        scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)
    assert 'gold' not in order


def test_no_gold_when_native_replay_rejects(monkeypatch, tmp_path, matrix):
    state, path, order = wrapper(monkeypatch, tmp_path, matrix)
    def reject(*args, **kwargs):
        raise ValueError('native replay mismatch')
    monkeypatch.setattr(scoring, '_extension', lambda name: SimpleNamespace(load_sealed_continuation=reject))
    with pytest.raises(ValueError, match='native replay'):
        scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)
    assert 'gold' not in order


def test_no_gold_when_native_citation_or_attempt_validation_rejects(monkeypatch, tmp_path, matrix):
    state, path, order = wrapper(monkeypatch, tmp_path, matrix)
    state['results'][5]['pack']['annotation']['hypotheses'][0]['support_ids'] = ['unknown']
    with pytest.raises(ValueError, match='unavailable evidence'):
        scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)
    assert 'gold' not in order


def test_no_gold_when_effective_inference_changes_before_boundary(monkeypatch, tmp_path, matrix):
    state, path, order = wrapper(monkeypatch, tmp_path, matrix)
    count = 0
    def load(*args, **kwargs):
        nonlocal count
        count += 1
        result = deepcopy(state)
        if count > 1:
            result['seal'] = seal({**{key: value for key, value in state['seal'].items() if key != 'sha256'}, 'changed': True})
        return result
    monkeypatch.setattr(scoring, '_extension', lambda name: SimpleNamespace(load_sealed_continuation=load))
    with pytest.raises(ValueError, match='changed before'):
        scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)
    assert 'gold' not in order


def test_append_only_content_hash_report_preserves_original(monkeypatch, tmp_path, matrix):
    state, path, order = wrapper(monkeypatch, tmp_path, matrix)
    marker = tmp_path / 'artifacts/original-seal.json'
    marker.write_bytes(b'original immutable receipt bytes')
    result = scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)
    report_path = Path(result['report_json'])
    assert report_path.parent.name == result['score_sha256']
    assert 'Explicit transport retry calls: 1' in Path(result['report_markdown']).read_text(encoding='utf-8')
    report_path.write_bytes(b'corrupt existing report')
    with pytest.raises(ValueError, match='immutable'):
        scoring.score_component_continuation(tmp_path, state['plan']['sha256'], path)
    assert marker.read_bytes() == b'original immutable receipt bytes'


def test_extension_does_not_hide_a_missing_dependency(monkeypatch):
    def missing_dependency(name):
        raise ModuleNotFoundError('nested dependency is missing', name='synthetic_dependency')
    monkeypatch.setattr(scoring.importlib, 'import_module', missing_dependency)
    with pytest.raises(ModuleNotFoundError, match='nested dependency'):
        scoring._extension('component_continuation_run')
