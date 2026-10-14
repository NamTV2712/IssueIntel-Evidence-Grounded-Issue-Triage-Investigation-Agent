"""Descriptive run differences; citations stay bound to their original context."""
from collections import defaultdict, deque
import hashlib
import re

from issueintel.runtime.io import digest
from issueintel.annotations.public_inputs import model_evidence
from .pipeline import public_case
from .verifier import verify_hypotheses


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _binding(pack):
    case = public_case(pack['case'])
    snapshot = pack['snapshot']
    if (not re.fullmatch(r'[a-f0-9]{64}', snapshot['snapshot_sha256'])
            or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', snapshot['commit_sha'])
            or snapshot['protocol'] not in {'benchmark_base', 'issue_time'}):
        raise ValueError('Invalid snapshot identity')
    binding = {'case_id': case['case_id'], 'repository': case['repository'],
               'query_sha256': _hash(case['problem_statement']),
               'issue_url': case.get('issue_url'),
               'commit_sha': snapshot['commit_sha'],
               'snapshot_sha256': snapshot['snapshot_sha256'],
               'protocol': snapshot['protocol'], 'cutoff': snapshot.get('cutoff'),
               'availability_sha256': digest(snapshot.get('availability', {})),
               'query_provenance_sha256': None}
    evidence = pack['evidence']
    model_evidence(evidence)
    ids, locators = set(), set()
    issues = []
    for record in evidence:
        key = (record['kind'], record['locator'])
        if record['id'] in ids or key in locators or _hash(record['text']) != record['sha256']:
            raise ValueError('Ambiguous or corrupted evidence')
        ids.add(record['id']); locators.add(key)
        if record['kind'] == 'code':
            if (record['revision'] != snapshot['commit_sha']
                    or record['provenance'].get('snapshot_sha256') != snapshot['snapshot_sha256']):
                raise ValueError('Code evidence belongs to another snapshot')
        elif record['kind'] == 'issue':
            issues.append(record)
            if record['text'] != case['problem_statement'] or record['revision'] is not None:
                raise ValueError('Issue evidence differs from query')
        else:
            raise ValueError('Unsupported evidence kind')
    if len(issues) != 1:
        raise ValueError('Exactly one issue record is required')
    binding['query_provenance_sha256'] = digest({
        'available_at': issues[0].get('available_at'), 'provenance': issues[0]['provenance'],
        'issue_created_at': case.get('issue_created_at')})
    if pack.get('annotation') is not None:
        verify_hypotheses(pack['annotation'], evidence)
        identifiers = [h['id'] for h in pack['annotation']['hypotheses']]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('Ambiguous hypothesis IDs')
    return binding


def _reference(record):
    return {key: record[key] for key in ('id', 'sha256', 'revision')}


def _hypotheses(pack, binding):
    by_id = {e['id']: e for e in pack['evidence']}
    rows = []
    for hypothesis in (pack.get('annotation') or {}).get('hypotheses', []):
        # Local h/e IDs and list positions are not semantic identities.
        identity = {key: value for key, value in hypothesis.items()
                    if key not in {'id', 'support_ids', 'counter_ids'}}
        for field in ('support_ids', 'counter_ids'):
            identity[field] = sorted(digest({key: by_id[eid][key]
                for key in ('kind', 'locator', 'sha256', 'revision')}) for eid in hypothesis[field])
        fingerprint = digest({'hypothesis': identity, 'binding': binding})
        rows.append({'id': hypothesis['id'], 'statement': hypothesis['statement'],
                     'fingerprint': fingerprint})
    return rows


def compare_packs(before, after):
    try:
        return _compare_packs(before, after)
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Invalid saved investigation structure') from None


def _compare_packs(before, after):
    """Compare completed public packs without a model, gold, or correctness score."""
    left, right = _binding(before), _binding(after)
    same_case = left['case_id'] == right['case_id']
    # Custom jobs have request-derived IDs: equal query+repository is the only
    # available identity when depth/provider/revision changed.
    local_match = (left['case_id'].startswith('local:') and right['case_id'].startswith('local:')
                   and left['query_sha256'] == right['query_sha256'])
    if (left['repository'] != right['repository'] or left['issue_url'] != right['issue_url']
            or not (same_case or local_match)):
        raise ValueError('Choose two investigations of the same issue and repository')
    # Case IDs for custom inputs are job identifiers, not source bindings.
    scoped_left = {k: v for k, v in left.items() if k != 'case_id'}
    scoped_right = {k: v for k, v in right.items() if k != 'case_id'}
    changed = [key for key in scoped_left if scoped_left[key] != scoped_right[key]]
    a = {(e['kind'], e['locator']): e for e in before['evidence']}
    b = {(e['kind'], e['locator']): e for e in after['evidence']}
    differences = []
    for kind, locator in sorted(a.keys() | b.keys()):
        old, new = a.get((kind, locator)), b.get((kind, locator))
        status = ('added' if old is None else 'removed' if new is None else
                  'content_changed' if old['sha256'] != new['sha256'] else
                  'rebinding_required' if changed else 'unchanged')
        differences.append({'kind': kind, 'locator': locator, 'status': status,
                            'before': _reference(old) if old else None,
                            'after': _reference(new) if new else None})
    old_h = _hypotheses(before, scoped_left)
    new_h = _hypotheses(after, scoped_right)
    available = defaultdict(deque)
    for row in old_h:
        available[row['fingerprint']].append(row)
    matched, added = [], []
    for row in new_h:
        queue = available[row['fingerprint']]
        if queue:
            previous = queue.popleft()
            matched.append({'before_id': previous['id'], 'after_id': row['id'],
                            'statement': row['statement']})
        else:
            added.append(row)
    removed = [row for row in old_h if row in available[row['fingerprint']]]
    return {'schema': 'investigation_comparison_v1', 'before': left, 'after': right,
            'same_context': not changed, 'changed_bindings': changed,
            'evidence': differences,
            'evidence_counts': {status: sum(e['status'] == status for e in differences)
                for status in ('unchanged', 'added', 'removed', 'content_changed', 'rebinding_required')},
            'hypotheses': {'unchanged': matched, 'added': added, 'removed': removed},
            'correctness': 'unverified', 'evaluation_performed': False,
            'limitations': ['Differences describe saved outputs, not quality improvement or a verified fix.',
                'Equal excerpts at different contexts require fresh citation binding.',
                'Changed line ranges appear as added/removed; no semantic line alignment is claimed.']}
