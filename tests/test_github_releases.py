"""Synthetic HTTP only: no network, checkout, model or benchmark access."""
from copy import deepcopy

import pytest

from issueintel.integrations.github_index import GitHubReadError
from issueintel.integrations.github_releases import inspect_release, validate_release_check
from issueintel.runtime.io import digest


REPO = 'owner/project'
BASE = 'https://api.github.com/repos/' + REPO
TAG = 'v1.2.3'
REFERENCE = 'a' * 40
TARGET = 'b' * 40
STAMP = '2026-10-16T00:00:00Z'


def release_pages(*, target=TARGET, reference=REFERENCE, status='ahead', tag=TAG):
    """Reusable public metadata fixture for workbench/API tests."""
    merge = reference if status in {'ahead', 'identical'} else target if status == 'behind' else 'd' * 40
    ahead = 1 if status in {'ahead', 'diverged'} else 0
    behind = 1 if status in {'behind', 'diverged'} else 0
    return {
        BASE: {'id': 42, 'full_name': REPO, 'private': False, 'visibility': 'public'},
        BASE + '/releases/tags/' + tag: {'id': 12, 'tag_name': tag, 'name': 'Public release',
            'html_url': 'https://github.com/' + REPO + '/releases/tag/' + tag,
            'draft': False, 'prerelease': False, 'published_at': STAMP, 'target_commitish': 'moving-main'},
        BASE + '/git/ref/tags/' + tag: {'ref': 'refs/tags/' + tag, 'object': {'type': 'commit', 'sha': target}},
        BASE + '/git/commits/' + reference: {'sha': reference},
        BASE + '/git/commits/' + target: {'sha': target},
        BASE + '/compare/' + reference + '...' + target + '?per_page=1&page=1': {
            'status': status, 'ahead_by': ahead, 'behind_by': behind, 'total_commits': ahead,
            'base_commit': {'sha': reference}, 'merge_base_commit': {'sha': merge},
            'commits': [{'sha': 'e' * 40}], 'files': []},
    }


class ReleaseReader:
    """Strict URL fixture; mutation hooks simulate observed API changes."""
    def __init__(self, pages=None, *, next_url=None, hook=None):
        self.pages = release_pages() if pages is None else pages
        self.calls = []
        self.next_url = next_url
        self.hook = hook

    def get(self, url):
        self.calls.append(url)
        if self.hook:
            self.hook(self, url)
        value = deepcopy(self.pages[url])
        if isinstance(value, Exception):
            raise value
        return value, self.next_url, digest({'raw_fixture': value})


def reseal(value):
    value['sha256'] = digest({k: v for k, v in value.items() if k != 'sha256'})
    return value


def test_public_published_release_containment_is_bounded_and_replayable(monkeypatch):
    reader = ReleaseReader(next_url='https://api.github.com/repos/owner/project/compare/dont-follow?page=2')
    saved = []
    result = inspect_release('Owner/Project', TAG, REFERENCE, reader=reader, on_receipt=saved.append)
    assert result['repository'] == {'name': REPO, 'id': 42, 'url': 'https://github.com/' + REPO}
    assert result['containment']['status'] == 'contained'
    assert result['release']['publication_status'] == 'published'
    assert result['tag'] == {'ref': 'refs/tags/' + TAG, 'object_sha': TARGET, 'commit_sha': TARGET, 'peel_depth': 0}
    assert result['fix_status'] == result['historical_publication_binding'] == 'unverified'
    assert result['evaluation_performed'] is False and result['model_calls'] == 0
    assert len(reader.calls) == len(saved) == len(result['receipts']) == 9
    assert saved == result['receipts']
    assert all('dont-follow' not in call for call in reader.calls)
    assert 'target_commitish' not in result['release']
    assert result['receipts'][0]['response_sha256'] != result['receipts'][0]['raw_response_sha256']
    monkeypatch.setattr('issueintel.integrations.github_releases.GitHubReader', lambda *_a, **_k: pytest.fail('network constructed during replay'))
    assert validate_release_check(result) is result


@pytest.mark.parametrize('status,expected', [('ahead', 'contained'), ('behind', 'not_contained'), ('diverged', 'not_contained')])
def test_comparison_direction_is_reference_to_peeled_release_commit(status, expected):
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(release_pages(status=status)))
    assert result['containment']['status'] == expected
    validate_release_check(result)


def test_identical_commits_are_contained_and_requested_once():
    reader = ReleaseReader(release_pages(target=REFERENCE, status='identical'))
    result = inspect_release(REPO, TAG, REFERENCE, reader=reader)
    assert result['containment']['status'] == 'contained'
    assert reader.calls.count(BASE + '/git/commits/' + REFERENCE) == 1
    validate_release_check(result)


def test_nested_annotated_tags_are_peeled_by_exact_object_identity():
    pages = release_pages()
    pages[BASE + '/git/ref/tags/' + TAG]['object'] = {'type': 'tag', 'sha': 'c' * 40, 'url': 'https://evil.test/ignored'}
    pages[BASE + '/git/tags/' + 'c' * 40] = {'sha': 'c' * 40, 'object': {'type': 'tag', 'sha': 'd' * 40}}
    pages[BASE + '/git/tags/' + 'd' * 40] = {'sha': 'd' * 40, 'object': {'type': 'commit', 'sha': TARGET}}
    reader = ReleaseReader(pages)
    result = inspect_release(REPO, TAG, REFERENCE, reader=reader)
    assert result['tag']['object_sha'] == 'c' * 40
    assert result['tag']['commit_sha'] == TARGET and result['tag']['peel_depth'] == 2
    assert result['containment']['status'] == 'contained'
    assert all(url.startswith(BASE) for url in reader.calls)
    validate_release_check(result)


def test_maximum_five_annotated_tag_objects_stays_below_request_budget():
    pages = release_pages()
    objects = [str(i) * 40 for i in range(1, 6)]
    pages[BASE + '/git/ref/tags/' + TAG]['object'] = {'type': 'tag', 'sha': objects[0]}
    for i, sha in enumerate(objects):
        pages[BASE + '/git/tags/' + sha] = {'sha': sha, 'object':
            {'type': 'tag', 'sha': objects[i+1]} if i < 4 else {'type': 'commit', 'sha': TARGET}}
    reader = ReleaseReader(pages)
    result = inspect_release(REPO, TAG, REFERENCE, reader=reader)
    assert result['tag']['peel_depth'] == 5 and result['containment']['status'] == 'contained'
    assert len(reader.calls) == 14
    validate_release_check(result)


@pytest.mark.parametrize('missing,status,reason', [
    ('/releases/tags/' + TAG, 404, 'release_unavailable'),
    ('/git/ref/tags/' + TAG, 404, 'tag_ref_unavailable'),
    ('/git/commits/' + REFERENCE, 404, 'reference_commit_unavailable'),
    ('/git/commits/' + TARGET, 409, 'release_commit_unavailable'),
    ('/compare/' + REFERENCE + '...' + TARGET + '?per_page=1&page=1', 404, 'comparison_unavailable'),
    ('/compare/' + REFERENCE + '...' + TARGET + '?per_page=1&page=1', 409, 'comparison_unavailable'),
])
def test_unavailable_resources_remain_unknown(missing, status, reason):
    pages = release_pages()
    pages[BASE + missing] = GitHubReadError(status)
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))
    assert result['containment'] == {'status': 'unknown', 'reason': reason}
    if missing.startswith('/releases/'):
        assert result['release'] is None and not any('/compare/' in r['request_url'] for r in result['receipts'])
    receipt = next(r for r in result['receipts'] if r['status'] == status)
    assert receipt['response'] is None and receipt['raw_response_sha256'] is None
    assert receipt['raw_hash_kind'] == 'unavailable_http_error'
    validate_release_check(result)


@pytest.mark.parametrize('draft,published_at', [(True, STAMP), (False, None), (True, None)])
def test_draft_or_unpublished_release_never_implies_publication(draft, published_at):
    pages = release_pages()
    pages[BASE + '/releases/tags/' + TAG].update(draft=draft, published_at=published_at)
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))
    assert result['release']['publication_status'] == 'not_published'
    assert result['containment'] == {'status': 'unknown', 'reason': 'release_not_published'}
    assert not any('/compare/' in r['request_url'] for r in result['receipts'])
    validate_release_check(result)


def test_prerelease_is_public_but_flag_is_retained():
    pages = release_pages()
    pages[BASE + '/releases/tags/' + TAG]['prerelease'] = True
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))
    assert result['release']['publication_status'] == 'published' and result['release']['prerelease'] is True


@pytest.mark.parametrize('kind', ['tree', 'blob'])
def test_non_commit_tag_target_is_unknown(kind):
    pages = release_pages()
    pages[BASE + '/git/ref/tags/' + TAG]['object']['type'] = kind
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))
    assert result['tag']['commit_sha'] is None
    assert result['containment']['reason'] == 'tag_target_not_commit'
    validate_release_check(result)


def test_tag_cycle_or_depth_limit_never_claims_a_commit():
    pages = release_pages()
    pages[BASE + '/git/ref/tags/' + TAG]['object'] = {'type': 'tag', 'sha': 'c' * 40}
    pages[BASE + '/git/tags/' + 'c' * 40] = {'sha': 'c' * 40, 'object': {'type': 'tag', 'sha': 'c' * 40}}
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))
    assert result['containment']['reason'] == 'tag_cycle'
    validate_release_check(result)
    for i in range(1, 6):
        pages[BASE + '/git/tags/' + str(i) * 40] = {'sha': str(i) * 40, 'object': {'type': 'tag', 'sha': str(i+1) * 40}}
    pages[BASE + '/git/ref/tags/' + TAG]['object'] = {'type': 'tag', 'sha': '1' * 40}
    reader = ReleaseReader(pages)
    result = inspect_release(REPO, TAG, REFERENCE, reader=reader)
    assert result['containment']['reason'] == 'tag_depth_limit' and len(reader.calls) == 11
    assert not any('/git/tags/' + '6' * 40 in url for url in reader.calls)


@pytest.mark.parametrize('bad', ['', 'main..v1', '-v1', '/v1', 'v1/', 'v1.lock', 'v1//x', 'v1/./x', 'v1%2Fa', 'v1?x=1', 'v1#part', 'v1:tag', 'v1\\tag', 'v1\n', 'v'*201])
def test_invalid_tag_rejected_before_any_network(bad):
    reader = ReleaseReader()
    with pytest.raises(ValueError):
        inspect_release(REPO, bad, REFERENCE, reader=reader)
    assert reader.calls == []


@pytest.mark.parametrize('bad', ['main', 'a'*39, 'A'*40, 'a'*41, 'a'*40+'?x=1', None, True])
def test_reference_requires_full_lowercase_sha_before_network(bad):
    reader = ReleaseReader()
    with pytest.raises(ValueError):
        inspect_release(REPO, TAG, bad, reader=reader)
    assert reader.calls == []


def test_safe_slash_tag_and_sha256_object_ids():
    tag, reference, target = 'release/v1.2_3-rc1', 'a'*64, 'b'*64
    result = inspect_release(REPO, tag, reference, reader=ReleaseReader(release_pages(tag=tag, reference=reference, target=target)))
    assert result['tag']['commit_sha'] == target
    assert result['release']['url'].endswith('/tag/' + tag)
    validate_release_check(result)


@pytest.mark.parametrize('status', [401, 403, 429, 500, 503])
def test_auth_rate_and_network_failures_are_not_retried(status):
    pages = release_pages()
    pages[BASE + '/releases/tags/' + TAG] = GitHubReadError(status)
    reader = ReleaseReader(pages)
    saved = []
    with pytest.raises(GitHubReadError):
        inspect_release(REPO, TAG, REFERENCE, reader=reader, on_receipt=saved.append)
    assert len(reader.calls) == 2 and len(saved) == 1


@pytest.mark.parametrize('mutation', ['private', 'renamed', 'id'])
def test_repository_identity_before_and_after_rejects_transfer(mutation):
    def hook(reader, url):
        if url == BASE and reader.calls.count(BASE) == 2:
            if mutation == 'private':
                reader.pages[BASE]['private'] = True
            elif mutation == 'renamed':
                reader.pages[BASE]['full_name'] = 'other/project'
            else:
                reader.pages[BASE]['id'] = 43
    with pytest.raises(ValueError, match='identity|moved'):
        inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(hook=hook))


@pytest.mark.parametrize('mutation', ['release_id', 'release_tag', 'release_url', 'published', 'draft', 'prerelease', 'release_missing', 'tag_sha', 'tag_missing'])
def test_observed_release_or_tag_moves_reject_result(mutation):
    def hook(reader, url):
        if reader.calls.count(url) != 2:
            return
        if url == BASE + '/releases/tags/' + TAG:
            value = reader.pages[url]
            if mutation == 'release_id': value['id'] += 1
            elif mutation == 'release_tag': value['tag_name'] = 'other'
            elif mutation == 'release_url': value['html_url'] = 'https://evil.test/release'
            elif mutation == 'published': value['published_at'] = '2026-10-17T00:00:00Z'
            elif mutation == 'draft': value['draft'] = True
            elif mutation == 'prerelease': value['prerelease'] = True
            elif mutation == 'release_missing': reader.pages[url] = GitHubReadError(404)
        if url == BASE + '/git/ref/tags/' + TAG:
            if mutation == 'tag_sha': reader.pages[url]['object']['sha'] = 'c'*40
            elif mutation == 'tag_missing': reader.pages[url] = GitHubReadError(404)
    with pytest.raises(ValueError, match='moved|tag|URL'):
        inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(hook=hook))


@pytest.mark.parametrize('key,value', [
    ('status', 'unknown'), ('ahead_by', True), ('behind_by', -1), ('total_commits', 2),
    ('behind_by', 1), ('merge_base_commit', {'sha': TARGET}), ('base_commit', {'sha': TARGET}),
])
def test_inconsistent_compare_is_rejected(key, value):
    pages = release_pages()
    compare = next(v for k, v in pages.items() if '/compare/' in k)
    compare[key] = value
    with pytest.raises(ValueError, match='comparison'):
        inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))


def test_exact_commit_and_tag_object_identities_are_required():
    pages = release_pages()
    pages[BASE + '/git/commits/' + TARGET]['sha'] = REFERENCE
    with pytest.raises(ValueError, match='commit identity'):
        inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))
    pages = release_pages()
    pages[BASE + '/git/ref/tags/' + TAG]['object'] = {'type': 'tag', 'sha': 'c'*40}
    pages[BASE + '/git/tags/' + 'c'*40] = {'sha': 'd'*40, 'object': {'type': 'commit', 'sha': TARGET}}
    with pytest.raises(ValueError, match='object identity'):
        inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))


def test_durable_receipt_failure_or_cancellation_prevents_next_get():
    reader = ReleaseReader()
    def failed_write(_receipt):
        raise OSError('synthetic disk failure')
    with pytest.raises(OSError):
        inspect_release(REPO, TAG, REFERENCE, reader=reader, on_receipt=failed_write)
    assert reader.calls == [BASE]
    reader = ReleaseReader()
    saved = []
    def cancelled():
        if saved:
            raise InterruptedError('synthetic cancel')
    with pytest.raises(InterruptedError):
        inspect_release(REPO, TAG, REFERENCE, reader=reader, check_cancelled=cancelled, on_receipt=saved.append)
    assert reader.calls == [BASE] and len(saved) == 1


def test_callback_cannot_mutate_inspection_receipts():
    def mutate(receipt):
        receipt['response'].clear()
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(), on_receipt=mutate)
    validate_release_check(result)


@pytest.mark.parametrize('where', ['outer', 'receipt_hash', 'receipt_url', 'receipt_sequence', 'receipt_json_hash', 'receipt_body', 'status', 'tag', 'publication', 'observed', 'missing', 'extra'])
def test_repaired_outer_hash_does_not_hide_inconsistent_receipts(where):
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader())
    if where == 'outer': result['fix_status'] = 'fixed'
    elif where == 'receipt_hash': result['receipts'][0]['sha256'] = '0'*64
    elif where == 'receipt_url': result['receipts'][0]['request_url'] += '/other'
    elif where == 'receipt_sequence': result['receipts'][0]['sequence'] = True
    elif where == 'receipt_json_hash': result['receipts'][0]['response_sha256'] = '0'*64
    elif where == 'receipt_body': result['receipts'][0]['response']['id'] = 43
    elif where == 'status': result['containment']['status'] = 'not_contained'
    elif where == 'tag': result['tag']['commit_sha'] = REFERENCE
    elif where == 'publication': result['release']['published_at'] = '2026-01-01T00:00:00Z'
    elif where == 'observed': result['observed_at'] = STAMP
    elif where == 'missing': result['receipts'].pop()
    elif where == 'extra': result['receipts'].append(deepcopy(result['receipts'][-1]))
    reseal(result)
    with pytest.raises(ValueError):
        validate_release_check(result)


def test_resealed_nested_response_still_requires_before_after_identity_consistency():
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader())
    receipt = result['receipts'][0]
    receipt['response']['id'] = 43
    receipt['response_sha256'] = digest(receipt['response'])
    reseal(receipt); reseal(result)
    with pytest.raises(ValueError, match='moved'):
        validate_release_check(result)


def test_optional_workbench_source_is_bound_by_hash_and_shape():
    result = inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader())
    result['source'] = {'job_id': '1'*24, 'pack_sha256': '2'*64, 'commit_sha': '3'*40,
                        'snapshot_sha256': '4'*64, 'query_sha256': '5'*64}
    reseal(result)
    validate_release_check(result)
    for key, invalid in [('job_id', 'x'), ('pack_sha256', 'A'*64), ('commit_sha', 'main')]:
        bad = deepcopy(result); bad['source'][key] = invalid; reseal(bad)
        with pytest.raises(ValueError): validate_release_check(bad)
    result['source']['extra'] = True; reseal(result)
    with pytest.raises(ValueError, match='source binding'):
        validate_release_check(result)


def test_total_receipt_and_request_bounds_fail_closed(monkeypatch):
    monkeypatch.setattr('issueintel.integrations.github_releases.MAX_RECEIPT_BYTES', 50)
    reader = ReleaseReader()
    with pytest.raises(ValueError, match='20 MB'):
        inspect_release(REPO, TAG, REFERENCE, reader=reader)
    assert reader.calls == [BASE]
    monkeypatch.setattr('issueintel.integrations.github_releases.MAX_RECEIPT_BYTES', 20_000_000)
    monkeypatch.setattr('issueintel.integrations.github_releases.MAX_REQUESTS', 2)
    reader = ReleaseReader()
    with pytest.raises(ValueError, match='GET budget'):
        inspect_release(REPO, TAG, REFERENCE, reader=reader)
    assert len(reader.calls) == 2


def test_non_object_response_or_fake_raw_hash_rejected():
    pages = release_pages(); pages[BASE] = []
    with pytest.raises(ValueError, match='JSON object'):
        inspect_release(REPO, TAG, REFERENCE, reader=ReleaseReader(pages))
    class BadReader:
        def get(self, _url): return release_pages()[BASE], None, 'raw-not-hash'
    with pytest.raises(ValueError, match='identity'):
        inspect_release(REPO, TAG, REFERENCE, reader=BadReader())
