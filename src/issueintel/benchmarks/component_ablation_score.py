"""Separate descriptive scoring of sealed accepted-Main-Dev component runs.

Inference does not import this module. Restricted patch columns are opened only
after complete inference replay and an exact, explicit scoring declaration.
File localization measures retrieval order, never a verified root cause.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import re

from issueintel.evaluation.metrics import localization_metrics
from issueintel.investigation.verifier import verify_hypotheses, validate_critique
from issueintel.repository.snapshot import validate_repo_path
from issueintel.runtime.io import digest


SCHEMA = 'component_ablation_score_v1'
ARMS = ('lexical_symbol', 'plus_graph', 'plus_history', 'plus_verifier', 'plus_counter_search')
SELECTED_CASES = 25
_HASH = re.compile('[a-f0-9]{64}')
_RECEIPT = re.compile('[a-f0-9]{32}')
_TERMINAL = {'completed', 'failed', 'unavailable'}
_RECEIPT_STATES = {'reserved', 'settled', 'missing_usage_bound', 'failed_bound', 'breach'}


def _number(value, field, *, optional=False):
    if value is None and optional:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Scoring needs finite nonnegative ' + field)
    return value


def _integer(value, field, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise ValueError('Scoring needs nonnegative integer ' + field)
    return value


def _paths(value):
    if not isinstance(value, (list, tuple)) or any(not isinstance(path, str) for path in value):
        raise ValueError('Localization targets must be explicit path lists')
    result = []
    for path in value:
        checked = validate_repo_path(path)
        if checked != path:
            raise ValueError('Localization paths must be canonical')
        if path not in result:
            result.append(path)
    return result


def _receipt(row):
    if (not isinstance(row, dict) or not isinstance(row.get('reservation_id'), str)
            or not _RECEIPT.fullmatch(row['reservation_id']) or row.get('status') not in _RECEIPT_STATES
            or row.get('model') != 'openai/gpt-oss-120b'):
        raise ValueError('Invalid owned Groq accounting receipt')
    for field in ('input_bound', 'output_bound', 'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved'):
        _integer(row.get(field), field)
    for field in ('observed_input_tokens', 'observed_output_tokens'):
        _integer(row.get(field), field, optional=True)
    if ((row.get('observed_input_tokens') is None) != (row.get('observed_output_tokens') is None)
            or row['status'] == 'settled' and row.get('observed_input_tokens') is None):
        raise ValueError('Accounting receipt has partial or missing settled usage')
    return row


def _usage(receipts):
    """Sum unique reservation snapshots; bounds remain distinct from settlement."""
    rows = list(receipts)
    settled = [row for row in rows if row['status'] == 'settled']
    uncertain = [row for row in rows if row['status'] != 'settled']
    observed = [row for row in rows if row.get('observed_input_tokens') is not None]
    return {
        'unique_reservations': len(rows), 'status_counts': dict(sorted(Counter(row['status'] for row in rows).items())),
        'tokens_charged_or_reserved': sum(row['tokens_charged_or_reserved'] for row in rows),
        'micro_usd_charged_or_reserved': sum(row['micro_usd_charged_or_reserved'] for row in rows),
        'settled_tokens': sum(row['tokens_charged_or_reserved'] for row in settled),
        'settled_micro_usd': sum(row['micro_usd_charged_or_reserved'] for row in settled),
        'unsettled_or_unknown_tokens_bound': sum(row['tokens_charged_or_reserved'] for row in uncertain),
        'unsettled_or_unknown_micro_usd_bound': sum(row['micro_usd_charged_or_reserved'] for row in uncertain),
        'observed_input_tokens': sum(row['observed_input_tokens'] for row in observed),
        'observed_output_tokens': sum(row['observed_output_tokens'] for row in observed),
        'missing_observed_usage_reservations': len(rows) - len(observed),
        'provider_invoice_verified': False,
    }


def _time_summary(values):
    present = [value for value in values if value is not None]
    return {'observed_seconds': sum(present), 'observations': len(present),
            'missing_observations': len(values) - len(present),
            'mean_observed_seconds': sum(present) / len(present) if present else None}


def _citations(pack):
    annotation, evidence = pack.get('annotation'), pack.get('evidence')
    if not isinstance(annotation, dict) or not isinstance(evidence, list):
        raise ValueError('Completed arm needs native annotation and evidence')
    structural = verify_hypotheses(annotation, evidence)
    saved = pack.get('verification')
    if (not isinstance(saved, dict) or saved.get('reports') != structural['reports']
            or saved.get('independent_human') is not False):
        raise ValueError('Completed verification does not match its exact citations')
    if saved.get('kind') == 'llm_separate_pass':
        reviews = validate_critique({'reviews': saved.get('reviews')}, annotation, evidence)
    elif saved.get('kind') == 'structural':
        reviews = []
    else:
        raise ValueError('Unknown native verification kind')
    hypotheses = annotation['hypotheses']
    return {'hypotheses': len(hypotheses), 'citation_integrity': 'valid',
            'model_cited_counter_hypotheses': sum(bool(row['counter_ids']) for row in hypotheses),
            'counter_semantic_correctness': 'unverified',
            'critic_assessment_counts': dict(sorted(Counter(row['assessment'] for row in reviews).items())),
            'critic_semantic_correctness': 'unreviewed', 'independent_human': False}


def _history(pack):
    row = pack.get('historical_source_audit')
    if not isinstance(row, dict) or row.get('sha256') != digest({key: item for key, item in row.items() if key != 'sha256'}):
        raise ValueError('Completed arm has no exact historical source audit')
    for name in ('selected_sources', 'eligible_sources', 'included_sources'):
        _integer(row.get(name), name)
    if (type(row.get('enabled')) is not bool or not row['included_sources'] <= row['eligible_sources'] <= row['selected_sources']
            or row['included_sources'] > 4 or not row['enabled'] and row['included_sources'] != 0):
        raise ValueError('Historical coverage audit is inconsistent')
    return {key: row[key] for key in ('selected_sources', 'eligible_sources', 'included_sources', 'enabled')}


def _ranked_paths(pack):
    result = []
    for row in pack['evidence']:
        if row.get('kind') != 'code':
            continue
        locator = row.get('locator')
        if not isinstance(locator, str) or not re.fullmatch(r'.+:\d+-\d+', locator):
            raise ValueError('Code evidence has no exact file/line locator')
        path = _paths([locator.rsplit(':', 1)[0]])[0]
        if path not in result:
            result.append(path)
    return result


def _counter_candidates(pack, gold_files):
    search = pack.get('refinement', {}).get('search')
    if search is None:
        added_ids = []
    else:
        added_ids = search.get('added_ids') if isinstance(search, dict) else None
        if (not isinstance(added_ids, list) or len(added_ids) > 6 or len(set(added_ids)) != len(added_ids)
                or search.get('policy') != 'falsification_focused_retrieval_v1'
                or search.get('counter_evidence_confirmed') is not False):
            raise ValueError('Counter candidates must remain bounded unverified retrieval additions')
    by_id = {row['id']: row for row in pack['evidence']}
    if any(identifier not in by_id or by_id[identifier].get('kind') != 'code' for identifier in added_ids):
        raise ValueError('Counter candidate IDs must cite included code evidence')
    added = {by_id[identifier]['locator'].rsplit(':', 1)[0] for identifier in added_ids}
    initial = {row['locator'].rsplit(':', 1)[0] for row in pack['evidence']
               if row.get('kind') == 'code' and row['id'] not in added_ids}
    new = added - initial
    return {'counter_added_code_file_count': len(added),
            'counter_added_gold_file_hits': len(added & set(gold_files)),
            'counter_added_new_code_file_count': len(new),
            'counter_added_new_gold_file_hits': len(new & set(gold_files)),
            'counter_candidate_semantic_correctness': 'unverified'}


def summarize_component_ablation(spec, results, gold_targets, calls):
    """Pure complete-matrix metrics, including failed arms and unavailable files.

    ``gold_targets`` maps each selected case to files/available_files/patch_sha256.
    Calls are globally owned once; arm references are useful allocation metadata,
    not extra bills and not independent cold-run cost estimates.
    """
    if not isinstance(spec, dict) or not isinstance(spec.get('cases'), list) or len(spec['cases']) != SELECTED_CASES:
        raise ValueError('Scoring requires the 25-case registered Main Dev matrix')
    ids = [row['case_id'] for row in spec['cases']]
    if len(set(ids)) != SELECTED_CASES or any(not isinstance(cid, str) or not cid.startswith('verified:') for cid in ids):
        raise ValueError('Scoring case identities must be distinct original Main Dev IDs')
    if [row['arm_id'] for row in spec.get('arms', [])] != list(ARMS):
        raise ValueError('Scoring requires the registered five component arms')
    if not isinstance(gold_targets, dict) or set(gold_targets) != set(ids):
        raise ValueError('Gold selection must match all registered cases exactly')
    targets = {}
    for cid, row in gold_targets.items():
        if not isinstance(row, dict) or set(row) != {'files', 'available_files', 'patch_sha256'}:
            raise ValueError('Gold targets need explicit files, snapshot coverage and patch hash')
        if not isinstance(row['patch_sha256'], str) or not _HASH.fullmatch(row['patch_sha256']):
            raise ValueError('Gold targets need recorded original patch hashes')
        targets[cid] = {'files': _paths(row['files']), 'available_files': _paths(row['available_files']),
                        'patch_sha256': row['patch_sha256']}
    if not isinstance(results, list) or len(results) != SELECTED_CASES * len(ARMS):
        raise ValueError('Incomplete inference cannot be scored')
    matrix = {}
    for row in results:
        key = (row.get('case_id'), row.get('arm_id')) if isinstance(row, dict) else (None, None)
        if key in matrix or key[0] not in ids or key[1] not in ARMS or row.get('status') not in _TERMINAL:
            raise ValueError('Results must contain every case/arm exactly once with terminal status')
        matrix[key] = row
    if not isinstance(calls, list):
        raise ValueError('Calls need a complete frozen list')
    call_map, reservation_map, reservation_owner = {}, {}, {}
    for call in calls:
        if (not isinstance(call, dict) or not isinstance(call.get('call_id'), str)
                or not _HASH.fullmatch(call['call_id']) or call['call_id'] in call_map
                or call.get('case_id') not in ids or call.get('owner_arm_id') not in ARMS
                or call.get('status') not in {'intent', 'returned', 'failed'}
                or not isinstance(call.get('receipts'), dict)):
            raise ValueError('Invalid or duplicate owned provider call')
        _number(call.get('latency_seconds'), 'provider latency', optional=True)
        for rid, receipt in call['receipts'].items():
            _receipt(receipt)
            if rid != receipt['reservation_id']:
                raise ValueError('Receipt identity differs from its owner map')
            if rid in reservation_owner and reservation_owner[rid] != call['call_id']:
                raise ValueError('Reservation cannot belong to more than one call')
            reservation_map[rid] = receipt
            reservation_owner[rid] = call['call_id']
        call_map[call['call_id']] = call
    details, referenced_calls, preparation_measurements = [], set(), {}
    for cid in ids:
        target = targets[cid]
        unavailable = sorted(set(target['files']) - set(target['available_files']))
        for arm in ARMS:
            row = matrix[cid, arm]
            referenced = row.get('call_ids')
            owned = row.get('owned_call_ids')
            if (not isinstance(referenced, list) or len(set(referenced)) != len(referenced)
                    or not isinstance(owned, list) or len(set(owned)) != len(owned)
                    or set(owned) - set(referenced) or set(referenced) - set(call_map)):
                raise ValueError('Arm provider references are incomplete or duplicated')
            expected_owned = {key for key, call in call_map.items() if call['case_id'] == cid and call['owner_arm_id'] == arm}
            if set(owned) != expected_owned or any(call_map[key]['case_id'] != cid for key in referenced):
                raise ValueError('Arm references change durable call ownership or case')
            referenced_calls.update(referenced)
            elapsed = _number(row.get('pipeline_latency_seconds'), 'pipeline latency', optional=True)
            preparation = _number(row.get('preparation_seconds'), 'preparation latency', optional=True)
            preparation_owner = row.get('preparation_owner_arm_id')
            preparation_id = row.get('preparation_measurement_id')
            if (preparation_owner not in ARMS or not isinstance(preparation_id, str)
                    or not _HASH.fullmatch(preparation_id)):
                raise ValueError('Shared preparation must retain its original owner and measurement identity')
            preparation_binding = (cid, preparation_owner, preparation)
            if preparation_id in preparation_measurements and preparation_measurements[preparation_id] != preparation_binding:
                raise ValueError('Arms changed their shared preparation measurement')
            preparation_measurements[preparation_id] = preparation_binding
            complete = row['status'] == 'completed'
            pack = row.get('pack')
            if complete and (not isinstance(pack, dict) or pack.get('case', {}).get('case_id') != cid
                             or pack['case'].get('split') != 'main_dev'):
                raise ValueError('Completed native pack differs from the selected Main Dev case')
            if not complete and pack is not None:
                raise ValueError('Failed arms cannot be represented as completed partial packs')
            citations, history = (_citations(pack), _history(pack)) if complete else (None, None)
            ranked = _ranked_paths(pack) if complete else []
            candidates = _counter_candidates(pack, target['files']) if complete else {
                'counter_added_code_file_count': 0, 'counter_added_gold_file_hits': 0,
                'counter_added_new_code_file_count': 0, 'counter_added_new_gold_file_hits': 0,
                'counter_candidate_semantic_correctness': 'unverified'}
            owned_receipts = [receipt for key in owned for receipt in call_map[key]['receipts'].values()]
            ref_receipts = {rid: receipt for key in referenced for rid, receipt in call_map[key]['receipts'].items()}
            details.append({'case_id': cid, 'arm_id': arm, 'status': row['status'],
                'ranked_code_paths': ranked, 'localization': localization_metrics(ranked, target['files'], k=5),
                'retrieval_file_recall_at_18': localization_metrics(ranked, target['files'], k=18)['recall_at_k'],
                **candidates,
                'unavailable_gold_files': unavailable, 'gold_patch_sha256': target['patch_sha256'],
                'citations': citations, 'history': history, 'pipeline_latency_seconds': elapsed,
                'preparation_seconds': preparation, 'preparation_owner_arm_id': preparation_owner,
                'preparation_measurement_id': preparation_id,
                'owned_calls': len(owned), 'referenced_calls': len(referenced),
                'owned_usage': _usage(owned_receipts), 'referenced_usage': _usage(ref_receipts.values()),
                'referenced_provider_latency': _time_summary([call_map[key].get('latency_seconds') for key in referenced]),
                'semantic_quality': 'unreviewed'})
    if referenced_calls != set(call_map):
        raise ValueError('Every frozen call must remain owned and referenced in the matrix')
    for measurement_id, (cid, owner, duration) in preparation_measurements.items():
        owned = matrix[cid, owner]
        if owned.get('preparation_measurement_id') != measurement_id or owned.get('preparation_seconds') != duration:
            raise ValueError('Shared preparation has no matching original owner result')
    aggregates = {}
    metric_names = ('recall_at_k', 'precision_at_k', 'mrr', 'all_files_at_k')
    for arm in ARMS:
        rows = [row for row in details if row['arm_id'] == arm]
        completed = [row for row in rows if row['status'] == 'completed']
        hypotheses = sum(row['citations']['hypotheses'] for row in completed)
        counters = sum(row['citations']['model_cited_counter_hypotheses'] for row in completed)
        metrics = {}
        for name in metric_names:
            values = [row['localization'][name] for row in rows if row['localization'][name] is not None]
            metrics[name] = {'mean': sum(values) / len(values) if values else None,
                             'defined_cases': len(values), 'undefined_no_gold_cases': SELECTED_CASES - len(values)}
        owned_calls = [call for call in calls if call['owner_arm_id'] == arm]
        owned_receipts = [receipt for call in owned_calls for receipt in call['receipts'].values()]
        recall18 = [row['retrieval_file_recall_at_18'] for row in rows if row['retrieval_file_recall_at_18'] is not None]
        aggregates[arm] = {'selected_cases': SELECTED_CASES, 'status_counts': dict(sorted(Counter(row['status'] for row in rows).items())),
            'completed_cases': len(completed), 'completion_coverage': len(completed) / SELECTED_CASES,
            'retrieval_localization': metrics, 'gold_file_denominator': 'all_patch_files_including_unavailable',
            'retrieval_file_recall_at_18_all_selected': {
                'mean': sum(recall18) / len(recall18) if recall18 else None,
                'defined_cases': len(recall18), 'undefined_no_gold_cases': SELECTED_CASES - len(recall18)},
            'counter_added_code_file_count': sum(row['counter_added_code_file_count'] for row in rows),
            'counter_added_gold_file_hits': sum(row['counter_added_gold_file_hits'] for row in rows),
            'counter_added_new_code_file_count': sum(row['counter_added_new_code_file_count'] for row in rows),
            'counter_added_new_gold_file_hits': sum(row['counter_added_new_gold_file_hits'] for row in rows),
            'counter_candidate_coverage_interpretation': 'exploratory_patch_file_hits_not_confirmed_counter_evidence',
            'cases_with_unavailable_gold_files': sum(bool(row['unavailable_gold_files']) for row in rows),
            'citation_valid_completed_arms': len(completed),
            'citation_valid_arm_coverage_of_selected': len(completed) / SELECTED_CASES,
            'model_cited_counter_hypotheses': counters, 'completed_hypotheses': hypotheses,
            'model_cited_counter_coverage_completed_hypotheses': counters / hypotheses if hypotheses else None,
            'history_included_cases': sum(row['history']['included_sources'] > 0 for row in completed),
            'history_coverage_of_selected': sum(row['history']['included_sources'] > 0 for row in completed) / SELECTED_CASES,
            'owned_provider_calls': len(owned_calls), 'owned_usage': _usage(owned_receipts),
            'owned_calls_without_receipts': sum(not call['receipts'] for call in owned_calls),
            'owned_provider_latency': _time_summary([call.get('latency_seconds') for call in owned_calls]),
            'pipeline_latency': _time_summary([row['pipeline_latency_seconds'] for row in rows]),
            'referenced_preparation_latency': _time_summary([row['preparation_seconds'] for row in rows]),
            'owned_preparation_latency': _time_summary([row['preparation_seconds'] for row in rows if row['preparation_owner_arm_id'] == arm])}
    contrasts = []
    indexed = {(row['case_id'], row['arm_id']): row for row in details}
    for left, right in zip(ARMS, ARMS[1:]):
        paired = []
        paired18 = []
        complete_pair = []
        for cid in ids:
            a, b = indexed[cid, left], indexed[cid, right]
            av, bv = a['localization']['recall_at_k'], b['localization']['recall_at_k']
            if av is not None and bv is not None:
                paired.append(bv - av)
                if a['status'] == b['status'] == 'completed':
                    complete_pair.append(bv - av)
            av18, bv18 = a['retrieval_file_recall_at_18'], b['retrieval_file_recall_at_18']
            if av18 is not None and bv18 is not None:
                paired18.append(bv18 - av18)
        contrasts.append({'from_arm': left, 'to_arm': right, 'selected_cases': SELECTED_CASES,
            'defined_paired_cases': len(paired), 'mean_recall_at_5_increment_all_selected': sum(paired) / len(paired) if paired else None,
            'both_completed_cases': sum(indexed[cid, left]['status'] == indexed[cid, right]['status'] == 'completed' for cid in ids),
            'mean_recall_at_5_increment_both_completed': sum(complete_pair) / len(complete_pair) if complete_pair else None,
            'defined_paired_cases_at_18': len(paired18),
            'mean_recall_at_18_increment_all_selected': sum(paired18) / len(paired18) if paired18 else None,
            'interpretation': 'descriptive_conditional_increment_one_trial_not_isolated_causal_effect'})
    return {'schema': SCHEMA, 'selected_cases': SELECTED_CASES, 'arms': list(ARMS), 'cases': details,
        'aggregates': aggregates, 'contrasts': contrasts,
        'global_unique_provider_calls': len(calls), 'global_unique_usage': _usage(reservation_map.values()),
        'global_call_status_counts': dict(sorted(Counter(call['status'] for call in calls).items())),
        'global_calls_without_receipts': sum(not call['receipts'] for call in calls),
        'global_provider_latency': _time_summary([call.get('latency_seconds') for call in calls]),
        'global_unique_preparation_latency': _time_summary([value[2] for value in preparation_measurements.values()]),
        'inference_gold_used': False, 'scoring_gold_used': True, 'holdout_or_live_access': False,
        'history_contrast_informative': any(row.get('history_included_sources', 0) > 0 for row in spec['cases']),
        'metric_definitions': {
            'ranked_files': 'first_occurrence_of_unique_code_file_paths_in_native_evidence_order',
            'recall_at_k': 'gold_files_in_first_k_unique_retrieved_paths_divided_by_all_patch_files',
            'precision_at_k': 'gold_files_in_first_k_unique_retrieved_paths_divided_by_returned_paths_up_to_k_empty_is_zero',
            'mrr': 'reciprocal_rank_of_first_gold_file_in_entire_bounded_retrieved_file_list',
            'all_files_at_k': 'all_patch_files_are_in_first_k_unique_retrieved_paths',
            'undefined_no_gold': 'retain_selected_case_count_and_report_undefined_count_separately',
        },
        'semantic_quality': 'unreviewed', 'independent_human': False,
        'limitations': [
            'File rankings use first occurrence in retrieved code evidence, not model diagnosis or causal correctness.',
            'Failed and unavailable arms have empty predictions; all selected cases remain in denominators.',
            'All patch files remain targets, including files absent from the strict code snapshot.',
            'Graph contrast changes retrieval allocation under the same excerpt cap; all arms build the graph.',
            'History covers explicitly selected proof-bound prior opening texts, not complete repository history.',
            'Zero history coverage makes the history contrast uninformative rather than demonstrating no history benefit.',
            'Verifier shares generation and does not rerank retrieval; localization is expected to be invariant when both arms complete.',
            'Counter-search appends candidates without reranking initial evidence; Recall@5 can remain mechanically unchanged. Recall@18 covers the bounded 12+6 code excerpts.',
            'Counter-added file hits are exploratory patch-localization coverage, not confirmed counter-evidence or correct falsification procedures.',
            'Citation integrity and model-cited counters do not establish semantic support or confirmed counter-evidence.',
            'Shared stage/call cache means owned costs depend on owner order; referenced costs must not be added into global spend.',
            'Same upper call budget does not imply equal actual spending; latency is one conditional warm-cache trial.',
            'Preparation is deduplicated by its case-batch measurement identity; a later resume can legitimately prepare again.',
            'Local receipts are accounting records, not provider invoices or independent verification of model execution.',
            'A call without a receipt has unknown cost; a zero recorded sum does not establish a free call.',
            'Development cases may have prior development exposure; no human, Holdout, Live or external-agent superiority claim.',
        ]}


def _patch_files(patch):
    """Read all old-side file identities, including binary/mode-only blocks.

    Unknown/ambiguous Git path syntax rejects scoring instead of shrinking the
    reference denominator. Hunk lines are never mistaken for file headers.
    """
    from .localization import _path

    if not isinstance(patch, str) or len(patch.encode('utf-8')) > 16 * 1024 * 1024:
        raise ValueError('Restricted patch text exceeds the bounded scorer input')
    blocks, current = [], []
    for line in patch.splitlines():
        if line.startswith('diff --git ') and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    files = []
    for block in blocks:
        header, old, selected = True, None, []
        for line in block:
            if line.startswith('@@ '):
                header = False
            elif header and line.startswith('--- '):
                old = _path(line[4:])
            elif header and line.startswith('+++ '):
                target = old or _path(line[4:])
                if target:
                    selected.append(target)
            elif header and line.startswith('rename from '):
                selected.append(_path(line[len('rename from '):], strip_prefix=False))
        if not selected and block and block[0].startswith('diff --git '):
            text = block[0][len('diff --git '):]
            candidates = set()
            # Git C-quoted paths and unquoted paths with spaces are supported.
            # A separator inside a quoted token will not parse as a complete
            # C-quoted path; multiple valid separators remain ambiguous.
            for match in re.finditer(r' (?=(?:b/|"b/))', text):
                left, right = text[:match.start()], text[match.end():]
                try:
                    if not left.startswith(('a/', '"a/')) or not right.startswith(('b/', '"b/')):
                        continue
                    old_path, new_path = _path(left), _path(right)
                    if old_path and new_path:
                        candidates.add((old_path, new_path))
                except (ValueError, UnicodeError):
                    continue
            if len(candidates) != 1:
                raise ValueError('Ambiguous or unsupported patch file identity; denominator cannot be reduced')
            selected = [next(iter(candidates))[0]]
        for path in selected:
            if path and path not in files:
                files.append(path)
    if patch.strip() and not files:
        raise ValueError('Nonempty patch has no verified file identities; denominator cannot be reduced')
    return files


def _gold_targets(root, spec, available):
    """Lazy, exact selected DEV patch columns; no test/hint/rationale columns."""
    from .dev_context import development_catalog
    from .strict_dev import _checked
    import pyarrow.parquet as parquet

    catalog = development_catalog(root)
    ids = [row['case_id'] for row in spec['cases']]
    if set(ids) - set(catalog.get('_members', {})):
        raise ValueError('Restricted selection is outside accepted original Main Dev')
    reference = catalog['_authority']['sources'][0]
    path = _checked(root, reference)
    names = [cid.removeprefix('verified:') for cid in ids]
    rows = parquet.read_table(path, columns=['instance_id', 'patch'],
        filters=[('instance_id', 'in', names)]).to_pylist()
    if (len(rows) != SELECTED_CASES or len({row['instance_id'] for row in rows}) != SELECTED_CASES
            or {row['instance_id'] for row in rows} != set(names)):
        raise ValueError('Restricted patch selection differs from the complete registered DEV matrix')
    targets = {}
    for row in rows:
        patch = row['patch']
        if not isinstance(patch, str):
            raise ValueError('Restricted patch must retain original text bytes')
        files = _patch_files(patch)
        cid = 'verified:' + row['instance_id']
        targets[cid] = {'files': files, 'available_files': available[cid],
                        'patch_sha256': sha256(patch.encode('utf-8')).hexdigest()}
    _checked(root, reference)  # Original source integrity must still hold after decoding.
    return targets, deepcopy(reference)


def _markdown(report):
    lines = ['# IssueIntel Main Dev component comparison', '',
        'Registration SHA256: ' + report['experiment_sha256'],
        'Complete inference seal SHA256: ' + report['inference_seal_sha256'], '',
        '25 accepted original Main Dev cases; five cumulative arms; one trial per arm/case.',
        'Retrieval file localization is a patch-file proxy. Failed arms and files outside the snapshot remain in denominators.',
        'Semantic grounding and counter-evidence quality remain unreviewed. Holdout and Live were not opened.', '',
        '| Arm | Completed / selected | Gold-defined cases | Recall@5 | Precision@5 | MRR | All-files@5 | Owned calls | Charged/reserved USD |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for arm, row in report['aggregates'].items():
        values = row['retrieval_localization']
        lines.append(f"| {arm} | {row['completed_cases']} / 25 | {values['recall_at_k']['defined_cases']} / 25 | {values['recall_at_k']['mean']} | {values['precision_at_k']['mean']} | {values['mrr']['mean']} | {values['all_files_at_k']['mean']} | {row['owned_provider_calls']} | {row['owned_usage']['micro_usd_charged_or_reserved'] / 1_000_000:.6f} |")
    lines += ['', 'Owned calls/costs count the original stage owner. Shared references are not additional paid calls.',
              'Global unique calls: ' + str(report['global_unique_provider_calls']),
              'Global charged/reserved USD: ' + str(report['global_unique_usage']['micro_usd_charged_or_reserved'] / 1_000_000), '',
              'Recall@18 and exploratory counter candidate file coverage (counts sum per-case file sets):', '',
              '| Arm | Recall@18 | Added candidate files | Added patch-file hits | New candidate files | New patch-file hits |',
              '| --- | --- | --- | --- | --- | --- |']
    for arm, row in report['aggregates'].items():
        lines.append(f"| {arm} | {row['retrieval_file_recall_at_18_all_selected']['mean']} | {row['counter_added_code_file_count']} | {row['counter_added_gold_file_hits']} | {row['counter_added_new_code_file_count']} | {row['counter_added_new_gold_file_hits']} |")
    lines += ['', 'Precision@5 divides by the number of returned paths up to five; MRR uses the entire bounded retrieved file list.', '',
              *('- ' + text for text in report['limitations']), '', 'Score SHA256: ' + report['sha256'], '']
    return '\n'.join(lines).encode('utf-8')


def score_component_ablation(root, identifier, authorization_path):
    """Replay full inference before any restricted gold read; publish append-only."""
    from .component_ablation import verify_run_authorization, _bytes, _mkdir, _create_file, _read_file
    from .component_ablation_run import load_sealed_inference
    from .dev_context import accepted_context

    root = Path(root).resolve()
    state = load_sealed_inference(root, identifier)
    spec, directory, seal = state['registration'], state['directory'], state['seal']
    decision = verify_run_authorization(root, spec, authorization_path, requested_cases=SELECTED_CASES)
    if decision['gold_scoring_authorized'] is not True:
        raise PermissionError('This exact registered inference has no separate DEV gold scoring authorization')
    available = {}
    for row in spec['cases']:
        case, snapshot, receipt = accepted_context(root, row['case_id'])
        if case.get('split') != 'main_dev' or snapshot.protocol != 'issue_time':
            raise ValueError('Scoring snapshot must remain accepted strict Main Dev')
        # Runner already replays source bindings; retain a final public-path projection
        # before crossing the restricted boundary, without handing gold to inference.
        available[row['case_id']] = [file.path for file in snapshot.files]
    second = load_sealed_inference(root, identifier)
    if second['seal'] != seal or second['results'] != state['results'] or second['calls'] != state['calls']:
        raise ValueError('Inference changed before the separate scoring boundary')
    targets, source = _gold_targets(root, spec, available)
    report = summarize_component_ablation(spec, state['results'], targets, state['calls'])
    final = load_sealed_inference(root, identifier)
    if final['seal'] != seal or final['results'] != state['results'] or final['calls'] != state['calls']:
        raise ValueError('Inference changed while separate scoring was computed')
    if verify_run_authorization(root, spec, authorization_path, requested_cases=SELECTED_CASES) != decision:
        raise ValueError('Scoring authorization changed during scoring')
    report.update(experiment_sha256=identifier, inference_seal_sha256=seal['sha256'],
                  gold_source=source, authorization_sha256=digest(decision),
                  gold_targets_sha256=digest(targets))
    report['sha256'] = digest(report)
    folder = _mkdir(root, Path(directory) / 'scores' / report['sha256'])
    files = {'score.json': _bytes(report), 'score.md': _markdown(report)}
    for name, raw in files.items():
        path = folder / name
        if path.exists():
            if _read_file(root, path, private=True, maximum=8 * 1024 * 1024)[1] != raw:
                raise ValueError('Existing immutable score bytes changed')
        else:
            _create_file(root, path, raw)
    return {'schema': SCHEMA, 'experiment_sha256': identifier, 'score_sha256': report['sha256'],
            'report_json': str(folder / 'score.json'), 'report_markdown': str(folder / 'score.md'),
            'selected_cases': SELECTED_CASES, 'aggregates': report['aggregates'],
            'global_unique_usage': report['global_unique_usage'],
            'semantic_quality': 'unreviewed', 'holdout_or_live_access': False}
