"""Synthetic Jev controller decisions; no credentials, network or user data."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from issueintel.investigation.controller import Action, DecisionState
from issueintel.investigation.typesafe_controller import (
    TypesafePlannedController, validate_controller_decisions)
from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import (MODEL, TypesafeError, build_request,
                                        validate_response)
from test_planned_pipeline import planned_repository, PlannedModel, run_planned


JOB_ID = 'a' * 24


def receipt(binding, request, response=None, *, status='settled'):
    row = {'schema': 'typesafe_decision_receipt_v1', 'provider': 'typesafe',
        'reservation_id': f"{binding['decision_index']:032x}",
        'created_at': '2026-10-17T00:00:00+00:00', 'model': MODEL,
        'binding': deepcopy(binding), 'request_sha256': digest(request),
        'response_sha256': digest(response) if response is not None else None,
        'status': status, 'input_bound': 64000,
        'input_tokens': 100 if status == 'settled' else None,
        'output_tokens': 10 if status == 'settled' else None,
        'micro_usd_charged_or_reserved': 5 if status == 'settled' else 2688,
        'price_date': '2026-10-17', 'price_source': 'https://docs.typesafe.ai/models',
        'currency': 'USD', 'is_provider_invoice': False}
    row['sha256'] = digest(row)
    return row


class FakeClient:
    config = SimpleNamespace(model=MODEL)

    def __init__(self, action=None, error=None, status='settled'):
        self.action = action
        self.error = error
        self.status = status
        self.calls = []
        self.last_receipt = None

    def decide(self, state, allowed, binding):
        self.last_receipt = None
        self.calls.append(deepcopy((state, allowed, binding)))
        request = build_request(state, allowed, MODEL)
        if self.error:
            if self.error == 'request_failed':
                self.last_receipt = receipt(binding, request, status='failed_bound')
            raise TypesafeError(self.error, receipt=self.last_receipt)
        action = self.action or ('retrieve' if 'retrieve' in allowed else 'verify')
        response = {'model': MODEL, 'answers': {'next_action': {'type': 'choice',
            'choice': action, 'confidence': 1.0,
            'probabilities': {item: float(item == action) for item in allowed}}},
            'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if self.status == 'missing_usage_bound':
            response.pop('usage')
        self.last_receipt = receipt(binding, request, response, status=self.status)
        normalized = validate_response(response, allowed, MODEL)
        return {**normalized, 'response': response, 'receipt': self.last_receipt}


def run_controlled(repository, client=None, **kwargs):
    client = client or FakeClient()
    controller = TypesafePlannedController(client, JOB_ID, **kwargs)
    pack = run_planned(repository, PlannedModel(), controller=controller)
    return pack, controller, client


def reseal(sidecar):
    sidecar.pop('sha256', None)
    sidecar['sha256'] = digest(sidecar)


def test_four_bounded_decisions_are_separate_from_groq_calls(planned_repository):
    pack, controller, client = run_controlled(planned_repository)
    sidecar = validate_controller_decisions(pack)
    assert len(client.calls) == len(controller.records) == len(sidecar['records']) == 4
    assert pack['workflow_plan']['provider_calls'] == 5
    assert sidecar['model'] == MODEL and sidecar['provider'] == 'typesafe'
    assert sidecar['workflow_plan_sha256'] == pack['workflow_plan']['sha256']
    assert all(row['reason'] == 'selected' and row['fallback'] is False for row in sidecar['records'])
    assert [row['allowed'] for row in sidecar['records']][-1] == ['verify', 'stop']
    assert [row['binding']['decision_index'] for row in sidecar['records']] == [1, 2, 3, 4]
    assert sidecar['records'][-1]['state']['decision']['rounds_remaining'] == 0
    assert len({row['binding']['annotation_sha256'] for row in sidecar['records']}) == 4
    assert all(row['receipt']['is_provider_invoice'] is False for row in sidecar['records'])


@pytest.mark.parametrize('action', ['verify', 'stop'])
def test_terminal_round_reuses_the_decision_without_an_extra_call(planned_repository, action):
    pack, _, client = run_controlled(planned_repository, FakeClient(action=action))
    assert len(client.calls) == len(validate_controller_decisions(pack)['records']) == 1
    assert pack['workflow_plan']['rounds'][0]['action'] == action
    assert pack['workflow_plan']['final_decision'] == {
        key: pack['workflow_plan']['rounds'][0][key]
        for key in ('action', 'reason', 'controller', 'fallback')}


def test_offline_plan_exports_zero_decisions_without_binding_or_client_calls(planned_repository):
    client = FakeClient()
    controller = TypesafePlannedController(client, JOB_ID)
    pack = run_planned(planned_repository, controller=controller)
    assert client.calls == []
    assert validate_controller_decisions(pack)['records'] == []
    assert pack['workflow_plan']['provider_calls'] == 0


@pytest.mark.parametrize('reason', ['disabled', 'missing_api_key', 'daily_usd_limit'])
def test_blocked_configuration_falls_back_with_explicit_zero_receipt(planned_repository, reason):
    pack, _, client = run_controlled(planned_repository, FakeClient(error=reason))
    records = validate_controller_decisions(pack)['records']
    assert len(client.calls) == 4
    assert all(row['fallback'] and row['reason'] == reason and row['receipt'] is None
               and row['attempt_status'] == 'blocked' for row in records)
    assert all(row['fallback'] for row in pack['workflow_plan']['rounds'])


def test_failed_network_attempt_keeps_bound_receipt_and_uses_rules(planned_repository):
    pack, _, _ = run_controlled(planned_repository, FakeClient(error='request_failed'))
    records = validate_controller_decisions(pack)['records']
    assert all(row['fallback'] and row['reason'] == 'request_failed'
               and row['attempt_status'] == 'failed_bound'
               and row['receipt']['micro_usd_charged_or_reserved'] == 2688 for row in records)


def test_valid_selection_with_missing_usage_keeps_full_cost_bound(planned_repository):
    pack, _, _ = run_controlled(planned_repository, FakeClient(action='stop', status='missing_usage_bound'))
    row = validate_controller_decisions(pack)['records'][0]
    assert row['fallback'] is False and row['attempt_status'] == 'missing_usage_bound'
    assert row['receipt']['micro_usd_charged_or_reserved'] == 2688


def test_invalid_response_never_passes_an_arbitrary_action_to_pipeline(planned_repository):
    class InvalidClient(FakeClient):
        def decide(self, state, allowed, binding):
            result = super().decide(state, allowed, binding)
            result['action'] = 'execute_shell'
            return result
    pack, _, _ = run_controlled(planned_repository, InvalidClient())
    rows = validate_controller_decisions(pack)['records']
    assert all(row['fallback'] and row['reason'] == 'invalid_response' for row in rows)
    assert [row['action'] for row in rows] == ['retrieve', 'retrieve', 'retrieve', 'verify']


def test_exception_strings_and_error_bodies_are_not_copied_to_pack(planned_repository):
    class BrokenClient(FakeClient):
        def decide(self, *args):
            raise RuntimeError('PRIVATE response credential http://private.invalid')
    pack, _, _ = run_controlled(planned_repository, BrokenClient())
    rows = validate_controller_decisions(pack)['records']
    assert all(row['fallback'] and row['reason'] == 'client_error'
               and row['attempt_status'] == 'unreceipted_error' for row in rows)
    assert 'PRIVATE' not in str(rows) and 'credential' not in str(rows)


def test_decision_callback_is_exactly_once_per_attempt_and_immutable(planned_repository):
    saved = []
    def callback(row):
        saved.append(deepcopy(row))
        row['reason'] = 'mutated_by_observer'
    pack, controller, _ = run_controlled(planned_repository, on_decision=callback)
    assert saved == controller.records == validate_controller_decisions(pack)['records']


def test_cancellation_in_decision_callback_propagates_without_next_provider_call(planned_repository):
    client = FakeClient()
    def cancelled(_):
        raise InterruptedError('cancelled')
    controller = TypesafePlannedController(client, JOB_ID, on_decision=cancelled)
    model = PlannedModel()
    with pytest.raises(InterruptedError):
        run_planned(planned_repository, model, controller=controller)
    assert len(client.calls) == 1 and model.generations == len(model.calls) == 1


def test_cancellation_in_client_is_not_converted_to_a_rule_action(planned_repository):
    class CancelledClient(FakeClient):
        def decide(self, *args):
            raise InterruptedError('cancelled')
    controller = TypesafePlannedController(CancelledClient(), JOB_ID)
    with pytest.raises(InterruptedError):
        run_planned(planned_repository, PlannedModel(), controller=controller)
    assert controller.records == []


def test_decision_persistence_failure_stops_before_next_model_or_controller_call(planned_repository):
    client = FakeClient()
    def failed(_):
        raise RuntimeError('private callback detail')
    controller = TypesafePlannedController(client, JOB_ID, on_decision=failed)
    model = PlannedModel()
    with pytest.raises(OSError, match='Typesafe decision persistence failed'):
        run_planned(planned_repository, model, controller=controller)
    assert len(client.calls) == 1 and model.generations == len(model.calls) == 1


def test_saved_replay_uses_no_network_credentials_ledger_or_files(planned_repository, monkeypatch):
    pack, _, client = run_controlled(planned_repository)
    monkeypatch.setattr(client, 'decide', lambda *args: pytest.fail('client called during replay'))
    monkeypatch.setattr('sqlite3.connect', lambda *args, **kwargs: pytest.fail('ledger read during replay'))
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    validate_controller_decisions(pack)


@pytest.mark.parametrize('change', [
    lambda sidecar: sidecar.update(workflow_plan_sha256='0' * 64),
    lambda sidecar: sidecar['source'].update(query_sha256='0' * 64),
    lambda sidecar: sidecar['records'].pop(),
    lambda sidecar: sidecar['records'][0]['state']['issue'].update(text='changed'),
    lambda sidecar: sidecar['records'][0]['binding'].update(annotation_sha256='0' * 64),
    lambda sidecar: sidecar['records'][0].update(request_sha256='0' * 64),
    lambda sidecar: sidecar['records'][0].update(allowed=['stop']),
    lambda sidecar: sidecar['records'][0].update(action='stop'),
    lambda sidecar: sidecar['records'][0].update(fallback=True),
    lambda sidecar: sidecar['records'][0].update(attempt_status='reserved'),
])
def test_source_state_action_count_or_binding_tamper_fails_even_after_resealing(planned_repository, change):
    pack, _, _ = run_controlled(planned_repository)
    pack = deepcopy(pack)
    change(pack['controller_decisions'])
    reseal(pack['controller_decisions'])
    with pytest.raises(ValueError):
        validate_controller_decisions(pack)


@pytest.mark.parametrize('field,value', [('input_tokens', 1000), ('micro_usd_charged_or_reserved', 0),
                                      ('price_source', 'https://untrusted.invalid'), ('status', 'reserved')])
def test_usage_and_cost_tamper_fails_after_both_seals_are_repaired(planned_repository, field, value):
    pack, _, _ = run_controlled(planned_repository)
    row = pack['controller_decisions']['records'][0]
    row['receipt'][field] = value
    reseal(row['receipt'])
    reseal(pack['controller_decisions'])
    with pytest.raises(ValueError):
        validate_controller_decisions(pack)


def test_consistent_receipt_cost_still_cannot_contradict_its_saved_response_usage(planned_repository):
    pack, _, _ = run_controlled(planned_repository)
    row = pack['controller_decisions']['records'][0]
    row['receipt']['input_tokens'] = 1000
    row['receipt']['micro_usd_charged_or_reserved'] = 42
    reseal(row['receipt'])
    reseal(pack['controller_decisions'])
    with pytest.raises(ValueError):
        validate_controller_decisions(pack)


@pytest.mark.parametrize('field', ['state', 'binding'])
def test_bool_and_int_equality_cannot_hide_changed_record_json(planned_repository, field):
    pack, _, _ = run_controlled(planned_repository)
    row = pack['controller_decisions']['records'][0]
    if field == 'binding':
        row[field]['decision_index'] = True  # True == 1 in Python, bytes differ.
    else:
        row[field]['issue']['truncated'] = 0  # 0 == False in Python.
    reseal(pack['controller_decisions'])
    with pytest.raises(ValueError):
        validate_controller_decisions(pack)


def test_response_probability_or_model_tamper_fails(planned_repository):
    pack, _, _ = run_controlled(planned_repository)
    row = pack['controller_decisions']['records'][0]
    row['response']['answers']['next_action']['probabilities']['retrieve'] = False
    row['receipt']['response_sha256'] = digest(row['response'])
    reseal(row['receipt'])
    reseal(pack['controller_decisions'])
    with pytest.raises(ValueError):
        validate_controller_decisions(pack)


def test_controller_context_rejects_benchmark_hidden_and_strict_before_client(planned_repository):
    pack, _, _ = run_controlled(planned_repository)
    client = FakeClient()
    controller = TypesafePlannedController(client, JOB_ID)
    case = deepcopy(pack['case'])
    case['split'] = 'main_dev'
    with pytest.raises(ValueError):
        controller.bind_context(case, pack['snapshot'], pack['evidence'], pack['annotation'])
    case.pop('split')
    case['gold'] = {'path': 'private.py'}
    with pytest.raises(ValueError):
        controller.bind_context(case, pack['snapshot'], pack['evidence'], pack['annotation'])
    case.pop('gold')
    snapshot = dict(pack['snapshot'], protocol='issue_time')
    with pytest.raises(ValueError):
        controller.bind_context(case, snapshot, pack['evidence'], pack['annotation'])
    assert client.calls == []


def test_direct_misuse_cannot_exceed_four_remote_decisions_or_expand_allowlist(planned_repository):
    pack, _, _ = run_controlled(planned_repository)
    client = FakeClient()
    controller = TypesafePlannedController(client, JOB_ID)
    controller.bind_context(pack['case'], pack['snapshot'], pack['evidence'], pack['annotation'])
    state = DecisionState(sum(row['kind'] == 'code' for row in pack['evidence']),
        len(pack['annotation']['hypotheses']), (), 1)
    with pytest.raises(ValueError):
        controller.decide(state, (Action.RETRIEVE, Action.ASK, Action.STOP))
    with pytest.raises(ValueError):
        controller.decide(DecisionState(999, state.hypotheses_count),
                          (Action.RETRIEVE, Action.VERIFY, Action.STOP))
    for _ in range(4):
        controller.decide(state, (Action.RETRIEVE, Action.VERIFY, Action.STOP))
    with pytest.raises(ValueError):
        controller.decide(state, (Action.RETRIEVE, Action.VERIFY, Action.STOP))
    assert len(client.calls) == 4


def test_binding_cannot_switch_to_another_issue_or_snapshot_inside_one_job(planned_repository):
    pack, _, _ = run_controlled(planned_repository)
    client = FakeClient()
    controller = TypesafePlannedController(client, JOB_ID)
    controller.bind_context(pack['case'], pack['snapshot'], pack['evidence'], pack['annotation'])
    changed_case = dict(pack['case'], problem_statement='different issue')
    with pytest.raises(ValueError, match='cannot change'):
        controller.bind_context(changed_case, pack['snapshot'], pack['evidence'], pack['annotation'])
    changed_snapshot = dict(pack['snapshot'], snapshot_sha256='0' * 64)
    with pytest.raises(ValueError, match='cannot change'):
        controller.bind_context(pack['case'], changed_snapshot, pack['evidence'], pack['annotation'])
    assert client.calls == []


def test_bounded_state_excludes_machine_paths_receipts_and_generation_metadata(planned_repository):
    pack, _, client = run_controlled(planned_repository)
    state, _, binding = client.calls[0]
    assert len(state['hypotheses']) == 3 and len(state['evidence']) <= 8
    assert len(state['issue']['text'].encode()) <= 4000
    assert all(len(row['text'].encode()) <= 300 for row in state['evidence'])
    assert 'provenance' not in str(state) and 'receipt' not in str(state)
    assert 'judgment' not in str(state) and 'source_quality' not in str(state)
    assert 'snapshot_sha256' not in str(state)  # Kept in local binding, not prompt data.
    assert binding['query_sha256'] == pack['workflow_plan']['source']['query_sha256']


def test_multibyte_issue_is_bounded_without_changing_its_full_query_binding(planned_repository):
    case, path = planned_repository
    case = dict(case, problem_statement=case['problem_statement'] + '\n' + '🙂' * 5000)
    pack, _, client = run_controlled((case, path))
    state, _, binding = client.calls[0]
    assert len(state['issue']['text'].encode()) <= 4000 and state['issue']['truncated'] is True
    assert binding['query_sha256'] == pack['workflow_plan']['source']['query_sha256']
    assert state['issue']['text'] != pack['case']['problem_statement']
    validate_controller_decisions(pack)


def test_oversized_but_public_hypothesis_id_blocks_remote_request_and_replays(planned_repository):
    class LongIdModel(PlannedModel):
        def __call__(self, messages, max_output_tokens):
            result = super().__call__(messages, max_output_tokens)
            if 'hypotheses' in result:
                for index, row in enumerate(result['hypotheses']):
                    row['id'] = 'public_' + 'x' * 6000 + str(index)
            return result
    client = FakeClient()
    controller = TypesafePlannedController(client, JOB_ID)
    pack = run_planned(planned_repository, LongIdModel(), controller=controller)
    assert client.calls == []
    assert all(row['fallback'] and row['reason'] == 'request_too_large'
               and row['request_sha256'] is None and row['attempt_status'] == 'blocked'
               for row in validate_controller_decisions(pack)['records'])
