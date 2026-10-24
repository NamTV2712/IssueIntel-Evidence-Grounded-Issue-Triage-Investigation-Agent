"""Synthetic shared-pipeline component controls; no dataset, network or provider."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json

import pytest

from issueintel.investigation.pipeline import investigate
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import RepositorySnapshot, SourceFile
from issueintel.retrieval.ablation_policy import build_policy, validate_policy
from issueintel.retrieval.focused import expand_for_hypotheses, POLICY, COUNTER_POLICY
from issueintel.retrieval.verified_history import verified_history_evidence
from issueintel.runtime.io import digest


@pytest.fixture
def source(tmp_path):
    commit = 'a' * 40
    cutoff = '2020-01-03T00:00:00Z'
    proof = 'b' * 64
    case = {'case_id': 'main_dev:synthetic-1', 'instance_id': 'synthetic-1', 'split': 'main_dev',
            'repository': 'synthetic/repo', 'revision': commit, 'issue_numbers': [99],
            'issue_url': 'https://github.com/synthetic/repo/issues/99',
            'problem_statement': 'needle parser fails', 'issue_created_at': cutoff}
    receipt = {'commit_sha': commit, 'verified': True, 'source_id': 'https://example.invalid/push',
               'available_at': '2020-01-01T00:00:00Z', 'strict_dev_proof_sha256': proof}
    files = tuple(SourceFile(path, text, sha256(text.encode()).hexdigest()) for path, text in (
        ('parser.py', 'from helper import evaluate\ndef needle(value):\n    return evaluate(value)\n'),
        ('helper.py', 'def evaluate(value):\n    return value\n'),
        ('fallback.py', 'def recover_none(sentinel):\n    return [] if sentinel is None else sentinel\n')))
    payload = {'repository': str(tmp_path), 'commit_sha': commit, 'tree_sha': 'c' * 40,
               'files': files, 'protocol': 'issue_time', 'cutoff': cutoff,
               'availability': {'status': 'caller_verified_receipt', 'historical_certified': True,
                   'source_id': receipt['source_id'], 'available_at': receipt['available_at'],
                   'receipt_sha256': digest(receipt), 'receipt': receipt},
               'skipped': (), 'committer_time': '2019-01-01T00:00:00Z', 'snapshot_sha256': ''}
    snapshot = RepositorySnapshot(**payload)
    unsealed = asdict(snapshot); unsealed.pop('snapshot_sha256')
    payload['snapshot_sha256'] = digest(unsealed)
    snapshot = RepositorySnapshot(**payload); snapshot.verify()
    query = {'schema': 'main_dev_original_query_receipt_v1', 'verified': True,
             'case_id': case['case_id'], 'created_at': cutoff,
             'text_sha256': sha256(case['problem_statement'].encode()).hexdigest(),
             'strict_dev_proof_sha256': proof, 'source_urls': [case['issue_url']]}
    return case, snapshot, build_graph(snapshot), query, tmp_path


def run(source, policy, **kwargs):
    case, snapshot, graph, receipt, root = source
    options = {'protocol': 'issue_time', 'snapshot': snapshot, 'graph': graph,
               'query_receipt': receipt, 'top_k': policy['top_k'],
               'independent_critic': policy['independent_critic'],
               'refinement_rounds': int(policy['counter_search']),
               'max_output_tokens': policy['max_output_tokens'], 'experiment_policy': policy}
    options.update(kwargs)
    return investigate(case, root, **options)


def history(number=1, *, created='2020-01-01T00:00:00Z', emitted=None, body='A prior observed symptom'):
    payload = {'number': number, 'url': f'https://api.github.com/repos/synthetic/repo/issues/{number}',
               'created_at': created, 'updated_at': created, 'title': 'Prior issue', 'body': body}
    text = payload['title'] + '\n' + payload['body'] + '\n'
    return {'source_url': payload['url'], 'fetched_at': '2026-10-12T00:00:00Z', 'payload': payload,
            'body_provenance': {'kind': 'issue_creation_event', 'available_at': created,
                'source_url': f'https://data.gharchive.org/{(emitted or created)[:10]}-{int((emitted or created)[11:13])}.json.gz',
                'content_sha256': sha256(text.encode()).hexdigest(), 'history_complete': True,
                'payload': {'type': 'IssuesEvent', 'public': True, 'repo': {'name': 'synthetic/repo'},
                            'created_at': emitted or created, 'payload': {'action': 'opened', 'issue': deepcopy(payload)}}}}


class Completion:
    def __init__(self):
        self.payloads = []

    def __call__(self, messages, max_output_tokens):
        payload = json.loads(messages[1]['content']); self.payloads.append(payload)
        if 'case' in payload:
            identifier = next(row['id'] for row in payload['evidence'] if row['kind'] == 'code')
            return {'hypotheses': [{'id': f'h{i}', 'statement': f'Needle mechanism {i}',
                'support_ids': [identifier], 'counter_ids': [], 'counter_status': 'not_found',
                'falsification_test': f'Inspect recover_none sentinel guard {i}'} for i in range(3)],
                'abstention': {'label': 'uncertain', 'reason': 'No execution proof'}}
        return {'reviews': [{'hypothesis_id': h['id'], 'assessment': 'uncertain',
                            'reason': 'Not executed', 'evidence_ids': h['support_ids']}
                           for h in payload['hypotheses']]}


def policy(**kwargs):
    return build_policy('e' * 64, 'synthetic_arm', **kwargs)


def test_native_graph_changes_evidence_under_same_total_cap(source):
    lexical = run(source, policy())
    graph = run(source, policy(graph=True))
    left = [row for row in lexical['evidence'] if row['kind'] == 'code']
    right = [row for row in graph['evidence'] if row['kind'] == 'code']
    assert {row['locator'].split(':')[0] for row in left} == {'parser.py'}
    assert {row['locator'].split(':')[0] for row in right} == {'parser.py', 'helper.py'}
    assert any(row['provenance']['retrieval'] == 'graph_neighbour' for row in right)
    assert not any(row['provenance']['retrieval'] == 'graph_neighbour' for row in left)
    assert len(left) <= 12 and len(right) <= 12
    assert lexical['run_id'] != graph['run_id']
    assert lexical['leakage_audit']['strict_issue_time_verified'] is True
    assert lexical['annotation'] is None


def test_verifier_switch_executes_real_separate_native_pass(source):
    off, on = Completion(), Completion()
    baseline = run(source, policy(), complete_json=off, provider='synthetic', model='fixture')
    verified = run(source, policy(verifier=True), complete_json=on, provider='synthetic', model='fixture')
    assert len(off.payloads) == 1 and len(on.payloads) == 2
    assert off.payloads[0] == on.payloads[0]
    assert baseline['verification']['kind'] == 'structural'
    assert verified['verification']['kind'] == 'llm_separate_pass'
    assert baseline['verification']['independent_human'] is False
    assert verified['verification']['independent_human'] is False
    assert [row['id'] for row in baseline['evidence']] == [row['id'] for row in verified['evidence']]


def test_counter_switch_searches_falsification_then_regenerates_same_snapshot(source):
    completion = Completion()
    pack = run(source, policy(graph=True, counter_search=True, verifier=True),
               complete_json=completion, provider='synthetic', model='fixture')
    assert len(completion.payloads) == 3
    assert not any(row['locator'].startswith('fallback.py:') for row in completion.payloads[0]['evidence'])
    assert any(row['locator'].startswith('fallback.py:') for row in completion.payloads[1]['evidence'])
    assert pack['refinement']['policy'] == COUNTER_POLICY
    assert pack['refinement']['generation_passes'] == 2
    assert pack['refinement']['retrieval_rounds'] == 1
    assert pack['refinement']['search']['counter_evidence_confirmed'] is False
    assert all(row['purpose'] == 'unverified_falsification_search' for row in pack['refinement']['search']['queries'])
    assert all('Needle mechanism' not in row['query'] for row in pack['refinement']['search']['queries'])
    assert all(row['revision'] == source[1].commit_sha for row in pack['evidence'] if row['kind'] == 'code')
    assert pack['leakage_audit']['strict_issue_time_verified'] is True


def test_history_is_real_extra_prompt_text_only_in_selected_history_arm(source):
    candidates = [history()]
    off, on = Completion(), Completion()
    baseline = run(source, policy(), historical_sources=candidates, complete_json=off)
    historical = run(source, policy(history=True), historical_sources=candidates, complete_json=on)
    assert not any(row['kind'] == 'historical_issue' for row in off.payloads[0]['evidence'])
    record = next(row for row in on.payloads[0]['evidence'] if row['kind'] == 'historical_issue')
    assert record['text'] == 'Prior issue\nA prior observed symptom\n'
    assert record['available_at'] < source[0]['issue_created_at']
    assert 'receipt_sha256' not in json.dumps(record) and 'event_source_url' not in json.dumps(record)
    assert historical['historical_source_audit']['eligible_sources'] == 1
    assert historical['historical_source_audit']['included_sources'] == 1
    assert baseline['historical_source_audit']['eligible_sources'] == 1
    assert baseline['historical_source_audit']['included_sources'] == 0
    assert baseline['historical_source_audit']['selection_sha256'] == historical['historical_source_audit']['selection_sha256']


@pytest.mark.parametrize('change, expected', [
    (lambda r: r.pop('body_provenance'), 'independent_opening_event_missing'),
    (lambda r: r['payload'].update(pull_request={}), 'source_repository_or_issue_identity_mismatch'),
    (lambda r: r['body_provenance']['payload'].update(public=False), 'independent_opening_binding_invalid'),
    (lambda r: r['body_provenance']['payload']['payload']['issue'].update(updated_at='2020-01-02T00:00:00Z'), 'opening_version_ambiguous'),
    (lambda r: r.update(payload_sha256='0' * 64), 'source_identity_or_payload_hash_invalid'),
    (lambda r: r.update(source_url='https://api.github.com/repos/other/repo/issues/1'), 'source_repository_or_issue_identity_mismatch'),
    (lambda r: r['body_provenance'].update(event_sha256='0' * 64), 'independent_opening_binding_invalid'),
])
def test_unknown_or_invalid_history_stays_unavailable(source, change, expected):
    candidate = history(); change(candidate)
    pack = run(source, policy(history=True), historical_sources=[candidate])
    audit = pack['historical_source_audit']
    assert audit['eligible_sources'] == audit['included_sources'] == 0
    assert audit['sources'][0]['reason'] == expected
    assert not any(row['kind'] == 'historical_issue' for row in pack['evidence'])


@pytest.mark.parametrize('candidate', [
    history(created='2020-01-03T00:00:00Z'),
    history(emitted='2020-01-03T00:00:00Z'),
    history(created='2020-01-04T00:00:00Z'),
    history(number=99),
])
def test_history_equal_or_later_cutoff_and_self_issue_are_never_admitted(source, candidate):
    evidence, audit = verified_history_evidence([candidate], source[0], enabled=True)
    assert evidence == [] and audit['eligible_sources'] == 0
    assert audit['sources'][0]['reason'] in {'source_not_strictly_before_cutoff', 'target_issue_is_not_prior_history'}


def test_empty_history_is_explicit_zero_coverage_not_no_benefit_evidence(source):
    pack = run(source, policy(history=True))
    audit = pack['historical_source_audit']
    assert audit['selected_sources'] == audit['eligible_sources'] == audit['included_sources'] == 0
    assert audit['coverage'] == 'explicit_selected_candidates_only_not_exhaustive'


def test_additional_target_refs_from_query_receipt_are_excluded(source):
    query = deepcopy(source[3])
    query['source_urls'].append('https://api.github.com/repos/synthetic/repo/issues/1')
    pack = run(source, policy(history=True), query_receipt=query, historical_sources=[history()])
    assert pack['historical_source_audit']['included_sources'] == 0
    assert pack['historical_source_audit']['sources'][0]['reason'] == 'target_issue_is_not_prior_history'


def test_history_has_fixed_four_by_1200_cap_and_retains_eligible_denominator(source):
    candidates = [history(number=i, body='long historical text ' * 150) for i in range(1, 7)]
    evidence, audit = verified_history_evidence(candidates, source[0], enabled=True)
    assert len(evidence) == 4 and all(len(row['text']) == 1200 for row in evidence)
    assert audit['eligible_sources'] == 6 and audit['included_sources'] == 4
    assert audit['sources'][-1]['selection_reason'] == 'included_source_limit'
    assert all(row['provenance']['truncated'] for row in evidence)


@pytest.mark.parametrize('component, value', [('top_k', 8), ('graph_expand', 1),
    ('retrieval_mode', []), ('history_mode', 'current_context'), ('counter_search', 1),
    ('max_output_tokens', True), ('registration_sha256', ''), ('arm_id', '../escape')])
def test_rehashed_unsupported_policy_is_rejected(component, value):
    recipe = policy(); recipe[component] = value
    recipe['sha256'] = digest({k: v for k, v in recipe.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        validate_policy(recipe)


@pytest.mark.parametrize('change', [
    {'top_k': 8}, {'independent_critic': True}, {'refinement_rounds': 1},
    {'max_output_tokens': 512}, {'protocol': 'benchmark_base'}, {'repository_context': {}},
    {'planned_workflow': True}, {'recoverable': True}, {'resumable': True},
    {'prepared_evidence': []}, {'controller': object()},
])
def test_policy_and_boundary_mismatches_fail_before_completion(source, change):
    with pytest.raises(ValueError, match='strict Main Dev recipe'):
        run(source, policy(), complete_json=lambda *a, **k: pytest.fail('completion called'), **change)


def test_experiment_denies_unverified_query_and_nondev(source):
    with pytest.raises(ValueError, match='original-query receipt'):
        run(source, policy(), query_receipt=None)
    case, snapshot, graph, receipt, root = source
    with pytest.raises(ValueError, match='strict Main Dev recipe'):
        investigate(dict(case, split='main_test'), root, protocol='issue_time', snapshot=snapshot,
                    graph=graph, query_receipt=receipt, experiment_policy=policy(),
                    top_k=12, independent_critic=False)


def test_raw_history_restricted_fields_and_bounds_fail_before_completion(source):
    candidate = history(); candidate['human_rationale'] = 'forbidden'
    with pytest.raises(ValueError, match='Hidden'):
        run(source, policy(history=True), historical_sources=[candidate])
    with pytest.raises(ValueError, match='bounded'):
        run(source, policy(history=True), historical_sources=[history(i) for i in range(1, 22)])
    with pytest.raises(ValueError, match='repeats'):
        run(source, policy(history=True), historical_sources=[history(), history()])


def test_historical_source_switch_cannot_enter_default_product_path(source):
    case, snapshot, graph, receipt, root = source
    with pytest.raises(ValueError, match='explicit controlled'):
        investigate(case, root, snapshot=snapshot, graph=graph, protocol='issue_time',
                    query_receipt=receipt, historical_sources=[])


def test_omitted_policy_preserves_existing_default_retrieval_and_focus_trace(source):
    case, snapshot, graph, receipt, root = source
    default = investigate(case, root, snapshot=snapshot, graph=graph, protocol='issue_time', query_receipt=receipt)
    explicit = investigate(case, root, snapshot=snapshot, graph=graph, protocol='issue_time',
                           query_receipt=receipt, experiment_policy=None)
    assert default == explicit and 'experiment_policy' not in default
    completion = Completion()
    pack = investigate(case, root, snapshot=snapshot, graph=graph, protocol='issue_time', query_receipt=receipt,
                       top_k=1, refinement_rounds=1, complete_json=completion)
    assert pack['refinement']['policy'] == POLICY
    assert all('Needle mechanism' in row['query'] for row in pack['refinement']['search']['queries'])
    assert all(row['purpose'] == 'unverified_hypothesis_focus' for row in pack['refinement']['search']['queries'])


def test_invalid_focus_mode_is_rejected_before_search(source):
    with pytest.raises(ValueError, match='query mode'):
        expand_for_hypotheses([], [], source[1], source[2], query_mode='execute_test')


def test_counter_query_cap_applies_after_selecting_hypotheses_with_missing_counters(source):
    pack = run(source, policy())
    evidence = pack['evidence']
    identifier = next(row['id'] for row in evidence if row['kind'] == 'code')
    hypotheses = [{'id': f'h{i}', 'statement': f'Needle mechanism {i}',
        'support_ids': [identifier], 'counter_ids': [identifier] if i < 4 else [],
        'counter_status': 'found' if i < 4 else 'not_found',
        'falsification_test': 'recover_none sentinel' if i == 4 else 'evaluate helper'}
        for i in range(1, 6)]
    added, search = expand_for_hypotheses(hypotheses, evidence, source[1], source[2],
                                         query_mode='falsification_only')
    assert [row['seed_hypothesis_id'] for row in search['queries']] == ['h4', 'h5']
    assert any(row['locator'].startswith('fallback.py:') for row in added)
    assert len(search['queries']) <= search['max_queries'] == 3
    # The original product policy still selects its first three hypotheses.
    _, original = expand_for_hypotheses(hypotheses, evidence, source[1], source[2])
    assert [row['seed_hypothesis_id'] for row in original['queries']] == ['h1', 'h2', 'h3']


def test_experiment_stage_executor_reuses_exact_native_generation_across_verifier_arms(source):
    cache = {}
    stages = []
    def execute(stage, inputs, produce):
        key = (stage, digest(inputs)); stages.append((stage, key in cache))
        if key not in cache:
            cache[key] = deepcopy(produce())
        return deepcopy(cache[key])
    baseline_completion, critic_completion = Completion(), Completion()
    baseline = run(source, policy(), complete_json=baseline_completion,
                   experiment_stage_executor=execute)
    verified = run(source, policy(verifier=True), complete_json=critic_completion,
                   experiment_stage_executor=execute)
    assert baseline['annotation'] == verified['annotation']
    assert len(baseline_completion.payloads) == 1
    assert len(critic_completion.payloads) == 1 and 'hypotheses' in critic_completion.payloads[0]
    assert ('seed_generation', True) in stages and ('initial_retrieval', True) in stages
    assert verified['verification']['kind'] == 'llm_separate_pass'
    # Replaying a completed arm does not generate, retrieve or critique again.
    replayed = run(source, policy(verifier=True), experiment_stage_executor=execute,
                   complete_json=lambda *a, **k: pytest.fail('completed provider stage was repeated'))
    assert replayed == verified


def test_experiment_stage_executor_requires_explicit_policy_and_callable(source):
    case, snapshot, graph, receipt, root = source
    with pytest.raises(ValueError, match='explicit controlled'):
        investigate(case, root, experiment_stage_executor=lambda *a: pytest.fail('executor called'))
    with pytest.raises(ValueError, match='callable'):
        run(source, policy(), experiment_stage_executor={})


@pytest.mark.parametrize('stage, corrupt', [
    ('initial_retrieval', lambda p: p['evidence'][0].update(revision='0' * 40)),
    ('seed_generation', lambda p: p['annotation'].update(case_id='different')),
    ('verification', lambda p: p['verification'].update(reports=[])),
])
def test_cached_stages_are_revalidated_against_native_source_and_output_contract(source, stage, corrupt):
    def execute(selected, inputs, produce):
        payload = produce()
        if selected == stage:
            corrupt(payload)
        return payload
    with pytest.raises(ValueError):
        run(source, policy(), complete_json=Completion(), experiment_stage_executor=execute)


def test_cached_counter_candidates_replay_exact_snapshot_lines(source):
    def execute(stage, inputs, produce):
        payload = produce()
        if stage == 'focused_retrieval':
            row = payload['added_evidence'][0]
            row['text'] = 'fabricated historical result'
            row['sha256'] = sha256(row['text'].encode()).hexdigest()
        return payload
    with pytest.raises(ValueError, match='snapshot bytes'):
        run(source, policy(counter_search=True), complete_json=Completion(), experiment_stage_executor=execute)
