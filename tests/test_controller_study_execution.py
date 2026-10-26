"""Native synthetic HTTP, shared stages and fail-closed study ownership."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import sqlite3
from urllib.error import HTTPError

import pytest

from issueintel.evaluation.controller_study import runtime_config_descriptor, study_directory
from issueintel.evaluation.controller_study_execution import StudyExecution, SavedSharedFailure
from issueintel.investigation.controller import RuleController
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.planner import validate_planned
from issueintel.investigation.product_study import make_policy, stage_binding
from issueintel.investigation.providers import GroqProvider, ProviderError, _input_token_bound
from issueintel.investigation.typesafe_controller import TypesafePlannedController, validate_controller_decisions
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.budget import BudgetPolicy
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest
from issueintel.runtime.study_budget import StudyBudgetLedger, StudyBudgetStop
from issueintel.runtime.typesafe import TypesafeClient, TypesafeConfig, TypesafeError
from test_planned_pipeline import planned_repository, PlannedModel


class HTTP(BytesIO):
    status = 200
    headers = {'content-type': 'application/json'}


@pytest.fixture
def owned(tmp_path, planned_repository, monkeypatch):
    original, repository = planned_repository
    case = {**original, 'case_id': 'https://github.com/example/study/issues/7',
            'issue_url': 'https://github.com/example/study/issues/7',
            'repository': 'example/study', 'issue_numbers': [7]}
    snapshot = build_snapshot(repository, case['revision'])
    graph = build_graph(snapshot)
    policy = make_policy(case, snapshot, graph, study_id='a' * 64,
                         source_sha256='b' * 64, runtime_sha256='c' * 64)
    directory = study_directory(tmp_path, policy['study_id'])
    configs = tmp_path / 'configs'
    configs.mkdir()
    today = datetime.now(timezone.utc).date().isoformat()
    gp = configs / 'groq_prices.json'
    gp.write_text(json.dumps({'currency': 'USD', 'checked_at': today,
        'source': 'https://console.groq.com/docs/models', 'models': {
            'openai/gpt-oss-120b': {'input_per_million': '0.15', 'output_per_million': '0.60',
                'context_tokens': 131072, 'max_output_tokens': 32768}}}))
    tp = configs / 'typesafe_prices.json'
    tp.write_text(json.dumps({'currency': 'USD', 'checked_at': today,
        'source': 'https://docs.typesafe.ai/models', 'models': {
            'jev-1.13.0': {'input_per_million': '0.042', 'output_per_million': '0',
                'per_request_context': 64000, 'per_question_context': 32000}}}))
    groq = RuntimeConfig(tmp_path, 'openai/gpt-oss-120b', 4096,
        BudgetPolicy(3000000, Decimal('10'), daily_requests=50), gp, directory / 'native/groq.sqlite')
    typesafe = TypesafeConfig(tmp_path, True, 'jev-1.13.0', 20, Decimal('.053760'),
        Decimal('.053760'), tp, directory / 'native/typesafe.sqlite')
    providers = []
    for name, config in (('groq', groq), ('typesafe', typesafe)):
        providers.append({'provider': name, 'model': config.model,
            'config_sha256': digest(runtime_config_descriptor(tmp_path, config,
                provider=name, study_id=policy['study_id'])),
            'price_sha256': sha256(config.price_file.read_bytes()).hexdigest(),
            'price_date': today, 'max_attempts': 50 if name == 'groq' else 20})
    budget_policy = {'schema': 'study_budget_policy_v1', 'study_id': policy['study_id'],
        'config_sha256': 'd' * 64, 'providers': providers,
        'arms': [{'arm_id': f'case-01:{arm}', 'max_micro_usd': 200000, 'max_attempts': 13}
                 for arm in ('rules', 'jev')],
        'max_owned_attempts': 70, 'max_owned_micro_usd': 400000}
    budget = StudyBudgetLedger(directory / 'budget', budget_policy)
    model, requests, constructors = PlannedModel(), [], []
    settings = {'repair': False, 'transport_error': False, 'jev_error': False,
                'unknown_usage': False}
    def groq_http(request, timeout):
        body = json.loads(request.data)
        requests.append(('groq', body))
        if settings['transport_error']:
            raise OSError('synthetic-secret-must-not-be-recorded')
        if settings['repair']:
            settings['repair'] = False
            raw = json.dumps({'error': {'code': 'json_validate_failed',
                'failed_generation': '{"synthetic":true}'}}).encode()
            raise HTTPError(request.full_url, 400, 'synthetic', {}, BytesIO(raw))
        content = model(body['messages'], body['max_completion_tokens'])
        for index, row in enumerate(content.get('hypotheses', []), 1):
            row['id'] = f'H{index}'
        raw = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}]}
        if not settings['unknown_usage']:
            raw['usage'] = {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}
        return HTTP(json.dumps(raw).encode())
    def typesafe_http(request, timeout):
        body = json.loads(request.data)
        requests.append(('typesafe', body))
        if settings['jev_error']:
            raise OSError('synthetic-private-failure')
        allowed = list(body['questions']['next_action']['criteria'])
        selected = 'retrieve' if 'retrieve' in allowed else 'verify'
        return HTTP(json.dumps({'model': 'jev-1.13.0', 'answers': {'next_action': {
            'type': 'choice', 'choice': selected, 'confidence': 1,
            'probabilities': {action: int(action == selected) for action in allowed}}},
            'usage': {'input_tokens': 1000, 'output_tokens': 35}}).encode())
    def factory(name, native):
        def create(config, **kwargs):
            constructors.append(name)
            assert budget.inspect()['calls'][-1]['receipt'] is None
            return native(config, **kwargs)
        return create
    factories = {'groq': factory('groq', GroqProvider), 'typesafe': factory('typesafe', TypesafeClient),
                 'groq_transport': groq_http, 'typesafe_transport': typesafe_http}
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-key')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic-key')
    source = case, repository, snapshot, graph, policy
    def create(arm='rules', guard=lambda: None, **kwargs):
        return StudyExecution(tmp_path, directory, 'case-01', arm, policy,
            groq, typesafe, budget, guard, provider_factories=factories, **kwargs)
    return {'root': tmp_path, 'directory': directory, 'source': source, 'budget': budget,
            'groq': groq, 'typesafe': typesafe, 'create': create, 'factories': factories,
            'requests': requests, 'constructors': constructors, 'settings': settings}


def run(owned, execution, *, jev=False):
    case, repository, snapshot, graph, policy = owned['source']
    controller = TypesafePlannedController(execution.typesafe_client, execution.job_id,
        on_decision=execution.on_decision) if jev else RuleController()
    return investigate(case, repository, snapshot=snapshot, graph=graph,
        complete_json=execution.groq, provider='groq', model=owned['groq'].model,
        top_k=12, independent_critic=True, max_output_tokens=4096,
        planned_workflow=True, planner_policy='bounded_planner_v2', controller=controller,
        product_study_policy=policy, product_study_stage_executor=execution.stage)


def stage(owned, execution, produce, *, kind='seed_generation', inputs=None):
    binding = stage_binding(owned['source'][-1], kind, inputs or {'synthetic_input': 'fixed'})
    return execution.stage(kind, binding, produce)


MESSAGES = [{'role': 'system', 'content': 'Synthetic bounded request'},
            {'role': 'user', 'content': '{"hypotheses":[],"evidence":[]}'}]


def test_native_pair_shares_full_payload_and_every_failed_repair_cost(owned):
    owned['settings']['repair'] = True
    first = owned['create']()
    rules = run(owned, first)
    before = len([row for row in owned['requests'] if row[0] == 'groq'])
    second = owned['create']('jev')
    jev = run(owned, second, jev=True)
    validate_planned(rules)
    validate_planned(jev)
    validate_controller_decisions(jev)
    assert before == len([row for row in owned['requests'] if row[0] == 'groq'])
    assert len([row for row in owned['requests'] if row[0] == 'typesafe']) == 4
    seed = rules['workflow_plan']['seed_annotation']
    assert seed == jev['workflow_plan']['seed_annotation']
    assert seed['provenance']['repair_count'] == 1
    assert rules['workflow_plan']['provider_calls'] == jev['workflow_plan']['provider_calls'] == before
    snapshot = owned['budget'].inspect()
    calls = [row for row in snapshot['calls'] if row['provider'] == 'groq']
    assert calls[0]['outcome'] == 'failed'
    assert calls[0]['receipt']['status'] == 'failed_bound'
    assert all({ref['arm_id'] for ref in row['references']} == {'case-01:rules', 'case-01:jev'} for row in calls)
    records = second.inspect()['calls']
    assert len(records) == before + 4
    assert all(row['raw'] for row in records)
    assert len(second.decisions) == 4
    assert len(first.owned_call_ids) == before
    assert len(second.owned_call_ids) == 4
    assert set(first.call_ids) <= set(second.call_ids)


def test_joint_cap_denies_before_constructor_or_http(owned):
    spec = deepcopy(owned['budget'].policy)
    spec['max_owned_micro_usd'] = 1
    budget = StudyBudgetLedger(owned['directory'] / 'small-budget', spec)
    execution = StudyExecution(owned['root'], owned['directory'], 'case-01', 'rules',
        owned['source'][-1], owned['groq'], owned['typesafe'], budget, lambda: None,
        provider_factories=owned['factories'])
    with pytest.raises(StudyBudgetStop):
        stage(owned, execution, lambda: execution.groq(MESSAGES, 4096))
    assert budget.inspect()['owned_attempts'] == 0
    assert owned['constructors'] == owned['requests'] == []


def test_phase_candidate_bound_denied_before_reservation_construction_or_http(owned):
    proposed = []
    def deny(provider, cost):
        proposed.append((provider, cost))
        raise ValueError('private authority detail')
    execution = owned['create'](admit_call=deny)
    with pytest.raises(StudyBudgetStop, match='exact next call bound'):
        stage(owned, execution, lambda: execution.groq(MESSAGES, 4096))
    assert len(proposed) == 1 and proposed[0][0] == 'groq'
    assert proposed[0][1] == owned['groq'].price().micro_usd(_input_token_bound(MESSAGES), 4096)
    assert owned['budget'].inspect()['owned_attempts'] == 0
    assert owned['constructors'] == owned['requests'] == []


def test_groq_call_outside_native_stage_is_denied(owned):
    with pytest.raises(StudyBudgetStop, match='current native stage'):
        owned['create']().groq(MESSAGES, 4096)
    assert not owned['requests']


def test_provider_bound_matches_exact_schema_native_receipt(owned):
    execution = owned['create']()
    schema = {'type': 'object', 'properties': {}}
    stage(owned, execution, lambda: {'response': execution.groq.complete_structured(
        MESSAGES, max_output_tokens=4096, schema=schema)})
    row = owned['budget'].inspect()['calls'][0]
    assert row['input_bound'] == _input_token_bound(MESSAGES, schema)
    assert row['micro_usd_bound'] == owned['groq'].price().micro_usd(row['input_bound'], 4096)
    assert row['receipt']['input_bound'] == row['input_bound']
    call = execution.inspect()['calls'][0]
    assert row['request_sha256'] == digest(call['request'])


def test_missing_usage_retains_bound_and_stage_reuse_does_not_repeat_http(owned):
    owned['settings']['unknown_usage'] = True
    first = owned['create']()
    one = stage(owned, first, lambda: {'response': first.groq(MESSAGES, 4096)})
    second = owned['create']('jev')
    assert stage(owned, second, lambda: pytest.fail('shared native stage recomputed')) == one
    assert len(owned['requests']) == 1
    call = owned['budget'].inspect()['calls'][0]
    assert call['receipt']['status'] == 'missing_usage_bound'
    assert call['receipt']['micro_usd_charged_or_reserved'] == call['micro_usd_bound']
    assert len(call['references']) == 2


def test_raw_response_without_full_native_stage_is_failed_and_never_retried(owned):
    first = owned['create']()
    def produce():
        first.groq(MESSAGES, 4096)
        raise ValueError('synthetic native stage validation failure')
    with pytest.raises(ValueError):
        stage(owned, first, produce)
    second = owned['create']('jev')
    with pytest.raises(SavedSharedFailure):
        stage(owned, second, lambda: pytest.fail('retry forbidden'))
    assert len(owned['requests']) == 1
    saved = second.inspect()['stages'][0]
    assert saved['status'] == 'failed' and saved['payload'] is None
    assert len(owned['budget'].inspect()['calls'][0]['references']) == 2


def test_interrupted_stage_intent_blocks_reopen_without_native_payload(owned):
    execution = owned['create']()
    binding = stage_binding(owned['source'][-1], 'seed_generation', {'native': 'input'})
    execution.journal.append('stage_started', {'key': digest(binding), 'kind': 'seed_generation',
        'binding': binding, 'owner_arm': 'case-01:rules'})
    with pytest.raises(StudyBudgetStop, match='Interrupted'):
        owned['create']('jev')
    assert not owned['requests']


def test_duplicate_exact_wire_body_cannot_get_second_paid_owner(owned):
    execution = owned['create']()
    stage(owned, execution, lambda: {'value': execution.groq(MESSAGES, 4096)})
    with pytest.raises(StudyBudgetStop, match='reserved or retried'):
        stage(owned, execution, lambda: {'value': execution.groq(MESSAGES, 4096)},
              kind='planned_generation_1', inputs={'different': 'binding'})
    assert len(owned['requests']) == 1
    assert owned['budget'].inspect()['owned_attempts'] == 1


@pytest.mark.parametrize('target', ['event', 'raw', 'native', 'orphan'])
def test_reopen_detects_journal_raw_native_and_inventory_tampering(owned, target):
    execution = owned['create']()
    stage(owned, execution, lambda: {'value': execution.groq(MESSAGES, 4096)})
    if target == 'event':
        path = sorted(execution.journal.events_dir.iterdir())[-1]
        path.write_bytes(path.read_bytes() + b' ')
    elif target == 'raw':
        path = next(execution.journal.raw_dir.iterdir())
        path.write_bytes(b'changed')
    elif target == 'native':
        with sqlite3.connect(owned['groq'].ledger_file) as db:
            db.execute('UPDATE calls SET charged_micro_usd=0')
    else:
        (execution.journal.raw_dir / 'unowned.bin').write_bytes(b'orphan')
    with pytest.raises(StudyBudgetStop):
        owned['create']('jev')


def test_receipt_callback_integrity_stop_escapes_native_jev_fallback(owned):
    execution = owned['create']('jev')
    original = owned['budget'].publish_receipt
    def reject(identifier, receipt):
        if receipt.get('provider') == 'typesafe':
            raise StudyBudgetStop('synthetic durable persistence failure')
        return original(identifier, receipt)
    owned['budget'].publish_receipt = reject
    with pytest.raises(StudyBudgetStop):
        run(owned, execution, jev=True)
    assert len([row for row in owned['requests'] if row[0] == 'typesafe']) == 0
    assert execution.typesafe_client.last_receipt['status'] == 'failed_bound'
    assert owned['budget'].inspect()['calls'][-1]['outcome'] == 'unknown'


def test_ordinary_closed_jev_transport_failure_measures_native_fallback(owned):
    execution = owned['create']('jev')
    owned['settings']['jev_error'] = True
    pack = run(owned, execution, jev=True)
    validate_planned(pack)
    validate_controller_decisions(pack)
    assert all(row['fallback'] for row in execution.decisions)
    assert all(row['receipt']['status'] == 'failed_bound' for row in execution.decisions)
    assert all(row['outcome'] == 'failed' for row in owned['budget'].inspect()['calls']
               if row['provider'] == 'typesafe')
    for path in execution.journal.events_dir.iterdir():
        assert b'synthetic-private-failure' not in path.read_bytes()


def test_fresh_guard_changes_between_calls_blocks_schema_repair(owned):
    counter = {'allowed': True}
    def guard():
        if not counter['allowed']:
            raise ValueError('source metadata changed')
    execution = owned['create'](guard=guard)
    original = owned['factories']['groq_transport']
    def transport(request, timeout):
        response = original(request, timeout)
        counter['allowed'] = False
        return response
    execution.provider_factories['groq_transport'] = transport
    with pytest.raises(StudyBudgetStop):
        stage(owned, execution, lambda: execution.groq(MESSAGES, 4096))
    assert len(owned['requests']) == 1
    assert owned['budget'].inspect()['owned_attempts'] == 1


def test_readonly_inspect_never_constructs_provider_or_reads_key(owned, monkeypatch):
    execution = owned['create']()
    stage(owned, execution, lambda: {'value': execution.groq(MESSAGES, 4096)})
    before = list(owned['constructors'])
    monkeypatch.delenv('GROQ_API_KEY')
    monkeypatch.delenv('TYPESAFE_API_KEY')
    assert execution.inspect()['calls'][0]['status'] == 'completed'
    reopened = owned['create']('jev')
    assert reopened.inspect()['calls'][0]['status'] == 'completed'
    assert owned['constructors'] == before


def test_typesafe_exact_bound_admitted_before_construction(owned):
    from test_typesafe_runtime import binding
    admitted = []
    def admit(provider, cost):
        admitted.append((provider, cost))
        assert not owned['constructors']
        assert owned['budget'].inspect()['owned_attempts'] == 0
        raise ValueError('phase exhausted')
    execution = owned['create']('jev', admit_call=admit)
    with pytest.raises(StudyBudgetStop):
        execution.typesafe_client.decide({'synthetic': 'state'}, ['verify', 'stop'],
            binding(job=execution.job_id))
    assert admitted == [('typesafe', 2688)]
    assert owned['constructors'] == owned['requests'] == []


def test_native_orphan_reservation_blocks_inspect_without_refunding(owned):
    execution = owned['create']()
    stage(owned, execution, lambda: {'value': execution.groq(MESSAGES, 4096)})
    native = owned['groq'].ledger()
    orphan = native.reserve(owned['groq'].price(), 2000, 4096)
    with pytest.raises(StudyBudgetStop, match='orphan'):
        execution.inspect()
    assert native.receipt(orphan)['status'] == 'reserved'
    assert len(owned['requests']) == 1


def test_budget_orphan_intent_blocks_reopening_even_without_provider(owned):
    execution = owned['create']()
    policy = owned['budget'].providers['groq']
    owned['budget'].reserve('case-01:rules', 'orphan:1', 'e' * 64, 'groq',
        policy['model'], policy['config_sha256'], policy['price_sha256'], 2000, 4096, 3000)
    with pytest.raises(StudyBudgetStop, match='orphan'):
        owned['create']('jev')
    assert not owned['constructors'] and not owned['requests']


def test_persistence_of_closed_native_jev_failure_survives_callback_stop(owned):
    execution = owned['create']('jev')
    guard_state = {'failed': False}
    original_transport = execution.provider_factories['typesafe_transport']
    def transport(request, timeout):
        response = original_transport(request, timeout)
        guard_state['failed'] = True
        return response
    execution.provider_factories['typesafe_transport'] = transport
    def guard():
        if guard_state['failed']:
            raise ValueError('authorization changed while HTTP was in flight')
    execution.guard = guard
    with pytest.raises(StudyBudgetStop):
        run(owned, execution, jev=True)
    last = owned['budget'].inspect()['calls'][-1]
    assert last['provider'] == 'typesafe' and last['outcome'] == 'failed'
    assert last['receipt']['status'] == 'failed_bound'
    assert execution.typesafe_client.last_receipt == last['receipt']
