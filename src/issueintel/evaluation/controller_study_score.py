"""Descriptive, gold-free summaries of a sealed product controller study.

The caller admits and seals the registration, source files and terminal run.
This module only replays included JSON and native validators. It does not read
files, construct a provider, grade semantic truth, or supply evaluation gold.
Five selected case clusters remain the denominator even when an arm is absent.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
import re
from types import SimpleNamespace

from issueintel.investigation.controller import Action, DecisionState, RuleController
from issueintel.investigation.planner import GAP_POLICY, validate_planned
from issueintel.investigation.product_study import RECIPE, context_binding, validate_verification
from issueintel.investigation.typesafe_controller import validate_controller_decisions
from issueintel.runtime.io import digest
from issueintel.runtime.study_budget import StudyBudgetLedger, StudyBudgetStop, _validate_policy


SCHEMA = 'controller_study_scores_v1'
ARMS = ('rules', 'jev')
_HASH = re.compile(r'[a-f0-9]{64}')
_RESULT_FIELDS = {'schema', 'registration_sha256', 'unit_id', 'arm_id', 'status',
    'pack', 'failure', 'stage_refs', 'call_ids', 'owned_call_ids',
    'pipeline_latency_seconds', 'preparation_seconds',
    'preparation_measurement_id', 'preparation_owner_arm_id', 'sha256'}
_RESULT_OPTIONAL = {'source', 'stop_reason', 'study_id', 'preparation_sha256',
                    'preparation_measurement_sha256'}
_CALL_BASE = {'call_id', 'arm_id', 'stage', 'request_sha256', 'provider', 'model',
    'config_sha256', 'price_sha256', 'input_bound', 'output_bound', 'micro_usd_bound'}
_CALL_EXTRA = {'receipt', 'outcome', 'outcome_sha256', 'reason', 'references'}


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('Controller study score requires finite JSON data') from None


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _sealed(value, label):
    if (not isinstance(value, dict) or not _hash(value.get('sha256'))
            or digest({k: v for k, v in value.items() if k != 'sha256'}) != value['sha256']):
        raise ValueError(label + ' seal differs')


def _count(value):
    return type(value) is int and value >= 0


def _seconds(value, label):
    if value is not None and (type(value) not in (int, float)
                             or not math.isfinite(value) or value < 0):
        raise ValueError('Invalid controller study timing: ' + label)
    return value


def _ids(value, label):
    if (not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value)
            or len(value) != len(set(value))):
        raise ValueError('Invalid controller study IDs: ' + label)
    return value


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def _decision_rows(pack):
    """Rebuild exactly the DecisionState supplied by the native v2 planner."""
    plan = validate_planned(pack)
    if plan['policy'] != GAP_POLICY or plan['provider'] != 'groq' or plan['model'] != 'openai/gpt-oss-120b':
        raise ValueError('Controller study needs the native Planned v2 recipe')
    missing = pack['intake']['missing_information']
    if (not isinstance(missing, list) or len(missing) > 20
            or any(not isinstance(v, str) or not v or len(v) > 100 for v in missing)):
        raise ValueError('Invalid controller missing-information state')
    known = {row['id']: row for row in pack['evidence']}
    current = list(plan['seed_evidence_ids'])
    annotation = plan['seed_annotation']
    rows = []
    terminal = False
    if annotation is not None:
        for index, step in enumerate(plan['rounds'], 1):
            state = DecisionState(sum(known[v]['kind'] == 'code' for v in current),
                len(annotation['hypotheses']), tuple(missing),
                plan['limits']['max_retrieval_rounds'] - index + 1)
            rows.append((state, (Action.RETRIEVE, Action.VERIFY, Action.STOP), step))
            current += step['added_ids']
            if step['output_annotation'] is not None:
                annotation = step['output_annotation']
            terminal = step['action'] != 'retrieve'
        if not terminal:
            state = DecisionState(sum(known[v]['kind'] == 'code' for v in current),
                len(annotation['hypotheses']), tuple(missing), 0)
            rows.append((state, (Action.VERIFY, Action.STOP), plan['final_decision']))
    return rows


def _rule_shadow(pack):
    """Replay rules on each exact observed state; agreement is not accuracy.

    A selected Jev action and a fallback action are separate observations. A
    terminal round's repeated final_decision is counted once, as in the native
    controller sidecar. No counterfactual downstream trajectory is invented.
    """
    _json(pack)
    native = pack.get('controller_decisions')
    if native is not None:
        native = validate_controller_decisions(pack)
    rows = []
    for index, (state, allowed, actual) in enumerate(_decision_rows(pack), 1):
        expected = RuleController().decide(state, allowed).value
        record = native['records'][index - 1] if native is not None else None
        decision = {'evidence_count': state.evidence_count,
            'hypotheses_count': state.hypotheses_count,
            'missing_information': list(state.missing_information),
            'rounds_remaining': state.rounds_remaining}
        if record is not None and record['state']['decision'] != decision:
            raise ValueError('Rule shadow differs from the exact native DecisionState')
        fallback = actual['fallback']
        if type(fallback) is not bool or (fallback or actual['controller'] == 'rules') and actual['action'] != expected:
            raise ValueError('Native rules or fallback action differs from pure replay')
        rows.append({'decision_index': index, 'state': decision,
            'allowed': [a.value for a in allowed], 'actual_action': actual['action'],
            'rule_shadow_action': expected, 'agrees_with_rules': actual['action'] == expected,
            'controller': actual['controller'], 'fallback': fallback,
            'jev_selected_action': record['action'] if record is not None and not fallback else None,
            'attempt_status': record['attempt_status'] if record is not None else None,
            'fallback_reason': record['reason'] if record is not None and fallback else None})
    return rows


def validate_rule_shadow(pack):
    """Pure native-state rules replay, returning observed agreement rows."""
    try:
        return _rule_shadow(pack)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, UnicodeError):
        raise ValueError('Invalid native controller study decision structure') from None


def _pack_metrics(pack, arm):
    decisions = validate_rule_shadow(pack)
    plan = pack['workflow_plan']
    if arm == 'jev' and 'controller_decisions' not in pack:
        raise ValueError('Jev arm lost its full native decision sidecar')
    if arm == 'rules' and ('controller_decisions' in pack
            or any(row['controller'] != 'rules' for row in decisions)):
        raise ValueError('Rules arm differs from the registered controller')
    annotation, verification = pack['annotation'], pack['verification']
    if annotation is not None:
        validate_verification(verification, annotation, pack['evidence'],
                              critic_enabled=verification.get('kind') == 'llm_separate_pass')
    elif verification is not None:
        raise ValueError('A controller study critic needs an annotation')
    paths = {row['locator'].rsplit(':', 1)[0] for row in pack['evidence'] if row['kind'] == 'code'}
    snapshot_paths = {row['path'] for row in pack['snapshot']['files']}
    queries = [query for step in plan['rounds'] if step['search'] is not None
               for query in step['search']['queries']]
    hypotheses = (annotation or {}).get('hypotheses', [])
    cited = sum(len(row['support_ids']) + len(row['counter_ids']) for row in hypotheses)
    critic = 'not_run' if verification is None else verification['kind']
    return {'evidence_count': len(pack['evidence']),
        'code_excerpt_count': sum(row['kind'] == 'code' for row in pack['evidence']),
        'code_file_count': len(paths), 'snapshot_file_count': len(snapshot_paths),
        'code_file_snapshot_coverage': _rate(len(paths), len(snapshot_paths)),
        'hypothesis_count': len(hypotheses),
        'hypotheses_without_counter_citation': sum(not row['counter_ids'] for row in hypotheses),
        'hypotheses_with_model_counter_status_not_found': sum(row['counter_status'] == 'not_found' for row in hypotheses),
        'missing_information_count': len(pack['intake']['missing_information']),
        'generation_passes': plan['generation_passes'], 'logical_groq_calls': plan['provider_calls'],
        'generation_repairs': sum(value['provenance']['repair_count'] for value in
            [plan['seed_annotation']] + [s['output_annotation'] for s in plan['rounds']]
            if value is not None),
        'retrieval_rounds': sum(step['action'] == 'retrieve' for step in plan['rounds']),
        'retrieval_query_count': len(queries),
        'executed_query_count': sum(row['status'] == 'executed' for row in queries),
        'unavailable_query_count': sum(row['status'] == 'unavailable' for row in queries),
        'zero_yield_executed_query_count': sum(row['status'] == 'executed' and not row['added_ids'] for row in queries),
        'zero_yield_retrieval_rounds': sum(step['action'] == 'retrieve' and not step['added_ids'] for step in plan['rounds']),
        'added_excerpt_count': sum(len(step['added_ids']) for step in plan['rounds']),
        'query_gap_codes': dict(sorted(Counter(row['gap_code'] for row in queries).items())),
        'query_purposes': dict(sorted(Counter(row['purpose'] for row in queries).items())),
        'unavailable_reasons': dict(sorted(Counter(row['unavailable_reason'] for row in queries if row['status'] == 'unavailable').items())),
        'stop_reason': pack['stop_reason'], 'final_action': plan['final_decision']['action'],
        'structural_citation_integrity': {'status': 'valid' if annotation is not None else 'not_assessed',
            'hypotheses_checked': len(hypotheses), 'citation_references_checked': cited},
        'critic_kind': critic, 'critic_review_count': len((verification or {}).get('reviews', [])),
        'critic_assessments': dict(sorted(Counter(row['assessment'] for row in (verification or {}).get('reviews', [])).items())),
        'decision_count': len(decisions), 'action_counts': dict(sorted(Counter(row['actual_action'] for row in decisions).items())),
        'jev_selected_decisions': sum(row['jev_selected_action'] is not None for row in decisions),
        'fallback_decisions': sum(row['fallback'] for row in decisions),
        'selected_rule_agreement_count': sum(row['jev_selected_action'] is not None and row['agrees_with_rules'] for row in decisions),
        'fallback_rule_agreement_count': sum(row['fallback'] and row['agrees_with_rules'] for row in decisions),
        'rule_shadow': decisions}


def _usage_bucket(calls, records):
    result = {'attempts': len(calls), 'micro_usd_charged_or_reserved': 0,
        'receipt_count': 0, 'missing_receipt_count': 0, 'receipt_status_counts': {},
        'bounded_or_unreceipted_calls': 0, 'bounded_or_unreceipted_micro_usd': 0,
        'groq_tokens_charged_or_reserved': 0,
        'outcome_counts': dict(sorted(Counter(c['outcome'] for c in calls).items())),
        'observed_input_tokens': 0, 'observed_output_tokens': 0,
        'unknown_input_token_calls': 0, 'unknown_output_token_calls': 0,
        'known_latency_seconds_sum': 0, 'unknown_latency_calls': 0}
    statuses = Counter()
    for call in calls:
        receipt = call['receipt']
        charged = receipt['micro_usd_charged_or_reserved'] if receipt is not None else call['micro_usd_bound']
        result['micro_usd_charged_or_reserved'] += charged
        if receipt is None or receipt['status'] in {'reserved', 'failed_bound', 'missing_usage_bound'}:
            result['bounded_or_unreceipted_calls'] += 1
            result['bounded_or_unreceipted_micro_usd'] += charged
        if call['provider'] == 'groq':
            result['groq_tokens_charged_or_reserved'] += receipt['tokens_charged_or_reserved'] if receipt is not None else call['input_bound'] + call['output_bound']
        result['receipt_count' if receipt is not None else 'missing_receipt_count'] += 1
        if receipt is not None:
            statuses[receipt['status']] += 1
        for direction in ('input', 'output'):
            key = ('observed_' + direction + '_tokens') if call['provider'] == 'groq' else direction + '_tokens'
            observed = receipt.get(key) if receipt is not None else None
            if observed is None:
                result['unknown_' + direction + '_token_calls'] += 1
            else:
                result['observed_' + direction + '_tokens'] += observed
        latency = records.get(call['call_id'], {}).get('latency_seconds')
        if latency is None:
            result['unknown_latency_calls'] += 1
        else:
            result['known_latency_seconds_sum'] += latency
    result['receipt_status_counts'] = dict(sorted(statuses.items()))
    result['usd_charged_or_reserved'] = result['micro_usd_charged_or_reserved'] / 1_000_000
    result['latency_seconds_sum'] = result['known_latency_seconds_sum'] if not result['unknown_latency_calls'] else None
    return result


def _budget(spec, snapshot, call_records):
    """Replay native validation without constructing the filesystem ledger."""
    policy = spec['budget_policy']
    try:
        providers, arms = _validate_policy(policy)
        validator = SimpleNamespace(providers=providers, arms=arms)
        expected_arms = {row['unit_id'] + ':' + arm for row in spec['cases'] for arm in ARMS}
        if set(arms) != expected_arms or policy['study_id'] != spec['study_id']:
            raise ValueError('Study budget arms or study identity differ')
        if (not isinstance(snapshot, dict) or snapshot.get('schema') != 'study_budget_inspection_v1'
                or snapshot.get('policy_sha256') != digest(policy)
                or type(snapshot.get('locked')) is not bool
                or snapshot.get('is_provider_invoice') is not False
                or snapshot.get('automatic_retry') is not False
                or not _count(snapshot.get('event_count'))
                or not isinstance(snapshot.get('calls'), list)
                or not isinstance(snapshot.get('stop_reasons'), list)
                or any(not isinstance(v, str) or not v for v in snapshot['stop_reasons'])):
            raise ValueError('Invalid study budget inspection')
        calls, receipt_owners, global_refs, signatures = {}, {}, set(), set()
        for call in snapshot['calls']:
            if not isinstance(call, dict) or set(call) != _CALL_BASE | _CALL_EXTRA:
                raise ValueError('Invalid study call fields')
            StudyBudgetLedger._validate_call(validator, {key: call[key] for key in _CALL_BASE})
            cid = call['call_id']
            if cid in calls or call['outcome'] not in {'pending', 'completed', 'failed', 'unknown'}:
                raise ValueError('Duplicate or invalid study call outcome')
            signature = tuple(call[k] for k in ('provider', 'model', 'config_sha256', 'price_sha256', 'request_sha256'))
            if signature in signatures:
                raise ValueError('The same physical request has multiple study owners')
            signatures.add(signature)
            receipt = call['receipt']
            if receipt is not None:
                StudyBudgetLedger._validate_receipt(validator, call, receipt)
                rid = receipt['reservation_id']
                if rid in receipt_owners:
                    raise ValueError('One native receipt has multiple physical call owners')
                receipt_owners[rid] = cid
            if call['outcome'] == 'completed':
                if not _hash(call['outcome_sha256']) or receipt is None or receipt['status'] not in {'settled', 'missing_usage_bound'}:
                    raise ValueError('Completed study call lost its native payload or receipt')
            elif call['outcome_sha256'] is not None:
                raise ValueError('Non-completed call claims a reusable payload')
            if not isinstance(call['references'], list) or not call['references']:
                raise ValueError('Study call lost its owner cost reference')
            seen = set()
            for ref in call['references']:
                if (not isinstance(ref, dict) or set(ref) != {'arm_id', 'stage', 'request_sha256'}
                        or ref['arm_id'] not in arms or ref['request_sha256'] != call['request_sha256']
                        or not isinstance(ref['stage'], str) or not ref['stage']
                        or (ref['arm_id'], ref['stage']) in seen
                        or (ref['arm_id'], ref['stage']) in global_refs):
                    raise ValueError('Invalid or duplicated study cost reference')
                seen.add((ref['arm_id'], ref['stage']))
                global_refs.add((ref['arm_id'], ref['stage']))
            if {key: call[key] for key in ('arm_id', 'stage', 'request_sha256')} not in call['references']:
                raise ValueError('Study call lost its physical owner reference')
            calls[cid] = call
    except StudyBudgetStop as error:
        raise ValueError('Invalid native study accounting: ' + str(error)) from None
    records = {}
    supplied = list(call_records.values()) if isinstance(call_records, dict) else call_records or []
    if not isinstance(supplied, list):
        raise ValueError('Study call records must be a mapping or list')
    for record in supplied:
        if not isinstance(record, dict) or record.get('call_id') not in calls or record['call_id'] in records:
            raise ValueError('Unknown or duplicate physical call timing record')
        cid = record['call_id']
        _seconds(record.get('latency_seconds'), 'physical call')
        if record.get('request_sha256', calls[cid]['request_sha256']) != calls[cid]['request_sha256']:
            raise ValueError('Physical call timing request binding differs')
        if record.get('arm_id', calls[cid]['arm_id']) != calls[cid]['arm_id']:
            raise ValueError('Physical call timing owner differs')
        records[cid] = record
    physical = _usage_bucket(list(calls.values()), records)
    provider_usage = {p: _usage_bucket([c for c in calls.values() if c['provider'] == p], records) for p in providers}
    logical = {a: _usage_bucket([c for c in calls.values() if any(r['arm_id'] == a for r in c['references'])], records) for a in arms}
    native_providers = {p: {'attempts': row['attempts'], 'micro_usd_charged_or_reserved': row['micro_usd_charged_or_reserved']} for p, row in provider_usage.items()}
    native_arms = {a: {'attempts': row['attempts'], 'micro_usd_charged_or_reserved': row['micro_usd_charged_or_reserved']} for a, row in logical.items()}
    for observed, expected in ((snapshot.get('owned_attempts'), physical['attempts']),
            (snapshot.get('owned_micro_usd_charged_or_reserved'), physical['micro_usd_charged_or_reserved'])):
        if not _count(observed) or observed != expected:
            raise ValueError('Study budget totals differ from included physical calls')
    if _json(snapshot.get('by_provider')) != _json(native_providers) or _json(snapshot.get('arms')) != _json(native_arms):
        raise ValueError('Study budget arm/provider totals differ from included calls')
    return calls, {'physical_unique': physical, 'by_provider': provider_usage,
        'logical_by_cell': logical, 'logical_costs_are_additive_across_arms': False,
        'logical_by_arm': {arm: _usage_bucket([c for c in calls.values()
            if any(r['arm_id'].endswith(':' + arm) for r in c['references'])], records) for arm in ARMS},
        'is_provider_invoice': False, 'ledger_locked': snapshot['locked'],
        'ledger_stop_reasons': deepcopy(snapshot['stop_reasons'])}


def _results(spec, results):
    supplied = list(results.values()) if isinstance(results, dict) else results
    if not isinstance(supplied, list):
        raise ValueError('Study results must be a mapping or list')
    units = {row['unit_id']: row for row in spec['cases']}
    indexed = {}
    for row in supplied:
        if (not isinstance(row, dict) or not _RESULT_FIELDS <= set(row)
                or set(row) - _RESULT_FIELDS - _RESULT_OPTIONAL
                or row['schema'] != 'controller_study_arm_v1'
                or row['registration_sha256'] != spec['sha256']
                or row['unit_id'] not in units or row['arm_id'] not in ARMS
                or row['status'] not in {'completed', 'failed', 'unavailable'}):
            raise ValueError('Invalid controller study arm result')
        _sealed(row, 'Controller study arm')
        key = (row['unit_id'], row['arm_id'])
        if key in indexed:
            raise ValueError('Duplicate controller study arm result')
        if ((row['status'] == 'completed') != isinstance(row['pack'], dict)
                or row['status'] == 'completed' and row['failure'] is not None
                or row['status'] != 'completed' and (row['pack'] is not None or not isinstance(row['failure'], dict))):
            raise ValueError('Study completion/failure status differs from its native payload')
        _ids(row['call_ids'], 'call_ids'); _ids(row['owned_call_ids'], 'owned_call_ids')
        if not set(row['owned_call_ids']) <= set(row['call_ids']) or not isinstance(row['stage_refs'], list):
            raise ValueError('Study arm lost its owned-call or stage references')
        for field in ('pipeline_latency_seconds', 'preparation_seconds'):
            _seconds(row[field], field)
        measurement, owner = row['preparation_measurement_id'], row['preparation_owner_arm_id']
        if (measurement is not None and (not isinstance(measurement, str) or not measurement)
                or measurement is None and row['preparation_seconds'] is not None
                or owner is not None and owner not in ARMS and owner not in {row['unit_id'] + ':' + a for a in ARMS}
                or measurement is not None and owner is None):
            raise ValueError('Invalid shared preparation measurement')
        indexed[key] = row
    return indexed


def validate_results(spec, results):
    """Validate saved result shapes and declared measurements without IO."""
    return _results(spec, results)


def _summarize_study(spec, results, budget_snapshot, call_records=None):
    """Summarize included, validated study views without IO or quality labels.

    The CLI must independently require a full terminal seal before calling.
    Missing result cells are represented as not_attempted for API diagnostics;
    they remain in each arm's fixed denominator of five selected clusters.
    """
    _json([spec, results, budget_snapshot, call_records])
    _sealed(spec, 'Controller study registration')
    protocol = spec.get('protocol', {})
    arm_ids = protocol.get('arms', spec.get('arms'))
    if (spec.get('schema') != 'controller_study_registration_v1'
            or not isinstance(protocol, dict)
            or not _hash(spec.get('study_id')) or arm_ids != list(ARMS)
            or 'arms' in spec and spec['arms'] != list(ARMS)
            or 'case_count' in protocol and (type(protocol['case_count']) is not int or protocol['case_count'] != 5)
            or 'recipe' in protocol and _json(protocol['recipe']) != _json(RECIPE)
            or any(k in protocol and protocol[k] is not False for k in ('authority_granted', 'human_review', 'benchmark_evaluation'))
            or not isinstance(spec.get('cases'), list) or len(spec['cases']) != 5
            or any(not isinstance(row, dict) or not isinstance(row.get('unit_id'), str)
                   or not row['unit_id'] for row in spec['cases'])
            or len({row['unit_id'] for row in spec['cases']}) != 5):
        raise ValueError('Controller study requires exactly five selected case clusters and two arms')
    indexed = _results(spec, results)
    calls, usage = _budget(spec, budget_snapshot, call_records)
    cells, preparation = [], {}
    for case in spec['cases']:
        for arm in ARMS:
            unit = case['unit_id']; key = (unit, arm); row = indexed.get(key)
            logical_id = unit + ':' + arm
            referenced = sorted(c['call_id'] for c in calls.values() if any(r['arm_id'] == logical_id for r in c['references']))
            owned = sorted(c['call_id'] for c in calls.values() if c['arm_id'] == logical_id)
            metrics = None
            if row is not None:
                if ('study_id' in row and row['study_id'] != spec['study_id']
                        or 'source' in row and row['source'] != case.get('context')):
                    raise ValueError('Study arm result differs from its registered source')
                if (not set(row['call_ids']) <= set(referenced)
                        or not set(row['owned_call_ids']) <= set(owned)):
                    raise ValueError('Arm result cites a call owned or referenced by another cell')
                if row['status'] == 'completed':
                    pack = row['pack']
                    # Native assembly projects the graph to retrieved files and
                    # removes file text from the snapshot. Full descriptor hashes
                    # are admitted by the runner, not reconstructed from this
                    # deliberately smaller output projection.
                    context = context_binding(pack['case'], pack['snapshot'], pack['graph'])
                    if case.get('context') is not None and any(
                            context[k] != case['context'].get(k) for k in context
                            if k not in {'graph_sha256', 'snapshot_descriptor_sha256'}):
                        raise ValueError('Completed study pack differs from its selected original input')
                    metrics = _pack_metrics(pack, arm)
                    if 'stop_reason' in row and row['stop_reason'] != pack['stop_reason']:
                        raise ValueError('Study arm stop reason differs from its native pack')
                mid = row['preparation_measurement_id']
                if mid is not None:
                    value = {'measurement_id': mid, 'unit_id': unit,
                        'owner_arm_id': row['preparation_owner_arm_id'],
                        'seconds': row['preparation_seconds']}
                    if mid in preparation and _json(preparation[mid]) != _json(value):
                        raise ValueError('Shared preparation timing or owner changed between arm references')
                    preparation[mid] = value
            cells.append({'unit_id': unit, 'arm_id': arm,
                'status': row['status'] if row else 'not_attempted',
                'result_sha256': row['sha256'] if row else None,
                'native_pack_sha256': digest(row['pack']) if row and row['pack'] is not None else None,
                'workflow_plan_sha256': row['pack']['workflow_plan']['sha256'] if metrics is not None else None,
                'controller_decisions_sha256': row['pack'].get('controller_decisions', {}).get('sha256') if metrics is not None else None,
                'source': deepcopy(case.get('context')),
                'failure': deepcopy(row['failure']) if row else None,
                'stage_refs': deepcopy(row['stage_refs']) if row else [],
                'metrics': metrics, 'logical_usage': deepcopy(usage['logical_by_cell'][logical_id]),
                'referenced_call_ids': referenced, 'owned_call_ids': owned,
                'unreported_owned_call_ids': sorted(set(owned) - set(row['owned_call_ids'] if row else [])),
                'pipeline_latency_seconds': row['pipeline_latency_seconds'] if row else None,
                'preparation_measurement_id': row['preparation_measurement_id'] if row else None})
    by_arm = {}
    for arm in ARMS:
        rows = [row for row in cells if row['arm_id'] == arm]
        done = [row for row in rows if row['status'] == 'completed']
        by_arm[arm] = {'selected_case_denominator': 5,
            'status_counts': {s: sum(row['status'] == s for row in rows) for s in ('completed', 'failed', 'unavailable', 'not_attempted')},
            'completion_fraction_of_selected': len(done) / 5,
            'critic_coverage': {kind: sum(row['metrics']['critic_kind'] == kind for row in done) for kind in ('not_run', 'structural', 'llm_separate_pass')},
            'stop_reasons': dict(sorted(Counter(row['metrics']['stop_reason'] for row in done).items())),
            'completed_subset_sums': {key: sum(row['metrics'][key] for row in done) for key in (
                'retrieval_rounds', 'retrieval_query_count', 'executed_query_count', 'unavailable_query_count',
                'zero_yield_executed_query_count', 'zero_yield_retrieval_rounds', 'added_excerpt_count',
                'generation_passes', 'generation_repairs', 'decision_count', 'jev_selected_decisions',
                'fallback_decisions', 'selected_rule_agreement_count', 'fallback_rule_agreement_count')},
            'pipeline_active_timing': {'known_seconds_sum': sum(row['pipeline_latency_seconds'] for row in rows if row['pipeline_latency_seconds'] is not None),
                'unknown_arm_rows': sum(row['pipeline_latency_seconds'] is None for row in rows)}}
    paired = []
    for case in spec['cases']:
        pair = {row['arm_id']: row for row in cells if row['unit_id'] == case['unit_id']}
        if all(pair[a]['status'] == 'completed' for a in ARMS):
            paired.append({'unit_id': case['unit_id'], 'jev_minus_rules': {
                key: pair['jev']['metrics'][key] - pair['rules']['metrics'][key] for key in (
                    'evidence_count', 'code_file_count', 'retrieval_query_count',
                    'added_excerpt_count', 'generation_passes', 'critic_review_count')}})
    result = {'schema': SCHEMA, 'study_id': spec['study_id'], 'registration_sha256': spec['sha256'],
        'selected_case_clusters': 5, 'selected_arm_rows': 10, 'arms': by_arm, 'cells': cells,
        'paired_completed_subset': {'case_cluster_count': len(paired), 'selected_case_denominator': 5,
            'availability_fraction': len(paired) / 5, 'rows': paired},
        'usage': usage,
        'preparation_timing': {'unique_measurements': len(preparation),
            'known_seconds_sum': sum(row['seconds'] for row in preparation.values() if row['seconds'] is not None),
            'unknown_measurements': sum(row['seconds'] is None for row in preparation.values()),
            'arm_rows_without_measurement': sum(row['preparation_measurement_id'] is None for row in cells),
            'measurements': list(preparation.values()), 'references_are_additive': False},
        'limitations': ['Five selected case clusters and ten arm rows are not independent samples.',
            'Completed-pair contrasts describe the available subset; availability remains separate.',
            'Rule agreement describes observed states, not action accuracy or counterfactual trajectories.',
            'STOP is a controller action, not a solved-issue label.',
            'Retrieved excerpts and counter citations are not confirmed counterproof.',
            'Citation integrity is structural; in-treatment critics are not a common semantic-quality judge.',
            'Hypothesis truth, action accuracy and developer benefit are not computed.',
            'Missing directional token observations and latency remain unknown.',
            'Call latency measures active provider-wrapper time, not pure network latency.',
            'Charged/reserved USD is local receipt accounting, not a reconciled provider invoice.'],
        'independent_human_validation': False, 'semantic_truth_computed': False,
        'action_accuracy_computed': False, 'developer_benefit_computed': False}
    _json(result)
    result['sha256'] = digest(result)
    return result


def summarize_study(spec, results, budget_snapshot, call_records=None):
    """Pure descriptive metrics; admission and the full terminal seal are external.

    Results may be a list or mapping of controller_study_arm_v1 dictionaries.
    Optional call_records is a list or mapping of physical records with call_id,
    latency_seconds and, when present, matching request_sha256/arm_id bindings.
    """
    try:
        return _summarize_study(spec, results, budget_snapshot, call_records)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, UnicodeError):
        raise ValueError('Invalid controller study scoring structure') from None
