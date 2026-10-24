"""Registration and read-only preflight for a proposed strict-DEV experiment.

Registration is source preparation, never inference or spending authority.  The
separate runner must match an explicit experiment-specific author decision.
No gold, environment loader, provider or ledger is imported or constructed here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from issueintel.annotations.public_inputs import reject_hidden
from issueintel.investigation.pipeline import public_case
from issueintel.investigation.policy_agent import context_binding
from issueintel.repository.graph import build_graph
from issueintel.retrieval.ablation_policy import build_policy
from issueintel.retrieval.verified_history import verified_history_evidence
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest
from .dev_context import PARENT, accepted_context, public_catalog


SCOPE = 'accepted_original_main_dev_component_ablation_v1'
SCHEMA = 'component_ablation_registration_v1'
PROTOCOL_SCHEMA = 'component_ablation_protocol_v1'
AUTHORIZATION_SCHEMA = 'component_ablation_run_authorization_v1'
SELECTED_CASES = 25
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_HISTORY_BYTES = SELECTED_CASES * MAX_JSON_BYTES
MAX_REGISTRATION_BYTES = 2 * 1024 * 1024
ARMS = ('lexical_symbol', 'plus_graph', 'plus_history', 'plus_verifier', 'plus_counter_search')
ACCESS_FLAGS = ('holdout_access_authorized', 'live_access_authorized',
                'new_freeze_authorized', 'bulk_annotation_restart_authorized',
                'jev_spending_authorized')
_HASH = re.compile('[a-f0-9]{64}')
_CASE = re.compile(r'verified:[A-Za-z0-9_./-]{1,300}')


def proposed_protocol():
    """The supported design; changing its semantics requires a code revision."""
    return {
        'schema': PROTOCOL_SCHEMA, 'scope': SCOPE, 'execution_authorized': False,
        'selected_case_count': SELECTED_CASES, 'provider': 'groq',
        'model': 'openai/gpt-oss-120b', 'reasoning_effort': 'medium',
        'max_output_tokens': 2048,
        'selection': 'first_25_round_robin_repository_then_case_id_among_accepted_original_main_dev_no_outcome_filter',
        'arm_order': 'rotate_registered_arm_order_by_case_index_modulo_five',
        'trials_per_case_arm': 1,
        'arms': [
            {'arm_id': arm, 'graph': index >= 1, 'history': index >= 2,
             'verifier': index >= 3, 'counter_search': index >= 4}
            for index, arm in enumerate(ARMS)
        ],
        'retrieval': {'total_initial_code_excerpt_cap': 12,
                      'lexical_only_seed_cap': 12, 'graph_arm_seed_cap': 8,
                      'graph_neighbour_cap': 4, 'excerpt_character_cap': 1600,
                      'graph_built_for_all_arms': True,
                      'comparison': 'retrieval_allocation_under_equal_excerpt_caps_not_isolated_graph_effect'},
        'history': {'mode': 'verified_issue_openings', 'max_candidates_per_case': 20,
                    'max_source_bytes': 524288, 'max_case_bytes': MAX_JSON_BYTES,
                    'max_input_file_bytes': MAX_HISTORY_BYTES,
                    'max_included_sources': 4, 'max_excerpt_characters': 1200,
                    'selection': 'explicit_frozen_candidate_order_first_eligible',
                    'availability': 'independent_opening_event_and_issue_creation_strictly_before_target_cutoff',
                    'missing_sources': 'retain_case_report_coverage_zero_history_contrast_uninformative'},
        'limits': {'max_provider_calls_per_arm': 5,
                   'max_provider_calls_total': SELECTED_CASES * len(ARMS) * 5,
                   'generation_passes': 2, 'repair_slots_per_generation': 1,
                   'verifier_passes': 1, 'counter_search_rounds': 1,
                   'counter_queries': 3, 'counter_added_code_excerpts': 6},
        'primary_metrics': ['retrieval_file_recall_at_5_all_selected',
                            'completion_coverage', 'charged_or_reserved_tokens_and_cost',
                            'provider_and_pipeline_latency'],
        'secondary_metrics': ['retrieval_file_precision_at_5', 'retrieval_file_mrr',
                              'retrieval_all_patch_files_at_5', 'citation_integrity',
                              'model_cited_counter_coverage', 'history_source_coverage',
                              'retrieval_file_recall_at_18_all_selected',
                              'counter_added_code_file_count', 'counter_added_gold_file_hits'],
        'gold_policy': 'separate_explicitly_authorized_scoring_only_after_complete_inference_seal',
        'semantic_quality': 'unreviewed_not_human_or_causal_validation',
        'prior_dev_exposure': 'may_reuse_previously_inspected_development_cases_not_an_independent_sample',
        'interpretation': 'descriptive_conditional_increments_not_production_or_external_agent_performance',
        **{flag: False for flag in ACCESS_FLAGS},
    }


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _pairs(rows):
    value = {}
    for key, item in rows:
        if key in value:
            raise ValueError('Duplicate JSON fields are forbidden')
        value[key] = item
    return value


def _bad_number(value):
    raise ValueError('Non-finite JSON numbers are forbidden')


def _parse(raw):
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                          parse_constant=_bad_number)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid bounded JSON') from error


def _safe_path(root, value, *, private=False, missing=False):
    root = Path(root).resolve()
    supplied = Path(value)
    if '..' in supplied.parts:
        raise ValueError('Parent path traversal is forbidden')
    path = Path(os.path.abspath(supplied if supplied.is_absolute() else root / supplied))
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Experiment paths must stay inside the workspace')
    if private and (not path.is_relative_to(root / 'artifacts') or path == root / 'artifacts'):
        raise ValueError('Experiment input/output must stay in ignored artifacts')
    current = root
    parts = path.relative_to(root).parts
    for index, part in enumerate(parts):
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Experiment paths cannot traverse links or reparse points')
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Experiment ancestor is not a directory')
    return path


def _read_file(root, value, *, private=False, maximum=MAX_JSON_BYTES):
    path = _safe_path(root, value, private=private)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError('Experiment file is not a bounded regular file')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Experiment file exceeds its byte limit')
    _safe_path(root, path, private=private)
    return path, raw


def _seal(value):
    return {**value, 'sha256': digest(value)}


def _read_sealed(root, path):
    _, raw = _read_file(root, path, private=True, maximum=MAX_REGISTRATION_BYTES)
    value = _parse(raw)
    if (not isinstance(value, dict) or value.get('sha256') !=
            digest({key: item for key, item in value.items() if key != 'sha256'})):
        raise ValueError('Experiment content seal changed')
    return value


def _validate_protocol(value):
    expected = proposed_protocol()
    if (not isinstance(value, dict) or set(value) != set(expected)
            or type(value.get('max_output_tokens')) is not int
            or not 1 <= value['max_output_tokens'] <= 8192):
        raise ValueError('Unsupported component ablation protocol')
    expected['max_output_tokens'] = value['max_output_tokens']
    # Python treats False == 0; canonical JSON preserves the intended types.
    if _bytes(value) != _bytes(expected):
        raise ValueError('Unsupported component ablation protocol')
    return deepcopy(value)


def _fingerprint(root):
    """Pin all inference Python and configuration bytes; never traverse links."""
    root = Path(root).resolve()
    files = []
    for directory, suffixes in ((root / 'src/issueintel', {'.py'}),
                                (root / 'configs', {'.json', '.toml'})):
        _safe_path(root, directory)
        for current, folders, names in os.walk(directory, followlinks=False):
            for name in list(folders):
                path = Path(current) / name
                _safe_path(root, path)
                if name == '__pycache__':
                    folders.remove(name)
            files.extend(Path(current) / name for name in names if Path(name).suffix in suffixes)
    for name in ('run.py', 'pyproject.toml'):
        if (root / name).exists():
            files.append(root / name)
    if len(files) > 10000 or not any(path.suffix == '.py' for path in files):
        raise ValueError('Invalid inference source inventory')
    result = {}
    total = 0
    for path in sorted(files):
        _, raw = _read_file(root, path, maximum=4 * 1024 * 1024)
        total += len(raw)
        if total > 64 * 1024 * 1024:
            raise ValueError('Inference source inventory exceeds its byte limit')
        result[path.relative_to(root).as_posix()] = sha256(raw).hexdigest()
    return result


def _runtime_binding(root, protocol):
    config = RuntimeConfig.load(root)
    price_path, price_raw = _read_file(root, config.price_file)
    price = config.price()
    if (config.model != protocol['model'] or protocol['provider'] != 'groq'
            or type(config.max_output_tokens) is not int
            or protocol['max_output_tokens'] > config.max_output_tokens
            or protocol['max_output_tokens'] > price.max_output_tokens
            or protocol['max_output_tokens'] >= price.context_tokens):
        raise ValueError('Registered model/output limits differ from runtime or dated prices')
    policy = config.policy
    return {'provider': protocol['provider'], 'model': config.model,
            'reasoning_effort': protocol['reasoning_effort'],
            'max_output_tokens': protocol['max_output_tokens'],
            'runtime_max_output_tokens': config.max_output_tokens,
            'price_file': price_path.relative_to(root).as_posix(),
            'price_file_sha256': sha256(price_raw).hexdigest(),
            'price_checked_at': price.checked_at, 'price_source': price.source,
            'input_per_million_usd': str(price.input_per_million),
            'output_per_million_usd': str(price.output_per_million),
            'context_tokens': price.context_tokens, 'model_max_output_tokens': price.max_output_tokens,
            'ledger_file': Path(config.ledger_file).relative_to(root).as_posix(),
            'budget_policy': {
                'daily_total_tokens': policy.daily_total_tokens,
                'monthly_usd': str(policy.monthly_usd) if policy.monthly_usd is not None else None,
                'daily_usd': str(policy.daily_usd) if policy.daily_usd is not None else None,
                'daily_requests': policy.daily_requests,
                'tokens_unlimited_authorized': policy.tokens_unlimited_authorized,
                'usd_unlimited_authorized': policy.usd_unlimited_authorized},
            'request_temperature': 0, 'transport': 'groq_native_guarded_provider',
            'provider_key_or_ledger_checked': False}


def _arm_recipes(protocol):
    recipes = []
    for row in protocol['arms']:
        policy = build_policy('0' * 64, row['arm_id'], graph=row['graph'], history=row['history'],
                              verifier=row['verifier'], counter_search=row['counter_search'],
                              max_output_tokens=protocol['max_output_tokens'])
        recipes.append({key: value for key, value in policy.items()
                        if key not in {'registration_sha256', 'sha256'}})
    return recipes


def execution_policy(spec, arm_id):
    """Bind a supported frozen recipe to its registration; grant no authority."""
    if (not isinstance(spec, dict) or spec.get('schema') != SCHEMA
            or spec.get('scope') != SCOPE or spec.get('sha256') !=
            digest({key: value for key, value in spec.items() if key != 'sha256'})):
        raise ValueError('Execution policy needs an exact sealed registration')
    protocol = _validate_protocol(spec.get('protocol'))
    if spec.get('arms') != _arm_recipes(protocol) or arm_id not in ARMS:
        raise ValueError('Choose an exact registered component arm')
    row = next(row for row in protocol['arms'] if row['arm_id'] == arm_id)
    return build_policy(spec['sha256'], arm_id, graph=row['graph'], history=row['history'],
                        verifier=row['verifier'], counter_search=row['counter_search'],
                        max_output_tokens=protocol['max_output_tokens'])


def _selected_rows(catalog):
    if not isinstance(catalog, dict) or not isinstance(catalog.get('cases'), list):
        raise ValueError('Accepted original Main Dev catalog is unavailable')
    groups = {}
    identifiers = set()
    for row in catalog['cases']:
        if (not isinstance(row, dict) or not isinstance(row.get('case_id'), str)
                or not _CASE.fullmatch(row['case_id']) or row['case_id'] in identifiers
                or not isinstance(row.get('repository'), str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', row['repository'])):
            raise ValueError('Accepted catalog identity is invalid or duplicated')
        identifiers.add(row['case_id'])
        groups.setdefault(row['repository'], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: row['case_id'])
    selected = []
    while len(selected) < SELECTED_CASES and any(groups.values()):
        for repository in sorted(groups):
            if groups[repository] and len(selected) < SELECTED_CASES:
                selected.append(groups[repository].pop(0))
    if len(selected) != SELECTED_CASES:
        raise ValueError('Need 25 accepted original Main Dev cases without replacements')
    return selected


def _catalog_binding(catalog):
    checkpoint = catalog.get('checkpoint_sha256')
    if checkpoint is not None and (not isinstance(checkpoint, str) or not _HASH.fullmatch(checkpoint)):
        raise ValueError('Invalid accepted Main Dev checkpoint identity')
    return {'parent_cohort_sha256': PARENT, 'checkpoint_sha256': checkpoint,
            'selected_membership': [{'case_id': row['case_id'], 'repository': row['repository']}
                                    for row in _selected_rows(catalog)],
            'new_freeze_authorized': False}


def _history_input(root, history_path, selected):
    original = None
    if history_path is None:
        supplied = {}
    else:
        _, original = _read_file(root, history_path, private=True, maximum=MAX_HISTORY_BYTES)
        supplied = _parse(original)
        reject_hidden(supplied)
    ids = {row['case_id'] for row in selected}
    if not isinstance(supplied, dict) or set(supplied) - ids:
        raise ValueError('History input may contain only registered selected Main Dev IDs')
    mapping = {row['case_id']: deepcopy(supplied.get(row['case_id'], [])) for row in selected}
    for sources in mapping.values():
        if not isinstance(sources, list) or len(sources) > 20:
            raise ValueError('History input needs at most 20 explicit receipts per case')
        if len(_bytes(sources)) > MAX_JSON_BYTES:
            raise ValueError('Frozen historical sources exceed two MiB per case')
    normalized = _bytes(mapping)
    if len(normalized) > MAX_HISTORY_BYTES:
        raise ValueError('Total frozen history input exceeds fifty MiB')
    return mapping, normalized, original


def _case_binding(root, row, sources):
    case, snapshot, receipt = accepted_context(root, row['case_id'])
    projected = public_case(case)
    snapshot.verify()
    proof = snapshot.availability.get('receipt')
    if (case.get('case_id') != row['case_id'] or case.get('split') != 'main_dev'
            or case.get('repository') != row['repository'] or snapshot.protocol != 'issue_time'
            or snapshot.cutoff != case.get('issue_created_at')
            or not isinstance(proof, dict) or proof.get('verified') is not True
            or not isinstance(receipt, dict)
            or receipt.get('schema') != 'main_dev_original_query_receipt_v1'
            or receipt.get('verified') is not True or receipt.get('case_id') != row['case_id']
            or receipt.get('created_at') != snapshot.cutoff
            or receipt.get('text_sha256') != sha256(case['problem_statement'].encode()).hexdigest()
            or not isinstance(receipt.get('strict_dev_proof_sha256'), str)
            or not _HASH.fullmatch(receipt['strict_dev_proof_sha256'])
            or proof.get('strict_dev_proof_sha256') != receipt['strict_dev_proof_sha256']
            or not isinstance(receipt.get('source_urls'), list) or not receipt['source_urls']
            or any(not isinstance(url, str) or not url.startswith('https://') for url in receipt['source_urls'])):
        raise ValueError('Selected context is not the exact accepted original strict Main Dev source')
    graph = build_graph(snapshot)
    if graph.commit_sha != snapshot.commit_sha or graph.snapshot_sha256 != snapshot.snapshot_sha256:
        raise ValueError('Prepared graph differs from its exact snapshot')
    _, audit = verified_history_evidence(sources, case, enabled=True,
                                         target_source_urls=receipt['source_urls'])
    return {'case_id': row['case_id'], 'repository': row['repository'],
            'source': context_binding(case, snapshot), 'public_case_sha256': digest(projected),
            'query_receipt_sha256': digest(receipt),
            'strict_dev_proof_sha256': receipt['strict_dev_proof_sha256'],
            'graph_sha256': digest(graph.to_dict()), 'graph_parser_version': graph.parser_version,
            'history_sources_sha256': digest(sources), 'history_audit_sha256': digest(audit),
            'history_selected_sources': audit['selected_sources'],
            'history_eligible_sources': audit['eligible_sources'],
            'history_included_sources': audit['included_sources']}


def _directory(root, identifier, *, missing=False):
    if not isinstance(identifier, str) or not _HASH.fullmatch(identifier):
        raise ValueError('Choose an exact registered component experiment SHA256')
    return _safe_path(root, Path(root) / 'artifacts/experiments/component_ablation' / identifier,
                      private=True, missing=missing)


def _mkdir(root, path):
    path = _safe_path(root, path, private=True, missing=True)
    current = Path(root).resolve()
    for part in path.relative_to(current).parts:
        current /= part
        current.mkdir(exist_ok=True)
        _safe_path(root, current)
    return path


def _create_file(root, path, content):
    path = _safe_path(root, path, private=True, missing=True)
    with path.open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    _safe_path(root, path, private=True)


def register_component_ablation(root, protocol_path=None, *, history_path=None):
    """Freeze public accepted sources and proposed controls, making zero calls."""
    root = Path(root).resolve()
    path, protocol_raw = _read_file(root, protocol_path or root / 'configs/component_ablation_protocol.json')
    protocol = _validate_protocol(_parse(protocol_raw))
    before = _fingerprint(root)
    runtime = _runtime_binding(root, protocol)
    catalog = public_catalog(root)
    catalog_ref = _catalog_binding(catalog)
    selected = catalog_ref['selected_membership']
    mapping, history_raw, history_original = _history_input(root, history_path, selected)
    cases = [_case_binding(root, row, mapping[row['case_id']]) for row in selected]
    if before != _fingerprint(root) or catalog_ref != _catalog_binding(public_catalog(root)):
        raise ValueError('Source/configuration or accepted membership changed during registration')
    spec = _seal({
        'schema': SCHEMA, 'scope': SCOPE, 'registered_at': datetime.now(timezone.utc).isoformat(),
        'protocol': protocol, 'protocol_source': path.relative_to(root).as_posix(),
        'protocol_sha256': sha256(protocol_raw).hexdigest(), 'fingerprint': before,
        'catalog': catalog_ref, 'cases': cases, 'arms': _arm_recipes(protocol),
        'limits': deepcopy(protocol['limits']), 'runtime': runtime,
        'history_input': {'file': 'history-input.json', 'sha256': sha256(history_raw).hexdigest(),
                          'selection_sha256': digest(mapping), 'bytes': len(history_raw),
                          'provided_file': 'history-source.json' if history_original is not None else None,
                          'provided_file_sha256': sha256(history_original).hexdigest() if history_original is not None else None},
        'execution_authorized': False, 'gold_read': False,
        'gold_scoring_authorized': False, 'quality_claim': False,
        **{flag: False for flag in ACCESS_FLAGS}})
    directory = _directory(root, spec['sha256'], missing=True)
    _mkdir(root, directory.parent)
    directory.mkdir()  # Exact content directory is exclusive; never overwrite.
    _safe_path(root, directory, private=True)
    _create_file(root, directory / 'protocol.json', protocol_raw)
    _create_file(root, directory / 'history-input.json', history_raw)
    if history_original is not None:
        _create_file(root, directory / 'history-source.json', history_original)
    _create_file(root, directory / 'registration.json', _bytes(spec))  # Publish last.
    return {'experiment_sha256': spec['sha256'], 'selected_cases': len(cases),
            'arms': list(ARMS), 'registration': str(directory / 'registration.json'),
            'execution_authorized': False, 'gold_read': False,
            'history_covered_cases': sum(row['history_included_sources'] > 0 for row in cases),
            'history_contrast_informative': any(row['history_included_sources'] > 0 for row in cases)}


def load_history_sources(root, spec, directory):
    """Replay the private frozen selection, never the mutable original file."""
    descriptor = spec['history_input']
    if (not isinstance(descriptor, dict) or set(descriptor) !=
            {'file', 'sha256', 'selection_sha256', 'bytes', 'provided_file', 'provided_file_sha256'}
            or descriptor['file'] != 'history-input.json'):
        raise ValueError('Frozen history descriptor changed')
    _, raw = _read_file(root, Path(directory) / descriptor['file'], private=True,
                        maximum=MAX_HISTORY_BYTES)
    if type(descriptor['bytes']) is not int or len(raw) != descriptor['bytes'] or sha256(raw).hexdigest() != descriptor['sha256']:
        raise ValueError('Frozen historical source bytes changed')
    mapping = _parse(raw)
    reject_hidden(mapping)
    ids = [row['case_id'] for row in spec['cases']]
    if (not isinstance(mapping, dict) or set(mapping) != set(ids)
            or digest(mapping) != descriptor['selection_sha256']
            or any(not isinstance(value, list) or len(value) > 20
                   or len(_bytes(value)) > MAX_JSON_BYTES for value in mapping.values())):
        raise ValueError('Frozen historical selection changed')
    original_name = descriptor['provided_file']
    if original_name is not None:
        if original_name != 'history-source.json':
            raise ValueError('Frozen historical original descriptor changed')
        _, original = _read_file(root, Path(directory) / original_name, private=True,
                                maximum=MAX_HISTORY_BYTES)
        if sha256(original).hexdigest() != descriptor['provided_file_sha256']:
            raise ValueError('Frozen historical original bytes changed')
        source = _parse(original)
        reject_hidden(source)
        if (not isinstance(source, dict) or set(source) - set(ids)
                or {cid: source.get(cid, []) for cid in ids} != mapping):
            raise ValueError('Frozen normalized history differs from its supplied selection')
    elif descriptor['provided_file_sha256'] is not None or any(mapping.values()):
        raise ValueError('Unprovided historical source selection is not empty')
    return mapping


def _static_registration(root, identifier):
    root = Path(root).resolve()
    directory = _directory(root, identifier)
    spec = _read_sealed(root, directory / 'registration.json')
    fields = {'schema', 'scope', 'registered_at', 'protocol', 'protocol_source', 'protocol_sha256',
              'fingerprint', 'catalog', 'cases', 'arms', 'limits', 'runtime', 'history_input',
              'execution_authorized', 'gold_read', 'gold_scoring_authorized', 'quality_claim',
              'sha256', *ACCESS_FLAGS}
    if (set(spec) != fields or spec['schema'] != SCHEMA or spec['scope'] != SCOPE
            or spec['sha256'] != identifier
            or any(spec[key] is not False for key in (*ACCESS_FLAGS, 'execution_authorized', 'gold_read',
                                                     'gold_scoring_authorized', 'quality_claim'))):
        raise ValueError('Registered experiment scope or access contract changed')
    protocol = _validate_protocol(spec['protocol'])
    if (spec['arms'] != _arm_recipes(protocol) or spec['limits'] != protocol['limits']
            or not isinstance(spec['cases'], list) or len(spec['cases']) != SELECTED_CASES
            or len({row['case_id'] for row in spec['cases']}) != SELECTED_CASES
            or [{'case_id': row['case_id'], 'repository': row['repository']} for row in spec['cases']]
            != spec['catalog']['selected_membership'] or spec['catalog']['parent_cohort_sha256'] != PARENT):
        raise ValueError('Registered arms, sources or selection contract changed')
    _, raw = _read_file(root, directory / 'protocol.json', private=True)
    if sha256(raw).hexdigest() != spec['protocol_sha256'] or _parse(raw) != protocol:
        raise ValueError('Frozen protocol bytes changed')
    load_history_sources(root, spec, directory)
    return spec, directory


def load_registration(root, identifier):
    """Reject stale code/config/protocol/prices before a runner can admit work."""
    root = Path(root).resolve()
    spec, directory = _static_registration(root, identifier)
    if spec['fingerprint'] != _fingerprint(root):
        raise ValueError('Registered inference code or configuration changed')
    _, raw = _read_file(root, spec['protocol_source'])
    if sha256(raw).hexdigest() != spec['protocol_sha256'] or _parse(raw) != spec['protocol']:
        raise ValueError('Registered source protocol bytes changed')
    if _runtime_binding(root, spec['protocol']) != spec['runtime']:
        raise ValueError('Registered model, prices or budget configuration changed')
    return spec, directory


def preflight_component_ablation(root, identifier):
    """Replay every selected public context without environment or writes."""
    root = Path(root).resolve()
    spec, directory = load_registration(root, identifier)
    catalog = _catalog_binding(public_catalog(root))
    if catalog != spec['catalog']:
        raise ValueError('Registered accepted source membership or checkpoint changed')
    mapping = load_history_sources(root, spec, directory)
    cases = [_case_binding(root, row, mapping[row['case_id']]) for row in spec['cases']]
    if cases != spec['cases']:
        raise ValueError('Registered query, receipt, graph or historical proof changed')
    # Detect source edits while the longer replay was running, before spending.
    if _fingerprint(root) != spec['fingerprint']:
        raise ValueError('Inference source changed during preflight')
    return {'schema': 'component_ablation_preflight_v1', 'experiment_sha256': identifier,
            'source_replay_valid': True, 'selected_cases': len(cases), 'arms': list(ARMS),
            'history_covered_cases': sum(row['history_included_sources'] > 0 for row in cases),
            'history_contrast_informative': any(row['history_included_sources'] > 0 for row in cases),
            'execution_authorized': False, 'requires_exact_run_authorization': True,
            'provider_or_ledger_constructed': False, 'gold_read': False,
            'holdout_or_live_access': False, 'quality_claim': False}


def inspect_component_ablation(root, identifier):
    """Old proposed registrations remain inspectable, with stale state explicit."""
    spec, directory = _static_registration(root, identifier)
    try:
        preflight = preflight_component_ablation(root, identifier)
        valid, reason = True, None
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        # Fixed diagnostic classes only: do not serialize source/API exception text.
        preflight = None
        valid, reason = False, type(error).__name__
    return {'schema': 'component_ablation_inspection_v1', 'experiment_sha256': identifier,
            'registration': str(directory / 'registration.json'), 'scope': spec['scope'],
            'selected_cases': len(spec['cases']), 'arms': [row['arm_id'] for row in spec['arms']],
            'source_replay_valid': valid, 'preflight_error_type': reason, 'preflight': preflight,
            'execution_authorized': False, 'quality_claim': False, 'gold_read': False}


def verify_run_authorization(root, spec, authorization_path, *, requested_cases):
    """Match an explicitly recorded new decision; never create or infer one.

    This validates the included declaration, not the identity of its author.
    The runner must additionally verify the first-case pilot before broader work.
    """
    if (not isinstance(spec, dict) or spec.get('schema') != SCHEMA
            or spec.get('scope') != SCOPE or spec.get('sha256') !=
            digest({key: value for key, value in spec.items() if key != 'sha256'})):
        raise ValueError('Authorization needs an exact sealed registration')
    if type(requested_cases) is not int or not 1 <= requested_cases <= SELECTED_CASES:
        raise ValueError('Request 1–25 exact registered Main Dev cases')
    _, raw = _read_file(root, authorization_path, private=True)
    value = _parse(raw)
    fields = {'schema', 'scope', 'registration_sha256', 'code_fingerprint_sha256',
              'protocol_sha256', 'case_ids', 'provider', 'model', 'reasoning_effort',
              'max_output_tokens', 'max_provider_calls_per_arm', 'max_provider_calls_total',
              'authorized_case_count', 'phase', 'author_request', 'date',
              'execution_authorized', 'gold_scoring_authorized', *ACCESS_FLAGS}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Need a new exact experiment-specific author decision')
    if (value['schema'] != AUTHORIZATION_SCHEMA or value['scope'] != SCOPE
            or value['registration_sha256'] != spec['sha256']
            or value['code_fingerprint_sha256'] != digest(spec['fingerprint'])
            or value['protocol_sha256'] != spec['protocol_sha256']
            or value['case_ids'] != [row['case_id'] for row in spec['cases']]
            or value['provider'] != 'groq' or value['model'] != spec['runtime']['model']
            or value['reasoning_effort'] != spec['runtime']['reasoning_effort']
            or type(value['max_output_tokens']) is not int
            or value['max_output_tokens'] != spec['runtime']['max_output_tokens']
            or type(value['max_provider_calls_per_arm']) is not int
            or value['max_provider_calls_per_arm'] != spec['limits']['max_provider_calls_per_arm']
            or type(value['authorized_case_count']) is not int
            or value['authorized_case_count'] not in {1, SELECTED_CASES}
            or value['phase'] != ('first_case_pilot' if value['authorized_case_count'] == 1 else 'registered_matrix')
            or requested_cases > value['authorized_case_count']
            or type(value['max_provider_calls_total']) is not int
            or value['max_provider_calls_total'] != value['authorized_case_count'] * len(spec['arms']) * value['max_provider_calls_per_arm']
            or value['execution_authorized'] is not True
            or type(value['gold_scoring_authorized']) is not bool
            or any(value[flag] is not False for flag in ACCESS_FLAGS)
            or not isinstance(value['author_request'], str) or not value['author_request'].strip()
            or len(value['author_request']) > 8000
            or not isinstance(value['date'], str)):
        raise ValueError('Run authorization does not match the registered experiment and bounds')
    try:
        date.fromisoformat(value['date'])
    except (ValueError, TypeError):
        raise ValueError('Author decision needs a valid recorded date') from None
    return deepcopy(value)
