"""Typesafe chooses a bounded product action; the pipeline executes it.

Saved decisions can be replayed without credentials, network, a ledger or a
model. The seals establish local integrity and source binding, not model quality
or a provider invoice. Model probabilities describe its action choice only.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from issueintel.annotations.models import Annotation
from issueintel.annotations.public_inputs import model_hypotheses, model_payload
from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import (MODEL, TypesafeError, build_request,
                                        validate_receipt, validate_response)
from .controller import Action, DecisionState, RuleController
from .planner import validate_planned_case


SCHEMA = 'typesafe_controller_v1'
STATE_SCHEMA = 'typesafe_planned_state_v1'
MAX_DECISIONS = 4
_HEX64 = re.compile(r'[a-f0-9]{64}')
_REASONS = {
    'selected', 'disabled', 'missing_spending_limits', 'missing_api_key',
    'invalid_configuration', 'invalid_or_stale_prices', 'request_too_large',
    'ledger_unavailable', 'ledger_locked', 'duplicate_attempt',
    'daily_request_limit', 'daily_usd_limit', 'monthly_usd_limit',
    'receipt_persistence_failed', 'request_failed', 'redirect_forbidden',
    'response_too_large', 'invalid_response', 'invalid_receipt',
    'usage_bound_breach', 'invalid_reservation', 'client_error',
}
_BLOCKED = {
    'disabled', 'missing_spending_limits', 'missing_api_key',
    'invalid_configuration', 'invalid_or_stale_prices', 'request_too_large',
    'ledger_unavailable', 'ledger_locked', 'duplicate_attempt',
    'daily_request_limit', 'daily_usd_limit', 'monthly_usd_limit',
}
_RECORD_KEYS = {'decision_index', 'allowed', 'state', 'state_sha256', 'binding',
                'request_sha256', 'action', 'fallback', 'reason',
                'attempt_status', 'response', 'receipt'}


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _prefix(text, maximum):
    """A UTF-8 bound also keeps multibyte issue text inside the request cap."""
    return text.encode('utf-8')[:maximum].decode('utf-8', errors='ignore')


def _descriptor(snapshot):
    return snapshot.to_dict() if hasattr(snapshot, 'to_dict') else snapshot


def _context(case, snapshot, evidence, annotation):
    descriptor = _descriptor(snapshot)
    validate_planned_case(case, descriptor['protocol'])
    model_payload(case, evidence)  # Reject hidden fields before projecting.
    if (not isinstance(descriptor.get('commit_sha'), str)
            or not re.fullmatch(r'[a-f0-9]{40}', descriptor['commit_sha'])
            or not isinstance(descriptor.get('snapshot_sha256'), str)
            or not _HEX64.fullmatch(descriptor['snapshot_sha256'])):
        raise ValueError('Typesafe needs a fixed product snapshot')
    for row in evidence:
        if (row.get('sha256') != _sha(row['text'])
                or row['kind'] == 'code' and row.get('revision') != descriptor['commit_sha']):
            raise ValueError('Typesafe evidence differs from its source binding')
    parsed = Annotation.from_dict(annotation).validate_evidence(evidence)
    if (parsed.case_id != case['case_id'] or parsed.source_quality or parsed.judgment
            or parsed.provenance.kind != 'llm'
            or any(row.correctness != 'unverified' for row in parsed.hypotheses)):
        raise ValueError('Typesafe accepts only unverified public hypotheses')
    return deepcopy(case), deepcopy(descriptor), deepcopy(evidence), parsed.to_dict()


def _state(context, decision):
    case, snapshot, evidence, annotation = context
    if (not isinstance(decision, DecisionState)
            or type(decision.evidence_count) is not int
            or type(decision.hypotheses_count) is not int
            or type(decision.rounds_remaining) is not int
            or not 0 <= decision.rounds_remaining <= 3
            or decision.evidence_count != sum(row['kind'] == 'code' for row in evidence)
            or decision.hypotheses_count != len(annotation['hypotheses'])
            or not isinstance(decision.missing_information, tuple)
            or len(decision.missing_information) > 20
            or any(not isinstance(item, str) or not item or len(item) > 100
                   for item in decision.missing_information)):
        raise ValueError('Typesafe decision counts differ from bound public context')
    hypotheses = []
    for row in model_hypotheses(annotation['hypotheses'])[:3]:
        hypotheses.append({'id': row['id'], 'statement': _prefix(row['statement'], 600),
            'falsification_test': _prefix(row['falsification_test'], 600),
            'counter_status': row['counter_status'],
            'support_ids': row['support_ids'], 'counter_ids': row['counter_ids']})
    excerpts = []
    for row in (item for item in evidence if item['kind'] != 'issue'):
        if len(excerpts) == 8:
            break
        locator = row.get('locator', '')
        text = _prefix(row['text'], 300)
        excerpts.append({'id': row['id'], 'kind': row['kind'],
            'locator': _prefix(locator, 200), 'locator_truncated': len(locator.encode()) > 200,
            'sha256': row['sha256'], 'revision': row.get('revision'),
            'text': text, 'text_truncated': text != row['text']})
    issue = _prefix(case['problem_statement'], 4000)
    return {'schema': STATE_SCHEMA,
        'issue': {'text': issue, 'truncated': issue != case['problem_statement']},
        'hypotheses': hypotheses, 'evidence': excerpts,
        'decision': {'evidence_count': decision.evidence_count,
            'hypotheses_count': decision.hypotheses_count,
            'missing_information': list(decision.missing_information),
            'rounds_remaining': decision.rounds_remaining},
        'limitations': ['Hypotheses and retrieved candidates are unverified.',
            'Choose an action only; no repository execution or new source acquisition.']}


def _binding(context, job_id, index):
    case, snapshot, evidence, annotation = context
    return {'job_id': job_id, 'decision_index': index,
        'query_sha256': _sha(case['problem_statement']),
        'snapshot_sha256': snapshot['snapshot_sha256'], 'commit_sha': snapshot['commit_sha'],
        'annotation_sha256': digest(annotation), 'evidence_sha256': digest(evidence)}


def _allowed(state, allowed):
    expected = ((Action.RETRIEVE, Action.VERIFY, Action.STOP)
                if state.rounds_remaining else (Action.VERIFY, Action.STOP))
    if not isinstance(allowed, tuple) or allowed != expected:
        raise ValueError('Typesafe accepts only the planned action allowlist')
    return [item.value for item in allowed]


def _reason(error):
    reason = getattr(error, 'reason', 'client_error')
    return reason if reason in _REASONS - {'selected'} else 'client_error'


class TypesafePlannedController:
    name = 'jev'

    def __init__(self, client, job_id, on_decision=None):
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{24}', job_id):
            raise ValueError('Typesafe needs a product job identity')
        if getattr(getattr(client, 'config', None), 'model', None) != MODEL:
            raise ValueError('Typesafe needs the pinned Jev model')
        self.client = client
        self.job_id = job_id
        self.on_decision = on_decision
        self.last_fallback = False
        self.records = []
        self.context = None

    def bind_context(self, case, snapshot, evidence, annotation):
        context = _context(case, snapshot, evidence, annotation)
        if self.context is not None:
            previous = _binding(self.context, self.job_id, 1)
            current = _binding(context, self.job_id, 1)
            if (self.context[0]['case_id'] != context[0]['case_id']
                    or any(previous[key] != current[key] for key in
                           ('query_sha256', 'snapshot_sha256', 'commit_sha'))):
                raise ValueError('Typesafe cannot change the issue or snapshot within a job')
        self.context = context

    def decide(self, state, allowed):
        if self.context is None:
            raise ValueError('Typesafe needs bound context before deciding')
        if len(self.records) >= MAX_DECISIONS:
            raise ValueError('Typesafe decision bound exceeded')
        public_state = _state(self.context, state)
        choices = _allowed(state, allowed)
        index = len(self.records) + 1
        binding = _binding(self.context, self.job_id, index)
        record = {'decision_index': index, 'allowed': choices, 'state': public_state,
            'state_sha256': digest(public_state), 'binding': binding,
            'request_sha256': None, 'action': None, 'fallback': False,
            'reason': 'selected', 'attempt_status': 'selected',
            'response': None, 'receipt': None}
        try:
            record['request_sha256'] = digest(build_request(public_state, choices, MODEL))
            result = self.client.decide(public_state, choices, binding)
            if not isinstance(result, dict):
                raise TypesafeError('invalid_response')
            response = result.get('response')
            normalized = validate_response(response, choices, MODEL)
            if any(result.get(key) != value for key, value in normalized.items()):
                raise TypesafeError('invalid_response')
            receipt = validate_receipt(result.get('receipt'), binding,
                                       record['request_sha256'], response)
            if receipt['status'] not in {'settled', 'missing_usage_bound'}:
                raise TypesafeError('invalid_receipt')
            record.update(action=normalized['action'], response=deepcopy(response),
                          receipt=deepcopy(receipt), attempt_status=receipt['status'])
            self.last_fallback = False
            action = Action(normalized['action'])
        except InterruptedError:
            raise
        except (TypesafeError, KeyError, TypeError, ValueError, RuntimeError, AttributeError) as error:
            # Never copy an error string or HTTP body into public job metadata.
            reason = _reason(error)
            receipt = getattr(error, 'receipt', None) or getattr(self.client, 'last_receipt', None)
            if receipt is None and isinstance(locals().get('result'), dict):
                receipt = result.get('receipt')
            if receipt is not None:
                try:
                    validate_receipt(receipt, binding, record['request_sha256'])
                    if receipt['status'] == 'reserved':
                        # A paid attempt may have happened but could not settle.
                        # Preserve its bound and stop before any further paid stage.
                        raise OSError('Typesafe reservation requires accounting recovery')
                    record['receipt'] = deepcopy(receipt)
                except TypesafeError:
                    receipt = None
                    reason = 'invalid_receipt'
            record.update(fallback=True, reason=reason,
                attempt_status=(receipt['status'] if receipt is not None else
                                'blocked' if reason in _BLOCKED else 'unreceipted_error'))
            self.last_fallback = True
            action = RuleController().decide(state, allowed)
            record['action'] = action.value
        self.records.append(deepcopy(record))
        if self.on_decision is not None:
            try:
                self.on_decision(deepcopy(record))
            except InterruptedError:
                raise
            except Exception:
                # The planner catches typed controller errors to use rules. A
                # failed durable observer must instead stop before another call.
                raise OSError('Typesafe decision persistence failed') from None
        return action

    def export_decisions(self, pack):
        plan = pack['workflow_plan']
        sidecar = {'schema': SCHEMA, 'provider': 'typesafe', 'model': MODEL,
            'source': deepcopy(plan['source']), 'workflow_plan_sha256': plan['sha256'],
            'job_id': self.job_id, 'max_decisions': MAX_DECISIONS,
            'records': deepcopy(self.records)}
        sidecar['sha256'] = digest(sidecar)
        validate_controller_decisions(dict(pack, controller_decisions=sidecar))
        return sidecar


def _record(record, context, decision, allowed, expected, job_id, index):
    if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
        raise ValueError('Invalid saved Typesafe decision record')
    state = _state(context, decision)
    choices = _allowed(decision, allowed)
    binding = _binding(context, job_id, index)
    if (type(record['decision_index']) is not int or record['decision_index'] != index
            or record['allowed'] != choices or record['state'] != state
            or record['state_sha256'] != digest(state)
            or digest(record['state']) != digest(state)
            or record['binding'] != binding or digest(record['binding']) != digest(binding)
            or type(record['fallback']) is not bool or record['reason'] not in _REASONS
            or expected['controller'] != 'jev' or expected['action'] != record['action']
            or expected['fallback'] != record['fallback']):
        raise ValueError('Saved Typesafe decision differs from planned public state')
    try:
        request_sha256 = digest(build_request(state, choices, MODEL))
    except TypesafeError as error:
        if (error.reason != 'request_too_large' or record['reason'] != error.reason
                or record['request_sha256'] is not None or record['receipt'] is not None
                or not record['fallback'] or record['response'] is not None
                or record['attempt_status'] != 'blocked'):
            raise ValueError('Saved Typesafe blocked request differs') from None
        request_sha256 = None
    if record['request_sha256'] != request_sha256:
        raise ValueError('Saved Typesafe request digest differs')
    receipt = record['receipt']
    if receipt is not None:
        validate_receipt(receipt, binding, request_sha256, record['response'])
        if (receipt['status'] == 'reserved'
                or record['attempt_status'] != receipt['status']):
            raise ValueError('Saved Typesafe decision has no final receipt')
    if record['fallback']:
        if (record['reason'] == 'selected' or record['response'] is not None
                or record['action'] != RuleController().decide(decision, allowed).value
                or record['reason'] in _BLOCKED and receipt is not None
                or receipt is None and record['attempt_status'] !=
                   ('blocked' if record['reason'] in _BLOCKED else 'unreceipted_error')):
            raise ValueError('Saved Typesafe fallback differs from rules')
    else:
        normalized = validate_response(record['response'], choices, MODEL)
        if (record['reason'] != 'selected' or record['action'] != normalized['action']
                or receipt is None or receipt['status'] not in {'settled', 'missing_usage_bound'}):
            raise ValueError('Saved Typesafe selection differs from its response')


def validate_controller_decisions(pack):
    """Pure replay of controller source, requests, allowed actions and receipts."""
    from .planner import validate_planned
    try:
        plan = validate_planned(pack)
        sidecar = pack['controller_decisions']
        if (not isinstance(sidecar, dict) or set(sidecar) != {'schema', 'provider', 'model',
                'source', 'workflow_plan_sha256', 'job_id', 'max_decisions', 'records', 'sha256'}
                or sidecar['schema'] != SCHEMA or sidecar['provider'] != 'typesafe'
                or sidecar['model'] != MODEL or sidecar['source'] != plan['source']
                or sidecar['workflow_plan_sha256'] != plan['sha256']
                or type(sidecar['max_decisions']) is not int or sidecar['max_decisions'] != MAX_DECISIONS
                or not isinstance(sidecar['job_id'], str)
                or not re.fullmatch(r'[a-f0-9]{24}', sidecar['job_id'])
                or digest({key: value for key, value in sidecar.items() if key != 'sha256'}) != sidecar['sha256']
                or not isinstance(sidecar['records'], list) or len(sidecar['records']) > MAX_DECISIONS):
            raise ValueError('Saved Typesafe controller seal or source differs')
        known = {row['id']: row for row in pack['evidence']}
        current = [known[identifier] for identifier in plan['seed_evidence_ids']]
        annotation = plan['seed_annotation']
        expected = []
        terminal = False
        if annotation is not None:
            for index, step in enumerate(plan['rounds'], 1):
                context = _context(pack['case'], pack['snapshot'], current, annotation)
                decision = DecisionState(sum(row['kind'] == 'code' for row in current),
                    len(annotation['hypotheses']), tuple(pack['intake']['missing_information']),
                    plan['limits']['max_retrieval_rounds'] - index + 1)
                expected.append((context, decision, (Action.RETRIEVE, Action.VERIFY, Action.STOP), step))
                current = current + [known[identifier] for identifier in step['added_ids']]
                if step['output_annotation'] is not None:
                    annotation = step['output_annotation']
                if step['action'] != 'retrieve':
                    terminal = True
            if not terminal:
                context = _context(pack['case'], pack['snapshot'], current, annotation)
                decision = DecisionState(sum(row['kind'] == 'code' for row in current),
                    len(annotation['hypotheses']), tuple(pack['intake']['missing_information']), 0)
                expected.append((context, decision, (Action.VERIFY, Action.STOP), plan['final_decision']))
        if len(sidecar['records']) != len(expected):
            raise ValueError('Saved Typesafe decision count differs from the workflow')
        for index, (record, values) in enumerate(zip(sidecar['records'], expected), 1):
            _record(record, *values, sidecar['job_id'], index)
        return sidecar
    except (TypesafeError, KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError('Invalid saved Typesafe controller decisions') from None
