"""Gold-free common model assessment of a sealed strict Main Dev comparison.

This is an external adapter: inference imports still come from the frozen root.
It never changes an inference pack, reuses an in-treatment critic as a fresh
judge, or turns model assessments into human or causal truth labels.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import date
from hashlib import sha256
import importlib
import importlib.util
from itertools import combinations
import json
import math
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
import time


PLAN_SCHEMA = 'component_grounding_plan_v1'
AUTHORIZATION_SCHEMA = 'component_grounding_run_authorization_v1'
RECORD_SCHEMA = 'component_grounding_unit_v1'
SEAL_SCHEMA = 'component_grounding_seal_v1'
SCORE_SCHEMA = 'component_grounding_score_v1'
SCOPE = 'accepted_original_main_dev_component_grounding_v1'
ARMS = ('lexical_symbol', 'plus_graph', 'plus_history', 'plus_verifier', 'plus_counter_search')
LABELS = ('supported', 'unsupported', 'contradicted', 'uncertain')
JUDGE_CONFIG = {'provider': 'groq', 'model': 'openai/gpt-oss-120b',
                'reasoning_effort': 'medium', 'max_output_tokens': 2048}
_HASH = re.compile('[a-f0-9]{64}')
_CLOSED = {'settled', 'failed_bound', 'missing_usage_bound'}


def _extension(name):
    qualified = 'issueintel.benchmarks.' + name
    try:
        return importlib.import_module(qualified)
    except ModuleNotFoundError as error:
        if error.name != qualified:
            raise
    alias = '_issueintel_' + name
    if alias not in sys.modules:
        spec = importlib.util.spec_from_file_location(alias, Path(__file__).with_name(name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(alias, None)
            raise
    return sys.modules[alias]


def _core():
    return _extension('component_continuation')


def _parent():
    return _extension('component_continuation_run')


def _digest(value):
    return _core().digest(value)


def _seal(value):
    return _core().seal(value)


def _canonical(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _sources(parent):
    paths = [Path(__file__).resolve()]
    for name in ('issueintel.investigation.verifier', 'issueintel.annotations.public_inputs',
                 'issueintel.annotations.structured'):
        paths.append(Path(importlib.import_module(name).__file__).resolve())
    paths.extend(Path(row['path']) for row in parent['plan']['adapter_sources'])
    rows = []
    for path in sorted(set(paths)):
        raw = _core()._raw(Path(path.anchor), path, private=False, max_bytes=4 * 1024 * 1024)
        rows.append({'path': str(path), 'sha256': sha256(raw).hexdigest()})
    return rows


def _parent_state(root, identifier, parent_loader=None):
    loader = parent_loader or _parent().load_sealed_continuation
    state = loader(Path(root).resolve(), identifier)
    for name in ('registration', 'plan', 'seal'):
        _core()._check_seal(state[name])
    spec, plan, seal = (state[key] for key in ('registration', 'plan', 'seal'))
    ids = [row['case_id'] for row in spec['cases']]
    expected = {(cid, arm) for cid in ids for arm in ARMS}
    if (len(ids) != 25 or len(set(ids)) != 25
            or any(not isinstance(cid, str) or not cid.startswith('verified:') for cid in ids)
            or [row['arm_id'] for row in spec['arms']] != list(ARMS)
            or any(spec.get('runtime', {}).get(key) != JUDGE_CONFIG[key]
                   for key in ('model', 'reasoning_effort', 'max_output_tokens'))
            or plan['sha256'] != identifier or plan['registration_sha256'] != spec['sha256']
            or plan['case_ids'] != ids or seal.get('schema') != 'component_continuation_inference_seal_v2'
            or seal.get('continuation_plan_sha256') != identifier
            or seal.get('registration_sha256') != spec['sha256']
            or seal.get('selected_case_ids') != ids or seal.get('arms') != list(ARMS)
            or seal.get('terminal_results') != 125 or seal.get('gold_read') is not False
            or seal.get('quality_claim') is not False or seal.get('holdout_or_live_access') is not False
            or len(state['results']) != 125
            or {(row['case_id'], row['arm_id']) for row in state['results']} != expected
            or seal.get('effective_results') != {row['case_id'] + '/' + row['arm_id']: row['sha256']
                                                for row in state['results']}):
        raise ValueError('Common grounding requires the exact full 125-cell native inference seal')
    for row in state['results']:
        _core()._check_seal(row)
        if row['status'] not in {'completed', 'failed', 'unavailable'}:
            raise ValueError('Grounding cannot admit unfinished inference')
        if row['status'] == 'completed':
            pack = row.get('pack')
            if (not isinstance(pack, dict) or pack.get('case', {}).get('case_id') != row['case_id']
                    or pack['case'].get('split') != 'main_dev'
                    or pack.get('leakage_audit', {}).get('gold_in_inference') is not False
                    or pack.get('leakage_audit', {}).get('strict_issue_time_verified') is not True):
                raise ValueError('Common grounding requires accepted original strict Main Dev packs')
        elif row.get('pack') is not None:
            raise ValueError('Failed inference cannot supply a grounding pack')
    if seal.get('completed_results') != sum(row['status'] == 'completed' for row in state['results']):
        raise ValueError('Parent inference completion count changed')
    return state


class _CapturedRequest(Exception):
    pass


def _wire_request(messages, schema):
    # The native hypothesis projection iterates a set. Object field ordering is
    # not a treatment; declare one stable rendering across Python hash seeds.
    messages = deepcopy(messages)
    for message in messages:
        if message.get('role') == 'user':
            payload = json.loads(message['content'])
            if not isinstance(payload, dict):
                raise ValueError('Common judge user input must be one JSON object')
            message['content'] = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':'), allow_nan=False)
    return {'messages': messages, 'schema': deepcopy(schema), **JUDGE_CONFIG}


def _project(pack):
    verifier = importlib.import_module('issueintel.investigation.verifier')
    inputs = importlib.import_module('issueintel.annotations.public_inputs')
    # Check hidden fields before dropping any local metadata.
    verifier.verify_hypotheses(pack['annotation'], pack['evidence'])
    payload = _canonical({'hypotheses': inputs.model_hypotheses(pack['annotation']['hypotheses']),
                          'evidence': inputs.model_evidence(pack['evidence'])})
    return payload, _request(payload)


def _request(payload):
    captured = []
    class Capture:
        def complete_structured(self, messages, *, max_output_tokens, schema):
            captured.append(_wire_request(messages, schema))
            if max_output_tokens != JUDGE_CONFIG['max_output_tokens']:
                raise ValueError('Frozen common judge output bound changed')
            raise _CapturedRequest
    try:
        importlib.import_module('issueintel.investigation.verifier').verify_hypotheses(
            {'hypotheses': payload['hypotheses']}, payload['evidence'], Capture(), max_output_tokens=2048)
    except _CapturedRequest:
        pass
    if len(captured) != 1:
        raise ValueError('Frozen common judge did not expose one structured request')
    return captured[0]


def _make_plan(state):
    rows = {(row['case_id'], row['arm_id']): row for row in state['results']}
    ids = [row['case_id'] for row in state['registration']['cases']]
    units, bindings = {}, []
    for cid in ids:
        for arm in ARMS:
            row = rows[(cid, arm)]
            unit_id = None
            if row['status'] == 'completed':
                payload, request = _project(row['pack'])
                unit_id = _digest(request)
                if unit_id not in units:
                    units[unit_id] = {'unit_id': unit_id, 'request': request, 'payload': payload, 'case_ids': []}
                elif units[unit_id]['payload'] != payload:
                    raise ValueError('Common judge request identity has different projected data')
                if cid not in units[unit_id]['case_ids']:
                    units[unit_id]['case_ids'].append(cid)
            bindings.append({'case_id': cid, 'arm_id': arm, 'arm_sha256': row['sha256'],
                             'source_status': row['status'], 'unit_id': unit_id})
    if len(units) > 125 or len([unit for unit in units.values() if ids[0] in unit['case_ids']]) > 5:
        raise ValueError('Common judge unit bounds changed')
    return _seal({'schema': PLAN_SCHEMA, 'scope': SCOPE, 'parent_plan_sha256': state['plan']['sha256'],
        'parent_inference_seal_sha256': state['seal']['sha256'],
        'registration_sha256': state['registration']['sha256'], 'case_ids': ids, 'arms': list(ARMS),
        'judge_config': deepcopy(JUDGE_CONFIG), 'sources': _sources(state),
        'native_fingerprint': state['registration']['fingerprint'],
        'parent_inventory': state['seal'].get('files', {}),
        'parent_receipt_ids': sorted({rid for call in state.get('calls', []) for rid in call['receipts']}),
        'arm_bindings': bindings, 'units': list(units.values()), 'max_provider_calls_total': len(units),
        'fresh_common_judge': True, 'reuses_original_critic': False, 'canonical_user_json': True,
        'gold_read': False, 'independent_human': False, 'semantic_accuracy_verified': False,
        'causal_truth_verified': False})


def create_grounding_plan(root, parent_plan_id, *, parent_loader=None):
    return _make_plan(_parent_state(root, parent_plan_id, parent_loader))


def grounding_directory(root, plan):
    return _core().safe_path(root, Path('artifacts/experiments/component_grounding') / plan['sha256'], missing=True)


def _validate_plan(root, plan, parent_loader=None):
    _core()._check_seal(plan)
    if plan.get('schema') != PLAN_SCHEMA or plan.get('scope') != SCOPE:
        raise ValueError('Unsupported common grounding plan')
    state = _parent_state(root, plan['parent_plan_sha256'], parent_loader)
    if plan != _make_plan(state):
        raise ValueError('Common grounding source, seal, projection or unit binding changed')
    return state


def save_grounding_plan(root, plan, *, parent_loader=None):
    _validate_plan(root, plan, parent_loader)
    return _core().write_new(root, grounding_directory(root, plan) / 'plan.json', plan)


def load_grounding_plan(root, identifier, *, parent_loader=None):
    if isinstance(identifier, str) and _HASH.fullmatch(identifier):
        identifier = Path('artifacts/experiments/component_grounding') / identifier / 'plan.json'
    plan = _core().read(root, identifier)
    _validate_plan(root, plan, parent_loader)
    return plan, grounding_directory(root, plan)


def _selected_units(plan, count):
    cases = set(plan['case_ids'][:count])
    return [unit for unit in plan['units'] if cases.intersection(unit['case_ids'])]


def verify_grounding_authorization(root, plan, authorization_path, *, requested_cases):
    value = _core()._parse(_core()._raw(root, authorization_path))
    return _validate_authorization(value, plan, requested_cases=requested_cases)


def _validate_authorization(value, plan, *, requested_cases):
    if type(requested_cases) is not int or requested_cases not in {1, 25}:
        raise ValueError('Grounding authorizes the first case or the registered 25-case matrix')
    fields = {'schema', 'scope', 'plan_sha256', 'parent_plan_sha256', 'parent_inference_seal_sha256',
        'registration_sha256', 'case_ids', 'unit_ids', 'provider', 'model', 'reasoning_effort',
        'max_output_tokens', 'authorized_case_count', 'phase', 'max_provider_calls_total',
        'execution_authorized', 'gold_access_authorized', 'holdout_or_live_access',
        'jev_spending_authorized', 'author_request', 'date'}
    count = value.get('authorized_case_count') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or set(value) != fields or value['schema'] != AUTHORIZATION_SCHEMA
            or value['scope'] != SCOPE or type(count) is not int or count not in {1, 25}
            or requested_cases > count or value['plan_sha256'] != plan['sha256']
            or any(value[key] != plan[key] for key in ('parent_plan_sha256', 'parent_inference_seal_sha256',
                                                      'registration_sha256', 'case_ids'))
            or value['unit_ids'] != [unit['unit_id'] for unit in plan['units']]
            or any(value[key] != JUDGE_CONFIG[key] for key in JUDGE_CONFIG)
            or type(value['max_output_tokens']) is not int
            or type(value['max_provider_calls_total']) is not int
            or value['max_provider_calls_total'] != len(_selected_units(plan, count))
            or value['phase'] != ('first_case_pilot' if count == 1 else 'registered_matrix')
            or value['execution_authorized'] is not True
            or any(value[key] is not False for key in ('gold_access_authorized', 'holdout_or_live_access', 'jev_spending_authorized'))
            or not isinstance(value['author_request'], str) or not value['author_request'].strip()
            or len(value['author_request']) > 8000):
        raise ValueError('Exact common grounding phase authorization is required')
    try:
        date.fromisoformat(value['date'])
    except (ValueError, TypeError):
        raise ValueError('Common grounding authority requires a dated decision') from None
    return value


def _write_unit(root, path, value, *, new=False):
    """Only this study's open intent may be atomically closed or receipted."""
    if new:
        return _core().write_new(root, path, value)
    _core()._check_seal(value)
    path = _core().safe_path(root, path)
    if path.parent.name != 'units' or path.parents[2].name != 'component_grounding':
        raise ValueError('Common grounding writes belong to owned unit journals')
    previous = _core().read(root, path)
    if previous['status'] != 'intent':
        if previous != value:
            raise ValueError('A closed common grounding unit cannot be replaced')
        return path
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()
    if len(raw) > _core().MAX_FILE_BYTES:
        raise ValueError('Common grounding journal exceeds byte bound')
    temporary = None
    try:
        with NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    _core().safe_path(root, path)
    return path


def _native_result(unit, complete):
    return importlib.import_module('issueintel.investigation.verifier').verify_hypotheses(
        {'hypotheses': unit['payload']['hypotheses']}, unit['payload']['evidence'], complete, max_output_tokens=2048)


def _validate_record(record, plan, unit, *, ledger=None, require_closed=True):
    _core()._check_seal(record)
    fields = {'schema', 'plan_sha256', 'unit_id', 'call_id', 'request_sha256', 'status', 'receipts',
              'response', 'response_sha256', 'verification', 'failure', 'latency_seconds', 'sha256'}
    if (set(record) != fields or record['schema'] != RECORD_SCHEMA
            or record['plan_sha256'] != plan['sha256'] or record['unit_id'] != unit['unit_id']
            or record['call_id'] != _digest([plan['sha256'], unit['unit_id']])
            or record['request_sha256'] != _digest(unit['request'])
            or record['status'] not in {'intent', 'reviewed', 'failed'}
            or not isinstance(record['receipts'], dict) or len(record['receipts']) > 1):
        raise ValueError('Common grounding unit ownership changed')
    native = _parent()._api()[1]
    for rid, receipt in record['receipts'].items():
        native._receipt(receipt, JUDGE_CONFIG['model'])
        if (rid != receipt['reservation_id'] or receipt['output_bound'] != 2048
                or receipt['input_bound'] + receipt['output_bound'] > 131072
                or ledger is not None and ledger.receipt(rid) != receipt):
            raise ValueError('Common grounding receipt differs from its owner or ledger')
    if require_closed and (record['status'] == 'intent' or not record['receipts']
                          or any(row['status'] not in _CLOSED for row in record['receipts'].values())):
        raise ValueError('Common grounding paid outcome remains unknown; no automatic retry')
    if record['status'] != 'intent' and (type(record['latency_seconds']) not in (int, float)
            or not math.isfinite(record['latency_seconds']) or record['latency_seconds'] < 0):
        raise ValueError('Invalid common grounding active timing')
    if record['status'] == 'intent' and (record['verification'] is not None or record['failure'] is not None
                                         or record['latency_seconds'] is not None):
        raise ValueError('An open unit cannot claim a terminal result')
    if record['status'] == 'failed' and (not isinstance(record['failure'], dict)
            or not isinstance(record['failure'].get('error_type'), str)
            or not record['failure']['error_type'] or len(record['failure']['error_type']) > 128):
        raise ValueError('A failed unit must retain its bounded failure classification')
    response = record['response']
    if (response is None) != (record['response_sha256'] is None) or response is not None and _digest(response) != record['response_sha256']:
        raise ValueError('Common grounding raw response changed')
    if record['status'] == 'reviewed':
        if (len(record['receipts']) != 1 or any(receipt['status'] not in {'settled', 'missing_usage_bound'}
                                             for receipt in record['receipts'].values())):
            raise ValueError('A reviewed unit requires one returned-compatible closed receipt')
        importlib.import_module('issueintel.annotations.structured')._validate_wire_schema(response, unit['request']['schema'])
        class Replay:
            def complete_structured(self, messages, *, max_output_tokens, schema):
                if _wire_request(messages, schema) != unit['request']:
                    raise ValueError('Frozen native common judge request changed')
                return deepcopy(response)
        if record['failure'] is not None or _native_result(unit, Replay()) != record['verification']:
            raise ValueError('Common grounding result differs from its native response')
    elif record['verification'] is not None:
        raise ValueError('An incomplete common judge cannot claim a native review')
    return record


def _records(root, plan, *, ledger=None, require_closed=True):
    directory = grounding_directory(root, plan)
    units = {unit['unit_id']: unit for unit in plan['units']}
    found, reservations = {}, set(plan['parent_receipt_ids'])
    files = _core().inventory(root, directory)
    metadata, intents, score_files = {}, [], []
    closed = require_closed or 'study-sealed.json' in files
    for name in files:
        parts = Path(name).parts
        if parts[0] != 'units':
            value = _core().read(root, directory / name)
            if len(parts) == 1 and name in {'plan.json', 'pilot-gate.json', 'study-sealed.json'}:
                metadata[name] = value
            elif (len(parts) == 2 and parts[0] in {'execution-intents', 'scores'}
                    and parts[1] == value['sha256'] + '.json'):
                (intents if parts[0] == 'execution-intents' else score_files).append(value)
            else:
                raise ValueError('Unexpected or unowned common grounding study inventory')
            continue
        if (len(parts) != 2 or not parts[1].endswith('.json')
                or parts[1][:-5] not in units):
            raise ValueError('Unexpected common grounding unit file')
        unit_id = parts[1][:-5]
        if parts[1] != unit_id + '.json' or unit_id in found:
            raise ValueError('Common grounding unit has duplicate or aliased filenames')
        record = _validate_record(_core().read(root, directory / name), plan, units[unit_id],
                                  ledger=ledger, require_closed=closed)
        for rid in record['receipts']:
            if rid in reservations:
                raise ValueError('Common grounding receipt has multiple owners or reuses inference cost')
            reservations.add(rid)
        found[unit_id] = record
    if metadata.get('plan.json') != plan:
        raise ValueError('The study inventory must retain its exact immutable plan')
    covered = set()
    for intent in intents:
        fields = {'schema', 'plan_sha256', 'requested_case_count', 'authorization',
                  'authorization_sha256', 'gold_read', 'sha256'}
        if (set(intent) != fields or intent['schema'] != 'component_grounding_execution_intent_v1'
                or intent['plan_sha256'] != plan['sha256'] or intent['gold_read'] is not False
                or intent['authorization_sha256'] != _digest(intent['authorization'])):
            raise ValueError('Common grounding execution intent changed')
        _validate_authorization(intent['authorization'], plan, requested_cases=intent['requested_case_count'])
        if intent['requested_case_count'] == 25 and 'pilot-gate.json' not in metadata:
            raise ValueError('Matrix execution intent lacks its reviewed pilot gate')
        covered.update(unit['unit_id'] for unit in _selected_units(plan, intent['requested_case_count']))
    if set(found) - covered:
        raise ValueError('Common grounding unit has no owning execution intent')
    if 'pilot-gate.json' in metadata and metadata['pilot-gate.json'] != _pilot_gate(plan, found):
        raise ValueError('Common grounding pilot gate changed')
    if 'study-sealed.json' in metadata and metadata['study-sealed.json'] != _study_seal(root, plan, found):
        raise ValueError('Common grounding study seal changed')
    if score_files:
        if 'study-sealed.json' not in metadata:
            raise ValueError('Common grounding score requires a sealed study')
        expected = _seal(summarize_grounding(plan, found))
        if any(score != expected for score in score_files):
            raise ValueError('A score namespace may contain only the deterministic common grounding score')
    return found


def _fast_guard(root, plan, state, unit, context_guard=None):
    if _sources(state) != plan['sources']:
        raise ValueError('Executing common judge or frozen native source changed')
    if context_guard is not None:
        context_guard(root, plan, state, unit)
        if _sources(state) != plan['sources']:
            raise ValueError('Common judge source changed during current-case guard')
        return
    parent = _parent()
    parent._core().validate_plan(root, state['plan'], full_replay=False)
    directory = state['directory']
    if (parent._core().read(root, directory / 'inference-sealed.json') != state['seal']
            or parent._inference_inventory(root, directory) != plan['parent_inventory']):
        raise ValueError('Immutable parent inference inventory changed')
    registration, native = parent._api()
    sources = registration.load_history_sources(root, state['registration'], state['base_directory'])
    rows = {row['case_id']: row for row in state['registration']['cases']}
    for cid in unit['case_ids']:
        native._replay_context(root, state['registration'], rows[cid], sources[cid])
    parent._core().validate_plan(root, state['plan'], full_replay=False)
    if (_sources(state) != plan['sources']
            or parent._core().read(root, directory / 'inference-sealed.json') != state['seal']
            or parent._inference_inventory(root, directory) != plan['parent_inventory']):
        raise ValueError('Frozen judge or parent inference changed during current-case replay')


def _pilot_gate(plan, records):
    pilot = _selected_units(plan, 1)
    if not pilot or any(records.get(unit['unit_id'], {}).get('status') != 'reviewed' for unit in pilot):
        raise ValueError('All common grounding first-case pilot units must be reviewed before matrix expansion')
    return _seal({'schema': 'component_grounding_pilot_gate_v1', 'plan_sha256': plan['sha256'],
        'units': {unit['unit_id']: records[unit['unit_id']]['sha256'] for unit in pilot}, 'gold_read': False})


def _study_inventory(root, plan):
    return {name: value for name, value in _core().inventory(root, grounding_directory(root, plan)).items()
            if name != 'study-sealed.json' and Path(name).parts[0] != 'scores'}


def _study_seal(root, plan, records):
    if set(records) != {unit['unit_id'] for unit in plan['units']}:
        raise ValueError('All frozen common judge units must be terminal before sealing')
    _pilot_gate(plan, records)
    return _seal({'schema': SEAL_SCHEMA, 'plan_sha256': plan['sha256'],
        'parent_inference_seal_sha256': plan['parent_inference_seal_sha256'],
        'unit_results': {uid: row['sha256'] for uid, row in records.items()},
        'files': _study_inventory(root, plan), 'gold_read': False, 'independent_human': False})


def run_grounding(root, identifier, authorization_path, *, limit=1, provider_factory=None,
                  config_factory=None, parent_loader=None, context_guard=None, load_environment=False):
    if type(limit) is not int or limit not in {1, 25}:
        raise ValueError('Run the first-case common judge pilot or the exact 25-case matrix')
    root = Path(root).resolve()
    plan, directory = load_grounding_plan(root, identifier, parent_loader=parent_loader)
    authority = verify_grounding_authorization(root, plan, authorization_path, requested_cases=limit)
    state = _parent_state(root, plan['parent_plan_sha256'], parent_loader)
    native = _parent()._api()[1]
    stopped = None
    with native._lock(root, {'component_grounding': plan['sha256']}):
        records = _records(root, plan)
        seal_path = directory / 'study-sealed.json'
        if seal_path.exists():
            if _core().read(root, seal_path) != _study_seal(root, plan, records):
                raise ValueError('Sealed common grounding study changed')
            return _progress(plan, directory, records, None)
        if limit == 25:
            _core().write_new(root, directory / 'pilot-gate.json', _pilot_gate(plan, records))
        intent = _seal({'schema': 'component_grounding_execution_intent_v1', 'plan_sha256': plan['sha256'],
            'requested_case_count': limit, 'authorization': authority, 'authorization_sha256': _digest(authority),
            'gold_read': False})
        _core().write_new(root, directory / 'execution-intents' / (intent['sha256'] + '.json'), intent)
        selected = _selected_units(plan, limit)
        if selected:
            _fast_guard(root, plan, state, selected[0], context_guard)
        if load_environment:
            importlib.import_module('issueintel.runtime.io').load_local_env(root / '.env')
        factory = config_factory or importlib.import_module('issueintel.runtime.config').RuntimeConfig.load
        config = replace(factory(root), **{key: value for key, value in JUDGE_CONFIG.items() if key != 'provider'})
        ledger = config.ledger()
        _records(root, plan, ledger=ledger)
        for unit in selected:
            uid = unit['unit_id']
            if uid in records:
                continue
            _fast_guard(root, plan, state, unit, context_guard)
            if verify_grounding_authorization(root, plan, authorization_path, requested_cases=limit) != authority:
                raise ValueError('Common grounding authority changed during execution')
            records = _records(root, plan, ledger=ledger)
            if len(records) >= len(selected) or len(records) >= plan['max_provider_calls_total']:
                raise ValueError('Common judge call bound includes every previous owned attempt')
            path = directory / 'units' / (uid + '.json')
            record = {'schema': RECORD_SCHEMA, 'plan_sha256': plan['sha256'], 'unit_id': uid,
                'call_id': _digest([plan['sha256'], uid]), 'request_sha256': _digest(unit['request']),
                'status': 'intent', 'receipts': {}, 'response': None, 'response_sha256': None,
                'verification': None, 'failure': None, 'latency_seconds': None}
            _write_unit(root, path, _seal(record), new=True)
            started = time.monotonic()
            class Complete:
                calls = 0
                def complete_structured(self, messages, *, max_output_tokens, schema):
                    self.calls += 1
                    if self.calls != 1 or _wire_request(messages, schema) != unit['request']:
                        raise ValueError('Common judge can send only its one frozen native request')
                    _fast_guard(root, plan, state, unit, context_guard)
                    if verify_grounding_authorization(root, plan, authorization_path, requested_cases=limit) != authority:
                        raise ValueError('Common grounding authority changed before provider construction')
                    def on_receipt(receipt):
                        native._receipt(receipt, config.model)
                        if record['receipts'] and receipt['reservation_id'] not in record['receipts']:
                            raise ValueError('One common judge HTTP call cannot own two reservations')
                        if receipt['reservation_id'] in set(plan['parent_receipt_ids']) | {
                                rid for saved in records.values() for rid in saved['receipts']}:
                            raise ValueError('Common judge cannot reuse an owned inference or judge reservation')
                        record['receipts'][receipt['reservation_id']] = deepcopy(receipt)
                        _write_unit(root, path, _seal(record))
                    provider = (provider_factory or native.GroqProvider)(config, on_receipt=on_receipt,
                        call_context={'category': 'ARTIFACT_GENERATION_COST', 'stage': 'component_grounding_judge',
                                      'reasoning_effort': 'medium', 'diagnostic': True})
                    response = provider.complete_structured(unit['request']['messages'],
                        max_output_tokens=max_output_tokens, schema=unit['request']['schema'])
                    record.update(response=deepcopy(response), response_sha256=_digest(response))
                    _write_unit(root, path, _seal(record))
                    importlib.import_module('issueintel.annotations.structured')._validate_wire_schema(response, unit['request']['schema'])
                    return response
            try:
                verification = _native_result(unit, Complete())
                _fast_guard(root, plan, state, unit, context_guard)
                if verify_grounding_authorization(root, plan, authorization_path, requested_cases=limit) != authority:
                    raise ValueError('Common grounding authority changed before result publication')
                record.update(status='reviewed', verification=verification,
                              latency_seconds=round(time.monotonic() - started, 6))
                _validate_record(_seal(record), plan, unit, ledger=ledger)
                _write_unit(root, path, _seal(record))
            except Exception as error:
                record.update(status='failed', verification=None, failure=native.failure_metadata(error),
                              latency_seconds=round(time.monotonic() - started, 6))
                _write_unit(root, path, _seal(record))
                closed = bool(record['receipts']) and all(row['status'] in _CLOSED for row in record['receipts'].values())
                if not closed or record['response'] is None:
                    stopped = 'unknown_or_transport_outcome_no_automatic_retry'
            records[uid] = _seal(record)
            if stopped:
                break
        # Full parent native replay is an admission/final boundary, not repeated
        # 125 times. Per-unit guards still replay every referenced current case.
        _validate_plan(root, plan, parent_loader)
        records = _records(root, plan, require_closed=False)
        if not stopped and limit == 25 and len(records) == len(plan['units']):
            _records(root, plan)
            _core().write_new(root, directory / 'study-sealed.json', _study_seal(root, plan, records))
        return _progress(plan, directory, records, stopped)


def _progress(plan, directory, records, stopped):
    return {'schema': 'component_grounding_progress_v1', 'plan_sha256': plan['sha256'],
        'completed_units': sum(row['status'] == 'reviewed' for row in records.values()),
        'failed_units': sum(row['status'] == 'failed' for row in records.values()),
        'provider_calls': len(records), 'study_sealed': (directory / 'study-sealed.json').exists(),
        'stop_reason': stopped, 'gold_read': False, 'independent_human': False}


def inspect_grounding(root, identifier, *, parent_loader=None):
    plan, directory = load_grounding_plan(root, identifier, parent_loader=parent_loader)
    records = _records(root, plan, require_closed=False)
    return {'schema': 'component_grounding_inspection_v1', 'plan_sha256': plan['sha256'],
        'unique_judge_units': len(plan['units']), 'completed_units': sum(row['status'] == 'reviewed' for row in records.values()),
        'failed_units': sum(row['status'] == 'failed' for row in records.values()),
        'unknown_units': sum(row['status'] == 'intent' or not row['receipts'] or any(
            receipt['status'] not in _CLOSED for receipt in row['receipts'].values()) for row in records.values()),
        'study_sealed': (directory / 'study-sealed.json').exists(),
        'provider_or_ledger_constructed': False, 'gold_read': False}


def _usage(records):
    receipts = [row for record in records.values() for row in record['receipts'].values()]
    settled = [row for row in receipts if row['status'] == 'settled']
    return {'unique_reservations': len(receipts), 'status_counts': dict(Counter(row['status'] for row in receipts)),
        'tokens_charged_or_reserved': sum(row['tokens_charged_or_reserved'] for row in receipts),
        'micro_usd_charged_or_reserved': sum(row['micro_usd_charged_or_reserved'] for row in receipts),
        'settled_micro_usd': sum(row['micro_usd_charged_or_reserved'] for row in settled),
        'unsettled_or_unknown_micro_usd_bound': sum(row['micro_usd_charged_or_reserved'] for row in receipts if row['status'] != 'settled'),
        'observed_input_tokens': sum(row['observed_input_tokens'] for row in receipts if row['observed_input_tokens'] is not None),
        'observed_output_tokens': sum(row['observed_output_tokens'] for row in receipts if row['observed_output_tokens'] is not None),
        'provider_invoice_verified': False}


def _timing(records):
    values, unknown = [], 0
    for record in records:
        value = record['latency_seconds']
        if value is None:
            unknown += 1
        elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Common grounding timing is not a finite observed duration')
        else:
            values.append(value)
    total = round(math.fsum(values), 6)
    return {'observed_units': len(values), 'unknown_units': unknown, 'observed_seconds': total,
        'mean_seconds': round(total / len(values), 6) if values else None,
        'minimum_seconds': min(values) if values else None, 'maximum_seconds': max(values) if values else None,
        'interpretation': 'active_unit_duration_including_wrapper_source_guards_and_journal_io_not_pure_network'}


def summarize_grounding(plan, records):
    by_arm, fractions, arm_results = {}, {}, []
    for arm in ARMS:
        selected = [row for row in plan['arm_bindings'] if row['arm_id'] == arm]
        counts, source_counts, per_case = Counter(), Counter(), {}
        for row in selected:
            source_counts[row['source_status']] += 1
            result = records.get(row['unit_id']) if row['unit_id'] else None
            labels = Counter(review['assessment'] for review in result['verification']['reviews']) if result and result['status'] == 'reviewed' else Counter()
            if labels:
                counts.update(labels)
                per_case[row['case_id']] = {label: labels[label] / sum(labels.values()) for label in LABELS}
            arm_results.append({**row, 'judge_status': result['status'] if result else 'source_unavailable' if row['unit_id'] is None else 'not_attempted',
                'assessment_counts': {label: labels[label] for label in LABELS},
                'shared_unit_latency_seconds': result['latency_seconds'] if result else None})
        fractions[arm] = per_case
        referenced = {row['unit_id']: records[row['unit_id']] for row in selected if row['unit_id'] in records}
        by_arm[arm] = {'selected_cases': 25, 'reviewed_cases': len(per_case),
            'source_unavailable_cases': sum(row['unit_id'] is None for row in selected),
            'source_status_counts': dict(source_counts),
            'judge_failed_cases': sum(records.get(row['unit_id'], {}).get('status') == 'failed' for row in selected),
            'judge_not_attempted_cases': sum(row['unit_id'] is not None and row['unit_id'] not in records for row in selected),
            'judge_unknown_cases': sum(records.get(row['unit_id'], {}).get('status') == 'intent' for row in selected),
            'reviewed_without_assessment_cases': sum(records.get(row['unit_id'], {}).get('status') == 'reviewed'
                and not records[row['unit_id']]['verification']['reviews'] for row in selected),
            'referenced_unique_units': len(referenced), 'referenced_unit_latency': _timing(referenced.values()),
            'reference_timing_is_additive_across_arms': False,
            'assessment_counts': {label: counts[label] for label in LABELS}, 'uncertain_hypotheses': counts['uncertain'],
            'macro_case_fractions': {label: sum(item[label] for item in per_case.values()) / len(per_case) if per_case else None for label in LABELS},
            'macro_denominator': 'reviewed_cases_only_selected_denominator_reported_separately'}
    paired = []
    for left, right in combinations(ARMS, 2):
        ids = [cid for cid in plan['case_ids'] if cid in fractions[left] and cid in fractions[right]]
        paired.append({'left_arm': left, 'right_arm': right, 'common_reviewed_cases': len(ids), 'case_ids': ids,
            'mean_fraction_difference_right_minus_left': {label: sum(fractions[right][cid][label] - fractions[left][cid][label]
                for cid in ids) / len(ids) if ids else None for label in LABELS}})
    return {'schema': SCORE_SCHEMA, 'kind': 'automatic_model_assessment', 'plan_sha256': plan['sha256'],
        'case_clusters': 25, 'arm_results': len(arm_results), 'cases': arm_results, 'unique_judge_units': len(plan['units']),
        'completed_units': sum(row['status'] == 'reviewed' for row in records.values()),
        'failed_units': sum(row['status'] == 'failed' for row in records.values()),
        'by_arm': by_arm, 'paired_comparisons': paired, 'global_unique_usage': _usage(records),
        'global_unique_unit_latency': _timing(records.values()),
        'inference_provider_calls_added': 0, 'judge_calls_are_separate_phase': True,
        'independent_human': False, 'semantic_accuracy_verified': False, 'causal_truth_verified': False,
        'gold_read': False, 'same_model_bias_possible': True,
        'limitations': ['Fresh common judge assessments concern visible evidence, not semantic accuracy or root-cause truth.',
            'Original in-treatment critics are hidden; identical projected inputs share a unit and are not independent observations.',
            'Hypothesis and evidence wording is preserved and may reveal a treatment; hiding organizer metadata does not guarantee perfect blinding.',
            'The study has at most 25 case clusters, not 125 independent arm observations; shared units may also correlate cases.',
            'No counter-span quality, actual test execution or independent human benefit is established.',
            'Unknown/failed source and judge outcomes remain in selected-case denominators; label fractions use reviewed subsets only.',
            'Active call timing includes wrapper, source guards and journal I/O, not pure network latency.']}


def score_grounding(root, identifier, *, parent_loader=None):
    plan, directory = load_grounding_plan(root, identifier, parent_loader=parent_loader)
    records = _records(root, plan)
    saved = _core().read(root, directory / 'study-sealed.json')
    if saved != _study_seal(root, plan, records):
        raise ValueError('Common grounding study seal changed')
    report = _seal(summarize_grounding(plan, records))
    _validate_plan(root, plan, parent_loader)
    if _records(root, plan) != records or _core().read(root, directory / 'study-sealed.json') != saved:
        raise ValueError('Common grounding changed during read-only scoring')
    path = directory / 'scores' / (report['sha256'] + '.json')
    _core().write_new(root, path, report)
    return {**report, 'report_json': str(path)}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--mode', choices=('preview', 'run', 'inspect', 'score'), required=True)
    parser.add_argument('--parent-plan')
    parser.add_argument('--study')
    parser.add_argument('--authorization')
    parser.add_argument('--limit', type=int, choices=(1, 25), default=1)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.mode == 'preview':
        plan = create_grounding_plan(root, args.parent_plan)
        path = save_grounding_plan(root, plan)
        result = {'plan_sha256': plan['sha256'], 'plan_path': str(path), 'unique_judge_units': len(plan['units']),
                  'pilot_units': len(_selected_units(plan, 1)), 'gold_read': False, 'execution_authorized': False}
    elif args.mode == 'run':
        result = run_grounding(root, args.study, args.authorization, limit=args.limit, load_environment=True)
    elif args.mode == 'inspect':
        result = inspect_grounding(root, args.study)
    else:
        result = score_grounding(root, args.study)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
