"""Exact, private continuation contracts for one failed component pilot stage.

This adapter does not replace the frozen inference package.  Production callers
load it by file in a child whose ``issueintel`` imports come from the registered
execution root.  Preview never opens credentials, a ledger, or restricted gold.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import importlib
import json
import math
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile


PLAN_SCHEMA = 'component_continuation_plan_v1'
AUTHORIZATION_SCHEMA = 'component_continuation_run_authorization_v1'
CALL_SCHEMA = 'component_ablation_call_v2'
STAGE_SCHEMA = 'component_ablation_stage_v2'
ARM_SCHEMA = 'component_ablation_arm_v2'
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILES = 6000
ADAPTER_NAMES = {'component_continuation.py', 'component_continuation_run.py',
                 'component_continuation_score.py'}
_HASH = re.compile('[a-f0-9]{64}')
_TRANSPORT = {'RemoteDisconnected', 'ConnectionResetError', 'ConnectionAbortedError',
              'ConnectionRefusedError', 'BrokenPipeError', 'TimeoutError', 'URLError',
              'SSLError', 'OSError', 'gaierror'}


class ContinuationError(ValueError):
    """An exact saved-source or continuation contract did not replay."""


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return sha256(raw.encode('utf-8')).hexdigest()


def seal(value):
    return {**value, 'sha256': digest(value)}


_seal = seal


def _pairs(rows):
    value = {}
    for key, item in rows:
        if key in value:
            raise ContinuationError('Duplicate JSON fields')
        value[key] = item
    return value


def _parse(raw):
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(ContinuationError('Nonfinite JSON')))
    except (UnicodeError, RecursionError) as error:
        raise ContinuationError('Invalid bounded JSON') from error


def safe_path(root, value, *, private=True, missing=False):
    root = Path(root).resolve()
    supplied = Path(value)
    if '..' in supplied.parts:
        raise ContinuationError('Parent traversal is forbidden')
    path = Path(os.path.abspath(supplied if supplied.is_absolute() else root / supplied))
    if not path.is_relative_to(root) or path == root:
        raise ContinuationError('Continuation path escapes its root')
    if private and (not path.is_relative_to(root / 'artifacts') or path == root / 'artifacts'):
        raise ContinuationError('Continuation state belongs in private artifacts')
    current = root
    for index, part in enumerate(path.relative_to(root).parts):
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ContinuationError('Links and reparse points are forbidden')
        if index < len(path.relative_to(root).parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ContinuationError('Continuation ancestor is not a directory')
    return path


_safe_path = safe_path


def _raw(root, path, *, max_bytes=MAX_FILE_BYTES, private=True):
    path = safe_path(root, path, private=private)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise ContinuationError('Continuation input is not a bounded regular file')
    with path.open('rb') as stream:
        # The maximum is an admission cap, not an allocation request for every
        # small journal. One extra byte also detects growth after lstat.
        raw = stream.read(info.st_size + 1)
    if len(raw) != info.st_size:
        raise ContinuationError('Continuation file changed during bounded read')
    safe_path(root, path, private=private)
    return raw


def read(root, path, *, max_bytes=MAX_FILE_BYTES):
    value = _parse(_raw(root, path, max_bytes=max_bytes))
    _check_seal(value)
    return value


_read = read


def _check_seal(value):
    if (not isinstance(value, dict) or value.get('sha256') !=
            digest({key: item for key, item in value.items() if key != 'sha256'})):
        raise ContinuationError('Continuation record seal changed')


def _mkdir(root, directory):
    directory = safe_path(root, directory, missing=True)
    directory.mkdir(parents=True, exist_ok=True)
    safe_path(root, directory)


def write_new(root, path, value):
    """Publish once. Identical re-publication is idempotent; changes are denied."""
    _check_seal(value)
    path = safe_path(root, path, missing=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_FILE_BYTES:
        raise ContinuationError('Continuation output exceeds byte bound')
    _mkdir(root, path.parent)
    try:
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if read(root, path) != value:
            raise ContinuationError('Immutable continuation record already exists') from None
    safe_path(root, path)
    return path


_write_new = write_new


def write(root, path, value):
    """Atomic journal update, restricted to newly owned overlay call/stage files."""
    _check_seal(value)
    path = safe_path(root, path, missing=True)
    if 'component_continuation' not in path.parts or path.parent.name not in {'calls', 'stages'}:
        raise ContinuationError('Mutable writes are restricted to owned overlay journals')
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_FILE_BYTES:
        raise ContinuationError('Continuation output exceeds byte bound')
    if path.exists():
        old = read(root, path)
        if old.get('status') != 'intent':
            if old != value:
                raise ContinuationError('Closed continuation journal cannot be replaced')
            return path
    _mkdir(root, path.parent)
    temporary = None
    try:
        with NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    safe_path(root, path)
    return path


_write = write


def inventory(root, directory):
    directory = safe_path(root, directory, missing=True)
    if not directory.exists():
        return {}
    files, total = {}, 0
    for current, folders, names in os.walk(directory, followlinks=False):
        safe_path(root, current)
        for name in folders:
            safe_path(root, Path(current) / name)
        for name in names:
            path = safe_path(root, Path(current) / name)
            raw = _raw(root, path)
            total += len(raw)
            if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ContinuationError('Continuation inventory exceeds bounds')
            files[path.relative_to(directory).as_posix()] = sha256(raw).hexdigest()
    return files


_inventory = inventory


def _apis(base_api=None):
    if base_api is not None:
        return base_api
    return (importlib.import_module('issueintel.benchmarks.component_ablation'),
            importlib.import_module('issueintel.benchmarks.component_ablation_run'))


def _adapter_sources(paths):
    if not isinstance(paths, (list, tuple)) or len(paths) != len(ADAPTER_NAMES):
        raise ContinuationError('Pin exactly the three continuation adapter modules')
    sources = []
    for supplied in paths:
        path = Path(os.path.abspath(supplied))
        if path.name not in ADAPTER_NAMES:
            raise ContinuationError('Unexpected adapter module')
        # Check every ancestor from the volume root before reading external code.
        checked = safe_path(Path(path.anchor), path, private=False)
        raw = _raw(Path(path.anchor), checked, private=False, max_bytes=4 * 1024 * 1024)
        sources.append({'path': str(path), 'sha256': sha256(raw).hexdigest()})
    if {Path(row['path']).name for row in sources} != ADAPTER_NAMES:
        raise ContinuationError('Adapter module list is duplicated or incomplete')
    return sorted(sources, key=lambda row: row['path'])


def _transport_failure(value):
    return (isinstance(value, dict) and value.get('error_type') == 'ProviderError'
            and value.get('terminal_output_error') is False
            and value.get('failure_phase') == 'request'
            and (value.get('transport_error_type') in _TRANSPORT or value.get('transport_reason_type') in _TRANSPORT)
            and value.get('http_status') is None and 'response_status' not in value
            and value.get('response_byte_count', 0) == 0
            and value.get('provider_code') == 'unknown')


def _replay_native_packs(root, spec, directory, results, stages, *, base_api=None):
    """Replay complete packs after the caller's full selected-cohort preflight.

    Only cases with complete packs need an additional inference context. Every
    selected source, including cases with no pack, is still checked by native
    preflight; native case/graph replay and the frozen pipeline remain intact.
    """
    registration, native = _apis(base_api)
    sources = registration.load_history_sources(root, spec, directory)
    for row in spec['cases']:
        outcomes = [arm for arm in results
                    if arm['case_id'] == row['case_id'] and arm['status'] == 'completed']
        if not outcomes:
            continue
        case, snapshot, receipt, graph = native._replay_context(root, spec, row, sources[row['case_id']])
        for arm in outcomes:
            used = []
            permitted = {ref['stage_key']: ref for ref in arm['stage_refs']}

            def replay(kind, inputs, produce):
                key = native.digest(native._stage_binding(spec, row, kind, inputs))
                saved = stages.get(key)
                if (key not in permitted or saved is None or saved['status'] != 'completed'
                        or saved['sha256'] != permitted[key]['sha256']):
                    raise native.UnsafeContinuation('Saved pack requires a missing native stage')
                if kind in {'initial_retrieval', 'focused_retrieval'} and native.digest(produce()) != saved['payload_sha256']:
                    raise native.UnsafeContinuation('Saved retrieval differs from native replay')
                used.append(key)
                return deepcopy(saved['payload'])

            def deny(*args, **kwargs):
                raise native.UnsafeContinuation('Offline pack replay cannot call a provider')

            deny.complete_structured = deny
            deny.complete_json = deny
            policy = arm['policy']
            pack = native.investigate(case, Path(snapshot.repository), complete_json=deny,
                provider='groq', model=spec['runtime']['model'], protocol='issue_time',
                snapshot=snapshot, graph=graph, query_receipt=receipt,
                top_k=policy['top_k'], independent_critic=policy['independent_critic'],
                max_output_tokens=policy['max_output_tokens'], refinement_rounds=int(policy['counter_search']),
                experiment_policy=policy, historical_sources=sources[row['case_id']],
                experiment_stage_executor=replay)
            if native.digest(pack) != native.digest(arm['pack']) or used != [ref['stage_key'] for ref in arm['stage_refs']]:
                raise native.UnsafeContinuation('Saved pack does not replay exactly from native stages')
    return True


def _base_state(root, identifier, *, base_api=None, full_replay=True):
    registration, runner = _apis(base_api)
    spec, directory = registration.load_registration(root, identifier)
    if full_replay:
        registration.preflight_component_ablation(root, identifier)
    files, arms, calls, stages = runner._records(root, spec, directory)
    if len(arms) != 5 or any(arm['case_id'] != spec['cases'][0]['case_id'] for arm in arms):
        raise ContinuationError('Recovery requires the exact five-arm first-case pilot')
    failed = [arm for arm in arms if arm['status'] != 'completed']
    if len(failed) != 1 or failed[0]['status'] != 'failed' or failed[0]['arm_id'] != 'plus_counter_search':
        raise ContinuationError('Recovery requires four completed arms and the failed counter-search arm')
    bad = [stage for stage in stages if stage['status'] != 'completed']
    if (len(bad) != 1 or bad[0]['status'] != 'failed'
            or bad[0]['binding']['stage'] not in {'seed_generation', 'refined_generation'}
            or bad[0]['owner_arm_id'] != failed[0]['arm_id']
            or bad[0]['terminal_output_failure'] or not _transport_failure(bad[0]['failure'])
            or len(bad[0]['call_ids']) != 1):
        raise ContinuationError('Only one closed generation transport failure is recoverable')
    by_call = {row['call_id']: row for row in calls}
    call = by_call[bad[0]['call_ids'][0]]
    if (call['status'] != 'failed' or call['response'] is not None or call['response_sha256'] is not None
            or not _transport_failure(call['failure']) or not call['receipts']
            or any(receipt['status'] != 'failed_bound' for receipt in call['receipts'].values())):
        raise ContinuationError('A returned or unknown paid response cannot authorize another attempt')
    # Native no-call replay proves the four complete packs and exact annotations.
    if full_replay:
        _replay_native_packs(root, spec, directory, arms,
                             {stage['stage_key']: stage for stage in stages}, base_api=base_api)
    return {'registration': spec, 'directory': directory, 'base_directory': directory,
            'inventory': files, 'arms': arms, 'calls': calls, 'stages': stages,
            'target_arm': failed[0], 'target_stage': bad[0], 'target_call': call}


def create_plan(root, identifier, *, adapter_paths, base_api=None):
    state = _base_state(root, identifier, base_api=base_api)
    return _make_plan(root, state, adapter_paths)


def prepare_plan(root, identifier, *, adapter_paths, base_api=None):
    """Preview and persist with one full native replay, never execution authority."""
    state = _base_state(root, identifier, base_api=base_api)
    plan = _make_plan(root, state, adapter_paths)
    # Refresh source/config/base-record hashes before writing. No paid action is
    # authorized by a preview; execution independently performs full admission.
    validate_plan(root, plan, base_api=base_api, full_replay=False)
    write_new(root, plan_path(root, plan), plan)
    return plan


def _make_plan(root, state, adapter_paths):
    spec, arm, stage, call = (state[key] for key in ('registration', 'target_arm', 'target_stage', 'target_call'))
    per_arm = spec['limits']['max_provider_calls_per_arm']
    total = spec['limits']['max_provider_calls_total']
    if per_arm != 5 or total != 625 or len(arm['call_ids']) > 3:
        raise ContinuationError('Unchanged call bounds cannot complete this continuation')
    return seal({'schema': PLAN_SCHEMA, 'registration_sha256': spec['sha256'],
        'base_directory': state['directory'].relative_to(Path(root).resolve()).as_posix(),
        'code_fingerprint_sha256': digest(spec['fingerprint']), 'protocol_sha256': spec['protocol_sha256'],
        'runtime_sha256': digest(spec['runtime']), 'case_ids': [row['case_id'] for row in spec['cases']],
        'adapter_sources': _adapter_sources(adapter_paths), 'base_files': state['inventory'],
        'target': {'case_id': arm['case_id'], 'arm_id': arm['arm_id'], 'arm_sha256': arm['sha256'],
            'stage_key': stage['stage_key'], 'stage_sha256': stage['sha256'],
            'call_id': call['call_id'], 'call_sha256': call['sha256'], 'request_sha256': call['request_sha256']},
        'completed_arms': sorted([{'case_id': row['case_id'], 'arm_id': row['arm_id'], 'sha256': row['sha256']}
                                 for row in state['arms'] if row['status'] == 'completed'], key=lambda row: row['arm_id']),
        'limits': {'max_provider_calls_per_arm': per_arm, 'max_provider_calls_total': total,
            'referenced_calls_before': len(arm['call_ids']), 'owned_calls_before': len(arm['owned_call_ids']),
            'total_calls_before': len(state['calls']), 'max_new_calls_for_target': per_arm - len(arm['call_ids']),
            'remaining_total_calls': total - len(state['calls'])},
        'execution_authorized': False, 'gold_read': False, 'quality_claim': False})


def validate_plan(root, plan, *, base_api=None, full_replay=True):
    _check_seal(plan)
    if plan.get('schema') != PLAN_SCHEMA:
        raise ContinuationError('Unsupported continuation plan')
    state = _base_state(root, plan.get('registration_sha256'), base_api=base_api, full_replay=full_replay)
    expected = _make_plan(root, state, [row['path'] for row in plan.get('adapter_sources', [])])
    if plan != expected:
        raise ContinuationError('Continuation plan source, adapter, inventory or bounds changed')
    return state


def overlay_directory(root, plan):
    return safe_path(root, Path('artifacts/experiments/component_continuation') / plan['sha256'], missing=True)


def plan_path(root, plan):
    return overlay_directory(root, plan) / 'plan.json'


def save_plan(root, plan, *, base_api=None):
    validate_plan(root, plan, base_api=base_api)
    return write_new(root, plan_path(root, plan), plan)


def load_plan(root, identifier, *, base_api=None):
    if isinstance(identifier, str) and _HASH.fullmatch(identifier):
        identifier = safe_path(root, Path('artifacts/experiments/component_continuation') / identifier / 'plan.json')
    plan = read(root, identifier)
    validate_plan(root, plan, base_api=base_api)
    return plan, overlay_directory(root, plan)


def verify_authorization(root, plan, authorization_path, *, requested_cases, base_api=None):
    _check_seal(plan)
    if plan.get('schema') != PLAN_SCHEMA:
        raise ContinuationError('Authorization requires an exact continuation plan')
    if type(requested_cases) is not int or requested_cases not in {1, 25}:
        raise ContinuationError('Continuation authorizes the pilot or exact registered matrix')
    value = _parse(_raw(root, authorization_path))
    registration, _ = _apis(base_api)
    flags = registration.ACCESS_FLAGS
    fields = {'schema', 'plan_sha256', 'registration_sha256', 'code_fingerprint_sha256',
              'protocol_sha256', 'adapter_sources', 'case_ids', 'provider', 'model', 'reasoning_effort',
              'max_output_tokens', 'max_provider_calls_per_arm', 'max_provider_calls_total',
              'authorized_case_count', 'phase', 'author_request', 'date', 'execution_authorized',
              'manual_transport_attempt_authorized', 'gold_scoring_authorized',
              'base_authorization_path', 'base_authorization_sha256', *flags}
    if not isinstance(value, dict) or set(value) != fields:
        raise ContinuationError('Need an exact manual continuation authorization')
    if (value['schema'] != AUTHORIZATION_SCHEMA or value['plan_sha256'] != plan['sha256']
            or value['registration_sha256'] != plan['registration_sha256']
            or value['code_fingerprint_sha256'] != plan['code_fingerprint_sha256']
            or value['protocol_sha256'] != plan['protocol_sha256'] or value['adapter_sources'] != plan['adapter_sources']
            or value['case_ids'] != plan['case_ids'] or value['provider'] != 'groq'
            or value['model'] != 'openai/gpt-oss-120b' or value['reasoning_effort'] != 'medium'
            or type(value['max_output_tokens']) is not int or value['max_output_tokens'] != 2048
            or type(value['authorized_case_count']) is not int or value['authorized_case_count'] not in {1, 25}
            or requested_cases > value['authorized_case_count']
            or value['phase'] != ('first_case_pilot' if value['authorized_case_count'] == 1 else 'registered_matrix')
            or type(value['max_provider_calls_per_arm']) is not int or value['max_provider_calls_per_arm'] != 5
            or type(value['max_provider_calls_total']) is not int
            or value['max_provider_calls_total'] != value['authorized_case_count'] * 25
            or value['execution_authorized'] is not True or value['manual_transport_attempt_authorized'] is not True
            or value['gold_scoring_authorized'] is not False or any(value[key] is not False for key in flags)
            or not isinstance(value['author_request'], str) or not value['author_request'].strip()
            or len(value['author_request']) > 8000):
        raise ContinuationError('Continuation authorization differs from exact plan and limits')
    try:
        date.fromisoformat(value['date'])
    except (ValueError, TypeError):
        raise ContinuationError('Continuation authorization requires a dated decision') from None
    native_path = safe_path(root, value['base_authorization_path'])
    if sha256(_raw(root, native_path)).hexdigest() != value['base_authorization_sha256']:
        raise ContinuationError('Native authorization bytes changed')
    spec, _ = registration.load_registration(root, plan['registration_sha256'])
    registration.verify_run_authorization(root, spec, native_path, requested_cases=requested_cases)
    return deepcopy(value)


def validate_call(value, plan, spec, *, base_api=None, ledger=None, require_closed=True):
    _check_seal(value)
    _, runner = _apis(base_api)
    if value.get('schema') != CALL_SCHEMA or value.get('attempt_id') != plan['sha256']:
        raise ContinuationError('Overlay call attempt binding changed')
    ordinal = value.get('ordinal')
    if type(ordinal) is not int or ordinal not in {0, 1}:
        raise ContinuationError('Overlay native call ordinal changed')
    if value.get('call_id') != digest([plan['sha256'], value['attempt_id'], value.get('stage_key'), ordinal,
                                     value.get('request_sha256')]):
        raise ContinuationError('Overlay call identity changed')
    if not isinstance(value.get('native_request_sha256'), str) or not _HASH.fullmatch(value['native_request_sha256']):
        raise ContinuationError('Native reconstructed request binding changed')
    if (value['stage_key'] != plan['target']['stage_key'] or ordinal != 0) and value['native_request_sha256'] != value['request_sha256']:
        raise ContinuationError('A fresh native request cannot be substituted')
    if value['stage_key'] == plan['target']['stage_key'] and ordinal == 0 and value['request_sha256'] != plan['target']['request_sha256']:
        raise ContinuationError('Transport continuation must send the original frozen request')
    native = {key: item for key, item in value.items() if key not in {'attempt_id', 'ordinal', 'native_request_sha256'}}
    native['schema'] = runner.CALL_SCHEMA
    runner._validate_call(native, spec, ledger=ledger, require_closed=require_closed)
    return value


def validate_stage(value, plan, spec, row, *, base_api=None):
    _check_seal(value)
    _, runner = _apis(base_api)
    if value.get('schema') != STAGE_SCHEMA or value.get('attempt_id') != plan['sha256']:
        raise ContinuationError('Overlay stage attempt binding changed')
    native = {key: item for key, item in value.items() if key != 'attempt_id'}
    native['schema'] = runner.STAGE_SCHEMA
    runner._validate_stage(native, spec, row)
    return value


def _native_payload(stage, calls, runner):
    maximum = 2 if stage['binding']['stage'] in {'seed_generation', 'refined_generation'} else 1 if stage['binding']['stage'] == 'verification' else 0
    if len(stage['call_ids']) > maximum:
        raise ContinuationError('Overlay stage exceeds native call slots')
    paid = [calls[cid] for cid in stage['call_ids']]
    for ordinal, call in enumerate(paid):
        if (call['ordinal'] != ordinal or call['stage_key'] != stage['stage_key']
                or call['owner_arm_id'] != stage['owner_arm_id']
                or call['case_id'] != stage['binding']['case_id']):
            raise ContinuationError('Overlay stage changes paid ownership or ordinal')
    if stage['status'] != 'completed':
        return
    kind = stage['binding']['stage']
    if kind in {'seed_generation', 'refined_generation'}:
        annotation = stage['payload'].get('annotation', {})
        if not paid or paid[-1]['status'] != 'returned':
            raise ContinuationError('Completed generation lacks a returned native call')
        expected = {'hypotheses': [{key: row[key] for key in runner.MODEL_HYPOTHESIS_FIELDS}
                                   for row in annotation.get('hypotheses', [])],
                    'abstention': {key: annotation.get('abstention', {}).get(key) for key in ('label', 'reason')}}
        if (paid[-1]['response'] != expected or annotation.get('provenance', {}).get('repair_count') != len(paid) - 1):
            raise ContinuationError('Native annotation differs from its own attempt response or repair count')
    elif kind == 'verification':
        verification = stage['payload'].get('verification', {})
        if verification.get('kind') == 'llm_separate_pass':
            if len(paid) != 1 or paid[0]['status'] != 'returned' or paid[0]['response'] != {'reviews': verification.get('reviews')}:
                raise ContinuationError('Native critique differs from owned response')
        elif paid:
            raise ContinuationError('Structural verification acquired a paid call')


def load_effective_view(root, plan, overlay_directory=None, *, base_api=None, require_closed=True, full_replay=True):
    state = validate_plan(root, plan, base_api=base_api, full_replay=full_replay)
    registration, runner = _apis(base_api)
    spec = state['registration']
    directory = globals()['overlay_directory'](root, plan)
    if overlay_directory is not None and Path(overlay_directory) != directory:
        raise ContinuationError('Overlay location differs from exact plan')
    files = inventory(root, directory)
    rows = {row['case_id']: row for row in spec['cases']}
    calls, stages, arms = {}, {}, {}
    for name in files:
        parts = Path(name).parts
        if not parts or parts[0] != 'cases':
            continue
        if len(parts) != 4 or parts[2] not in {'calls', 'stages', 'arms'}:
            raise ContinuationError('Unexpected overlay inference inventory')
        value = read(root, directory / name)
        case_id = value.get('case_id', value.get('binding', {}).get('case_id'))
        if case_id not in rows or parts[1] != digest(case_id):
            raise ContinuationError('Overlay case location changed')
        if parts[2] == 'calls':
            validate_call(value, plan, spec, base_api=base_api, require_closed=require_closed)
            if parts[3] != value['call_id'] + '.json':
                raise ContinuationError('Overlay call filename changed')
            calls[value['call_id']] = value
        elif parts[2] == 'stages':
            validate_stage(value, plan, spec, rows[case_id], base_api=base_api)
            if parts[3] != value['stage_key'] + '.json':
                raise ContinuationError('Overlay stage filename changed')
            stages[value['stage_key']] = value
        else:
            if parts[3] != value.get('arm_id', '') + '.json':
                raise ContinuationError('Overlay arm filename changed')
            arms[(case_id, value['arm_id'])] = value
    base_calls = {row['call_id']: row for row in state['calls']}
    base_stages = {row['stage_key']: row for row in state['stages']}
    if set(calls) & set(base_calls):
        raise ContinuationError('New call collides with original paid intent')
    all_calls = {**base_calls, **calls}
    execution_stages = {**base_stages, **stages}
    for key, stage in stages.items():
        if key in base_stages and key != plan['target']['stage_key']:
            raise ContinuationError('A completed original stage cannot be replaced')
        _native_payload(stage, calls, runner)
    if set(calls) != {cid for stage in stages.values() for cid in stage['call_ids']}:
        raise ContinuationError('Overlay contains an orphan paid call')
    reservations = {}
    for cid, call in all_calls.items():
        for rid in call['receipts']:
            if rid in reservations and reservations[rid] != cid:
                raise ContinuationError('Receipt has multiple paid owners')
            reservations[rid] = cid
    owned_counts = {}
    for call in all_calls.values():
        owner = (call['case_id'], call['owner_arm_id'])
        owned_counts[owner] = owned_counts.get(owner, 0) + 1
    if any(count > 5 for count in owned_counts.values()):
        raise ContinuationError('Owned attempts exceed the unchanged per-arm bound')
    if len(all_calls) > 625:
        raise ContinuationError('Original plus continuation calls exceed registered total')
    effective = {(arm['case_id'], arm['arm_id']): arm for arm in state['arms']}
    for key, arm in arms.items():
        _check_seal(arm)
        expected_fields = set(state['target_arm']) | {'attempt_id', 'audit_stage_refs', 'audit_arm_refs',
                                                    'audit_call_ids', 'continuation_overhead'}
        if set(arm) != expected_fields:
            raise ContinuationError('Effective arm fields changed')
        original = effective.get(key)
        if original is not None and original['status'] == 'completed':
            raise ContinuationError('An original completed cell cannot be replaced')
        if (arm.get('schema') != ARM_SCHEMA or arm.get('attempt_id') != plan['sha256']
                or arm.get('experiment_sha256') != spec['sha256'] or arm.get('source') != rows[key[0]]['source']
                or arm.get('arm_id') not in registration.ARMS or arm.get('policy') != runner._policy(spec, key[1])
                or arm.get('status') not in {'completed', 'failed', 'unavailable'}):
            raise ContinuationError('Effective arm source or policy changed')
        refs = arm.get('stage_refs', [])
        if (not isinstance(refs, list) or len(refs) > 5
                or len({ref.get('stage_key') for ref in refs}) != len(refs)):
            raise ContinuationError('Effective arm stage inventory changed')
        timing_fields = ('pipeline_latency_seconds', 'preparation_seconds')
        if any(type(arm.get(field)) not in (int, float) or not math.isfinite(arm[field]) or arm[field] < 0 for field in timing_fields):
            raise ContinuationError('Effective timing must retain finite observations')
        overhead = arm.get('continuation_overhead')
        overhead_fields = {'pipeline_latency_seconds', 'preparation_seconds', 'preparation_measurement_id',
                           'preparation_owner_arm_id'}
        if original is not None:
            if (not isinstance(overhead, dict) or set(overhead) != overhead_fields
                    or any(type(overhead.get(field)) not in (int, float) or not math.isfinite(overhead[field]) or overhead[field] < 0 for field in timing_fields)
                    or not isinstance(overhead.get('preparation_measurement_id'), str)
                    or not _HASH.fullmatch(overhead['preparation_measurement_id'])
                    or overhead.get('preparation_owner_arm_id') != key[1]
                    or any(arm.get(field) != original.get(field) for field in timing_fields +
                           ('preparation_measurement_id', 'preparation_owner_arm_id'))):
                raise ContinuationError('Original timing and separate continuation overhead changed')
        elif overhead != {}:
            raise ContinuationError('Fresh matrix arms use their own native timing observations')
        execution_calls = []
        for ref in refs:
            stage = execution_stages.get(ref.get('stage_key'))
            if (stage is None or set(ref) != {'stage_key', 'sha256', 'owner_arm_id', 'cache_hit'}
                    or ref['sha256'] != stage['sha256'] or ref['owner_arm_id'] != stage['owner_arm_id']
                    or type(ref['cache_hit']) is not bool):
                raise ContinuationError('Effective execution stage reference changed')
            for cid in stage['call_ids']:
                if cid not in execution_calls:
                    execution_calls.append(cid)
        audit_calls = arm.get('audit_call_ids', [])
        expected_audit = state['target_stage']['call_ids'] if original is not None else []
        expected_audit_refs = ([{'stage_key': state['target_stage']['stage_key'], 'sha256': state['target_stage']['sha256'],
                                'owner_arm_id': state['target_stage']['owner_arm_id'], 'cache_hit': True}]
                               if original is not None else [])
        if (audit_calls != expected_audit or arm.get('audit_stage_refs', []) != expected_audit_refs
                or arm.get('audit_arm_refs', []) != ([{'case_id': original['case_id'], 'arm_id': original['arm_id'],
                                                     'sha256': original['sha256']}] if original is not None else [])):
            raise ContinuationError('Effective arm hid or changed the original failure audit')
        accounting = list(dict.fromkeys(execution_calls + audit_calls))
        if (arm.get('call_ids') != accounting or len(accounting) > 5
                or arm.get('owned_call_ids') != [cid for cid in accounting if all_calls[cid]['owner_arm_id'] == key[1]]):
            raise ContinuationError('Effective arm changed bounded paid accounting')
        owner_calls = [cid for cid, call in all_calls.items() if (call['case_id'], call['owner_arm_id']) == key]
        if len(owner_calls) > 5 or set(arm['owned_call_ids']) != set(owner_calls):
            raise ContinuationError('Effective arm has unreferenced owned paid attempts')
        if arm['status'] == 'completed':
            pack = arm.get('pack')
            generated = [execution_stages[ref['stage_key']]['payload']['annotation'] for ref in refs
                         if execution_stages[ref['stage_key']]['binding']['stage'] in {'seed_generation', 'refined_generation'}]
            if (not isinstance(pack, dict) or not refs or not generated
                    or any(execution_stages[ref['stage_key']]['status'] != 'completed' for ref in refs)
                    or pack.get('annotation') != generated[-1] or not pack.get('verification')
                    or pack.get('case', {}).get('case_id') != key[0] or pack['case'].get('split') != 'main_dev'
                    or pack.get('experiment_policy') != arm['policy']
                    or pack.get('leakage_audit', {}).get('gold_in_inference') is not False
                    or pack.get('leakage_audit', {}).get('strict_issue_time_verified') is not True):
                raise ContinuationError('Effective completed pack differs from saved native stages')
        elif arm.get('pack') is not None:
            raise ContinuationError('Failed effective arm cannot claim a completed pack')
        effective[key] = arm
    return {'registration': spec, 'directory': directory, 'base_directory': state['directory'], 'plan': plan,
        'base_inventory': state['inventory'], 'base_files': state['inventory'], 'files': files,
        'results': list(effective.values()), 'calls': list(all_calls.values()),
        'stages': state['stages'] + list(stages.values()), 'execution_stages': execution_stages,
        'base_calls': state['calls'], 'base_stages': state['stages'], 'overlay_calls': list(calls.values()),
        'base_arms': state['arms'], 'original_results': state['arms'],
        'overlay_stages': list(stages.values()), 'audit_arms': [state['target_arm']],
        'audit_stages': [state['target_stage']], 'audit_calls': [state['target_call']]}
