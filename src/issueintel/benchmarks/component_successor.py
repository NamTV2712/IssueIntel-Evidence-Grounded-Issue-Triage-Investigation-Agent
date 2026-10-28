"""Admit a replayed verifier into a separate, read-only successor view.

The parent journal remains immutable, including its interrupted intent. Recovered
stage and arm identities belong to this successor. Missing original latency is
null, never a synthetic zero. This module cannot open credentials or run models;
paid execution and final scoring require their own successor-aware gates.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path


PLAN_SCHEMA = 'component_successor_plan_v1'
STAGE_SCHEMA = 'component_recovered_verification_stage_v1'
ARM_SCHEMA = 'component_recovered_arm_v1'


def _recovery(adapter=None):
    if adapter is not None:
        return adapter
    path = Path(__file__).with_name('component_response_recovery.py')
    spec = importlib.util.spec_from_file_location('_component_successor_recovery', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pins(recovery):
    return {name: sha256(Path(path).read_bytes()).hexdigest() for name, path in (
        ('component_successor.py', __file__),
        ('component_response_recovery.py', recovery.__file__))}


def _recovered_records(candidate, parent, core):
    """Derive owned references without changing any original stage or call."""
    target, paid = _recovery()._target(parent)
    if (candidate['original_stage']['sha256'] != target['sha256']
            or candidate['original_call']['sha256'] != paid['sha256']):
        raise core.ContinuationError('Recovered response differs from the parent intent')
    stage = core.seal({
        'schema': STAGE_SCHEMA, 'experiment_sha256': candidate['registration_sha256'],
        'recovery_sha256': candidate['sha256'], 'original_stage_sha256': target['sha256'],
        'stage_key': target['stage_key'], 'binding': deepcopy(target['binding']),
        'owner_arm_id': target['owner_arm_id'], 'status': 'completed',
        'payload': deepcopy(candidate['verification_payload']),
        'payload_sha256': candidate['verification_payload_sha256'],
        'call_ids': list(target['call_ids']), 'failure': None,
        'terminal_output_failure': False, 'latency_seconds': None,
        'timing_status': 'not_recorded_before_interruption',
    })
    stages = {**parent['execution_stages'], stage['stage_key']: stage}
    refs, call_ids = [], []
    for ref in candidate['native_stage_refs']:
        saved = stages[ref['stage_key']]
        expected_sha = target['sha256'] if saved is stage else saved['sha256']
        if ref['sha256'] != expected_sha or saved['status'] != 'completed':
            raise core.ContinuationError('Recovered arm changed its native stage ancestry')
        refs.append({'stage_key': saved['stage_key'], 'sha256': saved['sha256'],
                     'owner_arm_id': saved['owner_arm_id'], 'cache_hit': True})
        call_ids.extend(cid for cid in saved['call_ids'] if cid not in call_ids)
    calls = {call['call_id']: call for call in parent['calls']}
    owner = (candidate['case_id'], candidate['arm_id'])
    owned = [cid for cid in call_ids
             if (calls[cid]['case_id'], calls[cid]['owner_arm_id']) == owner]
    all_owned = {cid for cid, call in calls.items()
                 if (call['case_id'], call['owner_arm_id']) == owner}
    if len(call_ids) > 5 or set(owned) != all_owned:
        raise core.ContinuationError('Recovered arm hides or exceeds owned paid attempts')
    arm = core.seal({
        'schema': ARM_SCHEMA, 'experiment_sha256': candidate['registration_sha256'],
        'recovery_sha256': candidate['sha256'], 'case_id': owner[0], 'arm_id': owner[1],
        'source': deepcopy(candidate['source']), 'policy': deepcopy(candidate['policy']),
        'status': 'completed', 'pack': deepcopy(candidate['pack']),
        'pack_sha256': candidate['pack_sha256'], 'failure': None, 'stop_reason': None,
        'stage_refs': refs,
        'audit_stage_refs': [{'stage_key': target['stage_key'], 'sha256': target['sha256'],
                              'owner_arm_id': target['owner_arm_id'], 'cache_hit': True}],
        'audit_arm_refs': [], 'audit_call_ids': [],
        'call_ids': call_ids, 'owned_call_ids': owned,
        'pipeline_latency_seconds': None, 'preparation_seconds': None,
        'preparation_measurement_id': None, 'preparation_owner_arm_id': None,
        'timing_status': 'not_recorded_before_interruption',
        'continuation_overhead': {},
    })
    return stage, arm


def _derive(root, recovery_id, *, base_api=None, parent_core=None, recovery_adapter=None):
    recovery = _recovery(recovery_adapter)
    core, api = recovery._core(parent_core), recovery._api(root, base_api)
    pins = _pins(recovery)
    # This re-executes the frozen verifier with the one saved response, checks
    # the complete candidate, and rejects forged fields even if rehashed.
    candidate = recovery.load_response_recovery(root, recovery_id, base_api=api, parent_core=core)
    parent_plan, directory = core.load_plan(root, candidate['parent_plan_sha256'], base_api=api)
    with api[1]._lock(root, {'component_ablation': candidate['registration_sha256']}):
        parent = core.load_effective_view(root, parent_plan, directory, base_api=api, full_replay=False)
        if (parent['files'] != candidate['parent_inventory']
                or parent['base_files'] != candidate['base_inventory']):
            raise core.ContinuationError('Parent changed after recovery; successor cannot replace new work')
        stage, arm = _recovered_records(candidate, parent, core)
        if _pins(recovery) != pins:
            raise core.ContinuationError('Successor adapters changed during admission')
        record = core.seal({
            'schema': PLAN_SCHEMA, 'recovery_sha256': recovery_id,
            'parent_plan_sha256': parent_plan['sha256'],
            'registration_sha256': candidate['registration_sha256'],
            'native_fingerprint_sha256': candidate['native_fingerprint_sha256'],
            'adapter_sources': pins, 'parent_adapter_sources': candidate['parent_adapter_sources'],
            'base_inventory': candidate['base_inventory'], 'parent_inventory': candidate['parent_inventory'],
            'parent_results': {row['case_id'] + '/' + row['arm_id']: row['sha256']
                               for row in parent['results']},
            'parent_calls': {row['call_id']: row['sha256'] for row in parent['calls']},
            'case_ids': list(parent_plan['case_ids']), 'arms': list(api[0].ARMS),
            'limits': {'max_provider_calls_per_arm': 5, 'max_provider_calls_total': 625},
            'recovered_stage': stage, 'recovered_arm': arm,
            'new_provider_calls': 0, 'execution_authorized': False,
            'gold_read': False, 'quality_claim': False, 'holdout_or_live_access': False,
        })
        return record, parent, core


def prepare_successor(root, recovery_id, *, base_api=None, parent_core=None, recovery_adapter=None):
    """Prepare an exact successor with one recovered outcome; no execution grant."""
    return _derive(Path(root).resolve(), recovery_id, base_api=base_api,
                   parent_core=parent_core, recovery_adapter=recovery_adapter)[0]


def _path(root, identifier, core):
    if not isinstance(identifier, str) or not core._HASH.fullmatch(identifier):
        raise core.ContinuationError('Successor requires its exact content identity')
    return core.safe_path(root, Path('artifacts/experiments/component_successor') / identifier / 'plan.json',
                          missing=True)


def save_successor(root, value, *, base_api=None, parent_core=None, recovery_adapter=None):
    """Recompute and publish a successor once, without modifying its parent."""
    core = _recovery(recovery_adapter)._core(parent_core)
    core._check_seal(value)
    expected, parent, core = _derive(root, value.get('recovery_sha256'), base_api=base_api,
                                    parent_core=core, recovery_adapter=recovery_adapter)
    if value != expected:
        raise core.ContinuationError('Successor does not replay from its frozen parent and recovery')
    # Serialize publication with native inference. Recheck the immutable parent
    # at the write boundary after the comparatively expensive native replay.
    api = _recovery(recovery_adapter)._api(root, base_api)
    with api[1]._lock(root, {'component_ablation': value['registration_sha256']}):
        current = core.load_effective_view(root, parent['plan'], base_api=api, full_replay=False)
        if current['files'] != value['parent_inventory'] or current['base_files'] != value['base_inventory']:
            raise core.ContinuationError('Parent changed before successor publication')
        return core.write_new(root, _path(root, value['sha256'], core), value)


def load_successor_view(root, identifier, *, base_api=None, parent_core=None, recovery_adapter=None):
    """Return validated execution outcomes AND unaltered audit history.

    Call records are the original physical calls, not copies owned by recovery.
    The original interrupted stage stays in audit_stages, while the execution
    map points to its recovered successor. Old runners/scorers cannot accept
    this schema implicitly; unknown timing needs explicit treatment downstream.
    """
    core = _recovery(recovery_adapter)._core(parent_core)
    value = core.read(root, _path(root, identifier, core))
    expected, parent, core = _derive(root, value.get('recovery_sha256'), base_api=base_api,
                                    parent_core=core, recovery_adapter=recovery_adapter)
    if value != expected or value['sha256'] != identifier:
        raise core.ContinuationError('Saved successor differs from native recovery')
    stage, arm = value['recovered_stage'], value['recovered_arm']
    original = next(row for row in parent['stages'] if row['sha256'] == stage['original_stage_sha256'])
    result = {**parent, 'successor_plan': value, 'parent_view': parent,
              'results': parent['results'] + [arm],
              'stages': parent['stages'] + [stage],
              'execution_stages': {**parent['execution_stages'], stage['stage_key']: stage},
              'audit_stages': parent['audit_stages'] + [original]}
    # Explicitly expose partial-matrix status. Neither a successful recovery
    # nor a schema-valid completed arm grants a full inference/quality seal.
    result['progress'] = {
        'terminal_arms': len(result['results']),
        'completed_arms': sum(row['status'] == 'completed' for row in result['results']),
        'failed_arms': sum(row['status'] == 'failed' for row in result['results']),
        'unavailable_arms': sum(row['status'] == 'unavailable' for row in result['results']),
        'physical_provider_calls': len(parent['calls']), 'new_provider_calls': 0,
        'original_timing_unknown_arms': 1, 'inference_sealed': False,
        'execution_authorized': False, 'gold_read': False, 'quality_claim': False,
    }
    return result


def main(argv=None):
    """Isolated offline entry point; native imports come only from --root/src."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--recovery', help='Replay and publish a successor for this recovery ID')
    selection.add_argument('--inspect', help='Replay a saved successor and report its partial progress')
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / 'src'))
    if args.recovery:
        value = prepare_successor(root, args.recovery)
        path = save_successor(root, value)
        output = {'successor_sha256': value['sha256'], 'path': str(path),
                  'parent_terminal_arms': len(value['parent_results']),
                  'successor_terminal_arms': len(value['parent_results']) + 1,
                  'physical_provider_calls': len(value['parent_calls']),
                  'new_provider_calls': 0, 'original_timing_unknown_arms': 1,
                  'execution_authorized': False, 'inference_sealed': False,
                  'gold_read': False, 'quality_claim': False}
    else:
        view = load_successor_view(root, args.inspect)
        output = {'successor_sha256': view['successor_plan']['sha256'], **view['progress']}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
