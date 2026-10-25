"""Synthetic conservation checks for one joint, cross-provider study allowance."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from issueintel.runtime.io import digest
from issueintel.runtime.study_budget import StudyBudgetLedger, StudyBudgetStop


TODAY = date.today().isoformat()


def hashed(text):
    return sha256(text.encode()).hexdigest()


def policy(**updates):
    value = {
        'schema': 'study_budget_policy_v1',
        'study_id': hashed('fixed synthetic controller comparison'),
        'config_sha256': hashed('study config'),
        'providers': [
            {'provider': 'groq', 'model': 'openai/gpt-oss-120b',
             'config_sha256': hashed('groq config'), 'price_sha256': hashed('groq prices'),
             'price_date': TODAY, 'max_attempts': 20},
            {'provider': 'typesafe', 'model': 'jev-1.13.0',
             'config_sha256': hashed('typesafe config'), 'price_sha256': hashed('typesafe prices'),
             'price_date': TODAY, 'max_attempts': 8},
        ],
        'arms': [{'arm_id': name, 'max_micro_usd': 20000, 'max_attempts': 9}
                 for name in ('case-01:rules', 'case-01:jev')],
        'max_owned_attempts': 20,
        'max_owned_micro_usd': 40000,
    }
    value.update(updates)
    return value


def arguments(*, arm='case-01:rules', stage='seed_generation:1', request='seed', provider='groq'):
    row = next(item for item in policy()['providers'] if item['provider'] == provider)
    return {'arm_id': arm, 'stage': stage, 'request_sha256': hashed(request),
            'provider': provider, 'model': row['model'],
            'config_sha256': row['config_sha256'], 'price_sha256': row['price_sha256'],
            'input_bound': 1000 if provider == 'groq' else 64000,
            'output_bound': 100 if provider == 'groq' else 0,
            'micro_usd_bound': 210 if provider == 'groq' else 2688}


def groq_receipt(*, rid='a' * 32, status='reserved', charged=None, **updates):
    if charged is None:
        charged = 21 if status == 'settled' else 210
    returned = status == 'settled'
    value = {'reservation_id': rid, 'created_at': TODAY + 'T00:00:00+00:00',
             'model': 'openai/gpt-oss-120b', 'input_bound': 1000, 'output_bound': 100,
             'tokens_charged_or_reserved': 110 if returned else 1100,
             'micro_usd_charged_or_reserved': charged, 'status': status,
             'price_date': TODAY, 'observed_input_tokens': 100 if returned else None,
             'observed_output_tokens': 10 if returned else None}
    value.update(updates)
    return value


def typesafe_receipt(*, rid='b' * 32, status='reserved', request='jev', **updates):
    returned = status == 'settled'
    value = {'schema': 'typesafe_decision_receipt_v1', 'provider': 'typesafe',
             'reservation_id': rid, 'created_at': TODAY + 'T00:00:00+00:00',
             'model': 'jev-1.13.0',
             'binding': {'job_id': 'c' * 24, 'decision_index': 1,
                         'query_sha256': hashed('query'), 'snapshot_sha256': hashed('snapshot'),
                         'commit_sha': 'd' * 40, 'annotation_sha256': hashed('annotation'),
                         'evidence_sha256': hashed('evidence')},
             'request_sha256': hashed(request),
             'response_sha256': hashed('response') if returned or status == 'missing_usage_bound' else None,
             'status': status, 'input_bound': 64000,
             'input_tokens': 1000 if returned else None,
             'output_tokens': 35 if returned else None,
             'micro_usd_charged_or_reserved': 42 if returned else 2688,
             'price_date': TODAY, 'price_source': 'https://docs.typesafe.ai/models',
             'currency': 'USD', 'is_provider_invoice': False}
    value.update(updates)
    value['sha256'] = digest(value)
    return value


def completed(ledger, *, args=None, rid='a' * 32):
    args = arguments() if args is None else args
    call = ledger.reserve(**args)
    ledger.publish_receipt(call, groq_receipt(rid=rid))
    ledger.publish_receipt(call, groq_receipt(rid=rid, status='settled'))
    ledger.finish(call, outcome='completed', outcome_sha256=hashed('full stage payload'))
    return call


def call_record(ledger, call):
    return next(row for row in ledger.inspect()['calls'] if row['call_id'] == call)


@pytest.mark.parametrize('mutation', [
    'missing_usd', 'zero_usd', 'bool_attempts', 'duplicate_arm', 'duplicate_provider',
    'moving_model', 'invalid_digest', 'stale_price', 'unexpected_field',
])
def test_missing_or_mutated_frozen_policy_never_creates_an_implicit_unlimited_allowance(tmp_path, mutation):
    spec = policy()
    if mutation == 'missing_usd':
        del spec['max_owned_micro_usd']
    elif mutation == 'zero_usd':
        spec['max_owned_micro_usd'] = 0
    elif mutation == 'bool_attempts':
        spec['max_owned_attempts'] = True
    elif mutation == 'duplicate_arm':
        spec['arms'].append(deepcopy(spec['arms'][0]))
    elif mutation == 'duplicate_provider':
        spec['providers'].append(deepcopy(spec['providers'][0]))
    elif mutation == 'moving_model':
        spec['providers'][0]['model'] = 'gpt-oss-latest'
    elif mutation == 'invalid_digest':
        spec['providers'][0]['price_sha256'] = 'not a hash'
    elif mutation == 'stale_price':
        spec['providers'][0]['price_date'] = (date.today() - timedelta(days=31)).isoformat()
    else:
        spec['unlimited'] = True
    with pytest.raises(StudyBudgetStop):
        StudyBudgetLedger(tmp_path, spec)


def test_reserve_is_durable_before_provider_construction_and_survives_reopen(tmp_path):
    spec = policy()
    ledger = StudyBudgetLedger(tmp_path, spec)
    call = ledger.reserve(**arguments())
    assert len(call) == 32
    before = ledger.inspect()
    reopened = StudyBudgetLedger(tmp_path, deepcopy(spec)).inspect()
    assert before == reopened
    assert before['owned_attempts'] == 1
    assert before['owned_micro_usd_charged_or_reserved'] == 210
    assert before['arms']['case-01:rules']['attempts'] == 1
    assert before['arms']['case-01:rules']['micro_usd_charged_or_reserved'] == 210
    assert before['by_provider']['groq']['attempts'] == 1
    assert before['policy_sha256'] == digest(spec)
    assert not before['locked']


def test_unknown_without_native_receipt_never_refunds_attempt_or_money(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    ledger.close_unknown(call, 'crash_before_callback')
    ledger = StudyBudgetLedger(tmp_path, policy())
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210
    assert ledger.inspect()['owned_attempts'] == 1
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments())
    ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    assert ledger.inspect()['arms']['case-01:jev']['micro_usd_charged_or_reserved'] == 210


def test_schema_repair_is_a_new_bounded_ordinal_not_a_retry_of_existing_owner(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    first = ledger.reserve(**arguments())
    ledger.finish(first, outcome='failed')
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments())
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments(stage='seed_generation:2'))
    second = ledger.reserve(**arguments(stage='seed_generation:2', request='schema repair'))
    assert second != first
    assert ledger.inspect()['owned_attempts'] == 2
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 420


@pytest.mark.parametrize('update', [
    {'model': 'moving-model-alias'}, {'provider': 'another-provider'},
    {'config_sha256': hashed('changed config')}, {'price_sha256': hashed('changed prices')},
    {'input_bound': 0}, {'input_bound': True}, {'output_bound': -1},
    {'micro_usd_bound': 0}, {'micro_usd_bound': 1.5}, {'request_sha256': 'not a digest'},
])
def test_incompatible_reservation_does_not_create_attempt(tmp_path, update):
    ledger = StudyBudgetLedger(tmp_path, policy())
    args = arguments()
    args.update(update)
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**args)
    assert ledger.inspect()['owned_attempts'] == 0


@pytest.mark.parametrize('kind', ['arm_usd', 'arm_count', 'global_usd', 'global_count', 'provider_count'])
def test_every_finite_limit_is_admitted_atomically(tmp_path, kind):
    spec = policy()
    if kind == 'arm_usd':
        spec['arms'][0]['max_micro_usd'] = 419
    elif kind == 'arm_count':
        spec['arms'][0]['max_attempts'] = 1
    elif kind == 'global_usd':
        spec['max_owned_micro_usd'] = 419
    elif kind == 'global_count':
        spec['max_owned_attempts'] = 1
    else:
        spec['providers'][0]['max_attempts'] = 1
    ledger = StudyBudgetLedger(tmp_path, spec)
    ledger.reserve(**arguments())
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments(stage='next:1', request='different request'))
    assert ledger.inspect()['owned_attempts'] == 1


def test_one_micro_usd_below_reservation_denies_before_caller_can_construct_provider(tmp_path):
    spec = policy(max_owned_micro_usd=209)
    ledger = StudyBudgetLedger(tmp_path, spec)
    constructed = []
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments())
        constructed.append('provider')
    assert constructed == []
    assert ledger.inspect()['owned_attempts'] == 0


def test_shared_completed_call_counts_paid_once_and_consumes_each_arm_allowance(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = completed(ledger)
    returned = ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    assert returned == call
    state = ledger.inspect()
    assert state['owned_attempts'] == 1
    assert state['owned_micro_usd_charged_or_reserved'] == 21
    assert state['arms']['case-01:rules']['attempts'] == 1
    assert state['arms']['case-01:jev']['attempts'] == 1
    assert state['arms']['case-01:rules']['micro_usd_charged_or_reserved'] == 21
    assert state['arms']['case-01:jev']['micro_usd_charged_or_reserved'] == 21
    assert call_record(ledger, call)['micro_usd_bound'] == 210


def test_shared_reference_does_not_get_free_capacity_or_skip_binding_validation(tmp_path):
    spec = policy()
    spec['arms'][1]['max_micro_usd'] = 20
    ledger = StudyBudgetLedger(tmp_path, spec)
    call = completed(ledger)
    with pytest.raises(StudyBudgetStop):
        ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    assert ledger.inspect()['arms']['case-01:jev']['attempts'] == 0


def test_identical_reference_is_idempotent_but_changed_binding_is_denied(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = completed(ledger)
    assert ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed')) == call
    before = ledger.inspect()
    assert ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed')) == call
    assert ledger.inspect() == before
    for stage, request in [('seed_generation:1', hashed('changed request'))]:
        with pytest.raises(StudyBudgetStop):
            ledger.reference(call, 'case-01:jev', stage, request)
    assert ledger.inspect() == before


def test_identical_request_cannot_be_given_a_second_paid_owner(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    completed(ledger)
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments(arm='case-01:jev'))
    assert ledger.inspect()['owned_attempts'] == 1


def test_same_call_referenced_at_multiple_stages_counts_once_per_arm(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = completed(ledger)
    ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    ledger.reference(call, 'case-01:jev', 'same_input_generation:1', hashed('seed'))
    state = ledger.inspect()
    assert state['owned_attempts'] == 1
    assert state['owned_micro_usd_charged_or_reserved'] == 21
    assert state['arms']['case-01:jev']['attempts'] == 1
    assert state['arms']['case-01:jev']['micro_usd_charged_or_reserved'] == 21


def test_pending_call_cannot_be_shared_before_its_accounting_outcome_is_closed(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    with pytest.raises(StudyBudgetStop):
        ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))


@pytest.mark.parametrize('state', ['failed', 'unknown'])
def test_terminal_unusable_call_can_be_attributed_without_becoming_reusable_payload(tmp_path, state):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    ledger.finish(call, outcome=state)
    assert ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed')) == call
    record = call_record(ledger, call)
    assert record['outcome'] == state
    assert record['outcome_sha256'] is None
    assert ledger.inspect()['arms']['case-01:jev']['attempts'] == 1
    assert ledger.inspect()['arms']['case-01:jev']['micro_usd_charged_or_reserved'] == 210


def test_shared_successful_generation_after_repair_attributes_failed_attempt_too(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    failed = ledger.reserve(**arguments())
    ledger.publish_receipt(failed, groq_receipt(status='failed_bound'))
    ledger.finish(failed, outcome='failed')
    successful = completed(ledger, args=arguments(stage='seed_generation:2', request='repaired request'),
                           rid='b' * 32)
    ledger.reference(failed, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    ledger.reference(successful, 'case-01:jev', 'seed_generation:2', hashed('repaired request'))
    state = ledger.inspect()
    assert state['owned_attempts'] == 2
    assert state['owned_micro_usd_charged_or_reserved'] == 231
    for arm in ('case-01:rules', 'case-01:jev'):
        assert state['arms'][arm]['attempts'] == 2
        assert state['arms'][arm]['micro_usd_charged_or_reserved'] == 231
    assert call_record(ledger, failed)['outcome_sha256'] is None
    assert call_record(ledger, successful)['outcome'] == 'completed'


def test_completed_outcome_requires_returned_receipt_and_payload_hash(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    with pytest.raises(StudyBudgetStop):
        ledger.finish(call, outcome='completed', outcome_sha256=hashed('payload'))
    ledger.publish_receipt(call, groq_receipt(status='failed_bound'))
    with pytest.raises(StudyBudgetStop):
        ledger.finish(call, outcome='completed', outcome_sha256=hashed('payload'))
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210


@pytest.mark.parametrize('status', ['failed_bound', 'missing_usage_bound'])
def test_native_unknown_or_failed_usage_keeps_original_bound(tmp_path, status):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    ledger.publish_receipt(call, groq_receipt(status=status))
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210
    assert call_record(ledger, call)['micro_usd_bound'] == 210


def test_returned_stage_with_missing_usage_shares_the_full_bound_not_zero_cost(tmp_path):
    spec = policy()
    spec['arms'][1]['max_micro_usd'] = 209
    ledger = StudyBudgetLedger(tmp_path, spec)
    call = ledger.reserve(**arguments())
    ledger.publish_receipt(call, groq_receipt(status='missing_usage_bound'))
    ledger.finish(call, outcome='completed', outcome_sha256=hashed('returned stage without usage'))
    with pytest.raises(StudyBudgetStop):
        ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210
    assert ledger.inspect()['arms']['case-01:jev']['attempts'] == 0


@pytest.mark.parametrize('update', [
    {'model': 'jev-1.13.0'}, {'input_bound': 999}, {'output_bound': 99},
    {'price_date': (date.today() - timedelta(days=1)).isoformat()},
    {'micro_usd_charged_or_reserved': 209},
])
def test_native_reserved_receipt_must_match_exact_frozen_bound(tmp_path, update):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    receipt = groq_receipt()
    receipt.update(update)
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, receipt)
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210


def test_one_native_reservation_cannot_move_between_calls_or_providers(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    first = ledger.reserve(**arguments())
    second = ledger.reserve(**arguments(stage='next:1', request='next'))
    ledger.publish_receipt(first, groq_receipt())
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(second, groq_receipt())
    assert ledger.inspect()['owned_attempts'] == 2
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 420


def test_reservation_identity_is_unique_across_provider_namespaces(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    first = ledger.reserve(**arguments())
    second = ledger.reserve(**arguments(provider='typesafe', stage='decision:1', request='jev'))
    ledger.publish_receipt(first, groq_receipt())
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(second, typesafe_receipt(rid='a' * 32))
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 2898


def test_terminal_receipt_is_immutable_even_after_reopen(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = completed(ledger)
    ledger = StudyBudgetLedger(tmp_path, policy())
    before = ledger.inspect()
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, groq_receipt(status='settled', charged=20))
    after = ledger.inspect()
    assert after['calls'] == before['calls']
    assert after['owned_micro_usd_charged_or_reserved'] == before['owned_micro_usd_charged_or_reserved']
    assert after['locked']
    assert 'native_receipt_integrity_failure' in after['stop_reasons']


def test_identical_terminal_callback_and_outcome_are_idempotent(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = completed(ledger)
    before = ledger.inspect()
    events_before = sorted(path.name for path in (tmp_path / 'events').glob('*.json'))
    ledger.publish_receipt(call, groq_receipt(status='settled'))
    ledger.finish(call, outcome='completed', outcome_sha256=hashed('full stage payload'))
    assert ledger.inspect() == before
    assert sorted(path.name for path in (tmp_path / 'events').glob('*.json')) == events_before


def test_one_call_cannot_bind_a_second_native_reservation(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    ledger.publish_receipt(call, groq_receipt())
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, groq_receipt(rid='b' * 32))
    assert call_record(ledger, call)['receipt']['reservation_id'] == 'a' * 32


def test_final_stage_outcome_cannot_be_relabelled_or_have_its_payload_replaced(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = completed(ledger)
    before = ledger.inspect()
    with pytest.raises(StudyBudgetStop):
        ledger.finish(call, outcome='completed', outcome_sha256=hashed('different payload'))
    with pytest.raises(StudyBudgetStop):
        ledger.finish(call, outcome='unknown')
    assert ledger.inspect() == before


def test_typesafe_receipt_is_source_bound_and_part_of_same_envelope(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments(provider='typesafe', stage='decision:1', request='jev'))
    ledger.publish_receipt(call, typesafe_receipt())
    ledger.publish_receipt(call, typesafe_receipt(status='settled'))
    ledger.finish(call, outcome='completed', outcome_sha256=hashed('typed decision'))
    state = ledger.inspect()
    assert state['owned_attempts'] == 1
    assert state['owned_micro_usd_charged_or_reserved'] == 42
    assert state['by_provider']['typesafe']['attempts'] == 1
    assert state['arms']['case-01:rules']['micro_usd_charged_or_reserved'] == 42


def test_typesafe_request_hash_cannot_be_changed_then_rehashed(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments(provider='typesafe', stage='decision:1', request='jev'))
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, typesafe_receipt(request='another decision'))
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 2688


@pytest.mark.parametrize('receipt_status', ['breach', 'settled'])
def test_reported_breach_is_preserved_and_locks_future_spending(tmp_path, receipt_status):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    ledger.publish_receipt(call, groq_receipt())
    breach = groq_receipt(status=receipt_status, charged=225,
                          observed_input_tokens=1100, observed_output_tokens=100,
                          tokens_charged_or_reserved=1200)
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, breach)
    ledger = StudyBudgetLedger(tmp_path, policy())
    state = ledger.inspect()
    assert state['locked']
    assert state['owned_micro_usd_charged_or_reserved'] >= 225
    assert call_record(ledger, call)['receipt'] == breach
    assert call_record(ledger, call)['micro_usd_bound'] == 210
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments(stage='next:1', request='next'))


def test_cross_provider_concurrent_reservations_cannot_overdraw_one_joint_cap(tmp_path):
    spec = policy(max_owned_micro_usd=2688)
    StudyBudgetLedger(tmp_path, spec)
    barrier = threading.Barrier(2)

    def reserve(provider):
        ledger = StudyBudgetLedger(tmp_path, deepcopy(spec))
        barrier.wait(timeout=10)
        try:
            return ledger.reserve(**arguments(provider=provider,
                                              stage=provider + ':1', request=provider))
        except StudyBudgetStop:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ['groq', 'typesafe']))
    assert sum(value is not None for value in results) == 1
    state = StudyBudgetLedger(tmp_path, spec).inspect()
    assert state['owned_attempts'] == 1
    assert state['owned_micro_usd_charged_or_reserved'] in {210, 2688}


def test_fsynced_intent_survives_failure_before_sqlite_commit_and_cannot_retry(tmp_path, monkeypatch):
    ledger = StudyBudgetLedger(tmp_path, policy())
    persisted = []

    def crashed(event):
        persisted.append(event)
        raise OSError('synthetic crash after event fsync')

    monkeypatch.setattr(ledger, '_after_event_persist', crashed)
    with pytest.raises(OSError):
        ledger.reserve(**arguments())
    assert len(persisted) == 1
    reopened = StudyBudgetLedger(tmp_path, policy())
    assert reopened.inspect()['owned_attempts'] == 1
    assert reopened.inspect()['owned_micro_usd_charged_or_reserved'] == 210
    with pytest.raises(StudyBudgetStop):
        reopened.reserve(**arguments())


@pytest.mark.parametrize('transition', ['terminal_receipt', 'finish', 'reference'])
def test_post_response_or_shared_use_crash_keeps_its_last_fsynced_fact(tmp_path, monkeypatch, transition):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    ledger.publish_receipt(call, groq_receipt())
    if transition != 'terminal_receipt':
        ledger.publish_receipt(call, groq_receipt(status='settled'))
    if transition == 'reference':
        ledger.finish(call, outcome='completed', outcome_sha256=hashed('full stage payload'))

    def crashed(event):
        raise OSError('synthetic crash after this fact was fsynced')

    monkeypatch.setattr(ledger, '_after_event_persist', crashed)
    with pytest.raises(OSError):
        if transition == 'terminal_receipt':
            ledger.publish_receipt(call, groq_receipt(status='settled'))
        elif transition == 'finish':
            ledger.finish(call, outcome='completed', outcome_sha256=hashed('full stage payload'))
        else:
            ledger.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    reopened = StudyBudgetLedger(tmp_path, policy())
    assert reopened.inspect()['owned_attempts'] == 1
    assert reopened.inspect()['owned_micro_usd_charged_or_reserved'] == 21
    assert call_record(reopened, call)['receipt']['status'] == 'settled'
    if transition in {'finish', 'reference'}:
        assert call_record(reopened, call)['outcome'] == 'completed'
    if transition == 'reference':
        assert reopened.inspect()['arms']['case-01:jev']['micro_usd_charged_or_reserved'] == 21
        before = reopened.inspect()
        reopened.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
        assert reopened.inspect() == before
    with pytest.raises(StudyBudgetStop):
        reopened.reserve(**arguments())


@pytest.mark.parametrize('mutation', ['delete', 'move', 'rewrite', 'extra'])
def test_paid_event_cannot_disappear_or_hide_in_an_unowned_inventory(tmp_path, mutation):
    ledger = StudyBudgetLedger(tmp_path, policy())
    ledger.reserve(**arguments())
    files = sorted((tmp_path / 'events').glob('*.json'))
    assert files
    event = files[-1]
    if mutation == 'delete':
        event.unlink()
    elif mutation == 'move':
        backup = tmp_path / 'backup'
        backup.mkdir()
        event.rename(backup / event.name)
    elif mutation == 'rewrite':
        record = json.loads(event.read_text())
        record['synthetic_tamper'] = True
        event.write_text(json.dumps(record), encoding='utf-8')
    else:
        (tmp_path / 'notes.json').write_text('{}', encoding='utf-8')
    with pytest.raises(StudyBudgetStop):
        StudyBudgetLedger(tmp_path, policy()).inspect()


def test_reopening_with_changed_frozen_policy_cannot_restore_capacity(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    ledger.reserve(**arguments())
    changed = policy(max_owned_micro_usd=50000)
    with pytest.raises(StudyBudgetStop):
        StudyBudgetLedger(tmp_path, changed)


def test_historical_accounting_reopens_after_prices_expire_but_new_spend_is_denied(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    ledger.reserve(**arguments())
    future = datetime.now(timezone.utc) + timedelta(days=31)
    reopened = StudyBudgetLedger(tmp_path, policy(), clock=lambda: future)
    assert reopened.inspect()['owned_attempts'] == 1
    with pytest.raises(StudyBudgetStop):
        reopened.reserve(**arguments(stage='later:1', request='later'))
    assert reopened.inspect()['owned_attempts'] == 1


def test_sqlite_index_tamper_cannot_be_silently_rebuilt_as_if_unspent(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    ledger.reserve(**arguments())
    with sqlite3.connect(tmp_path / 'index.sqlite') as db:
        db.execute('UPDATE indexed_events SET sha256=? WHERE seq=(SELECT MAX(seq) FROM indexed_events)',
                   (hashed('tampered index'),))
    with pytest.raises(StudyBudgetStop):
        StudyBudgetLedger(tmp_path, policy()).inspect()


def test_policy_file_cannot_be_rewritten_to_larger_caps(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    ledger.reserve(**arguments())
    stored = json.loads((tmp_path / 'policy.json').read_text())
    # Modifying any persisted policy bytes must fail even with the same caller policy.
    stored['synthetic_tamper'] = 'larger allowance'
    (tmp_path / 'policy.json').write_text(json.dumps(stored), encoding='utf-8')
    with pytest.raises(StudyBudgetStop):
        StudyBudgetLedger(tmp_path, policy())


def test_stop_is_not_caught_by_controller_runtimeerror_fallback():
    assert issubclass(StudyBudgetStop, OSError)
    assert not issubclass(StudyBudgetStop, RuntimeError)


def test_actual_native_product_ledger_settles_without_direction_observations(tmp_path):
    from issueintel.runtime.budget import BudgetLedger, BudgetPolicy, ModelPrice

    price = ModelPrice('openai/gpt-oss-120b', Decimal('0.15'), Decimal('0.60'), TODAY,
                       'https://console.groq.com/docs/model/openai/gpt-oss-120b', 131072, 4096)
    native = BudgetLedger(tmp_path / 'native.sqlite', BudgetPolicy(100000, Decimal('1')))
    study = StudyBudgetLedger(tmp_path / 'joint', policy())
    call = study.reserve(**arguments())
    rid = native.reserve(price, 1000, 100, context=None)
    study.publish_receipt(call, native.receipt(rid))
    native.reconcile(rid, price, {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110})
    receipt = native.receipt(rid)
    assert receipt['observed_input_tokens'] is None
    assert receipt['observed_output_tokens'] is None
    assert receipt['tokens_charged_or_reserved'] == 110
    assert receipt['micro_usd_charged_or_reserved'] == 21
    study.publish_receipt(call, receipt)
    study.finish(call, outcome='completed', outcome_sha256=hashed('valid native full stage'))
    study.reference(call, 'case-01:jev', 'seed_generation:1', hashed('seed'))
    reopened = StudyBudgetLedger(tmp_path / 'joint', policy()).inspect()
    assert reopened['owned_micro_usd_charged_or_reserved'] == 21
    assert reopened['arms']['case-01:jev']['micro_usd_charged_or_reserved'] == 21
    assert reopened['calls'][0]['receipt'] == receipt


@pytest.mark.parametrize('incoming,outgoing', [(None, 10), (100, None), (None, True)])
def test_partial_settled_token_observations_are_not_invented(tmp_path, incoming, outgoing):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    receipt = groq_receipt(status='settled', observed_input_tokens=incoming, observed_output_tokens=outgoing)
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, receipt)
    assert call_record(ledger, call)['receipt'] is None
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210


def test_observed_reserved_usd_overrun_is_kept_before_stopping(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    receipt = groq_receipt(charged=225)
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, receipt)
    reopened = StudyBudgetLedger(tmp_path, policy()).inspect()
    assert reopened['locked']
    assert reopened['calls'][0]['receipt'] == receipt
    assert reopened['calls'][0]['micro_usd_bound'] == 210
    assert reopened['owned_micro_usd_charged_or_reserved'] == 225


def test_index_trigger_injection_is_detected_before_new_admission(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    ledger.reserve(**arguments())
    with sqlite3.connect(tmp_path / 'index.sqlite') as db:
        db.execute('CREATE TRIGGER erase BEFORE INSERT ON indexed_events BEGIN DELETE FROM indexed_events; END')
    with pytest.raises(StudyBudgetStop):
        ledger.reserve(**arguments(stage='next:1', request='next'))


@pytest.mark.parametrize('bad', [[], {}, ['groq']])
def test_unhashable_provider_policy_fails_with_nonfallback_stop(tmp_path, bad):
    value = policy()
    value['providers'][0]['provider'] = bad
    with pytest.raises(StudyBudgetStop):
        StudyBudgetLedger(tmp_path / 'study', value)
    assert not (tmp_path / 'study').exists()


@pytest.mark.parametrize('bad', [[], {}, ['settled']])
def test_unhashable_receipt_status_keeps_bound_and_stops(tmp_path, bad):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    receipt = groq_receipt(status=bad)
    with pytest.raises(StudyBudgetStop):
        ledger.publish_receipt(call, receipt)
    saved = ledger.inspect()
    assert saved['locked']
    assert saved['owned_micro_usd_charged_or_reserved'] == 210
    assert saved['calls'][0]['receipt'] is None


@pytest.mark.parametrize('operation', ['finish', 'reference', 'publish_receipt'])
def test_unhashable_call_identity_is_a_nonfallback_stop(tmp_path, operation):
    ledger = StudyBudgetLedger(tmp_path, policy())
    with pytest.raises(StudyBudgetStop):
        if operation == 'finish':
            ledger.finish([], outcome='unknown', reason='synthetic')
        elif operation == 'reference':
            ledger.reference([], 'case-01:jev', 'seed_generation:1', hashed('seed'))
        else:
            ledger.publish_receipt([], groq_receipt())
    assert ledger.inspect()['owned_attempts'] == 0


@pytest.mark.parametrize('bad', [[], {}, ['unknown']])
def test_unhashable_finish_outcome_retains_open_owner(tmp_path, bad):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    with pytest.raises(StudyBudgetStop):
        ledger.finish(call, outcome=bad)
    assert call_record(ledger, call)['outcome'] == 'pending'
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210


def test_unhashable_reference_arm_cannot_hide_failed_repair_cost(tmp_path):
    ledger = StudyBudgetLedger(tmp_path, policy())
    call = ledger.reserve(**arguments())
    ledger.publish_receipt(call, groq_receipt(status='failed_bound'))
    ledger.finish(call, outcome='failed', reason='synthetic_repair')
    with pytest.raises(StudyBudgetStop):
        ledger.reference(call, [], 'seed_generation:1', hashed('seed'))
    assert ledger.inspect()['owned_micro_usd_charged_or_reserved'] == 210


