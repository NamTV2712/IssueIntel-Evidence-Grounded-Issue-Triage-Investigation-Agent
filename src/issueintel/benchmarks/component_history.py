"""Prepare strict prior opening candidates from accepted original DEV proofs.

This is a bounded local source scan, not acquisition, evaluation, annotation or
proof that repository history is complete. Raw receipts remain private. Each
candidate must independently pass the native prior-opening gate for its target.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import stat

from issueintel.annotations.public_inputs import reject_hidden
from issueintel.investigation.policy_agent import context_binding
from issueintel.retrieval.verified_history import verified_history_evidence, MAX_SOURCE_BYTES
from issueintel.runtime.io import digest
from . import component_ablation as experiment
from .dev_context import development_catalog, accepted_context, PARENT
from .strict_dev import _checked, VERSION, NAMESPACE


SCHEMA = 'component_ablation_cached_history_v1'
MAX_MEMBERS = 400
MAX_RECEIPTS = 2000
MAX_RECORD_BYTES = 1024 * 1024
MAX_PROOF_BYTES = 16 * 1024 * 1024
MAX_SCAN_BYTES = 256 * 1024 * 1024
_HASH = re.compile('[a-f0-9]{64}')
_URL = re.compile(r'https://api\.github\.com/repos/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/[1-9][0-9]*')


def _catalog(catalog):
    """Use only the original accepted members; never open another split."""
    if (not isinstance(catalog, dict) or catalog.get('original_total') != 400
            or catalog.get('evaluation_authorized') is not False
            or not isinstance(catalog.get('_members'), dict)
            or not 25 <= len(catalog['_members']) <= MAX_MEMBERS
            or not isinstance(catalog.get('_candidates'), dict)
            or len(catalog['_candidates']) != 400
            or catalog.get('accepted') != len(catalog['_members'])
            or not isinstance(catalog.get('cases'), list)
            or {row.get('case_id') for row in catalog['cases']} != set(catalog['_members'])
            or len(catalog['cases']) != len(catalog['_members'])):
        raise ValueError('Cached history requires the exact accepted original 400 Main Dev catalog')
    for cid, candidate in catalog['_candidates'].items():
        case = candidate.get('case') if isinstance(candidate, dict) else None
        if (not isinstance(case, dict) or case.get('case_id') != cid
                or case.get('split') != 'main_dev' or not experiment._CASE.fullmatch(cid)):
            raise ValueError('Cached history cannot include another split or new cases')
    for cid, member in catalog['_members'].items():
        if (cid not in catalog['_candidates'] or not isinstance(member, dict)
                or member.get('case_id') != cid or member.get('origin') not in {'frozen', 'recovery'}):
            raise ValueError('Accepted history source membership changed')
    public = {key: value for key, value in catalog.items() if not key.startswith('_')}
    selected = experiment._selected_rows(public)
    identity = {'parent_cohort_sha256': PARENT, 'checkpoint_sha256': catalog.get('checkpoint_sha256'),
        'accepted_members': [{'case_id': cid, 'member': catalog['_members'][cid],
            'source_case_sha256': digest(catalog['_candidates'][cid]['case'])}
            for cid in sorted(catalog['_members'])],
        'selected_membership': selected}
    return selected, identity


def _reference_path(root, reference):
    if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}
            or not isinstance(reference['path'], str) or len(reference['path']) > 1000
            or not isinstance(reference['sha256'], str) or not _HASH.fullmatch(reference['sha256'])):
        raise ValueError('Cached history needs a bounded immutable source reference')
    path = experiment._safe_path(root, reference['path'])
    if not path.is_relative_to(root / 'data'):
        raise ValueError('Cached history source references must stay under local data')
    return path


def _reference(root, reference, *, maximum):
    path = _reference_path(root, reference)
    _, raw = experiment._read_file(root, path, maximum=maximum)
    if sha256(raw).hexdigest() != reference['sha256'] or _checked(root, reference) != path:
        raise ValueError('Cached history source hash changed')
    value = experiment._parse(raw)
    if not isinstance(value, dict):
        raise ValueError('Cached history source document must be a JSON object')
    return value, raw


def _record(case, member, record, catalog):
    if (record.get('case_id') != case['case_id'] or record.get('case_sha256') != digest(case)
            or record.get('accepted') is not True or type(record.get('llm_calls')) is not int
            or record['llm_calls'] != 0 or record.get('gold_used') is not False
            or record.get('human_rationale_used') is not False
            or record.get('evaluation_authorized') is not False):
        raise ValueError('Cached history donor is not an isolated accepted Main Dev record')
    if member['origin'] == 'recovery':
        if (record.get('parent_cohort_sha256') != PARENT or record.get('receipt_sha256') !=
                digest({key: value for key, value in record.items() if key != 'receipt_sha256'})):
            raise ValueError('Recovered history donor identity changed')
    elif (record.get('version') != VERSION or record.get('namespace') != NAMESPACE
            or record.get('pool_sha256') != catalog.get('_parent', {}).get('pool_sha256')):
        raise ValueError('Frozen history donor identity changed')


def _scan(root, catalog):
    candidates, documents, checked, unavailable = {}, [], [], Counter()
    consumed = occurrences = duplicates = 0
    partial = False
    members = sorted(catalog['_members'])
    for index, cid in enumerate(members):
        member = catalog['_members'][cid]
        record_ref = member.get('receipt')
        record_size = _reference_path(root, record_ref).stat()
        if not stat.S_ISREG(record_size.st_mode) or record_size.st_size > MAX_RECORD_BYTES:
            raise ValueError('Cached history donor record exceeds its regular-file boundary')
        if consumed + record_size.st_size > MAX_SCAN_BYTES:
            partial = True; unavailable['scan_byte_limit_members_uninspected'] += len(members) - index
            break
        record, raw = _reference(root, record_ref, maximum=MAX_RECORD_BYTES)
        consumed += len(raw)
        checked.append((deepcopy(record_ref), len(raw)))
        _record(catalog['_candidates'][cid]['case'], member, record, catalog)
        proof_ref = record.get('proof')
        # Check metadata before reading or hashing a potentially oversized proof.
        proof_path = _reference_path(root, proof_ref)
        proof_info = proof_path.stat()
        if not stat.S_ISREG(proof_info.st_mode):
            raise ValueError('Cached history proof must be a regular source file')
        size = proof_info.st_size
        if size > MAX_PROOF_BYTES:
            partial = True; unavailable['proof_document_byte_limit'] += 1
            documents.append({'case_id': cid, 'record': record_ref, 'proof': proof_ref,
                              'status': 'unavailable_proof_document_byte_limit', 'receipt_sha256s': []})
            continue
        if consumed + size > MAX_SCAN_BYTES:
            partial = True; unavailable['scan_byte_limit_members_uninspected'] += len(members) - index
            break
        proof, raw = _reference(root, proof_ref, maximum=MAX_PROOF_BYTES)
        consumed += len(raw); checked.append((deepcopy(proof_ref), len(raw)))
        lists = [proof.get(key, []) for key in ('issue_receipts', 'current_issue_receipts')]
        if any(not isinstance(value, list) for value in lists):
            raise ValueError('Cached history proof receipt lists changed')
        receipts = [row for rows in lists for row in rows]
        row = {'case_id': cid, 'record': record_ref, 'proof': proof_ref,
               'status': 'inspected', 'receipt_occurrences': len(receipts), 'receipt_sha256s': []}
        documents.append(row)
        if not receipts:
            unavailable['no_supported_issue_receipt_list'] += 1
        for receipt in receipts:
            if occurrences == MAX_RECEIPTS:
                partial = True; unavailable['receipt_limit'] += 1
                row['status'] = 'partial_receipt_limit'
                break
            occurrences += 1
            if not isinstance(receipt, dict):
                raise ValueError('Cached history receipt must be an object')
            reject_hidden(receipt)
            encoded = experiment._bytes(receipt)
            checksum = digest(receipt)
            row['receipt_sha256s'].append(checksum)
            if len(encoded) > MAX_SOURCE_BYTES:
                unavailable['receipt_byte_limit'] += 1
                partial = True
                continue
            if checksum in candidates:
                duplicates += 1
                continue
            match = _URL.fullmatch(receipt.get('source_url', '')) if isinstance(receipt.get('source_url'), str) else None
            if match is None:
                unavailable['unsupported_source_url'] += 1
                continue
            candidates[checksum] = {'receipt': deepcopy(receipt), 'repository': match[1], 'sha256': checksum}
        if occurrences == MAX_RECEIPTS:
            if index < len(members) - 1:
                partial = True; unavailable['receipt_limit_members_uninspected'] += len(members) - index - 1
            break
    return candidates, documents, checked, {
        'accepted_members': len(members), 'donor_documents_inspected': len(documents),
        'receipt_occurrences_inspected': occurrences, 'duplicate_receipts': duplicates,
        'unique_github_issue_candidates': len(candidates), 'document_bytes_read': consumed,
        'partial': partial, 'unavailable_reasons': dict(sorted(unavailable.items()))}


def _target(root, row, candidates):
    case, snapshot, query = accepted_context(root, row['case_id'])
    proof = snapshot.availability.get('receipt')
    if (case.get('case_id') != row['case_id'] or case.get('split') != 'main_dev'
            or case.get('repository') != row['repository'] or snapshot.protocol != 'issue_time'
            or snapshot.cutoff != case.get('issue_created_at') or query.get('verified') is not True
            or query.get('schema') != 'main_dev_original_query_receipt_v1'
            or query.get('case_id') != row['case_id'] or query.get('created_at') != snapshot.cutoff
            or query.get('text_sha256') != sha256(case['problem_statement'].encode()).hexdigest()
            or not isinstance(proof, dict) or proof.get('verified') is not True
            or not isinstance(query.get('strict_dev_proof_sha256'), str)
            or not _HASH.fullmatch(query['strict_dev_proof_sha256'])
            or query['strict_dev_proof_sha256'] != proof.get('strict_dev_proof_sha256')
            or not isinstance(query.get('source_urls'), list) or not query['source_urls']):
        raise ValueError('History target must replay its exact accepted original strict Main Dev context')
    binding = context_binding(case, snapshot)
    eligible, reasons, inspected = [], Counter(), 0
    for item in candidates.values():
        if item['repository'] != case['repository']:
            continue
        inspected += 1
        _, audit = verified_history_evidence([item['receipt']], case, enabled=True,
                                             target_source_urls=query['source_urls'])
        result = audit['sources'][0]
        if not result['eligible']:
            reasons[result['reason']] += 1
        else:
            event_time = item['receipt']['body_provenance']['payload']['created_at']
            instant = datetime.fromisoformat(event_time.replace('Z', '+00:00')).astimezone(timezone.utc)
            eligible.append({**item, 'instant': instant})
    # Stable ordering: newest qualifying event, then exact canonical URL and hash.
    eligible.sort(key=lambda item: (item['receipt']['source_url'], item['sha256']))
    eligible.sort(key=lambda item: item['instant'], reverse=True)
    selected, identities = [], set()
    for item in eligible:
        identity = item['receipt']['source_url']
        if identity in identities:
            reasons['duplicate_eligible_issue_identity'] += 1
            continue
        identities.add(identity)
        if len(selected) == 20:
            reasons['candidate_count_limit'] += 1
            continue
        proposed = selected + [item['receipt']]
        if len(experiment._bytes(proposed)) > experiment.MAX_JSON_BYTES:
            reasons['case_receipt_byte_limit'] += 1
            continue
        selected = proposed
    _, audit = verified_history_evidence(selected, case, enabled=True,
                                         target_source_urls=query['source_urls'])
    return selected, {'case_id': row['case_id'], 'repository': row['repository'],
        'source': binding, 'query_receipt_sha256': digest(query), 'cutoff': case['issue_created_at'],
        'same_repository_receipts_inspected': inspected, 'eligible_receipts': len(eligible),
        'eligible_issue_identities': len(identities), 'selected_candidates': len(selected),
        'included_sources': audit['included_sources'], 'selected_receipt_sha256s': [digest(item) for item in selected],
        'selected_source_audit_sha256': audit['sha256'], 'selected_bytes': len(experiment._bytes(selected)),
        'rejected_or_unselected_reasons': dict(sorted(reasons.items())),
        'coverage': 'selected_cached_accepted_main_dev_opening_proofs_only_not_complete_history'}


def prepare_component_history(root, output_path):
    """Write one immutable private candidate map and audit, without any calls."""
    root = Path(root).resolve()
    path = experiment._safe_path(root, output_path, private=True, missing=True)
    if path.suffix != '.json':
        raise ValueError('Cached history output needs a private JSON file')
    audit_path = path.with_name(path.stem + '.audit.json')
    experiment._safe_path(root, audit_path, private=True, missing=True)
    if path.exists() or audit_path.exists():
        raise FileExistsError('Cached history output and audit must both be new')
    catalog = development_catalog(root)
    selected, catalog_identity = _catalog(catalog)
    candidates, documents, checked, scan = _scan(root, catalog)
    mapping, targets = {}, []
    for row in selected:
        sources, audit = _target(root, row, candidates)
        mapping[row['case_id']] = sources; targets.append(audit)
    raw = experiment._bytes(mapping)
    if len(raw) > experiment.MAX_HISTORY_BYTES:
        raise ValueError('Cached history output exceeds fifty MiB')
    # Do not publish if source files or membership moved during the longer scan.
    if _catalog(development_catalog(root))[1] != catalog_identity:
        raise ValueError('Accepted cached-history membership changed during preparation')
    for reference, size in checked:
        _, current = experiment._read_file(root, reference['path'], maximum=max(size, 1))
        if len(current) != size or sha256(current).hexdigest() != reference['sha256']:
            raise ValueError('Cached history source changed before publication')
    report = {'schema': SCHEMA, 'scope': experiment.SCOPE, 'parent_cohort_sha256': PARENT,
        'catalog_sha256': digest(catalog_identity), 'selected_case_ids': list(mapping),
        'history_file': path.relative_to(root).as_posix(), 'history_sha256': sha256(raw).hexdigest(),
        'history_bytes': len(raw), 'source_documents': documents, 'scan': scan, 'targets': targets,
        'selection': 'eligible_cached_openings_newest_event_then_canonical_url_then_receipt_hash',
        'limits': {'accepted_members': MAX_MEMBERS, 'receipt_occurrences': MAX_RECEIPTS,
                   'source_bytes': MAX_SOURCE_BYTES, 'per_case_bytes': experiment.MAX_JSON_BYTES,
                   'output_bytes': experiment.MAX_HISTORY_BYTES, 'candidates_per_case': 20,
                   'included_per_case': 4, 'record_bytes': MAX_RECORD_BYTES,
                   'proof_bytes': MAX_PROOF_BYTES, 'scan_bytes': MAX_SCAN_BYTES},
        'new_cases_imported': 0, 'network_calls': 0, 'provider_calls': 0, 'gold_read': False,
        'holdout_or_live_access': False, 'execution_authorized': False, 'quality_claim': False,
        'source_authenticity': 'trusted_official_source_acquisition_boundary',
        'absence_or_exhaustive_search_claim': False}
    report['sha256'] = digest(report)
    artifacts = experiment._safe_path(root, root / 'artifacts', missing=True)
    artifacts.mkdir(exist_ok=True)
    experiment._safe_path(root, artifacts)
    if path.parent != artifacts:
        experiment._mkdir(root, path.parent)
    experiment._create_file(root, path, raw)
    experiment._create_file(root, audit_path, experiment._bytes(report))
    covered = sum(row['included_sources'] > 0 for row in targets)
    return {'history_path': str(path), 'audit_path': str(audit_path),
            'history_sha256': report['history_sha256'], 'audit_sha256': report['sha256'],
            'selected_cases': len(targets), 'history_covered_cases': covered,
            'history_contrast_informative': covered > 0, 'partial_source_scan': scan['partial'],
            'provider_calls': 0, 'gold_read': False, 'quality_claim': False}
