"""Inspect one public GitHub release using bounded, replayable GET receipts.

This is an observation of the current API, not a historical publication proof or
proof that a referenced commit fixes an issue. No repository code is acquired.
Tag inputs deliberately support only ASCII letters, digits, ``_./-`` (200
characters maximum) and the safe-revision restrictions used by acquisition.
URLs returned by GitHub and compare pagination links are never followed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
import re
from urllib.parse import urlsplit

from issueintel.repository.acquire import _validate_revision, parse_github_reference
from issueintel.runtime.io import canonical_json, digest
from .github_index import GitHubReader, GitHubReadError


MAX_REQUESTS = 16
MAX_RECEIPT_BYTES = 20_000_000
MAX_RESPONSE_BYTES = 10_000_000
MAX_TAG_DEPTH = 5
SCHEMA = 'github_release_check_v1'
LIMITATIONS = [
    'Current GitHub REST observations are not proof of tag contents at publication time.',
    'Repository, release and tag are checked before and after; this is not an atomic snapshot and transient moves may be unobserved.',
    'Containment describes commit ancestry only; it does not establish that a commit fixes the issue or that tests passed.',
    'Only the explicitly selected tag is inspected; no release enumeration, compare pagination or Git history acquisition is performed.',
    'Receipt JSON has a replayable canonical hash; recorded transport-byte hashes cannot be recomputed from parsed JSON and do not authenticate the server.',
    'Missing or inaccessible resources remain unknown; absence does not prove that a release never existed.',
    'No model, target code, tests, benchmark split or evaluation is executed.',
]


def _time(value):
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError('Release observation requires a timezone-aware timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid release observation timestamp') from None
    if parsed.utcoffset() is None:
        raise ValueError('Release observation requires a timezone-aware timestamp')
    return value


def _sha(value, *, content=False):
    expression = r'[0-9a-f]{64}' if content else r'(?:[0-9a-f]{40}|[0-9a-f]{64})'
    if not isinstance(value, str) or re.fullmatch(expression, value) is None:
        raise ValueError('Release inspection requires a full lowercase object/hash identity')
    return value


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Release inspection requires a positive record identity')
    return value


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid or oversized release metadata')
    return value


def _inputs(repository, tag, reference_commit):
    reference = parse_github_reference(repository)
    if reference.issue_number is not None:
        raise ValueError('Release inspection requires a repository, not an issue URL')
    if not isinstance(tag, str) or not 1 <= len(tag) <= 200:
        raise ValueError('Release tag must contain 1–200 safe characters')
    _validate_revision(tag)
    return reference.repository.lower(), tag, _sha(reference_commit)


def _url_matches(value, repository, suffix):
    if not isinstance(value, str):
        raise ValueError('Release metadata requires its canonical GitHub URL')
    parsed = urlsplit(value)
    prefix = '/' + repository
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
            or parsed.path[:len(prefix)].lower() != prefix or parsed.path[len(prefix):] != suffix):
        raise ValueError('Release metadata URL escaped the selected repository or tag')


def _repository(value, repository):
    if (not isinstance(value, dict) or not isinstance(value.get('full_name'), str)
            or value['full_name'].lower() != repository or value.get('private') is not False
            or value.get('visibility', 'public') != 'public'):
        raise ValueError('Release repository identity changed or is not public')
    if 'html_url' in value:
        _url_matches(value['html_url'], repository, '')
    return {'name': repository, 'id': _positive(value.get('id')), 'url': 'https://github.com/' + repository}


def _release(response, repository, tag):
    status, value = response
    if status != 200:
        return None
    if not isinstance(value, dict) or value.get('tag_name') != tag:
        raise ValueError('Release does not match the requested exact tag')
    _url_matches(value.get('html_url'), repository, '/releases/tag/' + tag)
    if type(value.get('draft')) is not bool or type(value.get('prerelease')) is not bool:
        raise ValueError('Release publication flags must be explicit booleans')
    published_at = value.get('published_at')
    if published_at is not None:
        _time(published_at)
    name = value.get('name')
    if name is not None:
        _text(name, 1000)
    return {'publication_status': 'published' if not value['draft'] and published_at else 'not_published',
            'id': _positive(value.get('id')), 'tag': tag, 'name': name,
            'url': 'https://github.com/' + repository + '/releases/tag/' + tag,
            'published_at': published_at, 'prerelease': value['prerelease'], 'draft': value['draft']}


def _ref(response, tag):
    status, value = response
    if status != 200:
        return None
    if not isinstance(value, dict) or value.get('ref') != 'refs/tags/' + tag:
        raise ValueError('GitHub ref does not match the requested exact tag')
    obj = value.get('object')
    if not isinstance(obj, dict) or obj.get('type') not in {'commit', 'tag', 'tree', 'blob'}:
        raise ValueError('Invalid GitHub tag object type')
    return {'ref': value['ref'], 'object_sha': _sha(obj.get('sha')), 'object_type': obj['type']}


def _compare(value, reference, target):
    if not isinstance(value, dict):
        raise ValueError('GitHub comparison must be an object')
    base, merge = value.get('base_commit'), value.get('merge_base_commit')
    if not isinstance(base, dict) or _sha(base.get('sha')) != reference or not isinstance(merge, dict):
        raise ValueError('GitHub comparison is not bound to the reference commit')
    merge_sha = _sha(merge.get('sha'))
    ahead, behind, total = (value.get(key) for key in ('ahead_by', 'behind_by', 'total_commits'))
    if any(type(n) is not int or n < 0 for n in (ahead, behind, total)) or total != ahead:
        raise ValueError('GitHub comparison counts are inconsistent')
    status = value.get('status')
    if status == 'identical':
        valid = ahead == behind == 0 and reference == target == merge_sha
    elif status == 'ahead':
        valid = ahead > 0 and behind == 0 and reference != target and merge_sha == reference
    elif status == 'behind':
        valid = ahead == 0 and behind > 0 and reference != target and merge_sha == target
    elif status == 'diverged':
        valid = ahead > 0 and behind > 0 and reference != target and merge_sha not in {reference, target}
    else:
        valid = False
    if not valid:
        raise ValueError('GitHub comparison status, ancestry and counts are inconsistent')
    return {'status': 'contained' if status in {'identical', 'ahead'} else 'not_contained',
            'reason': 'reference_is_ancestor_or_identical' if status in {'identical', 'ahead'} else 'reference_not_ancestor'}


class _Recorder:
    def __init__(self, reader, check_cancelled, on_receipt):
        self.reader = reader
        self.check_cancelled = check_cancelled
        self.on_receipt = on_receipt
        self.receipts = []
        self.size = 0

    def get(self, url, accepted=()):
        if len(self.receipts) >= MAX_REQUESTS:
            raise ValueError('Release inspection exceeded its GET budget')
        if self.check_cancelled:
            self.check_cancelled()
        try:
            value, _next_url, raw_sha = self.reader.get(url)
            status = 200
            _sha(raw_sha, content=True)
            # Pagination may describe a very large comparison. Only scalar
            # ancestry facts are used; no returned URL is followed.
            if not isinstance(value, dict):
                raise ValueError('GitHub release response must be a JSON object')
        except GitHubReadError as error:
            if error.status not in accepted:
                raise
            status, value, raw_sha = error.status, None, None
        serialized = canonical_json(value).encode('utf-8')
        if len(serialized) > MAX_RESPONSE_BYTES:
            raise ValueError('Release response exceeds its 10 MB JSON bound')
        receipt = {'schema': 'github_release_response_v1', 'sequence': len(self.receipts) + 1,
                   'request_url': url, 'observed_at': datetime.now(timezone.utc).isoformat(),
                   'status': status, 'response': deepcopy(value), 'response_sha256': digest(value),
                   'raw_response_sha256': raw_sha,
                   'raw_hash_kind': 'transport_response_bytes' if status == 200 else 'unavailable_http_error'}
        receipt['sha256'] = digest(receipt)
        self.size += len(canonical_json(receipt).encode('utf-8'))
        if self.size + len(self.receipts) + 2 > MAX_RECEIPT_BYTES:
            raise ValueError('Release receipts exceed their total 20 MB JSON bound')
        # A failed durable write prevents the next GET; callbacks receive an
        # independent object and cannot alter the in-memory proof.
        if self.on_receipt:
            self.on_receipt(deepcopy(receipt))
        self.receipts.append(receipt)
        return status, value


def _inspect(repository, tag, reference, exchange):
    base = 'https://api.github.com/repos/' + repository
    identity = _repository(exchange.get(base)[1], repository)
    release_response = exchange.get(base + '/releases/tags/' + tag, (404, 409))
    release = _release(release_response, repository, tag)
    ref_response = exchange.get(base + '/git/ref/tags/' + tag, (404, 409))
    ref = _ref(ref_response, tag)
    result_tag = None
    reason = 'release_unavailable' if release is None else 'release_not_published'
    containment = {'status': 'unknown', 'reason': reason}
    if ref is not None:
        current, kind, depth, visited = ref['object_sha'], ref['object_type'], 0, set()
        reason = None
        while kind == 'tag':
            if current in visited:
                reason = 'tag_cycle'
                break
            if depth >= MAX_TAG_DEPTH:
                reason = 'tag_depth_limit'
                break
            visited.add(current)
            status, value = exchange.get(base + '/git/tags/' + current, (404, 409))
            if status != 200:
                reason = 'tag_object_unavailable'
                break
            if not isinstance(value, dict) or _sha(value.get('sha')) != current:
                raise ValueError('Annotated tag object identity mismatch')
            obj = value.get('object')
            if not isinstance(obj, dict) or obj.get('type') not in {'commit', 'tag', 'tree', 'blob'}:
                raise ValueError('Invalid annotated tag target')
            current, kind = _sha(obj.get('sha')), obj['type']
            depth += 1
        commit = current if kind == 'commit' and reason is None else None
        if reason is None and commit is None:
            reason = 'tag_target_not_commit'
        result_tag = {'ref': ref['ref'], 'object_sha': ref['object_sha'], 'commit_sha': commit, 'peel_depth': depth}
        if release is not None and release['publication_status'] == 'published':
            containment = {'status': 'unknown', 'reason': reason or 'commit_unavailable'}
            if commit is not None:
                available = True
                for sha in dict.fromkeys((reference, commit)):
                    status, value = exchange.get(base + '/git/commits/' + sha, (404, 409))
                    if status != 200:
                        available = False
                        containment = {'status': 'unknown', 'reason': 'reference_commit_unavailable' if sha == reference else 'release_commit_unavailable'}
                        break
                    if not isinstance(value, dict) or _sha(value.get('sha')) != sha:
                        raise ValueError('Git commit identity mismatch')
                if available:
                    status, value = exchange.get(base + '/compare/' + reference + '...' + commit + '?per_page=1&page=1', (404, 409))
                    containment = (_compare(value, reference, commit) if status == 200 else
                                   {'status': 'unknown', 'reason': 'comparison_unavailable'})
    elif release is not None and release['publication_status'] == 'published':
        containment = {'status': 'unknown', 'reason': 'tag_ref_unavailable'}
    after_ref = exchange.get(base + '/git/ref/tags/' + tag, (404, 409))
    after_release = exchange.get(base + '/releases/tags/' + tag, (404, 409))
    after_identity = _repository(exchange.get(base)[1], repository)
    if (after_identity != identity or after_ref[0] != ref_response[0]
            or _ref(after_ref, tag) != ref or after_release[0] != release_response[0]
            or _release(after_release, repository, tag) != release):
        raise ValueError('Repository, release or tag moved during inspection; run a new explicit check')
    result = {'schema': SCHEMA, 'repository': identity, 'requested': {'tag': tag, 'reference_commit': reference},
              'release': release, 'tag': result_tag, 'containment': containment,
              'observed_at': exchange.receipts[-1]['observed_at'], 'receipts': deepcopy(exchange.receipts),
              'fix_status': 'unverified', 'historical_publication_binding': 'unverified',
              'evaluation_performed': False, 'model_calls': 0, 'limitations': list(LIMITATIONS)}
    result['sha256'] = digest(result)
    return result


def inspect_release(repository, tag, reference_commit, *, reader=None, check_cancelled=None, on_receipt=None):
    """Inspect exactly one tag, persisting each receipt before another GET."""
    repository, tag, reference = _inputs(repository, tag, reference_commit)
    reader = reader or GitHubReader(repository, token=os.environ.get('GITHUB_TOKEN'))
    return _inspect(repository, tag, reference, _Recorder(reader, check_cancelled, on_receipt))


class _Replay:
    def __init__(self, receipts):
        if not isinstance(receipts, list) or not 1 <= len(receipts) <= MAX_REQUESTS:
            raise ValueError('Invalid release receipt count')
        if len(canonical_json(receipts).encode('utf-8')) > MAX_RECEIPT_BYTES:
            raise ValueError('Release receipts exceed their total 20 MB JSON bound')
        self.original = receipts
        self.receipts = []

    def get(self, url, accepted=()):
        if len(self.receipts) >= len(self.original):
            raise ValueError('Release replay is missing an expected receipt')
        receipt = self.original[len(self.receipts)]
        keys = {'schema', 'sequence', 'request_url', 'observed_at', 'status', 'response', 'response_sha256',
                'raw_response_sha256', 'raw_hash_kind', 'sha256'}
        if not isinstance(receipt, dict) or set(receipt) != keys:
            raise ValueError('Invalid release receipt shape')
        if (receipt['schema'] != 'github_release_response_v1' or type(receipt['sequence']) is not int
                or receipt['sequence'] != len(self.receipts) + 1 or receipt['request_url'] != url
                or type(receipt['status']) is not int or receipt['status'] not in (200, *accepted)
                or receipt['sha256'] != digest({k: v for k, v in receipt.items() if k != 'sha256'})
                or receipt['response_sha256'] != digest(receipt['response'])):
            raise ValueError('Release receipt hash, request or sequence mismatch')
        _time(receipt['observed_at'])
        if self.receipts and datetime.fromisoformat(receipt['observed_at'].replace('Z', '+00:00')) < datetime.fromisoformat(self.receipts[-1]['observed_at'].replace('Z', '+00:00')):
            raise ValueError('Release receipt observations are out of order')
        if receipt['status'] == 200:
            _sha(receipt['raw_response_sha256'], content=True)
            if receipt['raw_hash_kind'] != 'transport_response_bytes' or not isinstance(receipt['response'], dict):
                raise ValueError('Invalid successful release receipt')
        elif (receipt['raw_response_sha256'] is not None or receipt['response'] is not None
              or receipt['raw_hash_kind'] != 'unavailable_http_error'):
            raise ValueError('An HTTP error cannot provide successful response proof')
        if len(canonical_json(receipt['response']).encode('utf-8')) > MAX_RESPONSE_BYTES:
            raise ValueError('Release response exceeds its 10 MB JSON bound')
        self.receipts.append(deepcopy(receipt))
        return receipt['status'], receipt['response']


def validate_release_check(result):
    """Replay saved facts without network; a repaired outer hash is insufficient."""
    if not isinstance(result, dict) or result.get('schema') != SCHEMA:
        raise ValueError('Unsupported release check schema')
    if result.get('sha256') != digest({k: v for k, v in result.items() if k != 'sha256'}):
        raise ValueError('Release check hash mismatch')
    try:
        repository, tag, reference = _inputs(result['repository']['name'], result['requested']['tag'], result['requested']['reference_commit'])
        replay = _Replay(result['receipts'])
        expected = _inspect(repository, tag, reference, replay)
        if len(replay.receipts) != len(replay.original):
            raise ValueError('Release replay has unexpected extra receipts')
        if 'source' in result:
            source = result['source']
            if not isinstance(source, dict) or set(source) != {'job_id', 'pack_sha256', 'commit_sha', 'snapshot_sha256', 'query_sha256'}:
                raise ValueError('Invalid release source binding')
            if not isinstance(source['job_id'], str) or re.fullmatch('[0-9a-f]{24}', source['job_id']) is None:
                raise ValueError('Invalid release source job identity')
            _sha(source['commit_sha'])
            for key in ('pack_sha256', 'snapshot_sha256', 'query_sha256'):
                _sha(source[key], content=True)
            expected['source'] = deepcopy(source)
            expected['sha256'] = digest({k: v for k, v in expected.items() if k != 'sha256'})
        if canonical_json(result) != canonical_json(expected):
            raise ValueError('Release check derived facts differ from its saved receipts')
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Invalid release check shape') from None
    return result
