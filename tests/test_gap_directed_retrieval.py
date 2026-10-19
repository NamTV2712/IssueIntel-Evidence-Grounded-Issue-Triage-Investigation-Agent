"""Gap actions use synthetic pinned sources; no API or benchmark data."""
from copy import deepcopy
import hashlib
import subprocess

import pytest

from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from issueintel.retrieval.gap_directed import action_specs, expand_for_gaps, validate_search, POLICY
from issueintel.retrieval.search import retrieve_evidence


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def sources(tmp_path):
    def make(*, docs=True, long_caller=False, caller=True):
        path = tmp_path / ('long' if long_caller else 'docs' if docs else 'code')
        path.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-c', 'core.hooksPath=', '-C', str(path), *args]).decode().strip()
        git('init', '-q')
        files = {'parser.py': 'def parse_list(items):\n    return [item.strip() for item in items]\n',
                 'fallback.py': 'def recover_none(sentinel):\n    return [] if sentinel is None else sentinel\n',
                 'unrelated.py': 'def decode_bytes(payload):\n    return payload.decode()\n'}
        if caller:
            files['caller.py'] = ('from parser import parse_list\n' +
                ('def use(items): return parse_list(items)  # ' + 'x' * 1800 + '\n' if long_caller
                 else 'def use(items):\n    return parse_list(items)\n'))
        if docs:
            files.update({'README.md': 'parse_list expects a list of text items.\nMissing sentinel handling is optional.\n',
                          'CHANGELOG.md': 'parse_list history: recover_none sentinel guard changed previously.\n'})
        for name, text in files.items():
            (path / name).write_text(text, encoding='utf-8')
        git('add', '.')
        git('-c', 'user.name=Synthetic', '-c', 'user.email=test@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic gap fixture')
        snapshot = build_snapshot(path, 'HEAD'); graph = build_graph(snapshot)
        seed = next(row for row in retrieve_evidence('parse_list items', snapshot, graph, top_k=10, expand=0)
                    if row['locator'].startswith('parser.py:'))
        hypotheses = [{'id': f'h{index}', 'statement': 'parse_list assumes nonempty text items',
                       'support_ids': [seed['id']], 'counter_ids': [], 'counter_status': 'not_found',
                       'falsification_test': 'Inspect recover_none sentinel guard'} for index in range(3)]
        return snapshot, graph, [seed], hypotheses
    return make


def replay(trace, hypotheses, before, added, snapshot, graph, used, *, projected=False):
    snapshot_data = snapshot.to_dict()
    snapshot_data['files'] = [{key: value for key, value in file.items() if key != 'text'} for file in snapshot_data['files']]
    graph_data = graph.to_dict()
    if projected:
        paths = {row['locator'].rsplit(':', 1)[0] for row in before + added if row['kind'] == 'code'}
        graph_data['nodes'] = [node for node in graph_data['nodes'] if node['path'] in paths]
        ids = {node['id'] for node in graph_data['nodes']}
        graph_data['edges'] = [edge for edge in graph_data['edges'] if edge['source'] in ids and edge['target'] in ids]
    return validate_search(trace, hypotheses, [row['id'] for row in before], [row['id'] for row in added],
                           {row['id']: row for row in before + added},
                           {'snapshot_sha256': snapshot.snapshot_sha256, 'commit_sha': snapshot.commit_sha},
                           used, graph_data, snapshot_data)


def test_counter_caller_and_documentation_execute_distinct_scopes(sources):
    snapshot, graph, seed, hypotheses = sources()
    used = {sha('issue')}
    added, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    assert trace['policy'] == POLICY and trace['counter_evidence_confirmed'] is False
    assert [entry['purpose'] for entry in trace['queries']] == ['counter', 'caller', 'documentation']
    counter, caller, documentation = trace['queries']
    assert counter['query'] == 'counter\nInspect recover_none sentinel guard'
    assert caller['target_node_ids'] == ['symbol:parser.py:parse_list:1']
    by_id = {row['id']: row for row in added}
    assert any(by_id[item]['locator'].startswith('fallback.py:') for item in counter['added_ids'])
    assert len(caller['added_ids']) == 1
    assert by_id[caller['added_ids'][0]]['locator'].startswith('caller.py:')
    caller_proof = by_id[caller['added_ids'][0]]['provenance']
    assert caller_proof['caller_relation'] == 'exact_static_call'
    assert caller_proof['execution_observed'] is False
    assert all(by_id[item]['locator'].split(':')[0].endswith('.md') for item in documentation['added_ids'])
    assert len(added) <= 6 and len(trace['queries']) <= 3
    assert replay(trace, hypotheses, seed, added, snapshot, graph, used, projected=True) == 'new_candidates_added'
    assert len(used) == 4


def test_purpose_rotation_reaches_history_and_support_after_model_changes(sources):
    snapshot, graph, seed, hypotheses = sources()
    used = {sha('issue')}
    first, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    replay(trace, hypotheses, seed, first, snapshot, graph, used)
    changed = deepcopy(hypotheses)
    for row in changed:
        row['statement'] += ' changed model wording'
        row['falsification_test'] += ' changed condition'
    second, trace2 = expand_for_gaps(changed, seed + first, snapshot, graph, exclude_query_sha256s=used)
    assert [row['purpose'] for row in trace2['queries']] == ['history', 'support', 'counter']
    assert replay(trace2, changed, seed + first, second, snapshot, graph, used, projected=True) in {
        'no_new_evidence', 'new_candidates_added'}
    assert not {row['query_sha256'] for row in trace['queries']} & {row['query_sha256'] for row in trace2['queries']}


def test_absent_sources_record_scoped_unknowns_without_global_claims(sources):
    snapshot, graph, seed, hypotheses = sources(docs=False, caller=False)
    used = {sha('issue')}
    added, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    assert trace['queries'][1]['unavailable_reason'] == 'no_exact_callers_in_available_graph'
    assert trace['queries'][2]['unavailable_reason'] == 'no_documentation_in_snapshot'
    assert all(row['returned_ids'] == [] for row in trace['queries'][1:])
    replay(trace, hypotheses, seed, added, snapshot, graph, used, projected=True)
    added2, trace2 = expand_for_gaps(hypotheses, seed + added, snapshot, graph, exclude_query_sha256s=used)
    assert trace2['queries'][0]['purpose'] == 'history'
    assert trace2['queries'][0]['unavailable_reason'] == 'no_history_in_available_sources'
    replay(trace2, hypotheses, seed + added, added2, snapshot, graph, used, projected=True)


def context_record():
    text = 'Earlier issue mentions parse_list sentinel guard; this is current fetched content.'
    return {'id': 'ctx_example', 'kind': 'repository_context', 'locator': 'https://github.com/synthetic/repo/issues/7',
            'text': text, 'sha256': sha(text), 'revision': None, 'available_at': None,
            'provenance': {'source_record_sha256': 'a' * 64, 'receipt_sha256': 'b' * 64,
                           'source_key': 'issue:7', 'start_character': 0, 'end_character': len(text),
                           'historical_verified': False, 'causal_correctness': 'unverified'}}


def test_history_inspects_only_existing_receipt_bound_context(sources):
    snapshot, graph, seed, hypotheses = sources(docs=False)
    seed.append(context_record())
    used = {sha('issue'), sha('first'), sha('second'), sha('third')}
    added, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    history = trace['queries'][0]
    assert history['purpose'] == 'history' and history['status'] == 'executed'
    assert history['inspected_ids'] == ['ctx_example'] and history['returned_ids'] == []
    assert not any(row['kind'] == 'repository_context' for row in added)
    replay(trace, hypotheses, seed, added, snapshot, graph, used, projected=True)
    corrupted = deepcopy(seed); corrupted[-1]['provenance']['receipt_sha256'] = 'not a receipt'
    with pytest.raises(ValueError, match='receipt-bound'):
        expand_for_gaps(hypotheses, corrupted, snapshot, graph,
                        exclude_query_sha256s={sha('issue'), sha('first'), sha('second'), sha('third')})


@pytest.mark.parametrize('include_caller_path', [False, True])
def test_unrepresentable_long_caller_is_unknown_and_replays_with_projected_graph(sources, include_caller_path):
    snapshot, graph, seed, hypotheses = sources(long_caller=True)
    if include_caller_path:
        # Only a short import line is available; the one-line function cannot
        # fit the excerpt limit, while its exact static edge survives projection.
        caller_import = next(row for row in retrieve_evidence('parse_list', snapshot, graph, top_k=10, expand=0)
                             if row['locator'].startswith('caller.py:'))
        seed.append(caller_import)
    used = {sha('issue')}
    added, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    caller = next(row for row in trace['queries'] if row['purpose'] == 'caller')
    assert caller['status'] == 'unavailable' and caller['unavailable_reason'] == 'no_matching_snapshot_evidence'
    assert caller['returned_ids'] == caller['added_ids'] == []
    replay(trace, hypotheses, seed, added, snapshot, graph, used, projected=True)


def test_query_spec_tampering_is_rejected_without_consuming_history(sources):
    snapshot, graph, seed, hypotheses = sources()
    used = {sha('issue')}; added, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    for field, value in [('purpose', 'support'), ('reason', 'Author says this is true'),
                         ('target_node_ids', ['symbol:unrelated.py:decode_bytes:1']), ('query', 'changed query')]:
        changed = deepcopy(trace); changed['queries'][0][field] = value
        with pytest.raises(ValueError, match='identity|query|rationale|sources'):
            replay(changed, hypotheses, seed, added, snapshot, graph, used)
        assert used == {sha('issue')}


def test_caller_provenance_cannot_claim_an_unrelated_static_edge(sources):
    snapshot, graph, seed, hypotheses = sources()
    used = {sha('issue')}; added, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    changed = deepcopy(added)
    caller = next(row for row in changed if row['provenance']['gap_purpose'] == 'caller')
    caller['provenance']['caller_node_id'] = 'symbol:unrelated.py:decode_bytes:1'
    with pytest.raises(ValueError, match='incoming static call'):
        replay(trace, hypotheses, seed, changed, snapshot, graph, used)


def test_resealed_out_of_bounds_span_rejected_against_file_node(sources):
    snapshot, graph, seed, hypotheses = sources()
    used = {sha('issue')}; added, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    changed, changed_trace = deepcopy(added), deepcopy(trace)
    record = changed[0]; old_id = record['id']
    path, old_start, old_end = record['locator'].rsplit(':', 1)[0], *map(int, record['locator'].rsplit(':', 1)[1].split('-'))
    record['locator'] = f'{path}:{old_start + 1000}-{old_end + 1000}'
    record['id'] = 'e_' + sha(f"{snapshot.snapshot_sha256}:{record['locator']}:{record['sha256']}")[:20]
    changed_trace['added_ids'] = [record['id'] if item == old_id else item for item in changed_trace['added_ids']]
    for entry in changed_trace['queries']:
        for field in ('added_ids', 'returned_ids'):
            entry[field] = [record['id'] if item == old_id else item for item in entry[field]]
    with pytest.raises(ValueError, match='snapshot source binding'):
        replay(changed_trace, hypotheses, seed, changed, snapshot, graph, used)


def test_five_duplicate_hypotheses_do_not_expand_skip_trace_beyond_hash_history(sources):
    snapshot, graph, seed, hypotheses = sources()
    hypotheses += [dict(hypotheses[0], id=f'h{index}') for index in (3, 4)]
    used = {sha('issue')}
    first, trace = expand_for_gaps(hypotheses, seed, snapshot, graph, exclude_query_sha256s=used)
    replay(trace, hypotheses, seed, first, snapshot, graph, used)
    second, trace2 = expand_for_gaps(hypotheses, seed + first, snapshot, graph, exclude_query_sha256s=used)
    assert len(trace2['skipped_queries']) <= len(used) <= 10
    assert len({row['query_sha256'] for row in trace2['skipped_queries']}) == len(trace2['skipped_queries'])
    replay(trace2, hypotheses, seed + first, second, snapshot, graph, used)


def test_action_specs_are_pure_and_invalid_snapshot_binding_fails_before_search(sources):
    snapshot, graph, seed, hypotheses = sources()
    first = action_specs(hypotheses, seed, graph, exclude_query_sha256s={sha('issue')})
    assert first == action_specs(hypotheses, deepcopy(seed), graph.to_dict(), exclude_query_sha256s=[sha('issue')])
    corrupted = deepcopy(seed); corrupted[0]['revision'] = '0' * 40
    with pytest.raises(ValueError, match='snapshot'):
        expand_for_gaps(hypotheses, corrupted, snapshot, graph)
    with pytest.raises(ValueError, match='SHA256'):
        action_specs(hypotheses, seed, graph, exclude_query_sha256s=['not a hash'])


def test_caller_query_history_binds_changed_callee_even_with_identical_hypothesis_text(sources):
    snapshot, graph, seed, hypotheses = sources()
    fallback = next(row for row in retrieve_evidence('recover_none sentinel', snapshot, graph, top_k=10, expand=0)
                    if row['locator'].startswith('fallback.py:'))
    evidence = seed + [fallback]
    initial = action_specs(hypotheses, evidence, graph, exclude_query_sha256s={sha('issue')})
    changed = deepcopy(hypotheses)
    for row in changed:
        row['support_ids'] = [fallback['id']]
    following = action_specs(changed, evidence, graph, exclude_query_sha256s={sha('issue')})
    before = {row['purpose']: row for row in initial}
    after = {row['purpose']: row for row in following}
    assert before['caller']['target_node_ids'] != after['caller']['target_node_ids']
    assert before['caller']['query_sha256'] != after['caller']['query_sha256']
    assert before['counter']['query_sha256'] == after['counter']['query_sha256']
    assert before['documentation']['query_sha256'] == after['documentation']['query_sha256']
    used = {sha('issue'), before['caller']['query_sha256']}
    # Purpose rotation can select a different order; the new caller is still
    # eligible instead of being reported as the previously executed operation.
    specs = action_specs(changed, evidence, graph, exclude_query_sha256s=used)
    assert any(row['purpose'] == 'caller' and row['query_sha256'] == after['caller']['query_sha256'] for row in specs)
