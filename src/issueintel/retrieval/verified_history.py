"""Bounded prior opening texts with replayed independent availability proofs.

No acquisition is performed. Receipt authenticity remains the trusted caller
boundary used by strict DEV. This supports initial GitHub issue text only;
comments, PRs, later edits, aliases and unknown historical bodies stay unavailable.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from issueintel.annotations.public_inputs import reject_hidden
from issueintel.runtime.io import digest


MAX_SOURCES = 20
MAX_SOURCE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_EXCERPT = 1200
MAX_INCLUDED = 4
SCHEMA = 'verified_prior_issue_openings_v1'


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Historical source timestamps require text')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Historical source timestamps require timezone')
    return result.astimezone(timezone.utc)


def _reason(receipt, case, boundary, target_urls):
    """Reuse both generic source binding and the stronger strict opening gate."""
    from issueintel.benchmarks.temporal import _issue_receipt
    from issueintel.benchmarks.strict_dev import opening_versions

    payload = receipt.get('payload')
    if not isinstance(payload, dict) or type(payload.get('number')) is not int or payload['number'] <= 0:
        return 'unsupported_source_identity', None
    number = payload['number']
    repository = case['repository']
    source_url = f'https://api.github.com/repos/{repository}/issues/{number}'
    if receipt.get('source_url') != source_url or payload.get('url') != source_url or 'pull_request' in payload:
        return 'source_repository_or_issue_identity_mismatch', None
    target_numbers = {str(value) for value in case.get('issue_numbers', [])}
    target_url = case.get('issue_url')
    html_url = f'https://github.com/{repository}/issues/{number}'
    if (str(number) in target_numbers or target_url == html_url
            or source_url in target_urls or html_url in target_urls):
        return 'target_issue_is_not_prior_history', None
    proof = receipt.get('body_provenance')
    if not isinstance(proof, dict) or proof.get('kind') != 'issue_creation_event':
        return 'independent_opening_event_missing', None
    event = proof.get('payload')
    if not isinstance(event, dict) or event.get('type') != 'IssuesEvent':
        return 'independent_opening_event_missing', None
    try:
        parsed = _issue_receipt(receipt, repository, str(number))
        if not parsed['identity_valid']:
            return 'source_identity_or_payload_hash_invalid', None
        if not parsed['created_before_fetch']:
            return 'source_fetch_time_unverified', None
        if not parsed['original_text_supported']:
            return 'independent_opening_binding_invalid', None
        if not opening_versions({'issue_receipts': [receipt]}):
            return 'opening_version_ambiguous', None
        created, emitted, fetched = _time(parsed['created_at']), _time(event.get('created_at')), _time(receipt.get('fetched_at'))
        if not created <= emitted <= fetched:
            return 'opening_event_time_unverified', None
        if not created < boundary or not emitted < boundary:
            return 'source_not_strictly_before_cutoff', None
        if not parsed['rendered_issue_text'].strip():
            return 'empty_original_opening_text', None
        return None, parsed
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return 'malformed_or_unsupported_opening_proof', None


def verified_history_evidence(sources, case, *, enabled, target_source_urls=()):
    """Return eligible bounded excerpts and complete selected-source diagnostics.

    Disabled arms still replay the same frozen source list for coverage metadata,
    but include no historical text in the investigator's evidence.
    """
    if type(enabled) is not bool or not isinstance(sources, (list, tuple)) or len(sources) > MAX_SOURCES:
        raise ValueError('Historical source selection exceeds the bounded explicit list')
    if (not isinstance(target_source_urls, (list, tuple)) or len(target_source_urls) > 100
            or any(not isinstance(url, str) or len(url) > 2048 for url in target_source_urls)):
        raise ValueError('Historical exclusion URLs need bounded target query receipt references')
    if not isinstance(case.get('repository'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', case['repository']):
        raise ValueError('Historical source selection needs a canonical repository')
    boundary = _time(case.get('issue_created_at'))
    reject_hidden(sources)
    try:
        raw = [json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8') for source in sources]
    except (TypeError, ValueError, UnicodeError):
        raise ValueError('Historical source receipts must be finite JSON') from None
    if any(len(value) > MAX_SOURCE_BYTES for value in raw) or sum(map(len, raw)) > MAX_TOTAL_BYTES:
        raise ValueError('Historical source receipt bytes exceed bounds')
    if any(not isinstance(source, dict) for source in sources):
        raise ValueError('Historical source receipts must be objects')
    sources = deepcopy(sources)
    checksums = [digest(source) for source in sources]
    if len(set(checksums)) != len(checksums):
        raise ValueError('Historical source selection repeats a receipt')
    evidence, rows, identities = [], [], set()
    for receipt, checksum in zip(sources, checksums):
        reason, parsed = _reason(receipt, case, boundary, set(target_source_urls))
        identity = receipt.get('source_url')
        if reason is None and identity in identities:
            raise ValueError('Historical source selection repeats an issue identity')
        if reason is None:
            identities.add(identity)
        included = bool(enabled and reason is None and len(evidence) < MAX_INCLUDED)
        row = {'receipt_sha256': checksum, 'eligible': reason is None, 'reason': reason,
               'included': included, 'evidence_id': None,
               'selection_reason': ('included' if included else 'unavailable' if reason is not None
                                    else 'history_disabled' if not enabled else 'included_source_limit')}
        if row['included']:
            text = parsed['rendered_issue_text'][:MAX_EXCERPT]
            sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
            provenance = {'kind': 'verified_original_query', 'edit_history': 'opening_event_matched',
                          'repository': case['repository'], 'source_receipt_sha256': checksum,
                          'source_url': identity, 'event_source_url': receipt['body_provenance']['source_url'],
                          'retrieval': SCHEMA, 'historical_verified': True,
                          'availability_basis': 'independent_opening_event',
                          'event_created_at': receipt['body_provenance']['payload']['created_at'],
                          'full_text_sha256': parsed['content_sha256'], 'start_character': 0,
                          'end_character': len(text), 'truncated': len(text) != len(parsed['rendered_issue_text']),
                          'causal_correctness': 'unverified'}
            identifier = 'hist_' + digest([checksum, sha, len(text)])[:20]
            evidence.append({'id': identifier, 'kind': 'historical_issue',
                             'locator': f'https://github.com/{case["repository"]}/issues/{receipt["payload"]["number"]}',
                             'text': text, 'sha256': sha, 'revision': None,
                             'available_at': receipt['body_provenance']['payload']['created_at'],
                             'provenance': provenance})
            row['evidence_id'] = identifier
        rows.append(row)
    audit = {'schema': SCHEMA, 'selection_sha256': digest(sources), 'enabled': enabled,
             'target_source_urls_sha256': digest(list(target_source_urls)),
             'cutoff': case['issue_created_at'], 'strict_before_cutoff': True,
             'selected_sources': len(rows), 'eligible_sources': sum(row['eligible'] for row in rows),
             'included_sources': len(evidence), 'sources': rows,
             'max_included_sources': MAX_INCLUDED, 'max_excerpt_characters': MAX_EXCERPT,
             'selection_order': 'explicit_frozen_candidate_order_first_eligible',
             'source_authenticity': 'trusted_official_source_acquisition_boundary',
             'coverage': 'explicit_selected_candidates_only_not_exhaustive',
             'causal_correctness': 'unverified'}
    audit['sha256'] = digest(audit)
    return evidence, audit
