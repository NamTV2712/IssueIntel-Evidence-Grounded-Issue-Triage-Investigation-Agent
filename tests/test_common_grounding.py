"""Pure native common-critic assessment; no provider, data or target execution."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from issueintel.evaluation import common_grounding as common
from test_planned_pipeline import planned_repository, PlannedModel


def sealed(value):
    return {**value, 'sha256': common.digest(value)}


def native_pack(issue, flavor='shared'):
    text = 'def parse(value):\n    return value[0]\n'
    evidence = [{'id': 'code1', 'kind': 'code', 'locator': 'parser.py:1-2',
        'text': text, 'sha256': sha256(text.encode()).hexdigest(), 'revision': 'a' * 40,
        'provenance': {'kind': 'code', 'receipt_metadata': 'PRIVATE_RECEIPT_MARKER'}}]
    annotation = {'hypotheses': [{'id': f'H{index}', 'statement': f'{flavor} condition {index}',
        'support_ids': ['code1'], 'counter_ids': [], 'counter_status': 'not_found',
        'falsification_test': f'Inspect input condition {index}'} for index in range(1, 4)],
        'source_quality': {}, 'provenance': {'kind': 'llm', 'provider': 'groq',
            'model': 'openai/gpt-oss-120b', 'receipt_metadata': 'PRIVATE_ANNOTATION_MARKER'}}
    return {'case': {'case_id': issue}, 'annotation': annotation, 'evidence': evidence,
        'workflow_plan': {'seed_annotation': deepcopy(annotation)},
        'verification': {'old_critic_marker': 'PRIVATE_CRITIC_MARKER'}}


def source_rows(*, distinct=False):
    cases = [{'unit_id': f'case-{index:02d}', 'context': {
        'case_id': f'https://github.com/example/study/issues/{index}'}} for index in range(1, 6)]
    rows = []
    for selected in cases:
        for arm in common.ARMS:
            flavor = selected['unit_id'] + ':' + arm if distinct else 'shared'
            rows.append(sealed({'schema': 'controller_study_arm_v1',
                'unit_id': selected['unit_id'], 'arm_id': arm, 'source': selected['context'],
                'status': 'completed', 'pack': native_pack(selected['context']['case_id'], flavor),
                'failure': None}))
    return cases, rows


def plan_for(cases=None, rows=None, *, distinct=False):
    if cases is None:
        cases, rows = source_rows(distinct=distinct)
    return sealed({'schema': 'controller_common_grounding_plan_v1',
        'judge_config': common.JUDGE_CONFIG, 'fresh_common_judge': True,
        **common.make_units(cases, rows)})


def response_for(unit, labels=None):
    labels = labels or ['supported', 'unsupported', 'uncertain']
    return {'reviews': [{'hypothesis_id': row['id'], 'assessment': label,
        'reason': 'Synthetic visible-evidence assessment', 'evidence_ids': ['code1']}
        for row, label in zip(unit['payload']['hypotheses'], labels)]}


def receipt(rid='1' * 32, *, status='settled', observed=False):
    return {'reservation_id': rid, 'created_at': '2026-10-26T00:00:00+00:00',
        'model': 'openai/gpt-oss-120b', 'input_bound': 1000, 'output_bound': 2048,
        'tokens_charged_or_reserved': 150 if status == 'settled' else 3048,
        'micro_usd_charged_or_reserved': 100, 'status': status, 'price_date': '2026-10-26',
        'observed_input_tokens': 100 if observed else None,
        'observed_output_tokens': 50 if observed else None}


def reviewed(unit, rid='1' * 32, *, observed=False):
    response = response_for(unit)
    return {'unit_id': unit['unit_id'], 'request_sha256': common.digest(unit['request']),
        'status': 'reviewed', 'receipts': {rid: receipt(rid, observed=observed)},
        'response': response, 'verification': common.validate_response(unit, response),
        'failure': None, 'latency_seconds': 0.75}


def test_projection_is_native_fresh_public_critic_only():
    source = native_pack('https://github.com/example/study/issues/1')
    before = deepcopy(source)
    payload, request = common.project(source)
    assert source == before
    assert request['model'] == 'openai/gpt-oss-120b'
    assert request['reasoning_effort'] == 'low' and request['max_output_tokens'] == 2048
    assert json.loads(request['messages'][1]['content']) == payload
    assert request['messages'][1]['content'] == json.dumps(payload, ensure_ascii=False,
        sort_keys=True, separators=(',', ':'))
    rendered = json.dumps(request)
    assert all(marker not in rendered for marker in (
        'PRIVATE_RECEIPT_MARKER', 'PRIVATE_ANNOTATION_MARKER', 'PRIVATE_CRITIC_MARKER',
        'workflow_plan', 'reasoning_effort_DO_NOT_SEND'))
    assert set(payload) == {'hypotheses', 'evidence'}


def test_actual_native_planned_pack_projects_without_in_treatment_critic(planned_repository):
    from issueintel.investigation.pipeline import investigate
    case, repository = planned_repository
    pack = investigate(case, repository, planned_workflow=True, planner_policy='bounded_planner_v2',
        complete_json=PlannedModel(), provider='groq', model='openai/gpt-oss-120b',
        top_k=12, max_output_tokens=4096)
    # The extension consumes saved parent result JSON, whose native tuples have
    # already been rendered as arrays by the original immutable publication.
    pack = json.loads(json.dumps(pack))
    assert pack['annotation'] and pack['workflow_plan']['seed_annotation']
    assert pack['verification']['kind'] == 'llm_separate_pass'
    payload, request = common.project(pack)
    assert payload['hypotheses']
    assert request['max_output_tokens'] == 2048 and 'reviews' not in json.loads(request['messages'][1]['content'])


@pytest.mark.parametrize('where,key,value', [
    ('pack', 'gold_patch', 'SECRET_GOLD'), ('annotation', 'source_quality', {'human': 'rationale'}),
    ('evidence', 'human_notes', 'SECRET_HUMAN'), ('plan', 'hidden_tests', ['secret']),
    ('pack', 'PATCH', 'SECRET_GOLD'), ('evidence', 'kind', 'source_human'),
])
def test_restricted_raw_inputs_rejected_before_projection(where, key, value):
    source = native_pack('https://github.com/example/study/issues/1')
    target = {'pack': source, 'annotation': source['annotation'],
              'evidence': source['evidence'][0], 'plan': source['workflow_plan']}[where]
    target[key] = value
    with pytest.raises(ValueError):
        common.project(source)


def test_hashseed_independent_canonical_request():
    package = str(Path(__file__).resolve().parents[1] / 'src')
    source = native_pack('https://github.com/example/study/issues/1')
    program = ('import json,sys;sys.path.insert(0,sys.argv[1]);'
        'from issueintel.evaluation.common_grounding import project,digest;'
        'print(digest(project(json.loads(sys.argv[2]))[1]))')
    values = []
    for seed in ('3', '7654'):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        values.append(subprocess.check_output([sys.executable, '-S', '-c', program,
            package, json.dumps(source)], env=env, text=True).strip())
    assert values == [common.digest(common.project(source)[1])] * 2


def test_exact_request_dedup_preserves_ten_cells_and_five_clusters():
    cases, rows = source_rows()
    result = common.make_units(cases, rows)
    assert result['case_ids'] == [row['unit_id'] for row in cases]
    assert len(result['arm_bindings']) == 10 and len(result['units']) == 1
    unit = result['units'][0]
    assert unit['case_ids'] == result['case_ids']
    assert unit['unit_id'] == common.digest(unit['request'])
    assert {row['arm_sha256'] for row in result['arm_bindings']} == {row['sha256'] for row in rows}
    score = common.summarize(plan_for(cases, rows), {unit['unit_id']: reviewed(unit)})
    assert score['case_clusters'] == 5 and score['arm_results'] == 10
    assert score['global_unique_usage']['unique_reservations'] == 1
    assert score['global_unique_usage']['micro_usd_charged_or_reserved'] == 100
    assert score['global_unique_unit_latency']['observed_seconds'] == 0.75
    assert len(score['unit_aliases'][unit['unit_id']]) == 10
    assert score['paired_comparisons'][0]['common_reviewed_cases'] == 5
    for arm in common.ARMS:
        assert score['by_arm'][arm]['selected_cases'] == score['by_arm'][arm]['reviewed_cases'] == 5
        assert score['by_arm'][arm]['reference_timing_is_additive_across_arms'] is False
    assert score['independent_human'] is score['semantic_accuracy_verified'] is False
    assert score['causal_truth_verified'] is score['developer_benefit_verified'] is False


def test_distinct_native_output_requests_have_at_most_ten_owned_units():
    plan = plan_for(distinct=True)
    assert len(plan['units']) == 10
    records = {unit['unit_id']: reviewed(unit, f'{index:032x}') for index, unit in enumerate(plan['units'], 1)}
    score = common.summarize(plan, records)
    assert score['global_unique_usage']['unique_reservations'] == 10
    assert score['global_unique_usage']['micro_usd_charged_or_reserved'] == 1000


def test_failed_unavailable_and_missing_parent_cells_retain_selected_denominators():
    cases, rows = source_rows(distinct=True)
    for row, status in ((rows[0], 'failed'), (rows[3], 'unavailable')):
        row.update(status=status, pack=None, failure={'error_type': 'SyntheticFailure'})
        row['sha256'] = common.digest({key: value for key, value in row.items() if key != 'sha256'})
    rows.pop()
    plan = plan_for(cases, rows)
    assert len(plan['units']) == 7
    score = common.summarize(plan, {})
    assert score['arm_results'] == 10
    assert score['by_arm']['rules']['selected_cases'] == score['by_arm']['jev']['selected_cases'] == 5
    assert score['by_arm']['rules']['source_status_counts']['failed'] == 1
    assert score['by_arm']['jev']['source_status_counts']['unavailable'] == 1
    assert score['by_arm']['jev']['source_status_counts']['missing'] == 1
    assert score['paired_comparisons'][0]['common_reviewed_cases'] == 0
    assert all(value is None for value in score['paired_comparisons'][0]['mean_fraction_difference_right_minus_left'].values())


@pytest.mark.parametrize('mutation', ['count', 'duplicate-case', 'duplicate-row', 'hash', 'source', 'wrong-issue', 'failed-pack', 'unknown-status'])
def test_parent_binding_shape_and_hash_checked(mutation):
    cases, rows = source_rows()
    if mutation == 'count': cases.pop()
    elif mutation == 'duplicate-case': cases[1] = deepcopy(cases[0])
    elif mutation == 'duplicate-row': rows[1] = deepcopy(rows[0])
    elif mutation == 'hash': rows[0]['sha256'] = 'f' * 64
    else:
        row = rows[0]
        if mutation == 'source': row['source'] = {'case_id': 'another'}
        elif mutation == 'wrong-issue': row['pack']['case']['case_id'] = 'another'
        elif mutation == 'failed-pack': row.update(status='failed', failure={})
        else: row['status'] = 'pending'
        row['sha256'] = common.digest({key: value for key, value in row.items() if key != 'sha256'})
    with pytest.raises(ValueError):
        common.make_units(cases, rows)


@pytest.mark.parametrize('mutation', ['alias', 'request', 'payload', 'mapping', 'duplicate-unit', 'extra-unit', 'wrong-arm'])
def test_rehashed_plan_cannot_change_unit_projection_or_mapping(mutation):
    plan = plan_for()
    if mutation == 'alias': plan['units'][0]['case_ids'].pop()
    elif mutation == 'request': plan['units'][0]['request']['max_output_tokens'] = 4096
    elif mutation == 'payload': plan['units'][0]['payload']['hypotheses'][0]['statement'] = 'changed'
    elif mutation == 'mapping': plan['arm_bindings'][0]['unit_id'] = None
    elif mutation == 'duplicate-unit': plan['units'].append(deepcopy(plan['units'][0]))
    elif mutation == 'extra-unit': plan['units'][0]['unowned_extra'] = True
    else: plan['arms'] = ['rules', 'rules']
    plan['sha256'] = common.digest({key: value for key, value in plan.items() if key != 'sha256'})
    with pytest.raises(ValueError):
        common.summarize(plan, {})


@pytest.mark.parametrize('mutation', ['missing-review', 'wrong-id', 'unknown-citation', 'extra-field', 'boolean-assessment'])
def test_native_wire_schema_and_critic_validation(mutation):
    unit = plan_for()['units'][0]
    response = response_for(unit)
    if mutation == 'missing-review': response['reviews'].pop()
    elif mutation == 'wrong-id': response['reviews'][0]['hypothesis_id'] = 'H999'
    elif mutation == 'unknown-citation': response['reviews'][0]['evidence_ids'] = ['MISSING']
    elif mutation == 'extra-field': response['reviews'][0]['invented'] = 'field'
    else: response['reviews'][0]['assessment'] = True
    with pytest.raises(ValueError):
        common.validate_response(unit, response)


@pytest.mark.parametrize('mutation', ['response', 'verification', 'missing-receipt', 'duplicate-receipt', 'unknown-unit', 'wrong-request', 'nan-latency', 'boolean-latency', 'negative-cost', 'partial-observation', 'bad-date', 'wrong-model'])
def test_result_provenance_and_accounting_reject_tampering(mutation):
    plan = plan_for(distinct=True)
    unit = plan['units'][0]
    record = reviewed(unit)
    records = {unit['unit_id']: record}
    if mutation == 'response': record['response']['reviews'][0]['assessment'] = 'uncertain'
    elif mutation == 'verification': record['verification']['reviews'][0]['assessment'] = 'uncertain'
    elif mutation == 'missing-receipt': record['receipts'] = {}
    elif mutation == 'duplicate-receipt': records[plan['units'][1]['unit_id']] = reviewed(plan['units'][1])
    elif mutation == 'unknown-unit': records['f' * 64] = records.pop(unit['unit_id'])
    elif mutation == 'wrong-request': record['request_sha256'] = 'f' * 64
    elif mutation == 'nan-latency': record['latency_seconds'] = float('nan')
    elif mutation == 'boolean-latency': record['latency_seconds'] = True
    else:
        value = next(iter(record['receipts'].values()))
        if mutation == 'negative-cost': value['micro_usd_charged_or_reserved'] = -1
        elif mutation == 'partial-observation': value['observed_input_tokens'] = 100
        elif mutation == 'bad-date': value['price_date'] = 'not-a-date'
        else: value['model'] = 'different-model'
    with pytest.raises(ValueError):
        common.summarize(plan, records)


def test_low_native_settled_directional_unknown_is_not_fabricated():
    plan = plan_for(); unit = plan['units'][0]
    score = common.summarize(plan, {unit['unit_id']: reviewed(unit)})
    usage = score['global_unique_usage']
    assert usage['tokens_charged_or_reserved'] == 150
    assert usage['observed_input_tokens'] is usage['observed_output_tokens'] is None
    assert usage['known_input_tokens_sum'] == usage['known_output_tokens_sum'] == 0
    assert usage['directional_totals_cover_all_owned_attempts'] is False
    assert usage['directional_unknown_reservations'] == 1
    assert usage['directional_observed_reservations'] == 0


def test_unknown_attempt_bound_and_failed_judge_are_retained_separately():
    plan = plan_for(distinct=True)
    first, second = plan['units'][:2]
    records = {first['unit_id']: {'status': 'intent', 'receipts': {},
        'verification': None, 'response': None, 'latency_seconds': None, 'micro_usd_bound': 300},
        second['unit_id']: {'status': 'failed', 'receipts': {'2' * 32: receipt('2' * 32, status='failed_bound')},
            'verification': None, 'response': None, 'latency_seconds': 0.2}}
    score = common.summarize(plan, records)
    assert score['by_arm']['rules']['judge_unknown_cases'] == 1
    assert score['by_arm']['jev']['judge_failed_cases'] == 1
    assert score['global_unique_usage']['owned_attempts'] == 2
    assert score['global_unique_usage']['without_receipt_known_micro_usd_bound'] == 300
    assert score['global_unique_usage']['receipt_totals_cover_all_owned_attempts'] is False
    assert score['global_unique_unit_latency']['unknown_units'] == 1


def test_pure_operations_never_construct_provider_or_read_environment(monkeypatch):
    import issueintel.investigation.providers as providers
    import issueintel.runtime.io as runtime_io
    import issueintel.runtime.config as config
    import socket
    def forbidden(*args, **kwargs):
        pytest.fail('Pure common grounding cannot perform runtime operations')
    monkeypatch.setattr(providers, 'GroqProvider', forbidden)
    monkeypatch.setattr(runtime_io, 'load_local_env', forbidden)
    monkeypatch.setattr(config.RuntimeConfig, 'load', forbidden)
    monkeypatch.setattr(config.RuntimeConfig, 'ledger', forbidden)
    monkeypatch.setattr(common.StudyBudgetLedger, '__init__', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    plan = plan_for(); unit = plan['units'][0]
    assert common.summarize(plan, {unit['unit_id']: reviewed(unit)})['completed_units'] == 1


@pytest.mark.parametrize('value', [float('nan'), float('inf'), (1, 2), {1: 'not-text-key'}])
def test_non_json_or_nonfinite_values_rejected(value):
    source = native_pack('https://github.com/example/study/issues/1')
    source['injected_value'] = value
    with pytest.raises(ValueError):
        common.project(source)


@pytest.mark.parametrize('field,value', [('unit_id', []), ('arm_id', {}), ('status', [])])
def test_malformed_parent_types_fail_as_validation_errors(field, value):
    cases, rows = source_rows()
    rows[0][field] = value
    rows[0]['sha256'] = common.digest({key: item for key, item in rows[0].items() if key != 'sha256'})
    with pytest.raises(ValueError):
        common.make_units(cases, rows)


@pytest.mark.parametrize('field,value', [('case_id', []), ('arm_id', {}), ('source_status', []), ('unit_id', [])])
def test_malformed_mapping_types_fail_as_validation_errors(field, value):
    plan = plan_for()
    plan['arm_bindings'][0][field] = value
    plan['sha256'] = common.digest({key: item for key, item in plan.items() if key != 'sha256'})
    with pytest.raises(ValueError):
        common.summarize(plan, {})


def test_native_model_result_cannot_silently_become_structural_only():
    unit = plan_for()['units'][0]
    with pytest.raises(ValueError):
        common.native_result(unit, None)


def test_record_and_plan_hashes_verified_when_present():
    plan = plan_for(); unit = plan['units'][0]
    record = sealed(reviewed(unit))
    assert common.summarize(plan, {unit['unit_id']: record})['completed_units'] == 1
    record['sha256'] = 'f' * 64
    with pytest.raises(ValueError):
        common.summarize(plan, {unit['unit_id']: record})
    plan['sha256'] = 'f' * 64
    with pytest.raises(ValueError):
        common.summarize(plan, {})


@pytest.mark.parametrize('mutation', ['empty', 'too-many', 'duplicate'])
def test_completed_product_requires_native_hypothesis_count_and_unique_ids(mutation):
    source = native_pack('https://github.com/example/study/issues/1')
    rows = source['annotation']['hypotheses']
    if mutation == 'empty': rows.clear()
    elif mutation == 'too-many': rows.extend(deepcopy(rows))
    else: rows[1]['id'] = rows[0]['id']
    with pytest.raises(ValueError):
        common.project(source)


def test_known_directional_usage_remains_observed():
    plan = plan_for(); unit = plan['units'][0]
    score = common.summarize(plan, {unit['unit_id']: reviewed(unit, observed=True)})
    usage = score['global_unique_usage']
    assert usage['observed_input_tokens'] == 100 and usage['observed_output_tokens'] == 50
    assert usage['known_input_tokens_sum'] == 100 and usage['known_output_tokens_sum'] == 50
    assert usage['directional_totals_cover_all_owned_attempts'] is True
    assert usage['directional_observed_reservations'] == 1 and usage['directional_unknown_reservations'] == 0


def test_returned_review_with_missing_usage_keeps_conservative_native_bound():
    plan = plan_for(); unit = plan['units'][0]
    row = reviewed(unit)
    row['receipts']['1' * 32] = receipt(status='missing_usage_bound')
    usage = common.summarize(plan, {unit['unit_id']: row})['global_unique_usage']
    assert usage['settled_micro_usd'] == 0 and usage['unsettled_or_unknown_micro_usd_bound'] == 100
    assert usage['tokens_charged_or_reserved'] == 3048


def over_bound_receipt(mutation):
    value = receipt(observed=mutation != 'total-only')
    if mutation == 'total-only':
        value['tokens_charged_or_reserved'] = value['input_bound'] + value['output_bound'] + 1
    elif mutation == 'input':
        value['observed_input_tokens'] = value['input_bound'] + 1
        value['tokens_charged_or_reserved'] = value['observed_input_tokens'] + value['observed_output_tokens']
    else:
        value['observed_output_tokens'] = value['output_bound'] + 1
        value['tokens_charged_or_reserved'] = value['observed_input_tokens'] + value['observed_output_tokens']
    return value


@pytest.mark.parametrize('mutation', ['total-only', 'input', 'output'])
def test_settled_native_envelope_breach_cannot_publish_review_labels(mutation):
    plan = plan_for(); unit = plan['units'][0]
    record = reviewed(unit)
    record['receipts']['1' * 32] = over_bound_receipt(mutation)
    with pytest.raises(ValueError, match='common review'):
        common.summarize(plan, {unit['unit_id']: record})


@pytest.mark.parametrize('mutation', ['total-only', 'input', 'output'])
def test_failed_native_envelope_breach_keeps_usage_without_labels(mutation):
    plan = plan_for(); unit = plan['units'][0]
    value = over_bound_receipt(mutation)
    record = {'unit_id': unit['unit_id'], 'status': 'failed',
        'receipts': {'1' * 32: value}, 'response': None, 'verification': None,
        'latency_seconds': 0.2, 'failure': {'error_type': 'SyntheticBudgetBreach'}}
    score = common.summarize(plan, {unit['unit_id']: record})
    assert score['completed_units'] == 0 and score['failed_units'] == 1
    assert score['global_unique_usage']['native_envelope_breach_reservations'] == 1
    assert score['global_unique_usage']['tokens_charged_or_reserved'] == value['tokens_charged_or_reserved']
    assert score['global_unique_usage']['micro_usd_charged_or_reserved'] == value['micro_usd_charged_or_reserved']
    assert all(sum(row['assessment_counts'].values()) == 0 for row in score['cases'])


def test_explicit_failed_breach_receipt_is_retained_as_unknown_not_reviewed():
    plan = plan_for(); unit = plan['units'][0]
    value = receipt(status='breach')
    record = {'status': 'failed', 'receipts': {'1' * 32: value},
        'response': None, 'verification': None, 'latency_seconds': 0.1}
    score = common.summarize(plan, {unit['unit_id']: record})
    assert score['global_unique_usage']['native_envelope_breach_reservations'] == 1
    assert score['unknown_units'] == 1 and score['completed_units'] == 0


def test_partial_directional_observations_do_not_become_complete_totals():
    plan = plan_for(distinct=True)
    first, second = plan['units'][:2]
    records = {first['unit_id']: reviewed(first, '1' * 32, observed=True),
               second['unit_id']: reviewed(second, '2' * 32, observed=False)}
    usage = common.summarize(plan, records)['global_unique_usage']
    assert usage['observed_input_tokens'] is usage['observed_output_tokens'] is None
    assert usage['known_input_tokens_sum'] == 100 and usage['known_output_tokens_sum'] == 50
    assert usage['tokens_charged_or_reserved'] == 300
    assert usage['directional_observed_reservations'] == usage['directional_unknown_reservations'] == 1
    assert usage['directional_totals_cover_all_owned_attempts'] is False


def test_missing_receipt_does_not_hide_an_unobserved_attempt_in_totals():
    plan = plan_for(distinct=True)
    first, second = plan['units'][:2]
    records = {first['unit_id']: reviewed(first, observed=True),
        second['unit_id']: {'status': 'intent', 'receipts': {}, 'verification': None,
                           'response': None, 'latency_seconds': None, 'micro_usd_bound': 300}}
    usage = common.summarize(plan, records)['global_unique_usage']
    assert usage['observed_input_tokens'] is usage['observed_output_tokens'] is None
    assert usage['known_input_tokens_sum'] == 100 and usage['known_output_tokens_sum'] == 50
    assert usage['without_receipt_units'] == 1
    assert usage['directional_totals_cover_all_owned_attempts'] is False


def test_no_attempts_does_not_claim_observed_token_usage():
    usage = common.summarize(plan_for(), {})['global_unique_usage']
    assert usage['owned_attempts'] == 0
    assert usage['observed_input_tokens'] is usage['observed_output_tokens'] is None
    assert usage['known_input_tokens_sum'] == usage['known_output_tokens_sum'] == 0


@pytest.mark.parametrize('value', [None, {}, True])
def test_invalid_hypothesis_container_is_a_validation_error(value):
    source = native_pack('https://github.com/example/study/issues/1')
    source['annotation']['hypotheses'] = value
    with pytest.raises(ValueError, match='native annotation'):
        common.project(source)


def test_missing_hypothesis_container_is_a_validation_error():
    source = native_pack('https://github.com/example/study/issues/1')
    del source['annotation']['hypotheses']
    with pytest.raises(ValueError, match='native annotation'):
        common.project(source)


@pytest.mark.parametrize('value', [None, [], True])
def test_completed_parent_case_container_is_a_validation_error(value):
    cases, rows = source_rows()
    rows[0]['pack']['case'] = value
    rows[0]['sha256'] = common.digest({key: item for key, item in rows[0].items() if key != 'sha256'})
    with pytest.raises(ValueError, match='bound product pack'):
        common.make_units(cases, rows)
