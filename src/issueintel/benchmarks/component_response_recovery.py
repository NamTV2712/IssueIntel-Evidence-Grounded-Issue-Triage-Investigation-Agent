"""Offline proof for an interrupted verifier with a saved, settled response.

This is a successor *candidate*, never permission to retry or mutate its parent.
Generation is reusable only from a completed native stage. The one verifier
response is replayed through the frozen verifier and pipeline without a provider.
Original stage/arm timing remains unknown. Paid continuation needs a separate
adapter and authority that understand these explicit recovery references.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import re
import sys


SCHEMA = 'component_response_recovery_candidate_v1'
_HASH = re.compile('[a-f0-9]{64}')


class ResponseRecoveryError(ValueError):
    """A stored response cannot establish this exact native recovery."""


def _api(root, base_api=None):
    if base_api is not None:
        return base_api
    api = (importlib.import_module('issueintel.benchmarks.component_ablation'),
           importlib.import_module('issueintel.benchmarks.component_ablation_run'))
    expected = Path(root).resolve() / 'src' / 'issueintel'
    for name, module in tuple(sys.modules.items()):
        if name == 'issueintel' or name.startswith('issueintel.'):
            filename = getattr(module, '__file__', None)
            if name in {'issueintel.benchmarks.component_continuation',
                        'issueintel.benchmarks.component_continuation_run',
                        'issueintel.benchmarks.component_continuation_score',
                        'issueintel.benchmarks.component_response_recovery'}:
                continue
            if filename and expected not in Path(filename).resolve().parents:
                raise ResponseRecoveryError('Native inference must originate in the frozen root')
    return api


def _core(parent_core=None):
    if parent_core is not None:
        return parent_core
    # Load only this sibling, never add the adapter checkout to native sys.path.
    path = Path(__file__).with_name('component_continuation.py')
    spec = importlib.util.spec_from_file_location('_response_recovery_parent_core', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_hash():
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _same_request(actual, recorded, core):
    if set(actual) != set(recorded):
        return False
    left, right = deepcopy(actual), deepcopy(recorded)
    try:
        lm, rm = left['messages'], right['messages']
        if (not isinstance(lm, list) or not isinstance(rm, list) or len(lm) != 2 or len(rm) != 2
                or set(lm[1]) != {'role', 'content'} or set(rm[1]) != {'role', 'content'}
                or lm[1]['role'] != 'user' or rm[1]['role'] != 'user'):
            return False
        # Use strict JSON decoding; duplicate keys and nonfinite values are denied.
        lm[1]['content'] = core._parse(lm[1]['content'].encode('utf-8'))
        rm[1]['content'] = core._parse(rm[1]['content'].encode('utf-8'))
    except (KeyError, TypeError, AttributeError, ValueError):
        return False
    # Python equality conflates True/1 and 1/1.0; canonical JSON must retain
    # scalar types while allowing object-key order and whitespace differences.
    return core.digest(left) == core.digest(right)


class _StoredCritique:
    """One exact stored response, no provider, ledger or network construction."""

    def __init__(self, call, core):
        self.call, self.core, self.used = call, core, 0

    def complete_structured(self, messages, *, max_output_tokens, schema):
        request = {'messages': deepcopy(messages), 'schema': deepcopy(schema),
                   'max_output_tokens': max_output_tokens,
                   'model': self.call['request']['model'],
                   'reasoning_effort': self.call['request']['reasoning_effort']}
        if self.used or not _same_request(request, self.call['request'], self.core):
            raise ResponseRecoveryError('Recovered verifier request differs from its stored paid request')
        self.used += 1
        return deepcopy(self.call['response'])

    def complete_json(self, *args, **kwargs):
        raise ResponseRecoveryError('Only the exact structured native critique can be recovered')

    __call__ = complete_json


def _target(view):
    unfinished = [stage for stage in view['stages'] if stage['status'] == 'intent']
    if len(unfinished) != 1:
        raise ResponseRecoveryError('Recovery requires exactly one interrupted verifier stage')
    stage = unfinished[0]
    if (stage not in view['overlay_stages'] or stage['binding']['stage'] != 'verification'
            or stage['binding']['inputs'].get('independent_critic') is not True
            or stage['payload'] is not None or stage['payload_sha256'] is not None
            or stage['latency_seconds'] is not None or stage['failure'] is not None
            or stage['terminal_output_failure'] is not False or len(stage['call_ids']) != 1):
        raise ResponseRecoveryError('Only a missing native verifier payload is recoverable')
    owner = (stage['binding']['case_id'], stage['owner_arm_id'])
    if any((arm['case_id'], arm['arm_id']) == owner for arm in view['results']):
        raise ResponseRecoveryError('Recovery cannot replace a terminal arm')
    calls = {call['call_id']: call for call in view['calls']}
    call = calls[stage['call_ids'][0]]
    if (call not in view['overlay_calls'] or call['status'] != 'returned'
            or call['failure'] is not None or call['stage_key'] != stage['stage_key']
            or (call['case_id'], call['owner_arm_id']) != owner
            or len(call['receipts']) != 1
            or any(receipt['status'] != 'settled' for receipt in call['receipts'].values())):
        raise ResponseRecoveryError('Recovery requires one returned critique with a settled owned receipt')
    return stage, call


def prepare_response_recovery(root, parent_plan, *, base_api=None, parent_core=None):
    """Replay strict sources and derive a new immutable, non-executable candidate.

    No environment, RuntimeConfig, ledger, provider, gold or network is opened.
    Parent records are validated, then protected by the existing experiment lease.
    The returned record pins every existing journal byte and all native sources.
    """
    root = Path(root).resolve()
    core, api = _core(parent_core), _api(root, base_api)
    registration, native = api
    if not isinstance(parent_plan, str) or not _HASH.fullmatch(parent_plan):
        raise ResponseRecoveryError('Recovery needs an exact parent plan ID')
    plan, directory = core.load_plan(root, parent_plan, base_api=api)
    pinned_core = next(row for row in plan['adapter_sources']
                       if Path(row['path']).name == 'component_continuation.py')
    if sha256(Path(core.__file__).read_bytes()).hexdigest() != pinned_core['sha256']:
        raise ResponseRecoveryError('Loaded parent validator differs from its pinned source')
    adapter_hash = _source_hash()
    with native._lock(root, {'component_ablation': plan['registration_sha256']}):
        view = core.load_effective_view(root, plan, directory, base_api=api, full_replay=False)
        target, call = _target(view)
        spec = view['registration']
        row = next(row for row in spec['cases'] if row['case_id'] == call['case_id'])
        sources = registration.load_history_sources(root, spec, view['base_directory'])
        case, snapshot, query_receipt, graph = native._replay_context(
            root, spec, row, sources[row['case_id']])
        policy = native._policy(spec, target['owner_arm_id'])
        response = _StoredCritique(call, core)
        stages = view['execution_stages']
        used, recovered = [], {}

        def stage_executor(kind, inputs, produce):
            binding = native._stage_binding(spec, row, kind, inputs)
            key = native.digest(binding)
            saved = stages.get(key)
            if saved is None or saved['binding'] != binding:
                raise ResponseRecoveryError('Recovery would require a missing native stage')
            if key in [ref['stage_key'] for ref in used]:
                raise ResponseRecoveryError('Recovery repeated a native stage')
            used.append({'stage_key': key, 'sha256': saved['sha256'],
                         'owner_arm_id': saved['owner_arm_id'], 'original_status': saved['status']})
            if key == target['stage_key']:
                if kind != 'verification' or saved != target:
                    raise ResponseRecoveryError('Recovery target binding changed')
                # Frozen verify_hypotheses validates the exact source and response.
                payload = produce()
                if set(payload) != {'verification'} or response.used != 1:
                    raise ResponseRecoveryError('Native verification did not consume the exact saved response')
                recovered.update(payload=deepcopy(payload))
                return payload
            if saved['status'] != 'completed':
                raise ResponseRecoveryError('Recovery cannot regenerate a missing native payload')
            if kind in {'initial_retrieval', 'focused_retrieval'}:
                if native.digest(produce()) != native.digest(saved['payload']):
                    raise ResponseRecoveryError('Cached retrieval differs from the frozen native tools')
            return deepcopy(saved['payload'])

        pack = native.investigate(case, Path(snapshot.repository), complete_json=response,
            provider='groq', model=spec['runtime']['model'], protocol='issue_time',
            snapshot=snapshot, graph=graph, query_receipt=query_receipt,
            top_k=policy['top_k'], independent_critic=policy['independent_critic'],
            max_output_tokens=spec['runtime']['max_output_tokens'],
            refinement_rounds=int(policy['counter_search']), experiment_policy=policy,
            historical_sources=sources[row['case_id']], experiment_stage_executor=stage_executor)
        if (not recovered or response.used != 1 or used[-1]['stage_key'] != target['stage_key']
                or native.digest(pack['annotation']) != target['binding']['inputs']['annotation_sha256']
                or native.digest(pack['evidence']) != target['binding']['inputs']['evidence_sha256']
                or pack['verification'] != recovered['payload']['verification']
                or pack['leakage_audit']['gold_in_inference'] is not False
                or pack['leakage_audit']['strict_issue_time_verified'] is not True):
            raise ResponseRecoveryError('Recovered native pack changed its strict inputs or output')
        after = core.load_effective_view(root, plan, directory, base_api=api, full_replay=False)
        if (after['files'] != view['files'] or after['base_files'] != view['base_files']
                or _source_hash() != adapter_hash):
            raise ResponseRecoveryError('Recovery sources changed during replay')
        value = {
            'schema': SCHEMA, 'status': 'candidate_not_admitted_to_inference',
            'parent_plan_sha256': parent_plan,
            'registration_sha256': spec['sha256'],
            'native_fingerprint_sha256': plan['code_fingerprint_sha256'],
            'native_fingerprint': deepcopy(spec['fingerprint']),
            'parent_adapter_sources': deepcopy(plan['adapter_sources']),
            'recovery_adapter_sha256': adapter_hash,
            'base_inventory': deepcopy(view['base_files']),
            'parent_inventory': deepcopy(view['files']),
            'case_id': call['case_id'], 'arm_id': target['owner_arm_id'],
            'source': deepcopy(row['source']), 'policy': deepcopy(policy),
            'original_stage': {'stage_key': target['stage_key'], 'sha256': target['sha256'],
                               'status': 'intent', 'binding': deepcopy(target['binding'])},
            'original_call': {'call_id': call['call_id'], 'sha256': call['sha256'],
                              'request_sha256': call['request_sha256'],
                              'response_sha256': call['response_sha256'],
                              'reservation_ids': sorted(call['receipts'])},
            'native_stage_refs': used,
            'verification_payload': recovered['payload'],
            'verification_payload_sha256': native.digest(recovered['payload']),
            'pack': pack, 'pack_sha256': native.digest(pack),
            'timing': {'original_stage_latency_seconds': None,
                       'original_arm_pipeline_seconds': None,
                       'original_arm_preparation_seconds': None,
                       'original_arm_preparation_measurement_id': None,
                       'status': 'not_recorded_before_interruption'},
            'receipt_scope': 'stored_owned_receipt_validated_without_opening_ledger',
            'execution_authorized': False, 'gold_read': False, 'provider_calls': 0,
            'parent_mutated': False, 'quality_claim': False,
        }
        # Native graph structures may contain tuples. Publish the JSON wire
        # representation so reopening compares the exact same value, including
        # on an idempotent second save; hashes retain canonical JSON semantics.
        return core.seal(core._parse(json.dumps(value, ensure_ascii=False,
                                               allow_nan=False).encode('utf-8')))


def save_response_recovery(root, value, *, base_api=None, parent_core=None):
    """Recompute before immutable publication; candidate never grants execution."""
    core = _core(parent_core)
    core._check_seal(value)
    rebuilt = prepare_response_recovery(root, value.get('parent_plan_sha256'),
                                       base_api=base_api, parent_core=core)
    if rebuilt != value:
        raise ResponseRecoveryError('Recovery candidate does not replay from original source bytes')
    path = Path(root) / 'artifacts/experiments/component_response_recovery' / value['sha256'] / 'recovery.json'
    core.write_new(root, path, value)
    return path


def load_response_recovery(root, identifier, *, base_api=None, parent_core=None):
    """Replay a saved candidate, including fields changed and then rehashed."""
    core = _core(parent_core)
    if not isinstance(identifier, str) or not _HASH.fullmatch(identifier):
        raise ResponseRecoveryError('Recovery lookup requires its exact hash')
    path = Path(root) / 'artifacts/experiments/component_response_recovery' / identifier / 'recovery.json'
    value = core.read(root, path)
    if value.get('sha256') != identifier or value.get('schema') != SCHEMA:
        raise ResponseRecoveryError('Recovery identity or schema changed')
    rebuilt = prepare_response_recovery(root, value.get('parent_plan_sha256'),
                                       base_api=base_api, parent_core=core)
    if value != rebuilt:
        raise ResponseRecoveryError('Recovery candidate does not replay from original source bytes')
    return value
