"""Real, synthetic Git histories; never API calls or benchmark data."""
from copy import deepcopy
from pathlib import Path
import stat
import subprocess

import pytest

from issueintel.investigation.reachability import inspect_reachability
from issueintel.runtime.io import digest


def git(repo, *arguments):
    return subprocess.check_output(
        ['git', '-c', 'core.hooksPath=', '-c', 'commit.gpgsign=false',
         '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
         '-C', str(repo), *arguments], stderr=subprocess.PIPE,
    ).decode('utf-8').strip()


def commit(repo, text):
    (repo / 'synthetic.txt').write_text(text, encoding='utf-8')
    git(repo, 'add', 'synthetic.txt')
    git(repo, 'commit', '-qm', text)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / 'repository'
    repo.mkdir()
    git(repo, 'init', '-q')
    first = commit(repo, 'first')
    second = commit(repo, 'second')
    third = commit(repo, 'third')
    git(repo, 'tag', 'v-first', first)
    git(repo, 'tag', 'v-second', second)
    git(repo, '-c', 'tag.gpgsign=false', 'tag', '-a', 'v-third', third, '-m', 'Synthetic annotated tag')
    return repo, first, second, third


def test_ancestry_reference_and_both_tag_types_are_hash_bound_without_worktree_changes(history):
    repo, first, second, third = history
    before = git(repo, 'status', '--porcelain')
    result = inspect_reachability(repo, first, third, second[:10])
    assert result['schema'] == 'product_git_reachability_v1'
    assert result['source_commit_sha'] == first and result['target_commit_sha'] == third
    assert result['source_to_target'] == {
        'status': 'source_ancestor', 'reason': 'source_reaches_target', 'ancestry_complete': True}
    assert result['reference'] == {'requested_revision': second[:10], 'commit_sha': second,
                                   'status': 'contained', 'reason': 'reference_ancestor'}
    assert result['tags']['containing'] == [
        {'name': 'v-second', 'commit_sha': second}, {'name': 'v-third', 'commit_sha': third}]
    assert result['tags']['total'] == result['tags']['scanned'] == 3
    assert result['tags']['unavailable'] == 0
    assert result['tags']['total_count_exact'] is True
    assert result['history'] == {'shallow': False, 'altered': False,
                                  'complete': True, 'reason': 'complete_local_history'}
    payload = deepcopy(result)
    expected = payload.pop('sha256')
    assert expected == digest(payload)
    assert git(repo, 'status', '--porcelain') == before
    assert result['fix_status'] == result['publication_status'] == 'unverified'
    assert result['model_calls'] == 0 and result['evaluation_performed'] is False


def test_no_reference_does_not_implicitly_claim_a_fix_and_same_commit_can_be_inspected(history):
    repo, first, _, third = history
    result = inspect_reachability(repo, first, first)
    assert result['source_to_target']['status'] == 'same_commit'
    assert result['reference'] is None
    assert result['tags']['containing'] == [] and result['tags']['scanned'] == 0
    assert result['tags']['total'] == 3 and result['tags']['reason'] == 'reference_not_requested'
    backward = inspect_reachability(repo, third, first, third)
    assert backward['source_to_target']['status'] == 'target_ancestor'
    assert backward['reference']['status'] == 'not_contained'


def test_diverged_branches_are_distinguished_from_ancestor_and_reference_can_be_target(history):
    repo, first, _, third = history
    git(repo, 'checkout', '-qb', 'synthetic-side', first)
    side = commit(repo, 'side')
    result = inspect_reachability(repo, side, third, 'v-third')
    assert result['source_to_target'] == {
        'status': 'diverged', 'reason': 'no_ancestor_path', 'ancestry_complete': True}
    assert result['reference']['commit_sha'] == third
    assert result['reference']['status'] == 'contained'
    assert result['reference']['reason'] == 'reference_equals_target'


def test_noncommit_and_missing_tag_objects_are_unavailable_without_hiding_valid_tags(history):
    repo, first, second, third = history
    blob = git(repo, 'rev-parse', third + ':synthetic.txt')
    git(repo, 'tag', 'blob-tag', blob)
    # A syntactically valid ref whose object is absent is still part of the
    # captured inventory, rather than silently treated as not containing.
    (repo / '.git' / 'refs' / 'tags' / 'missing-tag').write_text('f' * 40 + '\n', encoding='ascii')
    result = inspect_reachability(repo, first, third, second)
    assert result['tags']['total'] == result['tags']['scanned'] == 5
    assert result['tags']['unavailable'] == 2
    assert {row['name'] for row in result['tags']['containing']} == {'v-second', 'v-third'}


def test_tag_scan_cap_counts_the_full_local_inventory_and_uses_stable_name_order(history):
    repo, first, second, third = history
    requests = ''.join(f'create refs/tags/a-{index:03d} {third}\n' for index in range(203))
    subprocess.run(['git', '-C', str(repo), 'update-ref', '--stdin'], input=requests.encode(),
                   check=True, capture_output=True)
    result = inspect_reachability(repo, first, third, second)
    assert result['tags']['limit'] == 200 and result['tags']['scanned'] == 200
    assert result['tags']['total'] == 206 and result['tags']['total_count_exact'] is True
    assert result['tags']['truncated'] and result['tags']['reason'] == 'local_tag_scan_limit'
    assert [row['name'] for row in result['tags']['containing']] == [f'a-{index:03d}' for index in range(200)]


def test_shallow_cache_never_claims_absence_but_can_prove_a_visible_positive_path(history, tmp_path):
    repo, first, second, third = history
    fourth = commit(repo, 'fourth')
    shallow = tmp_path / 'shallow'
    subprocess.run(['git', '-c', 'core.hooksPath=', 'clone', '--bare', '--depth=2', '--no-tags',
                    repo.as_uri(), str(shallow)], check=True, capture_output=True)
    # Make the older commit available as a separate fetched shallow root. The
    # fourth->third is visible, but the intermediate second commit is missing.
    subprocess.run(['git', '-C', str(shallow), 'fetch', '--depth=1', repo.as_uri(), first],
                   check=True, capture_output=True)
    unknown = inspect_reachability(shallow, first, fourth, first)
    assert unknown['source_to_target']['status'] == 'unknown'
    assert unknown['source_to_target']['ancestry_complete'] is False
    assert unknown['reference']['status'] == 'unknown'
    assert unknown['reference']['reason'] == 'shallow_history'
    # Incremental explicit fetches can leave all required objects present while
    # the repository still declares shallow boundaries. A positive is allowed
    # only if Git can actually establish the raw parent path.
    subprocess.run(['git', '-C', str(shallow), 'fetch', '--depth=1', repo.as_uri(), second],
                   check=True, capture_output=True)
    positive = inspect_reachability(shallow, third, fourth, third)
    assert positive['source_to_target']['status'] == 'source_ancestor'
    assert positive['source_to_target']['ancestry_complete'] is False
    assert positive['reference']['status'] == 'contained'
    assert positive['history']['shallow'] and not positive['history']['complete']


@pytest.mark.parametrize('alteration', ['replace', 'graft'])
def test_replacement_and_graft_histories_fail_closed(history, alteration):
    repo, first, second, third = history
    if alteration == 'replace':
        git(repo, 'replace', second, first)
    else:
        info = repo / '.git' / 'info'
        info.mkdir(exist_ok=True)
        (info / 'grafts').write_text(third + '\n', encoding='ascii')
    result = inspect_reachability(repo, first, third, second)
    assert result['history']['altered'] and not result['history']['complete']
    assert result['source_to_target']['status'] == 'unknown'
    assert result['source_to_target']['reason'] == 'altered_history'
    assert result['reference']['status'] == 'unknown'
    assert result['tags']['unavailable'] == 2
    # Exact commit identity remains a fact without any ancestry traversal.
    assert result['tags']['containing'] == [{'name': 'v-second', 'commit_sha': second}]


def test_missing_parent_commit_preserves_unknown(history):
    repo, first, second, third = history
    missing = repo / '.git' / 'objects' / second[:2] / second[2:]
    missing.chmod(stat.S_IWRITE)
    missing.unlink()
    result = inspect_reachability(repo, first, third, first)
    assert result['source_to_target']['status'] == 'unknown'
    assert result['reference']['status'] == 'unknown'
    assert result['history']['reason'] == 'history_incomplete'


@pytest.mark.parametrize('source,target,reference', [
    ('HEAD', None, None), ('a' * 39, None, None), ('--version', None, None),
    (None, 'HEAD', None), (None, None, '--upload-pack=bad'),
    (None, None, '../../main'), (None, None, 'HEAD\n'), (None, None, 'HEAD:synthetic.txt'),
    (None, None, 'no-such-reference'),
])
def test_unpinned_or_unsafe_revisions_are_rejected(history, source, target, reference):
    repo, first, _, third = history
    with pytest.raises(ValueError):
        inspect_reachability(repo, source or first, target or third, reference)


def test_cancellation_propagates_before_any_git_subprocess(history, monkeypatch):
    repo, first, _, third = history
    class Cancelled(Exception):
        pass
    def cancelled():
        raise Cancelled()
    monkeypatch.setattr('issueintel.investigation.reachability.subprocess.Popen',
                        lambda *a, **k: pytest.fail('Cancelled inspection spawned Git'))
    with pytest.raises(Cancelled):
        inspect_reachability(repo, first, third, check_cancelled=cancelled)


def test_output_limit_fails_closed_without_partial_tag_counts(history, monkeypatch):
    repo, first, _, third = history
    monkeypatch.setattr('issueintel.investigation.reachability._OUTPUT_LIMIT', 10)
    with pytest.raises(ValueError, match='bounds'):
        inspect_reachability(repo, first, third)


def test_time_budget_does_not_turn_unknown_ancestry_into_absence(history, monkeypatch):
    import issueintel.investigation.reachability as module
    repo, first, _, third = history
    original = module._LocalGit.call
    def timeout_on_traversal(self, *arguments):
        if arguments[0] == 'merge-base':
            raise module._Unavailable('inspection_timeout')
        return original(self, *arguments)
    monkeypatch.setattr(module._LocalGit, 'call', timeout_on_traversal)
    result = inspect_reachability(repo, first, third, first)
    assert result['source_to_target']['status'] == 'unknown'
    assert result['source_to_target']['reason'] == 'inspection_timeout'
    assert result['reference']['status'] == 'unknown'
    assert result['tags']['unavailable'] == 2
    assert result['tags']['containing'] == [{'name': 'v-first', 'commit_sha': first}]


def test_git_environment_cannot_retarget_the_selected_repository(history, tmp_path, monkeypatch):
    repo, first, _, third = history
    other = tmp_path / 'other'
    other.mkdir()
    git(other, 'init', '-q')
    commit(other, 'different')
    monkeypatch.setenv('GIT_DIR', str(other / '.git'))
    result = inspect_reachability(repo, first, third)
    assert result['source_commit_sha'] == first
    assert result['source_to_target']['status'] == 'source_ancestor'


def test_symbolic_and_oversized_tag_names_remain_counted_but_are_not_admitted(history):
    repo, first, second, third = history
    git(repo, 'symbolic-ref', 'refs/tags/symbolic-tag', git(repo, 'symbolic-ref', 'HEAD'))
    git(repo, 'pack-refs', '--all')
    # Packed refs can have a logical name longer than a filesystem component;
    # no giant ref-label can escape into the containing-tag UI.
    with (repo / '.git' / 'packed-refs').open('a', encoding='ascii', newline='\n') as handle:
        # Each component remains valid on Windows; the logical name exceeds
        # the product display bound without relying on an invalid filename.
        handle.write(third + ' refs/tags/zz-' + ('a' * 100 + '/') * 11 + 'tag\n')
    result = inspect_reachability(repo, first, third, second)
    assert result['tags']['total'] == result['tags']['scanned'] == 5
    assert result['tags']['unavailable'] == 2
    assert [row['name'] for row in result['tags']['containing']] == ['v-second', 'v-third']


def test_tag_containment_is_bound_to_captured_oid_even_if_ref_moves(history, monkeypatch):
    import issueintel.investigation.reachability as module
    repo, first, second, third = history
    original = module._ref_inventory
    def capture_then_move(local_git):
        captured = original(local_git)
        git(repo, 'tag', '-f', 'v-third', first)
        return captured
    monkeypatch.setattr(module, '_ref_inventory', capture_then_move)
    result = inspect_reachability(repo, first, third, second)
    assert {'name': 'v-third', 'commit_sha': third} in result['tags']['containing']
    assert git(repo, 'rev-parse', 'v-third') == first


def test_concurrent_shallow_boundary_cannot_hide_existing_parent_path(history, monkeypatch):
    import issueintel.investigation.reachability as module
    repo, first, second, third = history
    original = module._LocalGit.call
    def mutate_before_traversal(self, *arguments):
        if arguments[0] == 'merge-base':
            (repo / '.git' / 'shallow').write_text(third + '\n', encoding='ascii')
        return original(self, *arguments)
    monkeypatch.setattr(module._LocalGit, 'call', mutate_before_traversal)
    result = inspect_reachability(repo, first, third, second)
    assert result['source_to_target']['status'] == 'source_ancestor'
    assert result['reference']['status'] == 'contained'


def test_inherited_custom_grafts_do_not_certify_absence(history, tmp_path, monkeypatch):
    repo, first, second, third = history
    grafts = tmp_path / 'synthetic-grafts'
    grafts.write_text(third + '\n', encoding='ascii')
    monkeypatch.setenv('GIT_GRAFT_FILE', str(grafts))
    result = inspect_reachability(repo, first, third, second)
    assert result['history']['altered'] is True
    assert result['reference']['status'] == 'unknown'


def test_disconnected_full_histories_have_no_ancestor_path(history):
    repo, first, _, third = history
    tree = git(repo, 'rev-parse', third + '^{tree}')
    other = git(repo, 'commit-tree', tree, '-m', 'Synthetic independent root')
    result = inspect_reachability(repo, first, other, third)
    assert result['source_to_target']['status'] == 'diverged'
    assert result['source_to_target']['ancestry_complete'] is True
    assert result['reference']['status'] == 'not_contained'
