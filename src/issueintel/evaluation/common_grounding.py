"""Pure common-critic projections and descriptive paired-product summaries.

The driver owns source replay, journals, receipts, authority and paid admission.
This module performs no filesystem, environment, provider or target-code work.
Native model assessments are neither causal truth nor independent human review.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math
import re

from issueintel.annotations import public_inputs
from issueintel.annotations.structured import _validate_wire_schema
from issueintel.investigation.verifier import verify_hypotheses
from issueintel.runtime.study_budget import StudyBudgetLedger, StudyBudgetStop


ARMS = ('rules', 'jev')
LABELS = ('supported', 'unsupported', 'contradicted', 'uncertain')
JUDGE_CONFIG = {'provider': 'groq', 'model': 'openai/gpt-oss-120b',
                'reasoning_effort': 'low', 'max_output_tokens': 2048}
MAX_JSON_BYTES = 16_000_000
_HASH = re.compile('[a-f0-9]{64}')
_UNIT = re.compile('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
_CLOSED = {'settled', 'failed_bound', 'missing_usage_bound'}


def _bytes(value):
    stack, visited = [(value, 0)], 0
    while stack:
        item, depth = stack.pop(); visited += 1
        if depth > 80 or visited > 500000:
            raise ValueError('Common grounding JSON exceeds structural bounds')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('Common grounding JSON requires text object keys')
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif type(item) not in {str, int, float, bool, type(None)}:
            raise ValueError('Common grounding requires JSON data values')
        elif type(item) is float and not math.isfinite(item):
            raise ValueError('Common grounding requires finite JSON numbers')
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('Common grounding requires finite UTF-8 JSON') from None
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('Common grounding JSON exceeds its byte bound')
    return raw


def digest(value):
    return sha256(_bytes(value)).hexdigest()


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _hidden(pack):
    """Inspect raw data before projection, allowing empty native defaults only."""
    pending = [(pack, ())]
    while pending:
        value, path = pending.pop()
        if isinstance(value, dict):
            if value.get('kind') == 'source_human':
                raise ValueError('Common grounding cannot consume human rationale')
            for key, item in value.items():
                if key.casefold() in public_inputs.HIDDEN_FIELDS:
                    if key == 'annotation' and not path:
                        pass
                    elif key == 'source_quality' and item == {}:
                        continue
                    else:
                        raise ValueError('Common grounding cannot consume hidden benchmark fields')
                pending.append((item, (*path, key)))
        elif isinstance(value, list):
            pending.extend((item, (*path, str(index))) for index, item in enumerate(value))


def wire_request(messages, schema):
    """Canonical native structured request; user object ordering is not treatment."""
    _bytes({'messages': messages, 'schema': schema})
    if (not isinstance(messages, list) or len(messages) != 2
            or any(not isinstance(row, dict) or set(row) != {'role', 'content'}
                   or not isinstance(row['content'], str) for row in messages)
            or [row['role'] for row in messages] != ['system', 'user']
            or not isinstance(schema, dict)):
        raise ValueError('Common critic requires its native two-message request')
    copied = deepcopy(messages)
    try:
        payload = json.loads(copied[1]['content'])
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('Common critic user input must be finite JSON') from None
    if not isinstance(payload, dict):
        raise ValueError('Common critic user input must be an object')
    copied[1]['content'] = _bytes(payload).decode('utf-8')
    return {'messages': copied, 'schema': deepcopy(schema), **JUDGE_CONFIG}


class _CapturedRequest(Exception):
    pass


def _request(payload):
    captured = []
    class Capture:
        def complete_structured(self, messages, *, max_output_tokens, schema):
            if type(max_output_tokens) is not int or max_output_tokens != 2048:
                raise ValueError('Native common critic changed its output bound')
            captured.append(wire_request(messages, schema))
            raise _CapturedRequest
    try:
        verify_hypotheses({'hypotheses': payload['hypotheses']}, payload['evidence'],
                          Capture(), max_output_tokens=2048)
    except _CapturedRequest:
        pass
    if len(captured) != 1:
        raise ValueError('Native common critic did not expose one request')
    return captured[0]


def project(pack):
    """Return public hypotheses/evidence and a fresh common native critic request."""
    _bytes(pack)
    if (not isinstance(pack, dict) or not isinstance(pack.get('annotation'), dict)
            or not isinstance(pack['annotation'].get('hypotheses'), list)
            or not isinstance(pack.get('evidence'), list)):
        raise ValueError('Common grounding needs a completed native annotation and evidence')
    _hidden(pack)
    verify_hypotheses(pack['annotation'], pack['evidence'])
    payload = json.loads(_bytes({
        'hypotheses': public_inputs.model_hypotheses(pack['annotation']['hypotheses']),
        'evidence': public_inputs.model_evidence(pack['evidence'])}))
    if (not 3 <= len(payload['hypotheses']) <= 5
            or len({row['id'] for row in payload['hypotheses']}) != len(payload['hypotheses'])):
        raise ValueError('A completed product common review needs three to five distinct hypotheses')
    return payload, _request(payload)


def _case_ids(cases):
    _bytes(cases)
    if (not isinstance(cases, list) or len(cases) != 5
            or any(not isinstance(row, dict) or not isinstance(row.get('unit_id'), str)
                   or not _UNIT.fullmatch(row['unit_id']) or not isinstance(row.get('context'), dict)
                   or not isinstance(row['context'].get('case_id'), str)
                   or not row['context']['case_id'] for row in cases)):
        raise ValueError('Common product grounding retains exactly five registered case clusters')
    ids = [row['unit_id'] for row in cases]
    if len(set(ids)) != 5 or len({row['context']['case_id'] for row in cases}) != 5:
        raise ValueError('Common product grounding selection contains duplicate cases')
    return ids


def make_units(cases, results, arms=ARMS):
    """Map every selected cell to a fresh judge, deduplicated by exact request.

    ``cases`` is the ordered native registration's five case rows. Missing,
    failed and unavailable result rows remain selected cells without a pack.
    The driver independently requires the full terminal parent inference seal.
    """
    ids = _case_ids(cases)
    if type(arms) not in {tuple, list} or tuple(arms) != ARMS or any(not isinstance(arm, str) for arm in arms):
        raise ValueError('Common product grounding compares the registered rules/Jev arms')
    supplied = list(results.values()) if isinstance(results, dict) else results
    _bytes(supplied)
    if not isinstance(supplied, list) or len(supplied) > 10:
        raise ValueError('Common product grounding has at most ten selected result rows')
    indexed = {}
    selected = {row['unit_id']: row for row in cases}
    for row in supplied:
        if (not isinstance(row, dict) or row.get('schema') != 'controller_study_arm_v1'
                or not isinstance(row.get('unit_id'), str) or row['unit_id'] not in selected
                or not isinstance(row.get('arm_id'), str) or row['arm_id'] not in ARMS
                or not isinstance(row.get('status'), str) or row['status'] not in {'completed', 'failed', 'unavailable'}
                or not isinstance(row.get('sha256'), str) or not _HASH.fullmatch(row['sha256'])
                or digest({key: item for key, item in row.items() if key != 'sha256'}) != row['sha256']):
            raise ValueError('Common product grounding requires sealed native parent result identities')
        key = row['unit_id'], row['arm_id']
        if key in indexed or not _same(row.get('source'), selected[key[0]]['context']):
            raise ValueError('Duplicate parent result or changed registered source context')
        if row['status'] == 'completed':
            pack = row.get('pack')
            if (not isinstance(pack, dict) or row.get('failure') is not None
                    or not isinstance(pack.get('case'), dict)
                    or pack['case'].get('case_id') != selected[key[0]]['context']['case_id']):
                raise ValueError('Completed parent result lacks its bound product pack')
        elif row.get('pack') is not None or not isinstance(row.get('failure'), dict):
            raise ValueError('Failed or unavailable source cannot supply a grounding pack')
        indexed[key] = row
    units, bindings = {}, []
    for cid in ids:
        for arm in ARMS:
            row = indexed.get((cid, arm))
            status, uid = row['status'] if row else 'missing', None
            if status == 'completed':
                payload, request = project(row['pack'])
                uid = digest(request)
                if uid not in units:
                    units[uid] = {'unit_id': uid, 'request': request, 'payload': payload, 'case_ids': []}
                elif not _same(units[uid]['payload'], payload):
                    raise ValueError('One common request digest has conflicting projected inputs')
                if cid not in units[uid]['case_ids']:
                    units[uid]['case_ids'].append(cid)
            bindings.append({'case_id': cid, 'arm_id': arm,
                'arm_sha256': row['sha256'] if row else None, 'source_status': status, 'unit_id': uid})
    return {'case_ids': ids, 'arms': list(ARMS), 'arm_bindings': bindings, 'units': list(units.values())}


def _unit(unit):
    _bytes(unit)
    if (not isinstance(unit, dict) or set(unit) != {'unit_id', 'request', 'payload', 'case_ids'}
            or not isinstance(unit.get('unit_id'), str) or not _HASH.fullmatch(unit['unit_id'])
            or not isinstance(unit.get('payload'), dict) or set(unit['payload']) != {'hypotheses', 'evidence'}
            or not isinstance(unit.get('case_ids'), list) or not 1 <= len(unit['case_ids']) <= 5
            or any(not isinstance(cid, str) or not _UNIT.fullmatch(cid) for cid in unit['case_ids'])
            or len(set(unit['case_ids'])) != len(unit['case_ids'])
            or digest(unit['request']) != unit['unit_id'] or not _same(_request(unit['payload']), unit['request'])):
        raise ValueError('Common judge unit or native request binding changed')
    if (not 3 <= len(unit['payload']['hypotheses']) <= 5
            or len({row['id'] for row in unit['payload']['hypotheses']}) != len(unit['payload']['hypotheses'])):
        raise ValueError('Common judge unit hypotheses differ from the completed native product contract')
    return unit


def native_result(unit, complete):
    _unit(unit)
    if not callable(complete) and not callable(getattr(complete, 'complete_structured', None)):
        raise ValueError('A common model review needs its single owned critic callback')
    return verify_hypotheses({'hypotheses': unit['payload']['hypotheses']},
        unit['payload']['evidence'], complete, max_output_tokens=2048)


def validate_response(unit, response):
    """Validate one saved model response through both native wire and verifier."""
    _unit(unit); _bytes(response)
    _validate_wire_schema(response, unit['request']['schema'])
    class Replay:
        def complete_structured(self, messages, *, max_output_tokens, schema):
            if (type(max_output_tokens) is not int or max_output_tokens != 2048
                    or not _same(wire_request(messages, schema), unit['request'])):
                raise ValueError('Saved common response has a different native request')
            return deepcopy(response)
    return native_result(unit, Replay())


def _plan(plan):
    _bytes(plan)
    if (not isinstance(plan, dict) or plan.get('arms') != list(ARMS)
            or not isinstance(plan.get('case_ids'), list) or len(plan['case_ids']) != 5
            or any(not isinstance(cid, str) or not _UNIT.fullmatch(cid) for cid in plan['case_ids'])
            or len(set(plan['case_ids'])) != 5 or not isinstance(plan.get('units'), list)
            or len(plan['units']) > 10 or not isinstance(plan.get('arm_bindings'), list)
            or len(plan['arm_bindings']) != 10):
        raise ValueError('Invalid fixed five-case common grounding plan')
    if ('judge_config' in plan and not _same(plan['judge_config'], JUDGE_CONFIG)
            or 'sha256' in plan and (not isinstance(plan['sha256'], str) or not _HASH.fullmatch(plan['sha256'])
                or digest({key: value for key, value in plan.items() if key != 'sha256'}) != plan['sha256'])):
        raise ValueError('Common grounding plan seal or judge configuration changed')
    units = {}
    for unit in plan['units']:
        _unit(unit)
        if unit['unit_id'] in units:
            raise ValueError('Duplicate owned common judge unit')
        units[unit['unit_id']] = unit
    seen, mapped = set(), {uid: [] for uid in units}
    for row in plan['arm_bindings']:
        if (not isinstance(row, dict) or set(row) != {'case_id', 'arm_id', 'arm_sha256', 'source_status', 'unit_id'}
                or not isinstance(row['case_id'], str) or row['case_id'] not in plan['case_ids']
                or not isinstance(row['arm_id'], str) or row['arm_id'] not in ARMS
                or (row['case_id'], row['arm_id']) in seen
                or not isinstance(row['source_status'], str) or row['source_status'] not in {'completed', 'failed', 'unavailable', 'missing'}
                or (row['arm_sha256'] is None) != (row['source_status'] == 'missing')
                or row['arm_sha256'] is not None and (not isinstance(row['arm_sha256'], str) or not _HASH.fullmatch(row['arm_sha256']))
                or (row['unit_id'] is not None) != (row['source_status'] == 'completed')
                or row['unit_id'] is not None and (not isinstance(row['unit_id'], str) or row['unit_id'] not in units)):
            raise ValueError('Common grounding selected-cell mapping changed')
        seen.add((row['case_id'], row['arm_id']))
        if row['unit_id'] is not None and row['case_id'] not in mapped[row['unit_id']]:
            mapped[row['unit_id']].append(row['case_id'])
    if seen != {(cid, arm) for cid in plan['case_ids'] for arm in ARMS}:
        raise ValueError('Common grounding lost a selected case/arm cell')
    if [(row['case_id'], row['arm_id']) for row in plan['arm_bindings']] != [(cid, arm) for cid in plan['case_ids'] for arm in ARMS]:
        raise ValueError('Common grounding selected-cell order changed')
    for uid, unit in units.items():
        if unit['case_ids'] != mapped[uid]:
            raise ValueError('Common unit aliases differ from selected case bindings')
    return units


def _receipt(receipt):
    """Validate native receipt shape and return its declared-envelope breach.

    The driver separately checks the frozen monetary bound. Failed attempts
    keep over-bound receipts for accounting, but these cannot support a review.
    """
    _bytes(receipt)
    if not isinstance(receipt, dict):
        raise ValueError('Common judge receipt must be an object')
    try:
        checked = date.fromisoformat(receipt['price_date'])
        if checked.isoformat() != receipt['price_date']:
            raise ValueError()
        if (type(receipt['input_bound']) is not int or receipt['input_bound'] <= 0
                or type(receipt['output_bound']) is not int or receipt['output_bound'] != 2048
                or receipt['input_bound'] + 2048 > 131072
                or any(value is not None and (type(value) is not int or value < 0) for value in
                       (receipt['observed_input_tokens'], receipt['observed_output_tokens']))):
            raise ValueError()
        validator = object.__new__(StudyBudgetLedger)
        validator.providers = {'groq': {'price_date': receipt['price_date']}}
        call = {'provider': 'groq', 'model': JUDGE_CONFIG['model'],
            'input_bound': receipt['input_bound'], 'output_bound': 2048,
            'micro_usd_bound': receipt['micro_usd_charged_or_reserved']}
        breached = StudyBudgetLedger._validate_receipt(validator, call, receipt)
    except (KeyError, TypeError, AttributeError, ValueError, StudyBudgetStop):
        raise ValueError('Invalid native common-judge receipt') from None
    return breached


def _records(units, records):
    _bytes(records)
    if not isinstance(records, dict) or set(records) - set(units):
        raise ValueError('Common results contain an unknown owned judge unit')
    reservations = set()
    for uid, row in records.items():
        if (not isinstance(row, dict) or not isinstance(row.get('status'), str) or row['status'] not in {'intent', 'reviewed', 'failed'}
                or row.get('unit_id', uid) != uid
                or row.get('request_sha256', uid) != digest(units[uid]['request'])
                or not isinstance(row.get('receipts'), dict) or len(row['receipts']) > 1):
            raise ValueError('Common result ownership or status changed')
        if 'sha256' in row and (not isinstance(row['sha256'], str) or not _HASH.fullmatch(row['sha256'])
                or digest({key: value for key, value in row.items() if key != 'sha256'}) != row['sha256']):
            raise ValueError('Common result record seal changed')
        if row.get('latency_seconds') is not None and (type(row['latency_seconds']) not in (int, float)
                or not math.isfinite(row['latency_seconds']) or row['latency_seconds'] < 0):
            raise ValueError('Common judge latency must be finite, nonnegative observed time')
        if 'micro_usd_bound' in row and (type(row['micro_usd_bound']) is not int or row['micro_usd_bound'] < 0):
            raise ValueError('Common judge unknown-usage allowance must retain its integer bound')
        breached = False
        for rid, receipt in row['receipts'].items():
            breached = _receipt(receipt) or breached
            if rid != receipt['reservation_id'] or rid in reservations:
                raise ValueError('A native common-judge reservation has multiple owners')
            reservations.add(rid)
        if row['status'] == 'reviewed':
            if (breached or len(row['receipts']) != 1 or any(r['status'] not in {'settled', 'missing_usage_bound'} for r in row['receipts'].values())
                    or row.get('failure') is not None or row.get('latency_seconds') is None
                    or not _same(validate_response(units[uid], row.get('response')), row.get('verification'))):
                raise ValueError('A common review needs its exact native response and closed owned receipt')
        elif row.get('verification') is not None:
            raise ValueError('An unfinished or failed common judge cannot claim assessment labels')
    return records


def _timing(rows):
    rows = list(rows)
    observed = [row['latency_seconds'] for row in rows if row.get('latency_seconds') is not None]
    total = round(math.fsum(observed), 6)
    return {'observed_units': len(observed), 'unknown_units': len(rows) - len(observed),
        'observed_seconds': total, 'mean_seconds': round(total / len(observed), 6) if observed else None,
        'interpretation': 'observed_active_unit_duration_including_guards_and_journals_not_pure_network'}


def _usage(records):
    receipts = [receipt for row in records.values() for receipt in row['receipts'].values()]
    missing = [row for row in records.values() if not row['receipts']]
    observed = [row for row in receipts if row['observed_input_tokens'] is not None]
    known_input = sum(row['observed_input_tokens'] for row in observed)
    known_output = sum(row['observed_output_tokens'] for row in observed)
    complete = bool(receipts) and not missing and len(observed) == len(receipts)
    return {'owned_attempts': len(records), 'unique_reservations': len(receipts),
        'native_envelope_breach_reservations': sum(_receipt(receipt) for receipt in receipts),
        'status_counts': dict(Counter(receipt['status'] for receipt in receipts)),
        'tokens_charged_or_reserved': sum(row['tokens_charged_or_reserved'] for row in receipts),
        'micro_usd_charged_or_reserved': sum(row['micro_usd_charged_or_reserved'] for row in receipts),
        'settled_micro_usd': sum(row['micro_usd_charged_or_reserved'] for row in receipts if row['status'] == 'settled'),
        'unsettled_or_unknown_micro_usd_bound': sum(row['micro_usd_charged_or_reserved'] for row in receipts if row['status'] != 'settled'),
        'without_receipt_units': len(missing),
        'without_receipt_known_micro_usd_bound': sum(row['micro_usd_bound'] for row in missing if 'micro_usd_bound' in row),
        'without_receipt_unknown_bound_units': sum('micro_usd_bound' not in row for row in missing),
        'receipt_totals_cover_all_owned_attempts': not missing,
        # A sum of the observed subset is not a total when any owned attempt
        # lacks a receipt or directional observations. Keep both facts explicit.
        'observed_input_tokens': known_input if complete else None,
        'observed_output_tokens': known_output if complete else None,
        'known_input_tokens_sum': known_input, 'known_output_tokens_sum': known_output,
        'directional_totals_cover_all_owned_attempts': complete,
        'directional_observed_reservations': len(observed),
        'directional_unknown_reservations': len(receipts) - len(observed),
        'provider_invoice_verified': False}


def summarize(plan, records):
    """Descriptive fresh-model labels, retaining all five selected case clusters."""
    units = _plan(plan)
    _records(units, records)
    by_arm, fractions, cells = {}, {}, []
    for arm in ARMS:
        selected = [row for row in plan['arm_bindings'] if row['arm_id'] == arm]
        counts, sources, per_case = Counter(), Counter(), {}
        for binding in selected:
            sources[binding['source_status']] += 1
            result = records.get(binding['unit_id'])
            labels = Counter(review['assessment'] for review in result['verification']['reviews']) if result and result['status'] == 'reviewed' else Counter()
            if labels:
                counts.update(labels)
                per_case[binding['case_id']] = {label: labels[label] / sum(labels.values()) for label in LABELS}
            cells.append({**deepcopy(binding),
                'judge_status': result['status'] if result else 'source_unavailable' if binding['unit_id'] is None else 'not_attempted',
                'assessment_counts': {label: labels[label] for label in LABELS},
                'shared_unit_latency_seconds': result.get('latency_seconds') if result else None})
        fractions[arm] = per_case
        referenced = {row['unit_id']: records[row['unit_id']] for row in selected if row['unit_id'] in records}
        by_arm[arm] = {'selected_cases': 5, 'reviewed_cases': len(per_case),
            'source_status_counts': dict(sources),
            'source_unavailable_cases': sum(row['unit_id'] is None for row in selected),
            'judge_failed_cases': sum(records.get(row['unit_id'], {}).get('status') == 'failed' for row in selected),
            'judge_not_attempted_cases': sum(row['unit_id'] is not None and row['unit_id'] not in records for row in selected),
            'judge_unknown_cases': sum(row['unit_id'] in records and (records[row['unit_id']]['status'] == 'intent'
                or not records[row['unit_id']]['receipts'] or any(r['status'] not in _CLOSED for r in records[row['unit_id']]['receipts'].values())) for row in selected),
            'referenced_unique_units': len(referenced), 'referenced_unit_latency': _timing(referenced.values()),
            'reference_timing_is_additive_across_arms': False,
            'assessment_counts': {label: counts[label] for label in LABELS},
            'macro_case_fractions': {label: sum(row[label] for row in per_case.values()) / len(per_case) if per_case else None for label in LABELS},
            'macro_denominator': 'reviewed_cases_only_selected_denominator_reported_separately'}
    paired_ids = [cid for cid in plan['case_ids'] if cid in fractions['rules'] and cid in fractions['jev']]
    return {'schema': 'controller_common_grounding_scores_v1', 'kind': 'automatic_model_assessment',
        'plan_sha256': plan.get('sha256'), 'case_clusters': 5, 'arm_results': 10, 'cases': cells,
        'unique_judge_units': len(units), 'completed_units': sum(row['status'] == 'reviewed' for row in records.values()),
        'failed_units': sum(row['status'] == 'failed' for row in records.values()),
        'not_attempted_units': len(units) - len(records),
        'unknown_units': sum(row['status'] == 'intent' or not row['receipts']
            or any(receipt['status'] not in _CLOSED for receipt in row['receipts'].values()) for row in records.values()),
        'by_arm': by_arm, 'paired_comparisons': [{'left_arm': 'rules', 'right_arm': 'jev',
            'common_reviewed_cases': len(paired_ids), 'case_ids': paired_ids,
            'mean_fraction_difference_right_minus_left': {label: sum(fractions['jev'][cid][label] - fractions['rules'][cid][label] for cid in paired_ids) / len(paired_ids) if paired_ids else None for label in LABELS}}],
        'unit_aliases': {uid: [deepcopy(row) for row in plan['arm_bindings'] if row['unit_id'] == uid] for uid in units},
        'global_unique_usage': _usage(records), 'global_unique_unit_latency': _timing(records.values()),
        'fresh_common_judge': True, 'reuses_original_critic': False, 'inference_provider_calls_added': 0,
        'judge_calls_are_separate_phase': True, 'independent_human': False,
        'semantic_accuracy_verified': False, 'causal_truth_verified': False,
        'developer_benefit_verified': False, 'gold_read': False, 'same_model_bias_possible': True,
        'limitations': ['Common model labels concern visible evidence, not semantic accuracy or root-cause truth.',
            'Five selected case clusters and shared judge aliases are not ten independent observations.',
            'The original in-treatment critic is absent from judge input; source wording may still reveal treatment.',
            'Selected source failures and unknown judge outcomes remain in coverage; label fractions use reviewed subsets only.',
            'No independent human validation, executed falsification checks or developer benefit is established.',
            'Assessment cost is reported separately from inference; the driver must enforce the combined approved cap.']}
