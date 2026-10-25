"""Append-only research execution over a frozen component-ablation registration.

This adapter lives outside the frozen source tree.  All inference, prompts,
retrieval and output validation come from the registered native implementation.
Only new journal records are written; original results and receipts are evidence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import time


ARM_SCHEMA = 'component_ablation_arm_v2'
STAGE_SCHEMA = 'component_ablation_stage_v2'
CALL_SCHEMA = 'component_ablation_call_v2'
SEAL_SCHEMA = 'component_continuation_inference_seal_v2'


def _extension(name):
    """Load a sibling adapter without adding its checkout to native sys.path."""
    qualified = 'issueintel.benchmarks.' + name
    try:
        return importlib.import_module(qualified)
    except ModuleNotFoundError as error:
        if error.name != qualified:
            raise
    singleton = '_issueintel_' + name
    if singleton not in sys.modules:
        spec = importlib.util.spec_from_file_location(singleton, Path(__file__).with_name(name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[singleton] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[singleton]
            raise
    return sys.modules[singleton]


def _api(base_api=None):
    if base_api is not None:
        return base_api
    return (importlib.import_module('issueintel.benchmarks.component_ablation'),
            importlib.import_module('issueintel.benchmarks.component_ablation_run'))


def _native_origins(root, base_api):
    """An isolated production process must import every native module from root."""
    expected = (Path(root).resolve() / 'src' / 'issueintel').resolve()
    registration, native = base_api
    for value in (registration, native):
        path = Path(value.__file__).resolve()
        if expected not in path.parents:
            raise ValueError('Native component APIs must originate in the frozen root')
    for name, module in tuple(sys.modules.items()):
        if name == 'issueintel' or name.startswith('issueintel.'):
            filename = getattr(module, '__file__', None)
            # These three explicit adapters are the sole code allowed elsewhere.
            if filename and name not in {
                    'issueintel.benchmarks.component_continuation',
                    'issueintel.benchmarks.component_continuation_run',
                    'issueintel.benchmarks.component_continuation_score'}:
                if expected not in Path(filename).resolve().parents:
                    raise ValueError('An inference dependency originates outside the frozen root')


def _core():
    return _extension('component_continuation')


def _adapter_origins(plan):
    """Bind approved file hashes to the three modules actually executing."""
    core = _core()
    score = _extension('component_continuation_score')
    actual = core._adapter_sources([Path(core.__file__), Path(__file__), Path(score.__file__)])
    if actual != plan.get('adapter_sources'):
        raise core.ContinuationError('Executing adapter files differ from the approved continuation plan')


def _seal(value, native):
    return native._seal(value)


def _write(root, path, value, *, new=False):
    core = _core()
    return (core.write_new if new else core.write)(root, path, value)


def _case_directory(root, directory, case_id, native):
    return native._case_directory(root, directory, case_id)


def _record_path(root, directory, case_id, kind, identifier, native):
    return _case_directory(root, directory, case_id, native) / kind / (identifier + '.json')


def _overlay_records(root, directory, native):
    """Read all owned records, including unfinished intents, before admission."""
    core = _core()
    base = Path(directory) / 'cases'
    files = core.inventory(root, base)
    if len(files) > native.MAX_FILES:
        raise native.UnsafeContinuation('Inference inventory exceeds its limit')
    inventory, total = {}, 0
    calls, stages, arms = [], [], []
    for name, raw_sha in files.items():
        parts = Path(name).parts
        if len(parts) != 3 or parts[1] not in {'arms', 'calls', 'stages'} or Path(name).suffix != '.json':
            raise native.UnsafeContinuation('Unexpected inference inventory file')
        path = core.safe_path(root, base / name)
        total += path.lstat().st_size
        if total > native.MAX_TOTAL_BYTES:
            raise native.UnsafeContinuation('Inference inventory exceeds its limit')
        value = core.read(root, path, max_bytes=native.MAX_FILE_BYTES)
        inventory['cases/' + Path(name).as_posix()] = raw_sha
        kind = parts[1]
        {'calls': calls, 'stages': stages, 'arms': arms}[kind].append(value)
    return inventory, arms, calls, stages


def _execution_stage_map(view):
    value = view.get('execution_stages')
    if isinstance(value, dict):
        return value
    return {row['stage_key']: row for row in view['stages']}


def _same_native_request(rebuilt, frozen):
    """Permit only JSON-object key ordering introduced by durable payload reads."""
    left, right = deepcopy(rebuilt), deepcopy(frozen)
    canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if canonical(left) == canonical(right):
        return True
    if not isinstance(left.get('messages'), list) or not isinstance(right.get('messages'), list):
        return False
    if len(left['messages']) != len(right['messages']):
        return False
    for a, b in zip(left['messages'], right['messages']):
        if a == b:
            continue
        if a.get('role') != 'user' or b.get('role') != 'user' or set(a) != set(b):
            return False
        try:
            parsed_a, parsed_b = json.loads(a['content']), json.loads(b['content'])
        except (ValueError, TypeError, KeyError):
            return False
        if not isinstance(parsed_a, dict) or canonical(parsed_a) != canonical(parsed_b):
            return False
        a['content'], b['content'] = parsed_a, parsed_b
    return canonical(left) == canonical(right)


def _inference_inventory(root, directory):
    return {name: checksum for name, checksum in _core().inventory(root, directory).items()
            if name != 'inference-sealed.json' and Path(name).parts[0] != 'scores'}


def _matrix_references(view, native):
    """A seal accounts for execution and failed audit work by content identity."""
    results, calls, stages = view['results'], view['calls'], view['stages']
    referenced_stages = {ref['sha256'] for arm in results
                         for ref in arm['stage_refs'] + arm.get('audit_stage_refs', [])}
    if (referenced_stages != {stage['sha256'] for stage in stages}
            or {cid for arm in results for cid in arm['call_ids']} != {call['call_id'] for call in calls}
            or any(stage['status'] == 'intent' for stage in stages)):
        raise native.UnsafeContinuation('Effective matrix contains unfinished or unreferenced native work')
    owners = {(arm['case_id'], arm['arm_id']): arm for arm in results}
    for stage in stages:
        owner = owners.get((stage['binding']['case_id'], stage['owner_arm_id']))
        if owner is None or stage['sha256'] not in {ref['sha256'] for ref in
                owner['stage_refs'] + owner.get('audit_stage_refs', [])}:
            raise native.UnsafeContinuation('Effective native stage owner has no terminal execution or audit reference')
    return True


def _replay_completed(root, view, base_api):
    """Replay accepted native payloads; raw returned responses are never replayed."""
    registration, native = base_api
    spec, base_directory = view['registration'], view['base_directory']
    registration.preflight_component_ablation(root, spec['sha256'])
    return _core()._replay_native_packs(root, spec, base_directory, view['results'],
                                       _execution_stage_map(view), base_api=base_api)


def _pilot(view, base_api):
    registration, native = base_api
    spec = view['registration']
    first = spec['cases'][0]['case_id']
    arms = [row for row in view['results'] if row['case_id'] == first]
    if ({row['arm_id'] for row in arms} != set(registration.ARMS)
            or len(arms) != len(registration.ARMS) or any(row['status'] != 'completed' for row in arms)):
        raise native.UnsafeContinuation('All five effective first-case pilot packs must complete before matrix expansion')
    return _seal({'schema': 'component_continuation_pilot_gate_v2',
        'continuation_plan_sha256': view['plan']['sha256'], 'registration_sha256': spec['sha256'],
        'case_id': first, 'arm_results': {row['arm_id']: row['sha256'] for row in arms},
        'all_native_stages_completed': True, 'all_receipts_closed': True,
        'gold_read': False, 'quality_claim': False}, native)


class _Execution:
    def __init__(self, root, plan, directory, view, row, arm_id, sources, authorization_path,
                 requested_cases, provider_factory, base_api):
        self.root, self.plan, self.directory, self.view = root, plan, directory, view
        self.registration, self.native = base_api
        self.base_api, self.spec, self.row = base_api, view['registration'], row
        self.arm_id, self.sources = arm_id, sources
        self.authorization_path, self.requested_cases = authorization_path, requested_cases
        self.provider_factory = provider_factory
        self.stage_refs, self.call_ids, self.owned_call_ids = [], [], []
        self.current = None
        self.audit_call_ids = []
        self.audit_stage_refs, self.audit_arm_refs = [], []
        if (row['case_id'], arm_id) == (plan['target']['case_id'], plan['target']['arm_id']):
            target = plan['target']
            previous = next(value for value in view['audit_arms'] if value['sha256'] == target['arm_sha256'])
            failed = next(value for value in view['audit_stages'] if value['sha256'] == target['stage_sha256'])
            self.audit_call_ids = list(failed['call_ids'])
            self.audit_stage_refs = [{'stage_key': failed['stage_key'], 'sha256': failed['sha256'],
                                     'owner_arm_id': failed['owner_arm_id'], 'cache_hit': True}]
            self.audit_arm_refs = [{'case_id': row['case_id'], 'arm_id': arm_id, 'sha256': previous['sha256']}]
        self.config = replace(self.native.RuntimeConfig.load(root), model=self.spec['runtime']['model'],
            reasoning_effort=self.spec['runtime']['reasoning_effort'], max_output_tokens=self.spec['runtime']['max_output_tokens'])

    def guard(self):
        core = _core()
        core.validate_plan(self.root, self.plan, base_api=self.base_api, full_replay=False)
        core.verify_authorization(self.root, self.plan, self.authorization_path,
                                  requested_cases=self.requested_cases, base_api=self.base_api)
        return self.native._replay_context(self.root, self.spec, self.row, self.sources)

    def _path(self, kind, identifier):
        return _record_path(self.root, self.directory, self.row['case_id'], kind, identifier, self.native)

    def _validate_call(self, value, *, closed=True):
        core = _core()
        if value['schema'] == self.native.CALL_SCHEMA:
            return self.native._validate_call(value, self.spec, ledger=self.config.ledger(), require_closed=closed)
        return core.validate_call(value, self.plan, self.spec, base_api=self.base_api,
                                  ledger=self.config.ledger(), require_closed=closed)

    def _all_calls(self):
        _, _, own, _ = _overlay_records(self.root, self.directory, self.native)
        return list(self.view['base_calls']) + own

    def _reference(self, state, *, hit):
        self.stage_refs.append({'stage_key': state['stage_key'], 'sha256': state['sha256'],
                               'owner_arm_id': state['owner_arm_id'], 'cache_hit': hit})
        for cid in state['call_ids']:
            if cid not in self.call_ids:
                self.call_ids.append(cid)
            if state['owner_arm_id'] == self.arm_id and cid not in self.owned_call_ids:
                self.owned_call_ids.append(cid)

    def stage(self, kind, inputs, produce):
        native, core = self.native, _core()
        if kind not in native.STAGES or self.current is not None:
            raise native.UnsafeContinuation('Unknown or nested continuation stage')
        self.guard()
        binding = native._stage_binding(self.spec, self.row, kind, inputs)
        key = native.digest(binding)
        path = self._path('stages', key)
        saved = native._read(self.root, path) if path.exists() else _execution_stage_map(self.view).get(key)
        # The one pinned failed transport stage is audit evidence, never execution.
        if saved is not None and saved['sha256'] == self.plan['target']['stage_sha256']:
            saved = None
        if saved is not None:
            if saved['schema'] == native.STAGE_SCHEMA:
                native._validate_stage(saved, self.spec, self.row)
            else:
                core.validate_stage(saved, self.plan, self.spec, self.row, base_api=self.base_api)
            if saved['binding'] != binding or saved['status'] == 'intent':
                raise native.UnsafeContinuation('Interrupted native stage cannot be retried')
            all_calls = {call['call_id']: call for call in self._all_calls()}
            for cid in saved['call_ids']:
                paid = self._validate_call(all_calls[cid])
                if paid['stage_key'] != key or paid['owner_arm_id'] != saved['owner_arm_id']:
                    raise native.UnsafeContinuation('Cached stage changed its paid owner')
            self._reference(saved, hit=True)
            if saved['status'] == 'failed':
                raise native.SavedStageFailure(saved['terminal_output_failure'])
            return deepcopy(saved['payload'])
        state = {'schema': STAGE_SCHEMA, 'experiment_sha256': self.spec['sha256'],
            'attempt_id': self.plan['sha256'], 'stage_key': key, 'binding': binding,
            'owner_arm_id': self.arm_id, 'status': 'intent', 'payload': None, 'payload_sha256': None,
            'call_ids': [], 'failure': None, 'terminal_output_failure': False, 'latency_seconds': None}
        self.current = state
        _write(self.root, path, _seal(state, native), new=True)
        started = time.monotonic()
        try:
            payload = produce()
            self.guard()
            all_calls = {call['call_id']: call for call in self._all_calls()}
            for cid in state['call_ids']:
                self._validate_call(all_calls[cid])
            state.update(status='completed', payload=deepcopy(payload), payload_sha256=native.digest(payload),
                         latency_seconds=round(time.monotonic() - started, 6))
            core.validate_stage(_seal(state, native), self.plan, self.spec, self.row, base_api=self.base_api)
            _write(self.root, path, _seal(state, native))
            self._reference(_seal(state, native), hit=False)
            return deepcopy(payload)
        except Exception as error:
            rejected = self._critic_rejected(error, state)
            classified = native.CriticOutputRejected('Frozen critic response failed its native contract') if rejected else error
            state.update(status='failed', payload=None, payload_sha256=None,
                failure=native.failure_metadata(classified),
                terminal_output_failure=isinstance(classified, native.AnnotationError) or native.output_rejected(classified),
                latency_seconds=round(time.monotonic() - started, 6))
            _write(self.root, path, _seal(state, native))
            self._reference(_seal(state, native), hit=False)
            if rejected:
                raise classified from None
            raise
        finally:
            self.current = None

    def _critic_rejected(self, error, state):
        if type(error) is not ValueError or state['binding']['stage'] != 'verification' or len(state['call_ids']) != 1:
            return False
        try:
            self.guard()
            call = self._validate_call(self.native._read(self.root, self._path('calls', state['call_ids'][0])))
            if call['status'] != 'returned':
                return False
            payload = json.loads(call['request']['messages'][1]['content'])
            if set(payload) != {'hypotheses', 'evidence'}:
                return False
        except (ValueError, OSError, KeyError, TypeError, RuntimeError):
            return False
        verifier = importlib.import_module('issueintel.investigation.verifier')
        try:
            verifier.validate_critique(call['response'], {'hypotheses': payload['hypotheses']}, payload['evidence'])
        except ValueError:
            return True
        return False

    def __call__(self, messages, max_output_tokens=2048):
        return self.complete_json(messages, max_output_tokens=max_output_tokens)

    def complete_json(self, messages, *, max_output_tokens=2048):
        return self._call(messages, max_output_tokens, None)

    def complete_structured(self, messages, *, max_output_tokens=2048, schema):
        return self._call(messages, max_output_tokens, schema)

    def _call(self, messages, maximum, schema):
        native = self.native
        if self.current is None or self.current['binding']['stage'] not in {'seed_generation', 'refined_generation', 'verification'}:
            raise native.UnsafeContinuation('A continuation provider requires an active owned native stage')
        if maximum != self.spec['runtime']['max_output_tokens']:
            raise native.UnsafeContinuation('Continuation output bound changed')
        self.guard()
        calls = self._all_calls()
        for call in calls:
            self._validate_call(call)
        referenced = set(self.call_ids + self.audit_call_ids + self.current['call_ids'])
        owned = [call for call in calls if call['case_id'] == self.row['case_id'] and call['owner_arm_id'] == self.arm_id]
        maximum_calls = self.plan['limits']['max_provider_calls_per_arm']
        if len(referenced) >= maximum_calls or len(owned) >= maximum_calls:
            raise native.UnsafeContinuation('Continuation per-arm call bound includes old and shared attempts')
        prefix_maximum = min(self.plan['limits']['max_provider_calls_total'],
                             self.requested_cases * len(self.spec['arms']) * maximum_calls)
        if len(calls) >= prefix_maximum:
            raise native.UnsafeContinuation('Continuation experiment call bound includes every old attempt')
        request = {'messages': deepcopy(messages), 'schema': deepcopy(schema), 'max_output_tokens': maximum,
                   'model': self.spec['runtime']['model'], 'reasoning_effort': self.spec['runtime']['reasoning_effort']}
        native_request_sha256 = native.digest(request)
        ordinal = len(self.current['call_ids'])
        if self.current['stage_key'] == self.plan['target']['stage_key'] and ordinal == 0:
            previous = next(call for call in self.view['base_calls'] if call['call_id'] == self.plan['target']['call_id'])
            if not _same_native_request(request, previous['request']):
                raise native.UnsafeContinuation('Recovery first request differs from the pinned failed request')
            request = deepcopy(previous['request'])
        call_id = native.digest([self.plan['sha256'], self.plan['sha256'], self.current['stage_key'], ordinal, native.digest(request)])
        path = self._path('calls', call_id)
        state = {'schema': CALL_SCHEMA, 'experiment_sha256': self.spec['sha256'],
            'attempt_id': self.plan['sha256'], 'ordinal': ordinal, 'call_id': call_id,
            'case_id': self.row['case_id'], 'owner_arm_id': self.arm_id, 'stage_key': self.current['stage_key'],
            'request_sha256': native.digest(request), 'native_request_sha256': native_request_sha256,
            'request': request, 'status': 'intent',
            'receipts': {}, 'response': None, 'response_sha256': None, 'failure': None, 'latency_seconds': None}
        # Both ownership journals exist before any provider/HTTP construction.
        _write(self.root, path, _seal(state, native), new=True)
        self.current['call_ids'].append(call_id)
        if call_id not in self.owned_call_ids:
            self.owned_call_ids.append(call_id)
        _write(self.root, self._path('stages', self.current['stage_key']), _seal(self.current, native))

        def on_receipt(record):
            native._receipt(record, self.spec['runtime']['model'])
            if state['receipts'] and record['reservation_id'] not in state['receipts']:
                raise native.UnsafeContinuation('One continuation HTTP attempt cannot acquire two receipts')
            state['receipts'][record['reservation_id']] = deepcopy(record)
            _write(self.root, path, _seal(state, native))

        started = time.monotonic()
        try:
            provider = self.provider_factory(self.config, on_receipt=on_receipt,
                call_context={'category': 'ARTIFACT_GENERATION_COST',
                    'stage': 'component_ablation_' + self.current['binding']['stage'],
                    'reasoning_effort': self.spec['runtime']['reasoning_effort'], 'diagnostic': True})
            if schema is None:
                response = provider.complete_json(request['messages'], max_output_tokens=maximum)
            else:
                response = provider.complete_structured(request['messages'], max_output_tokens=maximum, schema=request['schema'])
            state.update(status='returned', response=deepcopy(response), response_sha256=native.digest(response),
                         latency_seconds=round(time.monotonic() - started, 6))
            self._validate_call(_seal(state, native))
            _write(self.root, path, _seal(state, native))
            self.guard()
            return deepcopy(response)
        except Exception as error:
            if state['status'] != 'returned':
                state.update(status='failed', failure=native.failure_metadata(error),
                             latency_seconds=round(time.monotonic() - started, 6))
                _write(self.root, path, _seal(state, native))
            raise


def seal_effective_view(root, plan, overlay_directory, *, base_api=None):
    api = _api(base_api)
    registration, native = api
    core = _core()
    _adapter_origins(plan)
    view = core.load_effective_view(root, plan, overlay_directory, base_api=api)
    spec = view['registration']
    expected = {(row['case_id'], arm) for row in spec['cases'] for arm in registration.ARMS}
    if {(arm['case_id'], arm['arm_id']) for arm in view['results']} != expected or len(view['results']) != 125:
        raise native.UnsafeContinuation('Complete registered 125-cell effective matrix is required for sealing')
    _pilot(view, api)
    _matrix_references(view, native)
    _replay_completed(root, view, api)
    inventory = _inference_inventory(root, overlay_directory)
    seal = _seal({'schema': SEAL_SCHEMA, 'continuation_plan_sha256': plan['sha256'],
        'registration_sha256': spec['sha256'], 'protocol_sha256': spec['protocol_sha256'],
        'history_input_sha256': spec['history_input']['sha256'], 'base_files': plan['base_files'],
        'files': inventory, 'selected_case_ids': plan['case_ids'], 'arms': list(registration.ARMS),
        'terminal_results': len(view['results']),
        'completed_results': sum(arm['status'] == 'completed' for arm in view['results']),
        'effective_results': {row['case_id'] + '/' + row['arm_id']: row['sha256'] for row in view['results']},
        'audit_arm_sha256s': sorted(row['sha256'] for row in view['audit_arms']),
        'provider_calls': len(view['calls']), 'gold_read': False, 'quality_claim': False,
        'holdout_or_live_access': False}, native)
    path = Path(overlay_directory) / 'inference-sealed.json'
    if path.exists():
        if native._read(root, path) != seal:
            raise native.UnsafeContinuation('An effective inference seal cannot be replaced')
    else:
        _write(root, path, seal, new=True)
    return seal


def load_sealed_continuation(root, identifier, *, base_api=None):
    """Validate all 125 effective outcomes before any selected Main-Dev gold read."""
    api = _api(base_api)
    _, native = api
    if base_api is None:
        _native_origins(root, api)
    plan, directory = _core().load_plan(root, identifier, base_api=api)
    _adapter_origins(plan)
    saved = native._read(root, directory / 'inference-sealed.json')
    # Recompute read-only: sealing must never publish a missing/changed seal here.
    view = _core().load_effective_view(root, plan, directory, base_api=api)
    before = _inference_inventory(root, directory)
    if saved.get('schema') != SEAL_SCHEMA or saved.get('files') != before:
        raise native.UnsafeContinuation('Effective inference seal inventory changed')
    _pilot(view, api)
    _matrix_references(view, native)
    _replay_completed(root, view, api)
    spec = view['registration']
    expected = {(row['case_id'], arm) for row in spec['cases'] for arm in api[0].ARMS}
    if (len(view['results']) != 125 or {(row['case_id'], row['arm_id']) for row in view['results']} != expected
            or saved.get('continuation_plan_sha256') != plan['sha256']
            or saved.get('registration_sha256') != spec['sha256']
            or saved.get('protocol_sha256') != spec['protocol_sha256']
            or saved.get('history_input_sha256') != spec['history_input']['sha256']
            or saved.get('base_files') != plan['base_files'] or saved.get('selected_case_ids') != plan['case_ids']
            or saved.get('arms') != list(api[0].ARMS) or saved.get('terminal_results') != 125
            or saved.get('completed_results') != sum(row['status'] == 'completed' for row in view['results'])
            or saved.get('effective_results') != {row['case_id'] + '/' + row['arm_id']: row['sha256'] for row in view['results']}
            or saved.get('audit_arm_sha256s') != sorted(row['sha256'] for row in view['audit_arms'])
            or saved.get('provider_calls') != len(view['calls']) or saved.get('gold_read') is not False
            or saved.get('quality_claim') is not False or saved.get('holdout_or_live_access') is not False
            or _inference_inventory(root, directory) != before):
        raise native.UnsafeContinuation('Effective inference seal no longer matches the full matrix')
    _core().validate_plan(root, plan, base_api=api)
    return {**view, 'plan': plan, 'directory': directory, 'seal': saved}


def run_component_continuation(root, identifier, authorization_path, *, limit=1,
                               provider_factory=None, base_api=None, load_environment=False):
    """Run the explicitly authorized recovery once, then the remaining native cells."""
    if type(limit) is not int or limit not in {1, 25}:
        raise ValueError('Continuation runs exactly the pilot or the registered 25-case matrix')
    root = Path(root).resolve()
    api = _api(base_api)
    registration, native = api
    if base_api is None:
        _native_origins(root, api)
    core = _core()
    plan, directory = core.load_plan(root, identifier, base_api=api)
    _adapter_origins(plan)
    authorization = core.verify_authorization(root, plan, authorization_path, requested_cases=limit, base_api=api)
    spec, base_directory = registration.load_registration(root, plan['registration_sha256'])
    stopped = None
    with native._lock(root, {'component_ablation': spec['sha256']}):
        # load_plan just performed full native cohort/pack replay. Refresh the
        # current source/config/record inventory under the lease, then keep the
        # existing exact current-case proof guards before every stage/call.
        view = core.load_effective_view(root, plan, directory, base_api=api, full_replay=False)
        if any(stage['status'] == 'intent' for stage in view['stages']):
            raise native.UnsafeContinuation('Interrupted native stage requires manual audit; no paid retry')
        if limit > 1:
            gate = _pilot(view, api)
            _replay_completed(root, view, api)
            path = directory / 'pilot-gate.json'
            if path.exists() and native._read(root, path) != gate:
                raise native.UnsafeContinuation('Effective pilot gate changed')
            if not path.exists():
                _write(root, path, gate, new=True)
        intent = _seal({'schema': 'component_continuation_execution_intent_v1',
            'continuation_plan_sha256': plan['sha256'], 'requested_prefix': limit,
            'authorization': authorization, 'authorization_sha256': native.digest(authorization),
            'gold_read': False, 'quality_claim': False}, native)
        _write(root, directory / 'execution-intents' / (intent['sha256'] + '.json'), intent, new=True)
        # Credentials are read only after plan/auth/replay and matrix admission.
        if load_environment:
            importlib.import_module('issueintel.runtime.io').load_local_env(root / '.env')
        if view['calls']:
            ledger = native.RuntimeConfig.load(root).ledger()
            for call in view['calls']:
                if call['schema'] == native.CALL_SCHEMA:
                    native._validate_call(call, spec, ledger=ledger)
                else:
                    core.validate_call(call, plan, spec, base_api=api, ledger=ledger)
        sources = registration.load_history_sources(root, spec, base_directory)
        existing = {(row['case_id'], row['arm_id']): row for row in view['results']
                    if row['sha256'] != plan['target']['arm_sha256']}
        for index, row in enumerate(spec['cases'][:limit]):
            order = registration.ARMS[index % 5:] + registration.ARMS[:index % 5]
            remaining = [arm for arm in order if (row['case_id'], arm) not in existing]
            if not remaining:
                continue
            preparation_id = native.digest([plan['sha256'], row['case_id'], time.time_ns()])
            prepared = time.monotonic()
            case, snapshot, receipt, graph = native._replay_context(root, spec, row, sources[row['case_id']])
            preparation = round(time.monotonic() - prepared, 6)
            for arm in remaining:
                # Admission replayed the whole registered cohort. Between arms,
                # retain exact fingerprints/inventory checks; each stage still
                # replays its current public case, and the seal replays all packs.
                view = core.load_effective_view(root, plan, directory, base_api=api, full_replay=False)
                execution = _Execution(root, plan, directory, view, row, arm, sources[row['case_id']],
                    authorization_path, limit, provider_factory or native.GroqProvider, api)
                policy = native._policy(spec, arm)
                started = time.monotonic()
                pack, failure, status = None, None, 'completed'
                try:
                    pack = native.investigate(case, Path(snapshot.repository), complete_json=execution,
                        provider='groq', model=spec['runtime']['model'], protocol='issue_time',
                        snapshot=snapshot, graph=graph, query_receipt=receipt, top_k=policy['top_k'],
                        independent_critic=policy['independent_critic'], max_output_tokens=policy['max_output_tokens'],
                        refinement_rounds=int(policy['counter_search']), experiment_policy=policy,
                        historical_sources=sources[row['case_id']], experiment_stage_executor=execution.stage)
                    if not pack.get('annotation') or not pack.get('verification'):
                        raise native.NoUsableEvidence('Native pipeline retained insufficient evidence')
                    execution.guard()
                except Exception as error:
                    pack, failure = None, native.failure_metadata(error)
                    status = 'unavailable' if isinstance(error, (native.SavedStageFailure, native.NoUsableEvidence)) else 'failed'
                    terminal = isinstance(error, (native.AnnotationError, native.NoUsableEvidence)) or native.output_rejected(error) or (
                        isinstance(error, native.SavedStageFailure) and error.terminal_output)
                    if not terminal:
                        stopped = 'source_provider_storage_or_unknown_paid_outcome_no_automatic_retry'
                latency = round(time.monotonic() - started, 6)
                calls = list(dict.fromkeys(execution.call_ids + execution.audit_call_ids))
                owned = list(dict.fromkeys(execution.owned_call_ids + execution.audit_call_ids))
                target_arm = next((saved for saved in view['audit_arms']
                    if saved['case_id'] == row['case_id'] and saved['arm_id'] == arm), None)
                result = _seal({'schema': ARM_SCHEMA, 'experiment_sha256': spec['sha256'],
                    'attempt_id': plan['sha256'], 'case_id': row['case_id'], 'arm_id': arm,
                    'source': row['source'], 'policy': policy, 'status': status, 'pack': pack,
                    'failure': failure, 'stop_reason': stopped, 'stage_refs': execution.stage_refs,
                    'audit_stage_refs': execution.audit_stage_refs, 'audit_arm_refs': execution.audit_arm_refs,
                    'audit_call_ids': execution.audit_call_ids, 'call_ids': calls, 'owned_call_ids': owned,
                    'pipeline_latency_seconds': target_arm['pipeline_latency_seconds'] if target_arm else latency,
                    'preparation_seconds': target_arm['preparation_seconds'] if target_arm else preparation,
                    'preparation_measurement_id': target_arm['preparation_measurement_id'] if target_arm else preparation_id,
                    'preparation_owner_arm_id': target_arm['preparation_owner_arm_id'] if target_arm else remaining[0],
                    'continuation_overhead': {'pipeline_latency_seconds': latency,
                        'preparation_seconds': preparation, 'preparation_measurement_id': preparation_id,
                        'preparation_owner_arm_id': remaining[0]} if target_arm else {}}, native)
                _write(root, _record_path(root, directory, row['case_id'], 'arms', arm, native), result, new=True)
                existing[(row['case_id'], arm)] = result
                if stopped:
                    break
            if stopped:
                break
        view = core.load_effective_view(root, plan, directory, base_api=api, require_closed=False)
        if not stopped and len(view['results']) == 125:
            seal_effective_view(root, plan, directory, base_api=api)
        return _seal({'schema': 'component_continuation_progress_v2',
            'continuation_plan_sha256': plan['sha256'], 'experiment_sha256': spec['sha256'],
            'requested_prefix': limit, 'selected_cases': 25,
            'completed_arms': sum(row['status'] == 'completed' for row in view['results']),
            'failed_arms': sum(row['status'] == 'failed' for row in view['results']),
            'unavailable_arms': sum(row['status'] == 'unavailable' for row in view['results']),
            'owned_provider_calls': len(view['calls']), 'stop_reason': stopped,
            'inference_sealed': (directory / 'inference-sealed.json').exists(),
            'gold_read': False, 'quality_claim': False}, native)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('preview', 'run', 'inspect', 'score'), required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--experiment')
    parser.add_argument('--plan')
    parser.add_argument('--authorization')
    parser.add_argument('--limit', type=int, default=1)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    api = _api()
    _native_origins(root, api)
    core = _core()
    if args.mode == 'preview':
        paths = [Path(__file__).with_name(name + '.py').resolve() for name in
                 ('component_continuation', 'component_continuation_run', 'component_continuation_score')]
        plan = core.prepare_plan(root, args.experiment, adapter_paths=paths, base_api=api)
        result = {'continuation_plan_sha256': plan['sha256'], 'plan_path': str(core.plan_path(root, plan)),
                  'limits': plan['limits'], 'gold_read': False, 'execution_authorized': False}
    elif args.mode == 'run':
        result = run_component_continuation(root, args.plan, args.authorization,
                                            limit=args.limit, load_environment=True)
    elif args.mode == 'score':
        result = _extension('component_continuation_score').score_component_continuation(
            root, args.plan, args.authorization)
    else:
        plan, directory = core.load_plan(root, args.plan, base_api=api)
        _adapter_origins(plan)
        view = core.load_effective_view(root, plan, directory, base_api=api, require_closed=False)
        result = {'schema': 'component_continuation_inspection_v2',
            'continuation_plan_sha256': plan['sha256'], 'terminal_arms': len(view['results']),
            'completed_arms': sum(row['status'] == 'completed' for row in view['results']),
            'owned_provider_calls': len(view['calls']),
            'inference_sealed': (directory / 'inference-sealed.json').exists(),
            'provider_or_ledger_constructed': False, 'gold_read': False, 'quality_claim': False}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
