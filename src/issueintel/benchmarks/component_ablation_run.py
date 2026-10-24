"""Owned, resumable strict-DEV component inference; restricted gold is absent.

Completed native stage payloads, including generation timestamps, are reused only
for identical inputs. An interrupted paid stage is never reconstructed by calling
the model again. Files and receipts establish local consistency, not authorship.
"""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import math
import os
import re
import time

from issueintel.annotations.models import AnnotationError
from issueintel.annotations.public_inputs import MODEL_HYPOTHESIS_FIELDS
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.providers import GroqProvider, ProviderError, failure_metadata, output_rejected
from issueintel.repository.graph import build_graph
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest, write_json
from . import component_ablation as registration
from .dev_context import accepted_context
from .strict_dev import _lock


ARM_SCHEMA = 'component_ablation_arm_v1'
STAGE_SCHEMA = 'component_ablation_stage_v1'
CALL_SCHEMA = 'component_ablation_call_v1'
SEAL_SCHEMA = 'component_ablation_inference_seal_v1'
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILES = 5000
STAGES = {'initial_retrieval', 'seed_generation', 'focused_retrieval', 'refined_generation', 'verification'}
_HASH = re.compile('[a-f0-9]{64}')
_CLOSED_RECEIPTS = {'settled', 'failed_bound', 'missing_usage_bound'}


class UnsafeContinuation(ValueError):
    """A missing or ambiguous owned record cannot authorize another paid call."""


class SavedStageFailure(RuntimeError):
    def __init__(self, terminal_output):
        super().__init__('A frozen identical stage previously failed; no retry')
        self.terminal_output = terminal_output


class NoUsableEvidence(ValueError):
    """Native retrieval completed without enough evidence for generation."""


class CriticOutputRejected(AnnotationError):
    """The one closed critic response failed native output validation."""


def _seal(value):
    return {**value, 'sha256': digest(value)}


def _read(root, path):
    _, raw = registration._read_file(root, path, private=True, maximum=MAX_FILE_BYTES)
    value = registration._parse(raw)
    if (not isinstance(value, dict) or value.get('sha256') !=
            digest({key: item for key, item in value.items() if key != 'sha256'})):
        raise UnsafeContinuation('Inference record content changed')
    return value


def _write(root, path, value):
    path = registration._safe_path(root, path, private=True, missing=True)
    raw = registration._bytes(value)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Inference record exceeds its byte limit')
    registration._mkdir(root, path.parent)
    write_json(path, value)
    registration._safe_path(root, path, private=True)


def _case_directory(root, directory, case_id):
    return registration._safe_path(root, directory / 'cases' / digest(case_id), private=True, missing=True)


def _finite_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _receipt(record, model):
    fields = {'reservation_id', 'created_at', 'model', 'input_bound', 'output_bound',
              'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved', 'status',
              'price_date', 'observed_input_tokens', 'observed_output_tokens'}
    if (not isinstance(record, dict) or set(record) != fields or record['model'] != model
            or not isinstance(record['reservation_id'], str)
            or not re.fullmatch('[a-f0-9]{32}', record['reservation_id'])
            or record['status'] not in _CLOSED_RECEIPTS | {'reserved', 'breach'}
            or any(type(record[key]) is not int or record[key] < 0 for key in
                   ('input_bound', 'output_bound', 'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved'))
            or any(record[key] is not None and (type(record[key]) is not int or record[key] < 0)
                   for key in ('observed_input_tokens', 'observed_output_tokens'))):
        raise UnsafeContinuation('Invalid owned provider receipt')
    observed_in, observed_out = record['observed_input_tokens'], record['observed_output_tokens']
    if ((observed_in is None) != (observed_out is None)
            or record['status'] == 'settled' and (observed_in is None
                or record['tokens_charged_or_reserved'] != observed_in + observed_out
                or observed_in > record['input_bound'] or observed_out > record['output_bound'])):
        raise UnsafeContinuation('Settled or observed provider usage is inconsistent')
    return record


def _validate_call(value, spec, *, ledger=None, require_closed=True):
    fields = {'schema', 'experiment_sha256', 'call_id', 'case_id', 'owner_arm_id', 'stage_key',
              'request_sha256', 'request', 'status', 'receipts', 'response', 'response_sha256',
              'failure', 'latency_seconds', 'sha256'}
    if (set(value) != fields or value['schema'] != CALL_SCHEMA
            or value['experiment_sha256'] != spec['sha256']
            or not isinstance(value['call_id'], str) or not _HASH.fullmatch(value['call_id'])
            or not isinstance(value['stage_key'], str) or not _HASH.fullmatch(value['stage_key'])
            or value['case_id'] not in {row['case_id'] for row in spec['cases']}
            or value['owner_arm_id'] not in registration.ARMS
            or value['request_sha256'] != digest(value['request'])
            or value['request'].get('model') != spec['runtime']['model']
            or value['request'].get('reasoning_effort') != spec['runtime']['reasoning_effort']
            or value['request'].get('max_output_tokens') != spec['runtime']['max_output_tokens']
            or value['status'] not in {'intent', 'returned', 'failed'}
            or not isinstance(value['receipts'], dict) or len(value['receipts']) > 1):
        raise UnsafeContinuation('Provider call binding changed')
    for rid, receipt in value['receipts'].items():
        _receipt(receipt, spec['runtime']['model'])
        if (rid != receipt['reservation_id'] or receipt['output_bound'] != spec['runtime']['max_output_tokens']
                or receipt['input_bound'] + receipt['output_bound'] > spec['runtime']['context_tokens']
                or ledger is not None and ledger.receipt(rid) != receipt):
            raise UnsafeContinuation('Owned ledger receipt changed')
    if require_closed and (value['status'] == 'intent' or not value['receipts']
                           or any(row['status'] not in _CLOSED_RECEIPTS for row in value['receipts'].values())):
        raise UnsafeContinuation('Paid outcome remains unknown; never retry automatically')
    if value['status'] != 'intent' and not _finite_time(value['latency_seconds']):
        raise UnsafeContinuation('Provider latency changed')
    if value['status'] == 'returned':
        if (not isinstance(value['response'], dict) or value['response_sha256'] != digest(value['response'])
                or value['failure'] is not None):
            raise UnsafeContinuation('Returned provider response changed')
    elif value['response'] is not None or value['response_sha256'] is not None:
        raise UnsafeContinuation('Nonreturned call cannot contain an accepted response')
    return value


def _replay_context(root, spec, row, sources):
    if registration._case_binding(root, row, sources) != row:
        raise UnsafeContinuation('Registered strict query, snapshot, graph or history changed')
    case, snapshot, query_receipt = accepted_context(root, row['case_id'])
    # Bind the actual objects supplied to the pipeline, not only a prior replay.
    from issueintel.investigation.policy_agent import context_binding
    if (context_binding(case, snapshot) != row['source'] or digest(query_receipt) != row['query_receipt_sha256']):
        raise UnsafeContinuation('Accepted context changed between replays')
    graph = build_graph(snapshot)
    if digest(graph.to_dict()) != row['graph_sha256']:
        raise UnsafeContinuation('Actual inference graph differs from registration')
    return case, snapshot, query_receipt, graph


def _policy(spec, arm_id):
    from issueintel.retrieval.ablation_policy import validate_policy
    recipe = next(row for row in spec['arms'] if row['arm_id'] == arm_id)
    payload = {**recipe, 'registration_sha256': spec['sha256']}
    return validate_policy(_seal(payload))


def _stage_binding(spec, row, stage, inputs):
    return {'experiment_sha256': spec['sha256'], 'case_id': row['case_id'], 'stage': stage,
            'inputs': deepcopy(inputs), 'model': spec['runtime']['model'],
            'reasoning_effort': spec['runtime']['reasoning_effort'],
            'max_output_tokens': spec['runtime']['max_output_tokens'],
            'inference_fingerprint_sha256': digest(spec['fingerprint'])}


def _validate_stage(value, spec, row):
    fields = {'schema', 'experiment_sha256', 'stage_key', 'binding', 'owner_arm_id', 'status',
              'payload', 'payload_sha256', 'call_ids', 'failure', 'terminal_output_failure',
              'latency_seconds', 'sha256'}
    if (set(value) != fields or value['schema'] != STAGE_SCHEMA
            or value['experiment_sha256'] != spec['sha256']
            or value['binding'].get('case_id') != row['case_id']
            or value['binding'].get('stage') not in STAGES
            or not isinstance(value['binding'].get('inputs'), dict)
            or value['binding'] != _stage_binding(spec, row, value['binding']['stage'], value['binding'].get('inputs'))
            or value['stage_key'] != digest(value['binding'])
            or value['owner_arm_id'] not in registration.ARMS
            or value['status'] not in {'intent', 'completed', 'failed'}
            or not isinstance(value['call_ids'], list) or len(value['call_ids']) > 5
            or len(set(value['call_ids'])) != len(value['call_ids'])
            or any(not isinstance(key, str) or not _HASH.fullmatch(key) for key in value['call_ids'])
            or type(value['terminal_output_failure']) is not bool):
        raise UnsafeContinuation('Stage source or owner changed')
    if value['status'] == 'completed':
        if (not isinstance(value['payload'], dict) or value['payload_sha256'] != digest(value['payload'])
                or value['failure'] is not None or not _finite_time(value['latency_seconds'])):
            raise UnsafeContinuation('Completed native stage payload changed')
    elif value['payload'] is not None or value['payload_sha256'] is not None:
        raise UnsafeContinuation('Incomplete stage cannot contain an accepted payload')
    return value


class _ArmExecution:
    def __init__(self, root, spec, directory, row, arm_id, sources, authorization_path,
                 requested_cases, provider_factory):
        self.root, self.spec, self.directory, self.row = root, spec, directory, row
        self.arm_id, self.sources = arm_id, sources
        self.authorization_path, self.requested_cases = authorization_path, requested_cases
        self.provider_factory = provider_factory
        self.case_directory = _case_directory(root, directory, row['case_id'])
        self.stage_refs, self.call_ids, self.owned_call_ids = [], [], []
        self.current = None
        self.config = replace(RuntimeConfig.load(root), model=spec['runtime']['model'],
                              reasoning_effort=spec['runtime']['reasoning_effort'],
                              max_output_tokens=spec['runtime']['max_output_tokens'])

    def guard(self):
        spec, directory = registration.load_registration(self.root, self.spec['sha256'])
        if spec != self.spec or directory != self.directory:
            raise UnsafeContinuation('Registration changed during inference')
        registration.verify_run_authorization(self.root, spec, self.authorization_path,
                                             requested_cases=self.requested_cases)
        return _replay_context(self.root, spec, self.row, self.sources)

    def _call_path(self, call_id):
        return self.case_directory / 'calls' / (call_id + '.json')

    def stage(self, stage, inputs, produce):
        if stage not in STAGES or self.current is not None:
            raise UnsafeContinuation('Unknown or nested experimental stage')
        self.guard()
        binding = _stage_binding(self.spec, self.row, stage, inputs)
        key = digest(binding)
        path = self.case_directory / 'stages' / (key + '.json')
        hit = path.exists()
        if hit:
            state = _validate_stage(_read(self.root, path), self.spec, self.row)
            if state['binding'] != binding or state['status'] == 'intent':
                raise UnsafeContinuation('Interrupted stage has no safely reusable payload')
            for cid in state['call_ids']:
                record = _validate_call(_read(self.root, self._call_path(cid)), self.spec, ledger=self.config.ledger())
                if record['stage_key'] != key or record['owner_arm_id'] != state['owner_arm_id']:
                    raise UnsafeContinuation('Stage references a different paid owner')
            self._reference(state, hit=True)
            if state['status'] == 'failed':
                raise SavedStageFailure(state['terminal_output_failure'])
            return deepcopy(state['payload'])
        state = {'schema': STAGE_SCHEMA, 'experiment_sha256': self.spec['sha256'],
                 'stage_key': key, 'binding': binding, 'owner_arm_id': self.arm_id,
                 'status': 'intent', 'payload': None, 'payload_sha256': None,
                 'call_ids': [], 'failure': None, 'terminal_output_failure': False,
                 'latency_seconds': None}
        self.current = state
        _write(self.root, path, _seal(state))
        started = time.monotonic()
        try:
            payload = produce()
            self.guard()
            for cid in state['call_ids']:
                _validate_call(_read(self.root, self._call_path(cid)), self.spec, ledger=self.config.ledger())
            state.update(status='completed', payload=deepcopy(payload), payload_sha256=digest(payload),
                         latency_seconds=round(time.monotonic() - started, 6))
            _validate_stage(_seal(state), self.spec, self.row)
            _write(self.root, path, _seal(state))
            self._reference(_seal(state), hit=False)
            return deepcopy(payload)
        except Exception as error:
            critic_rejected = self._critic_output_rejected(error, state)
            classified = CriticOutputRejected('Frozen critic response failed its native contract') if critic_rejected else error
            state.update(status='failed', payload=None, payload_sha256=None, failure=failure_metadata(classified),
                         terminal_output_failure=isinstance(classified, AnnotationError) or output_rejected(classified),
                         latency_seconds=round(time.monotonic() - started, 6))
            _write(self.root, path, _seal(state))
            self._reference(_seal(state), hit=False)
            if critic_rejected:
                raise classified from None
            raise
        finally:
            self.current = None

    def _critic_output_rejected(self, error, state):
        if type(error) is not ValueError or state['binding']['stage'] != 'verification' or len(state['call_ids']) != 1:
            return False
        # ValueError also represents integrity/source errors. Classify output
        # only after a fresh successful authority replay and a closed returned
        # response whose critique demonstrably fails the actual native contract.
        try:
            self.guard()
            call = _validate_call(_read(self.root, self._call_path(state['call_ids'][0])),
                                  self.spec, ledger=self.config.ledger())
            if call['status'] != 'returned':
                return False
            payload = registration._parse(call['request']['messages'][1]['content'].encode('utf-8'))
            if not isinstance(payload, dict) or set(payload) != {'hypotheses', 'evidence'}:
                return False
        except (ValueError, OSError, KeyError, TypeError, RuntimeError):
            return False
        from issueintel.investigation.verifier import validate_critique
        try:
            validate_critique(call['response'], {'hypotheses': payload['hypotheses']}, payload['evidence'])
        except ValueError:
            return True
        return False

    def _reference(self, state, *, hit):
        self.stage_refs.append({'stage_key': state['stage_key'], 'sha256': state['sha256'],
                               'owner_arm_id': state['owner_arm_id'], 'cache_hit': hit})
        for cid in state['call_ids']:
            if cid not in self.call_ids:
                self.call_ids.append(cid)
            if state['owner_arm_id'] == self.arm_id and cid not in self.owned_call_ids:
                self.owned_call_ids.append(cid)

    def __call__(self, messages, max_output_tokens=2048):
        return self.complete_json(messages, max_output_tokens=max_output_tokens)

    def complete_structured(self, messages, *, max_output_tokens=2048, schema):
        return self._call(messages, max_output_tokens, schema)

    def complete_json(self, messages, *, max_output_tokens=2048):
        return self._call(messages, max_output_tokens, None)

    def _call(self, messages, maximum, schema):
        if self.current is None or self.current['binding']['stage'] not in {'seed_generation', 'refined_generation', 'verification'}:
            raise UnsafeContinuation('Provider call requires an owned active native stage')
        if maximum != self.spec['runtime']['max_output_tokens']:
            raise UnsafeContinuation('Call output limit changed')
        self.guard()
        if len(self.call_ids) + len(self.current['call_ids']) >= self.spec['limits']['max_provider_calls_per_arm']:
            raise UnsafeContinuation('Common per-arm provider call bound reached')
        inventory = _inventory(self.root, self.directory, calls_only=True)
        ledger = self.config.ledger()
        owner_calls = 0
        for name in inventory:
            previous = _validate_call(_read(self.root, self.directory / name), self.spec, ledger=ledger)
            owner_calls += previous['case_id'] == self.row['case_id'] and previous['owner_arm_id'] == self.arm_id
        if owner_calls >= self.spec['limits']['max_provider_calls_per_arm']:
            raise UnsafeContinuation('Owned arm attempt bound reached, including interrupted attempts')
        if len(inventory) >= self.requested_cases * len(self.spec['arms']) * self.spec['limits']['max_provider_calls_per_arm']:
            raise UnsafeContinuation('Authorized experiment provider call bound reached')
        request = {'messages': deepcopy(messages), 'schema': deepcopy(schema),
                   'max_output_tokens': maximum, 'model': self.spec['runtime']['model'],
                   'reasoning_effort': self.spec['runtime']['reasoning_effort']}
        ordinal = len(self.current['call_ids'])
        cid = digest([self.current['stage_key'], ordinal, digest(request)])
        path = self._call_path(cid)
        if path.exists():
            raise UnsafeContinuation('A prior paid intent cannot be attempted again')
        state = {'schema': CALL_SCHEMA, 'experiment_sha256': self.spec['sha256'],
                 'call_id': cid, 'case_id': self.row['case_id'], 'owner_arm_id': self.arm_id,
                 'stage_key': self.current['stage_key'], 'request_sha256': digest(request),
                 'request': request, 'status': 'intent', 'receipts': {}, 'response': None,
                 'response_sha256': None, 'failure': None, 'latency_seconds': None}
        # Call ownership must be durable before constructing a transport/provider.
        _write(self.root, path, _seal(state))
        self.current['call_ids'].append(cid)
        self.owned_call_ids.append(cid)
        stage_path = self.case_directory / 'stages' / (self.current['stage_key'] + '.json')
        _write(self.root, stage_path, _seal(self.current))

        def on_receipt(record):
            _receipt(record, self.spec['runtime']['model'])
            if state['receipts'] and record['reservation_id'] not in state['receipts']:
                raise UnsafeContinuation('One owned HTTP attempt cannot acquire multiple reservations')
            state['receipts'][record['reservation_id']] = deepcopy(record)
            _write(self.root, path, _seal(state))

        started = time.monotonic()
        try:
            provider = self.provider_factory(self.config, on_receipt=on_receipt,
                call_context={'category': 'ARTIFACT_GENERATION_COST',
                              'stage': 'component_ablation_' + self.current['binding']['stage'],
                              'reasoning_effort': self.spec['runtime']['reasoning_effort'], 'diagnostic': True})
            if schema is None:
                response = provider.complete_json(messages, max_output_tokens=maximum)
            else:
                response = provider.complete_structured(messages, max_output_tokens=maximum, schema=schema)
            state.update(status='returned', response=deepcopy(response), response_sha256=digest(response),
                         latency_seconds=round(time.monotonic() - started, 6))
            _validate_call(_seal(state), self.spec, ledger=self.config.ledger())
            _write(self.root, path, _seal(state))
            self.guard()
            return deepcopy(response)
        except Exception as error:
            # A saved returned response is retained even if a later source guard fails.
            if state['status'] != 'returned':
                state.update(status='failed', failure=failure_metadata(error),
                             latency_seconds=round(time.monotonic() - started, 6))
                _write(self.root, path, _seal(state))
            raise


def _inventory(root, directory, *, calls_only=False):
    base = directory / 'cases'
    if not base.exists():
        return {}
    files, total = {}, 0
    for current, folders, names in os.walk(base, followlinks=False):
        registration._safe_path(root, Path(current), private=True)
        for name in folders:
            registration._safe_path(root, Path(current) / name, private=True)
        for name in names:
            path = registration._safe_path(root, Path(current) / name, private=True)
            if path.suffix != '.json':
                raise UnsafeContinuation('Unexpected inference inventory file')
            if calls_only and path.parent.name != 'calls':
                continue
            _, raw = registration._read_file(root, path, private=True, maximum=MAX_FILE_BYTES)
            total += len(raw)
            if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                raise UnsafeContinuation('Inference inventory exceeds its limit')
            files[path.relative_to(directory).as_posix()] = sha256(raw).hexdigest()
    return files


def _arm_path(root, directory, case_id, arm_id):
    return _case_directory(root, directory, case_id) / 'arms' / (arm_id + '.json')


def _validate_arm(value, spec, row, *, call_records, stage_records, require_closed=True):
    if (value.get('schema') != ARM_SCHEMA or value.get('experiment_sha256') != spec['sha256']
            or value.get('case_id') != row['case_id'] or value.get('source') != row['source']
            or value.get('arm_id') not in registration.ARMS
            or value.get('policy') != _policy(spec, value['arm_id'])
            or value.get('status') not in {'completed', 'failed', 'unavailable'}
            or not _finite_time(value.get('pipeline_latency_seconds'))
            or not _finite_time(value.get('preparation_seconds'))
            or not isinstance(value.get('preparation_measurement_id'), str)
            or not _HASH.fullmatch(value['preparation_measurement_id'])
            or value.get('preparation_owner_arm_id') not in registration.ARMS
            or not isinstance(value.get('stage_refs'), list) or len(value['stage_refs']) > 5
            or len({ref['stage_key'] for ref in value['stage_refs']}) != len(value['stage_refs'])
            or not isinstance(value.get('call_ids'), list) or len(value['call_ids']) > 5
            or len(set(value['call_ids'])) != len(value['call_ids'])):
        raise UnsafeContinuation('Arm result binding changed')
    stage_calls = []
    for ref in value['stage_refs']:
        stage = stage_records[ref['stage_key']]
        _validate_stage(stage, spec, row)
        if (set(ref) != {'stage_key', 'sha256', 'owner_arm_id', 'cache_hit'}
                or ref['sha256'] != stage['sha256'] or ref['owner_arm_id'] != stage['owner_arm_id']
                or type(ref['cache_hit']) is not bool
                or not ref['cache_hit'] and ref['owner_arm_id'] != value['arm_id']):
            raise UnsafeContinuation('Arm stage ownership changed')
        stage_calls.extend(cid for cid in stage['call_ids'] if cid not in stage_calls)
        for ordinal, cid in enumerate(stage['call_ids']):
            call = _validate_call(call_records[cid], spec, require_closed=require_closed)
            if (call['stage_key'] != stage['stage_key'] or call['case_id'] != row['case_id']
                    or call['owner_arm_id'] != stage['owner_arm_id']
                    or cid != digest([stage['stage_key'], ordinal, call['request_sha256']])):
                raise UnsafeContinuation('Arm paid call references changed')
    if value['call_ids'] != stage_calls or value.get('owned_call_ids') != [
            cid for cid in stage_calls if call_records[cid]['owner_arm_id'] == value['arm_id']]:
        raise UnsafeContinuation('Arm paid call accounting changed')
    if value['status'] == 'completed':
        pack = value.get('pack')
        if (not isinstance(pack, dict) or pack.get('case', {}).get('case_id') != row['case_id']
                or pack.get('experiment_policy') != value['policy']
                or pack.get('leakage_audit', {}).get('gold_in_inference') is not False
                or pack.get('leakage_audit', {}).get('strict_issue_time_verified') is not True
                or not pack.get('annotation') or not pack.get('verification')
                or any(stage_records[ref['stage_key']]['status'] != 'completed' for ref in value['stage_refs'])):
            raise UnsafeContinuation('Completed native pipeline pack changed')
        generated = [stage_records[ref['stage_key']]['payload']['annotation'] for ref in value['stage_refs']
                     if stage_records[ref['stage_key']]['binding']['stage'] in {'seed_generation', 'refined_generation'}]
        if not generated or pack['annotation'] != generated[-1]:
            raise UnsafeContinuation('Pack generation differs from its immutable native stage')
    elif value.get('pack') is not None:
        raise UnsafeContinuation('Failed arm cannot claim a completed pack')
    return value


def _records(root, spec, directory, *, require_closed=True):
    inventory = _inventory(root, directory)
    calls, stages, arms = {}, {}, {}
    rows = {row['case_id']: row for row in spec['cases']}
    for name in inventory:
        value = _read(root, directory / name)
        parts = Path(name).parts
        if len(parts) != 4 or parts[0] != 'cases' or value.get('case_id', value.get('binding', {}).get('case_id')) not in rows:
            raise UnsafeContinuation('Inference inventory location changed')
        cid = value.get('case_id', value.get('binding', {}).get('case_id'))
        if parts[1] != digest(cid):
            raise UnsafeContinuation('Inference case directory changed')
        if parts[2] == 'calls':
            _validate_call(value, spec, require_closed=require_closed)
            if parts[3] != value['call_id'] + '.json':
                raise UnsafeContinuation('Call filename changed')
            calls[value['call_id']] = value
        elif parts[2] == 'stages':
            _validate_stage(value, spec, rows[cid])
            if parts[3] != value['stage_key'] + '.json':
                raise UnsafeContinuation('Stage filename changed')
            stages[value['stage_key']] = value
        elif parts[2] == 'arms':
            if parts[3] != value.get('arm_id', '') + '.json':
                raise UnsafeContinuation('Arm filename changed')
            arms[(cid, value['arm_id'])] = value
        else:
            raise UnsafeContinuation('Unknown inference record type')
    for value in arms.values():
        _validate_arm(value, spec, rows[value['case_id']], call_records=calls, stage_records=stages,
                      require_closed=require_closed)
    for stage in stages.values():
        kind = stage['binding']['stage']
        maximum = 2 if kind in {'seed_generation', 'refined_generation'} else 1 if kind == 'verification' else 0
        if len(stage['call_ids']) > maximum:
            raise UnsafeContinuation('Stage exceeds its native generation/critic call slots')
        if stage['status'] != 'completed':
            continue
        paid = [calls[cid] for cid in stage['call_ids']]
        if kind in {'seed_generation', 'refined_generation'}:
            if not paid or paid[-1]['status'] != 'returned':
                raise UnsafeContinuation('Completed generation has no owned returned response')
            annotation = stage['payload'].get('annotation')
            expected = {'hypotheses': [{key: row[key] for key in MODEL_HYPOTHESIS_FIELDS}
                                      for row in annotation['hypotheses']],
                        'abstention': {key: annotation['abstention'][key] for key in ('label', 'reason')}}
            if (paid[-1]['response'] != expected
                    or annotation['provenance']['repair_count'] != len(paid) - 1):
                raise UnsafeContinuation('Cached generation differs from its returned model response')
        elif kind == 'verification':
            verification = stage['payload'].get('verification', {})
            if verification.get('kind') == 'llm_separate_pass':
                if (len(paid) != 1 or paid[0]['status'] != 'returned'
                        or paid[0]['response'] != {'reviews': verification.get('reviews')}):
                    raise UnsafeContinuation('Cached critique differs from its returned model response')
            elif paid:
                raise UnsafeContinuation('Structural verification cannot claim a paid critique')
    used = {cid for stage in stages.values() for cid in stage['call_ids']}
    if set(calls) != used:
        raise UnsafeContinuation('Orphan paid call cannot be ignored or retried')
    reservation_owners = {}
    for cid, call in calls.items():
        for rid in call['receipts']:
            if rid in reservation_owners and reservation_owners[rid] != cid:
                raise UnsafeContinuation('One reservation cannot belong to different calls')
            reservation_owners[rid] = cid
    return inventory, list(arms.values()), list(calls.values()), list(stages.values())


def _pilot(root, spec, directory):
    _, arms, calls, stages = _records(root, spec, directory)
    first = spec['cases'][0]['case_id']
    selected = [value for value in arms if value['case_id'] == first]
    if (len(selected) != len(registration.ARMS) or {row['arm_id'] for row in selected} != set(registration.ARMS)
            or any(row['status'] != 'completed' for row in selected)):
        raise UnsafeContinuation('All five first-case arms must complete before matrix expansion')
    return _seal({'schema': 'component_ablation_pilot_gate_v1', 'experiment_sha256': spec['sha256'],
                  'case_id': first, 'arm_results': {row['arm_id']: row['sha256'] for row in selected},
                  'all_native_stages_completed': True, 'all_receipts_closed': True,
                  'gold_read': False, 'quality_claim': False})


def _inference_seal(root, spec, directory):
    files, arms, calls, stages = _records(root, spec, directory)
    expected = {(row['case_id'], arm) for row in spec['cases'] for arm in registration.ARMS}
    if {(row['case_id'], row['arm_id']) for row in arms} != expected:
        raise UnsafeContinuation('Complete registered inference matrix is not terminal')
    if any(stage['status'] == 'intent' for stage in stages):
        raise UnsafeContinuation('Unfinished native stages prevent inference sealing')
    _matrix_references(arms, calls, stages)
    _pilot(root, spec, directory)
    seal = _seal({'schema': SEAL_SCHEMA, 'experiment_sha256': spec['sha256'],
                  'registration_sha256': spec['sha256'], 'protocol_sha256': spec['protocol_sha256'],
                  'history_input_sha256': spec['history_input']['sha256'], 'files': files,
                  'selected_case_ids': [row['case_id'] for row in spec['cases']],
                  'arms': list(registration.ARMS), 'terminal_results': len(arms),
                  'completed_results': sum(row['status'] == 'completed' for row in arms),
                  'gold_read': False, 'quality_claim': False, 'holdout_or_live_access': False})
    path = directory / 'inference-sealed.json'
    if path.exists():
        if _read(root, path) != seal:
            raise UnsafeContinuation('Previously sealed inference cannot be replaced')
    else:
        _write(root, path, seal)
    return seal


def _matrix_references(arms, calls, stages):
    """A complete matrix may not hide stray work outside terminal arm records."""
    if ({ref['stage_key'] for arm in arms for ref in arm['stage_refs']} != {row['stage_key'] for row in stages}
            or {cid for arm in arms for cid in arm['call_ids']} != {row['call_id'] for row in calls}):
        raise UnsafeContinuation('Complete matrix contains unreferenced native stages or paid calls')
    owners = {(arm['case_id'], arm['arm_id']): arm for arm in arms}
    for stage in stages:
        arm = owners.get((stage['binding']['case_id'], stage['owner_arm_id']))
        if arm is None or stage['stage_key'] not in {ref['stage_key'] for ref in arm['stage_refs']}:
            raise UnsafeContinuation('Native stage owner has no terminal arm reference')
    return True


def _replay_completed_packs(root, spec, directory, results, stages):
    """Cache-only native replay; never reconstruct an annotation from raw output."""
    registration.preflight_component_ablation(root, spec['sha256'])
    sources = registration.load_history_sources(root, spec, directory)
    by_stage = {row['stage_key']: row for row in stages}
    for row in spec['cases']:
        case, snapshot, query_receipt, graph = _replay_context(root, spec, row, sources[row['case_id']])
        for arm in (value for value in results if value['case_id'] == row['case_id'] and value['status'] == 'completed'):
            used = []
            permitted = {ref['stage_key'] for ref in arm['stage_refs']}

            def stage(stage, inputs, produce):
                key = digest(_stage_binding(spec, row, stage, inputs))
                if key not in permitted or by_stage[key]['status'] != 'completed':
                    raise UnsafeContinuation('Saved pack needs a missing or different native stage')
                if stage in {'initial_retrieval', 'focused_retrieval'} and digest(produce()) != digest(by_stage[key]['payload']):
                    raise UnsafeContinuation('Cached retrieval differs from deterministic native tool replay')
                used.append(key)
                return deepcopy(by_stage[key]['payload'])

            def deny_call(*args, **kwargs):
                raise UnsafeContinuation('Offline pack replay cannot call a provider')
            deny_call.complete_structured = deny_call
            deny_call.complete_json = deny_call
            policy = arm['policy']
            replayed = investigate(case, Path(snapshot.repository), complete_json=deny_call,
                provider='groq', model=spec['runtime']['model'], protocol='issue_time',
                snapshot=snapshot, graph=graph, query_receipt=query_receipt,
                top_k=policy['top_k'], independent_critic=policy['independent_critic'],
                max_output_tokens=policy['max_output_tokens'], refinement_rounds=int(policy['counter_search']),
                experiment_policy=policy, historical_sources=sources[row['case_id']], experiment_stage_executor=stage)
            if digest(replayed) != digest(arm['pack']) or used != [ref['stage_key'] for ref in arm['stage_refs']]:
                raise UnsafeContinuation('Saved pack does not replay exactly from immutable native stages')
    return True


def load_sealed_inference(root, identifier):
    """Validate complete frozen inference before a separate scorer can read gold."""
    root = Path(root).resolve()
    spec, directory = registration.load_registration(root, identifier)
    seal = _read(root, directory / 'inference-sealed.json')
    files, results, calls, stages = _records(root, spec, directory)
    if (seal.get('schema') != SEAL_SCHEMA or seal.get('experiment_sha256') != identifier
            or seal.get('registration_sha256') != identifier or seal.get('protocol_sha256') != spec['protocol_sha256']
            or seal.get('history_input_sha256') != spec['history_input']['sha256']
            or seal.get('files') != files or seal.get('gold_read') is not False
            or seal.get('quality_claim') is not False or seal.get('holdout_or_live_access') is not False
            or seal.get('selected_case_ids') != [row['case_id'] for row in spec['cases']]
            or seal.get('arms') != list(registration.ARMS)
            or seal.get('terminal_results') != len(spec['cases']) * len(registration.ARMS)
            or seal.get('completed_results') != sum(row['status'] == 'completed' for row in results)
            or {(row['case_id'], row['arm_id']) for row in results} != {
                (row['case_id'], arm) for row in spec['cases'] for arm in registration.ARMS}
            or any(stage['status'] == 'intent' for stage in stages)):
        raise UnsafeContinuation('Inference seal no longer matches the complete registered matrix')
    _pilot(root, spec, directory)
    _matrix_references(results, calls, stages)
    _replay_completed_packs(root, spec, directory, results, stages)
    # Source or files edited during replay cannot qualify a seal for gold access.
    if registration.load_registration(root, identifier)[0] != spec or _inventory(root, directory) != files:
        raise UnsafeContinuation('Inference changed during offline native replay')
    return {'registration': spec, 'directory': directory, 'seal': seal,
            'results': results, 'calls': calls, 'stages': stages}


def inspect_component_run(root, identifier):
    """Bounded saved-state diagnostics, without credentials, ledger or raw text.

    Stale registrations remain inspectable. This does not certify native source
    replay or grant execution/scoring authority; those gates are separate.
    """
    root = Path(root).resolve()
    spec, directory = registration._static_registration(root, identifier)
    base = {'schema': 'component_ablation_run_inspection_v1', 'experiment_sha256': identifier,
            'inference_seal_present': (directory / 'inference-sealed.json').exists(),
            'execution_authorized': False, 'provider_or_ledger_constructed': False,
            'gold_read': False, 'quality_claim': False}
    try:
        _, arms, calls, stages = _records(root, spec, directory, require_closed=False)
        receipts = [receipt for call in calls for receipt in call['receipts'].values()]
        intents = sum(row['status'] == 'intent' for row in stages)
        unknown = sum(row['status'] not in _CLOSED_RECEIPTS for row in receipts)
        orphan_outcome = sum(not call['receipts'] or call['status'] == 'intent' for call in calls)
        first = [row for row in arms if row['case_id'] == spec['cases'][0]['case_id']]
        failed_pilot = len(first) == len(registration.ARMS) and any(row['status'] != 'completed' for row in first)
        unsafe_failure = any(stage['status'] == 'failed' and not stage['terminal_output_failure'] for stage in stages)
        blocked = ('unknown_or_breached_paid_outcome_manual_audit_required' if unknown or orphan_outcome else
                   'interrupted_native_stage_manual_audit_required' if intents else
                   'source_provider_or_storage_failure_manual_audit_required' if unsafe_failure else
                   'first_case_pilot_did_not_pass_no_matrix_expansion' if failed_pilot else None)
        return {**base, 'saved_record_integrity_valid': True,
                'terminal_arm_counts': dict(sorted(Counter(row['status'] for row in arms).items())),
                'terminal_arms': len(arms), 'expected_arms': len(spec['cases']) * len(registration.ARMS),
                'owned_provider_calls': len(calls), 'receipt_status_counts': dict(sorted(Counter(row['status'] for row in receipts).items())),
                'incomplete_call_records': orphan_outcome, 'interrupted_native_stages': intents,
                'blocked_reason': blocked, 'native_source_replay_performed': False}
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        return {**base, 'saved_record_integrity_valid': False,
                'blocked_reason': 'saved_inference_record_validation_failed_manual_audit_required',
                'native_source_replay_performed': False}


def run_component_ablation(root, identifier, authorization_path, *, limit=1, provider_factory=GroqProvider):
    """Run a bounded registered prefix, retaining every terminal arm and paid intent.

    The first invocation runs one case. Matrix expansion needs a matching matrix
    declaration plus five successfully completed pilot arms. This function never
    loads credentials, asks a model to judge quality, or opens restricted gold.
    """
    if type(limit) is not int or not 1 <= limit <= registration.SELECTED_CASES:
        raise ValueError('Run a 1–25 registered Main Dev prefix')
    root = Path(root).resolve()
    spec, directory = registration.load_registration(root, identifier)
    registration.verify_run_authorization(root, spec, authorization_path, requested_cases=limit)
    registration.preflight_component_ablation(root, identifier)
    sources = registration.load_history_sources(root, spec, directory)
    stopped = None
    with _lock(root, {'component_ablation': identifier}):
        # Validate all prior paid records before any new work. Even a returned call
        # cannot recover a missing native stage with a fresh generation timestamp.
        _, prior, calls, stages = _records(root, spec, directory)
        if calls:
            config = RuntimeConfig.load(root)
            ledger = config.ledger()
            for call in calls:
                _validate_call(call, spec, ledger=ledger)
        if any(stage['status'] == 'intent' for stage in stages):
            raise UnsafeContinuation('Interrupted native stage needs manual audit; never paid retry')
        if limit > 1:
            gate = _pilot(root, spec, directory)
            _replay_completed_packs(root, spec, directory,
                [row for row in prior if row['case_id'] == spec['cases'][0]['case_id']], stages)
            gate_path = directory / 'pilot-gate.json'
            if gate_path.exists() and _read(root, gate_path) != gate:
                raise UnsafeContinuation('First-case pilot gate changed')
            if not gate_path.exists():
                _write(root, gate_path, gate)
        existing = {(row['case_id'], row['arm_id']): row for row in prior}
        for index, row in enumerate(spec['cases'][:limit]):
            order = registration.ARMS[index % len(registration.ARMS):] + registration.ARMS[:index % len(registration.ARMS)]
            remaining = [arm_id for arm_id in order if (row['case_id'], arm_id) not in existing]
            if not remaining:
                continue
            preparation_id = digest([row['case_id'], time.time_ns()])
            prepared = time.monotonic()
            case, snapshot, receipt, graph = _replay_context(root, spec, row, sources[row['case_id']])
            preparation = round(time.monotonic() - prepared, 6)
            for arm_id in order:
                if (row['case_id'], arm_id) in existing:
                    continue  # Completed or failed owned result is never replaced.
                execution = _ArmExecution(root, spec, directory, row, arm_id, sources[row['case_id']],
                                          authorization_path, limit, provider_factory)
                policy = _policy(spec, arm_id)
                started = time.monotonic()
                pack, failure, status = None, None, 'completed'
                try:
                    pack = investigate(case, Path(snapshot.repository), complete_json=execution,
                        provider='groq', model=spec['runtime']['model'], protocol='issue_time',
                        snapshot=snapshot, graph=graph, query_receipt=receipt,
                        top_k=policy['top_k'], independent_critic=policy['independent_critic'],
                        max_output_tokens=policy['max_output_tokens'], refinement_rounds=int(policy['counter_search']),
                        experiment_policy=policy, historical_sources=sources[row['case_id']],
                        experiment_stage_executor=execution.stage)
                    if not pack.get('annotation') or not pack.get('verification'):
                        raise NoUsableEvidence('Native pipeline retained insufficient evidence')
                    execution.guard()
                except Exception as error:
                    pack = None
                    failure = failure_metadata(error)
                    status = 'unavailable' if isinstance(error, (SavedStageFailure, NoUsableEvidence)) else 'failed'
                    terminal = isinstance(error, (AnnotationError, NoUsableEvidence)) or output_rejected(error) or (
                        isinstance(error, SavedStageFailure) and error.terminal_output)
                    if not terminal:
                        stopped = 'source_provider_storage_or_unknown_paid_outcome_no_automatic_retry'
                result = _seal({'schema': ARM_SCHEMA, 'experiment_sha256': identifier,
                    'case_id': row['case_id'], 'arm_id': arm_id, 'source': row['source'], 'policy': policy,
                    'status': status, 'pack': pack, 'failure': failure, 'stop_reason': stopped,
                    'stage_refs': execution.stage_refs, 'call_ids': execution.call_ids,
                    'owned_call_ids': execution.owned_call_ids,
                    'pipeline_latency_seconds': round(time.monotonic() - started, 6),
                    'preparation_seconds': preparation, 'preparation_measurement_id': preparation_id,
                    'preparation_owner_arm_id': remaining[0]})
                target = _arm_path(root, directory, row['case_id'], arm_id)
                if target.exists():
                    raise UnsafeContinuation('Owned arm result already exists')
                _write(root, target, result)
                existing[(row['case_id'], arm_id)] = result
                if stopped:
                    break
            if stopped:
                break
        # A failed/unknown receipt must remain visible even when it prevents the
        # stricter sealed reader from accepting the run for scoring.
        _, results, calls, stages = _records(root, spec, directory, require_closed=False)
        if not stopped and len(results) == len(spec['cases']) * len(registration.ARMS):
            _inference_seal(root, spec, directory)
        summary = _seal({'schema': 'component_ablation_progress_v1', 'experiment_sha256': identifier,
            'selected_cases': len(spec['cases']), 'requested_prefix': limit,
            'completed_arms': sum(row['status'] == 'completed' for row in results),
            'failed_arms': sum(row['status'] == 'failed' for row in results),
            'unavailable_arms': sum(row['status'] == 'unavailable' for row in results),
            'owned_provider_calls': len(calls), 'stop_reason': stopped,
            'inference_sealed': (directory / 'inference-sealed.json').exists(),
            'gold_read': False, 'quality_claim': False})
        _write(root, directory / 'progress.json', summary)
        return summary
