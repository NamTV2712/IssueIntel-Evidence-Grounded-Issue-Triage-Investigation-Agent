"""Separate, append-only scoring for a frozen research continuation.

This adapter can be loaded by filename beside the other continuation adapters;
``issueintel.*`` imports then still resolve to the untouched frozen runtime.
No inference driver imports restricted patch data through this module.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
import importlib
import importlib.util
from pathlib import Path
import re
import sys


SCHEMA = 'component_continuation_score_v1'
AUTHORIZATION_SCHEMA = 'component_continuation_score_authorization_v1'
SCOPE = 'accepted_main_dev_component_continuation_scoring'
ARMS = ('lexical_symbol', 'plus_graph', 'plus_history', 'plus_verifier', 'plus_counter_search')
SELECTED_CASES = 25
MAX_CALLS_PER_ARM = 5
MAX_CALLS_TOTAL = 625
_HASH = re.compile('[a-f0-9]{64}')


def _extension(name):
    qualified = 'issueintel.benchmarks.' + name
    try:
        return importlib.import_module(qualified)
    except ModuleNotFoundError as error:
        if error.name != qualified:
            raise
    alias = '_issueintel_' + name
    if alias in sys.modules:
        return sys.modules[alias]
    location = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location(alias, location)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(alias, None)
        raise
    return module


def _base_score():
    # In the isolated adapter child this is the frozen runtime's pure scorer.
    return importlib.import_module('issueintel.benchmarks.component_ablation_score')


def _sealed(value):
    from issueintel.runtime.io import digest

    return (isinstance(value, dict) and isinstance(value.get('sha256'), str)
            and _HASH.fullmatch(value['sha256']) is not None
            and value['sha256'] == digest({key: item for key, item in value.items() if key != 'sha256'}))


def _admission(state):
    """Recheck the exact closed matrix before the gold boundary, without gold."""
    spec, plan, seal = (state.get(name) for name in ('registration', 'plan', 'seal'))
    if not all(_sealed(value) for value in (spec, plan, seal)):
        raise ValueError('Scoring requires exact sealed registration, continuation plan and effective inference')
    ids = [row.get('case_id') for row in spec.get('cases', [])]
    expected = {(cid, arm) for cid in ids for arm in ARMS}
    rows, calls = state.get('results'), state.get('calls')
    if (len(ids) != SELECTED_CASES or len(set(ids)) != SELECTED_CASES
            or any(not isinstance(cid, str) or not cid.startswith('verified:') for cid in ids)
            or [row.get('arm_id') for row in spec.get('arms', [])] != list(ARMS)
            or spec.get('limits', {}).get('max_provider_calls_per_arm') != MAX_CALLS_PER_ARM
            or spec.get('limits', {}).get('max_provider_calls_total') != MAX_CALLS_TOTAL
            or not isinstance(rows, list) or len(rows) != SELECTED_CASES * len(ARMS)
            or {(row.get('case_id'), row.get('arm_id')) for row in rows} != expected
            or any(row.get('status') not in {'completed', 'failed', 'unavailable'} for row in rows)
            or any(row['status'] != 'completed' for row in rows if row['case_id'] == ids[0])
            or not isinstance(calls, list) or len(calls) > MAX_CALLS_TOTAL
            or len({row.get('call_id') for row in calls}) != len(calls)
            or any(row.get('status') not in {'returned', 'failed'} for row in calls)
            or any(len(row.get('call_ids', [])) > MAX_CALLS_PER_ARM for row in rows)):
        raise ValueError('Scoring requires all 125 effective terminal arms, a completed pilot and the original 5/625 call limits')
    if (seal.get('gold_read') is not False or seal.get('quality_claim') is not False
            or seal.get('holdout_or_live_access') is not False):
        raise ValueError('Effective inference cannot claim gold, quality or Holdout/Live access')
    for field in ('base_calls', 'overlay_calls', 'base_arms', 'audit_arms', 'stages',
                  'base_stages', 'overlay_stages'):
        if not isinstance(state.get(field), list):
            raise ValueError('Effective scoring needs complete original and continuation attempt audit records')
    if any(row.get('status') == 'intent' for row in state['stages']):
        raise ValueError('Incomplete native stage cannot cross the separate gold boundary')
    combined = state['base_calls'] + state['overlay_calls']
    if (len({row['call_id'] for row in combined}) != len(combined)
            or {row['call_id']: row for row in combined} != {row['call_id']: row for row in calls}):
        raise ValueError('Original and continuation accounting must partition every unique owned call')
    return state


def verify_scoring_authorization(root, state, authorization_path, *, base_api=None):
    """Validate an exact separate declaration, never infer it from run permission."""
    from issueintel.benchmarks import component_ablation as base
    from issueintel.runtime.io import digest

    _admission(state)
    _, raw = base._read_file(root, authorization_path, private=True, maximum=256 * 1024)
    value = base._parse(raw)
    fields = {'schema', 'scope', 'plan_sha256', 'registration_sha256',
              'effective_inference_seal_sha256', 'adapter_sources_sha256', 'selected_case_ids',
              'split', 'gold_scoring_authorized', 'new_provider_calls_authorized',
              'holdout_or_live_access', 'declared_at', 'declared_by'}
    if (not isinstance(value, dict) or set(value) != fields
            or value.get('schema') != AUTHORIZATION_SCHEMA or value.get('scope') != SCOPE
            or value.get('plan_sha256') != state['plan']['sha256']
            or value.get('registration_sha256') != state['registration']['sha256']
            or value.get('effective_inference_seal_sha256') != state['seal']['sha256']
            or value.get('adapter_sources_sha256') != digest(state['plan']['adapter_sources'])
            or value.get('selected_case_ids') != [row['case_id'] for row in state['registration']['cases']]
            or value.get('split') != 'main_dev' or value.get('gold_scoring_authorized') is not True
            or value.get('new_provider_calls_authorized') is not False
            or value.get('holdout_or_live_access') is not False
            or not isinstance(value.get('declared_by'), str) or not value['declared_by'].strip()
            or len(value['declared_by']) > 1000 or not isinstance(value.get('declared_at'), str)):
        raise PermissionError('Need separate Main Dev gold permission matching this exact effective inference seal')
    try:
        stamp = datetime.fromisoformat(value['declared_at'].replace('Z', '+00:00'))
        if stamp.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Gold permission needs an explicit recorded timestamp with timezone') from None
    return deepcopy(value)


def _unique_preparations(rows, frozen):
    found = {}
    for row in rows:
        key = row.get('preparation_measurement_id')
        binding = (row['case_id'], row.get('preparation_owner_arm_id'),
                   frozen._number(row.get('preparation_seconds'), 'preparation latency', optional=True))
        if (not isinstance(key, str) or not _HASH.fullmatch(key) or binding[1] not in ARMS
                or key in found and found[key] != binding):
            raise ValueError('Attempt preparation identity or duration changed')
        found[key] = binding
    return found


def _continuation_measurement(row, original, frozen):
    """Project fresh observations without replacing an original arm's timing."""
    fields = {'pipeline_latency_seconds', 'preparation_seconds',
              'preparation_measurement_id', 'preparation_owner_arm_id'}
    overhead = row.get('continuation_overhead')
    if original is None:
        if overhead != {}:
            raise ValueError('Fresh matrix timing cannot acquire continuation overhead')
        return row
    if (any(row.get(field) != original.get(field) for field in fields)
            or not isinstance(overhead, dict) or set(overhead) != fields
            or overhead.get('preparation_owner_arm_id') != row['arm_id']):
        raise ValueError('Recovered timing must preserve original observations and separate continuation overhead')
    for field in ('pipeline_latency_seconds', 'preparation_seconds'):
        frozen._number(overhead.get(field), 'continuation overhead timing')
    # _unique_preparations validates the measurement identity and ownership.
    return {**row, **overhead}


def _attempt_audit(state, frozen):
    original = {}
    for row in state['base_arms']:
        key = (row.get('case_id'), row.get('arm_id'))
        if key in original or row.get('status') not in {'completed', 'failed', 'unavailable'}:
            raise ValueError('Original attempt audit must retain each original terminal arm once')
        original[key] = row
    effective = {(row['case_id'], row['arm_id']): row for row in state['results']}
    if set(original) - set(effective):
        raise ValueError('Original attempts cannot fall outside the effective registered matrix')
    for key, row in original.items():
        if row['status'] == 'completed' and effective[key] != row:
            raise ValueError('Original completed arms cannot be replaced in the scoring projection')
    if any(row not in list(original.values()) or row['status'] == 'completed' for row in state['audit_arms']):
        raise ValueError('Failed attempt audit cannot change or complete an original saved arm')
    changed = [row for key, row in effective.items() if key not in original or row != original[key]]
    continuation_measurements = {
        (row['case_id'], row['arm_id']): _continuation_measurement(
            row, original.get((row['case_id'], row['arm_id'])), frozen)
        for row in changed}
    original_preparations = _unique_preparations(original.values(), frozen)
    new_preparations = _unique_preparations(continuation_measurements.values(), frozen)
    all_preparations = {**original_preparations}
    for key, binding in new_preparations.items():
        if key in all_preparations and all_preparations[key] != binding:
            raise ValueError('Continuation changed an original preparation measurement')
        all_preparations[key] = binding
    added_preparations = {key: row for key, row in new_preparations.items() if key not in original_preparations}
    old_failed_calls = [row for row in state['base_calls'] if row['status'] == 'failed']
    failed_stages = {row['stage_key'] for row in old_failed_calls}
    retries = [row for row in state['overlay_calls'] if row.get('stage_key') in failed_stages and row.get('ordinal') == 0]
    stage_attempts = []
    for phase, phase_stages in (('original', state['base_stages']), ('continuation', state['overlay_stages'])):
        for stage in phase_stages:
            stage_attempts.append({'phase': phase, 'stage_key': stage['stage_key'],
                'stage_sha256': stage['sha256'], 'status': stage['status'],
                'case_id': stage.get('binding', {}).get('case_id'),
                'owner_arm_id': stage.get('owner_arm_id'), 'stage': stage.get('binding', {}).get('stage'),
                'latency_seconds': frozen._number(stage.get('latency_seconds'), 'native stage latency', optional=True)})
    stage_keys = [(row['stage_key'], row['sha256']) for row in state['stages']]
    if (len(set(stage_keys)) != len(stage_keys)
            or {(row['stage_key'], row['stage_sha256']) for row in stage_attempts} != set(stage_keys)
            or len(stage_attempts) != len(stage_keys)):
        raise ValueError('Original and continuation stage timing must partition native attempts')
    schema_repairs = []
    seen_stages = set()
    for stage in state['stages']:
        key = (stage.get('stage_key'), stage.get('sha256'))
        if key in seen_stages or stage.get('status') != 'completed':
            continue
        seen_stages.add(key)
        if stage.get('binding', {}).get('stage') in {'seed_generation', 'refined_generation'}:
            count = stage.get('payload', {}).get('annotation', {}).get('provenance', {}).get('repair_count')
            frozen._integer(count, 'native annotation schema repair count')
            schema_repairs.append({'stage_key': key[0], 'stage_sha256': key[1], 'schema_repairs': count})
    reservations = lambda rows: [receipt for row in rows for receipt in row['receipts'].values()]
    original_times = [frozen._number(row.get('pipeline_latency_seconds'), 'original pipeline latency', optional=True)
                      for row in original.values()]
    continuation_times = [frozen._number(row.get('pipeline_latency_seconds'), 'continuation pipeline latency', optional=True)
                          for row in continuation_measurements.values()]
    details = []
    for key, row in effective.items():
        old = original.get(key)
        is_new = old is None or row != old
        fresh = continuation_measurements.get(key)
        details.append({'case_id': key[0], 'arm_id': key[1],
            'original_status': old['status'] if old else 'not_attempted', 'effective_status': row['status'],
            'recovered_from_original_failure': bool(old and old['status'] != 'completed' and row['status'] == 'completed'),
            'original_result_sha256': old.get('sha256') if old else None,
            'effective_result_sha256': row.get('sha256'),
            'original_pipeline_latency_seconds': old.get('pipeline_latency_seconds') if old else None,
            'continuation_pipeline_latency_seconds': fresh.get('pipeline_latency_seconds') if fresh else None,
            'original_preparation': {field: old.get(field) for field in ('preparation_measurement_id', 'preparation_owner_arm_id', 'preparation_seconds')} if old else None,
            'effective_preparation': {field: row.get(field) for field in ('preparation_measurement_id', 'preparation_owner_arm_id', 'preparation_seconds')},
            'continuation_preparation': {field: fresh.get(field) for field in ('preparation_measurement_id', 'preparation_owner_arm_id', 'preparation_seconds')} if fresh else None,
            'explicit_transport_retry_calls': sum((call['case_id'], call['owner_arm_id']) == key for call in retries),
            'new_attempt': is_new})
    return {'arm_attempts': details,
        'original_status_counts': dict(sorted(Counter(row['status'] for row in original.values()).items())),
        'effective_status_counts': dict(sorted(Counter(row['status'] for row in effective.values()).items())),
        'recovered_original_failed_arms': sum(row['recovered_from_original_failure'] for row in details),
        'original_unique_provider_calls': len(state['base_calls']),
        'continuation_unique_provider_calls': len(state['overlay_calls']),
        'explicit_transport_retry_calls': len(retries),
        'explicit_transport_retry_call_ids': [row['call_id'] for row in retries],
        'native_schema_repair_calls': sum(row['schema_repairs'] for row in schema_repairs),
        'native_stage_schema_repairs': schema_repairs,
        'original_usage': frozen._usage(reservations(state['base_calls'])),
        'continuation_usage': frozen._usage(reservations(state['overlay_calls'])),
        'original_failed_bound_usage': frozen._usage([receipt for row in old_failed_calls for receipt in row['receipts'].values() if receipt['status'] == 'failed_bound']),
        'original_failed_calls_without_receipts': sum(not row['receipts'] for row in old_failed_calls),
        'original_failed_call_ids': [row['call_id'] for row in old_failed_calls],
        'original_provider_active_time': frozen._time_summary([row.get('latency_seconds') for row in state['base_calls']]),
        'original_failed_provider_active_time': frozen._time_summary([row.get('latency_seconds') for row in old_failed_calls]),
        'continuation_provider_active_time': frozen._time_summary([row.get('latency_seconds') for row in state['overlay_calls']]),
        'original_pipeline_active_time': frozen._time_summary(original_times),
        'continuation_pipeline_active_time': frozen._time_summary(continuation_times),
        'all_attempt_pipeline_active_time': frozen._time_summary(original_times + continuation_times),
        'native_stage_attempts': stage_attempts,
        'original_native_stage_active_time': frozen._time_summary([row['latency_seconds'] for row in stage_attempts if row['phase'] == 'original']),
        'continuation_native_stage_active_time': frozen._time_summary([row['latency_seconds'] for row in stage_attempts if row['phase'] == 'continuation']),
        'original_unique_preparation_time': frozen._time_summary([row[2] for row in original_preparations.values()]),
        'continuation_added_unique_preparation_time': frozen._time_summary([row[2] for row in added_preparations.values()]),
        'all_attempt_unique_preparation_time': frozen._time_summary([row[2] for row in all_preparations.values()]),
        'preparation_measurements': [{'measurement_id': key, 'case_id': row[0], 'owner_arm_id': row[1],
            'seconds': row[2], 'phase': 'original' if key in original_preparations else 'continuation'}
            for key, row in sorted(all_preparations.items())],
        'provider_invoice_verified': False,
        'timing_interpretation': 'sum_of_recorded_active_attempt_durations_not_wall_clock_or_cold_run_benchmark',
        'transport_retries_are_schema_repairs': False}


def summarize_component_continuation(state, gold_targets):
    """Use frozen native metrics, while retaining the separate failed attempt."""
    from issueintel.runtime.io import digest

    _admission(state)
    frozen = _base_score()
    audit = _attempt_audit(state, frozen)
    rows = deepcopy(state['results'])
    original = {(row['case_id'], row['arm_id']): row for row in state['base_arms']}
    # Native v1 only has one preparation field. Keep its original ownership
    # invariant in that projection; every added measurement is reported below.
    for row in rows:
        old = original.get((row['case_id'], row['arm_id']))
        if old is not None:
            for field in ('preparation_seconds', 'preparation_measurement_id', 'preparation_owner_arm_id'):
                row[field] = old[field]
    report = frozen.summarize_component_ablation(state['registration'], rows, gold_targets, state['calls'])
    projected_preparation = report['global_unique_preparation_latency']
    report.update(schema=SCHEMA, frozen_metric_schema=frozen.SCHEMA,
        continuation_attempt_audit=audit, max_provider_calls_per_arm=MAX_CALLS_PER_ARM,
        max_provider_calls_total=MAX_CALLS_TOTAL,
        native_preparation_metric_projection=projected_preparation,
        global_unique_preparation_latency=audit['all_attempt_unique_preparation_time'],
        attempt_timing_source='continuation_attempt_audit_preserves_original_and_added_measurements')
    by_key = {(row['case_id'], row['arm_id']): row for row in audit['arm_attempts']}
    for row in report['cases']:
        row['attempt_audit'] = by_key[row['case_id'], row['arm_id']]
    for arm, aggregate in report['aggregates'].items():
        selected = [row for row in audit['arm_attempts'] if row['arm_id'] == arm]
        aggregate['original_attempt_status_counts'] = dict(sorted(Counter(row['original_status'] for row in selected).items()))
        aggregate['recovered_original_failed_arms'] = sum(row['recovered_from_original_failure'] for row in selected)
        aggregate['explicit_transport_retry_calls'] = sum(row['explicit_transport_retry_calls'] for row in selected)
    report['limitations'] += [
        'Original failed attempts remain failed in the attempt audit; effective completion describes the authorized continuation only.',
        'Original failed transport calls and their unknown charged/reserved bounds remain counted once. Explicit transport retries are separate from schema repairs.',
        'Per-arm native v1 preparation fields preserve original shared ownership and their metric projection is labeled separately. Global unique preparation includes every original and added measurement; the continuation attempt audit also preserves both active pipeline durations.',
        'Continuation timing is conditional on prior cached work and includes resumed attempts; it does not establish a cold-run or causal speed improvement.',
    ]
    report['continuation_attempt_audit_sha256'] = digest(audit)
    return report


def _markdown(report):
    frozen = _base_score()
    raw = frozen._markdown(report).decode('utf-8')
    audit = report['continuation_attempt_audit']
    lines = ['', '## Continuation attempt accounting', '',
        'Original attempts keep their saved status. Effective statuses describe the separately authorized continuation.',
        'Original status counts: ' + str(audit['original_status_counts']),
        'Effective status counts: ' + str(audit['effective_status_counts']),
        'Recovered originally failed arms: ' + str(audit['recovered_original_failed_arms']),
        'Explicit transport retry calls: ' + str(audit['explicit_transport_retry_calls']),
        'Original provider calls: ' + str(audit['original_unique_provider_calls']),
        'Continuation provider calls: ' + str(audit['continuation_unique_provider_calls']),
        'Original failed-bound USD: ' + str(audit['original_failed_bound_usage']['micro_usd_charged_or_reserved'] / 1_000_000),
        'Original pipeline active seconds: ' + str(audit['original_pipeline_active_time']['observed_seconds']),
        'Continuation pipeline active seconds: ' + str(audit['continuation_pipeline_active_time']['observed_seconds']),
        'Original unique preparation seconds: ' + str(audit['original_unique_preparation_time']['observed_seconds']),
        'Continuation added preparation seconds: ' + str(audit['continuation_added_unique_preparation_time']['observed_seconds']),
        'These are active attempt measurements with warm-cache reuse, not a wall-clock or cold-run benchmark.',
        'Local receipts do not verify provider invoices. Semantic, causal and independent human quality remain unreviewed.', '']
    return (raw + '\n'.join(lines)).encode('utf-8')


def score_component_continuation(root, identifier, authorization_path, *, base_api=None):
    """Open only selected Main Dev patches after exact effective native replay."""
    from issueintel.benchmarks import component_ablation as base
    from issueintel.benchmarks.dev_context import accepted_context
    from issueintel.runtime.io import digest

    root = Path(root).resolve()
    runner = _extension('component_continuation_run')
    load = lambda: _admission(runner.load_sealed_continuation(root, identifier, base_api=base_api))
    state = load()
    decision = verify_scoring_authorization(root, state, authorization_path, base_api=base_api)
    available = {}
    for row in state['registration']['cases']:
        case, snapshot, _ = accepted_context(root, row['case_id'])
        if case.get('split') != 'main_dev' or snapshot.protocol != 'issue_time':
            raise ValueError('Scoring snapshot must remain accepted strict Main Dev')
        available[row['case_id']] = [file.path for file in snapshot.files]
    # Validate citations, receipt ownership, old-attempt timing and all native
    # metric input contracts using empty public targets before opening gold.
    summarize_component_continuation(state, {cid: {'files': [], 'available_files': paths,
        'patch_sha256': '0' * 64} for cid, paths in available.items()})
    if load() != state:
        raise ValueError('Effective inference changed before the separate gold scoring boundary')
    if verify_scoring_authorization(root, state, authorization_path, base_api=base_api) != decision:
        raise ValueError('Separate gold permission changed before scoring')
    frozen = _base_score()
    targets, source = frozen._gold_targets(root, state['registration'], available)
    report = summarize_component_continuation(state, targets)
    if load() != state:
        raise ValueError('Effective inference changed while scoring')
    if verify_scoring_authorization(root, state, authorization_path, base_api=base_api) != decision:
        raise ValueError('Separate gold permission changed while scoring')
    report.update(experiment_sha256=state['registration']['sha256'],
        plan_sha256=state['plan']['sha256'], inference_seal_sha256=state['seal']['sha256'],
        gold_source=source, gold_targets_sha256=digest(targets), authorization_sha256=digest(decision))
    report['sha256'] = digest(report)
    folder = base._mkdir(root, Path(state['directory']) / 'scores' / report['sha256'])
    files = {'score.json': base._bytes(report), 'score.md': _markdown(report)}
    for name, raw in files.items():
        path = folder / name
        if path.exists():
            if base._read_file(root, path, private=True, maximum=16 * 1024 * 1024)[1] != raw:
                raise ValueError('Existing immutable continuation score bytes changed')
        else:
            base._create_file(root, path, raw)
    return {'schema': SCHEMA, 'plan_sha256': state['plan']['sha256'],
        'experiment_sha256': state['registration']['sha256'], 'score_sha256': report['sha256'],
        'report_json': str(folder / 'score.json'), 'report_markdown': str(folder / 'score.md'),
        'selected_cases': SELECTED_CASES, 'effective_terminal_arms': len(state['results']),
        'aggregates': report['aggregates'], 'global_unique_usage': report['global_unique_usage'],
        'continuation_attempt_audit': report['continuation_attempt_audit'],
        'semantic_quality': 'unreviewed', 'holdout_or_live_access': False}
