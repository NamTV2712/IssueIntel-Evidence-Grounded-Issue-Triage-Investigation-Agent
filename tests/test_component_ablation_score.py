"""Pure synthetic comparison scoring; no real benchmark, credentials or API."""
from copy import deepcopy
from hashlib import sha256
import json
import sys
from types import SimpleNamespace

import pytest

from issueintel.benchmarks import component_ablation_score as scoring
from issueintel.runtime.io import digest
from issueintel.investigation.verifier import verify_hypotheses


def source(kind, locator, text='synthetic source'):
    return {'id': digest([kind, locator])[:20], 'kind': kind, 'locator': locator,
            'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'revision': 'a' * 40 if kind == 'code' else None}


def pack(cid, paths=('hit.py', 'irrelevant.py'), *, history=False, counter=False):
    evidence = [source('issue', cid), *[source('code', path + f':{index + 1}-{index + 2}') for index, path in enumerate(paths)]]
    annotation = {'hypotheses': [{'id': 'h1', 'statement': 'A synthetic mechanism',
        'support_ids': [evidence[1]['id']] if len(evidence) > 1 else [evidence[0]['id']],
        'counter_ids': [evidence[-1]['id']] if counter else [],
        'counter_status': 'found' if counter else 'not_found',
        'falsification_test': 'Inspect a synthetic behavior under a controlled input'}]}
    audit = {'selected_sources': 2, 'eligible_sources': 1, 'included_sources': int(history), 'enabled': history}
    audit['sha256'] = digest(audit)
    result = {'case': {'case_id': cid, 'split': 'main_dev'}, 'evidence': evidence,
            'annotation': annotation, 'verification': verify_hypotheses(annotation, evidence),
            'historical_source_audit': audit}
    if counter:
        result['refinement'] = {'search': {'policy': 'falsification_focused_retrieval_v1',
            'added_ids': [evidence[-1]['id']], 'counter_evidence_confirmed': False}}
    return result


def call(cid, arm='lexical_symbol', *, status='settled', observed=(10, 20), rid=None):
    identity = digest([cid, arm])
    reservation = rid or identity[:32]
    receipt = {'reservation_id': reservation, 'model': 'openai/gpt-oss-120b',
        'input_bound': 100, 'output_bound': 200, 'tokens_charged_or_reserved': 30,
        'micro_usd_charged_or_reserved': 4, 'status': status,
        'observed_input_tokens': observed[0], 'observed_output_tokens': observed[1]}
    return {'call_id': identity, 'case_id': cid, 'owner_arm_id': arm, 'status': 'returned',
            'receipts': {reservation: receipt}, 'latency_seconds': 0.2}


@pytest.fixture
def matrix():
    spec = {'cases': [{'case_id': f'verified:synthetic__{index}', 'history_included_sources': 1} for index in range(25)],
            'arms': [{'arm_id': arm} for arm in scoring.ARMS]}
    rows, calls, targets = [], [], {}
    for case_index, case in enumerate(spec['cases']):
        cid = case['case_id']
        owned = call(cid)
        calls.append(owned)
        targets[cid] = {'files': ['hit.py', 'outside.py'], 'available_files': ['hit.py', 'irrelevant.py'],
                        'patch_sha256': 'b' * 64}
        for index, arm in enumerate(scoring.ARMS):
            rows.append({'case_id': cid, 'arm_id': arm, 'status': 'completed',
                         'pack': pack(cid, history=index >= 2, counter=index == 4),
                         'call_ids': [owned['call_id']],
                         'owned_call_ids': [owned['call_id']] if index == 0 else [],
                         'pipeline_latency_seconds': 0.5, 'preparation_seconds': 0.1,
                         'preparation_measurement_id': digest([cid, 'synthetic preparation']),
                         'preparation_owner_arm_id': scoring.ARMS[case_index % 5]})
    return spec, rows, targets, calls


def summarize(matrix):
    return scoring.summarize_component_ablation(*matrix)


def test_all_patch_files_remain_denominator_and_paths_deduped(matrix):
    matrix[1][0]['pack'] = pack(matrix[1][0]['case_id'], ('hit.py', 'hit.py', 'irrelevant.py'))
    result = summarize(matrix)
    first = result['cases'][0]
    assert first['ranked_code_paths'] == ['hit.py', 'irrelevant.py']
    assert first['localization']['recall_at_k'] == 0.5
    assert first['localization']['precision_at_k'] == 0.5
    assert first['localization']['mrr'] == 1
    assert first['localization']['all_files_at_k'] == 0
    assert first['unavailable_gold_files'] == ['outside.py']
    assert result['aggregates']['lexical_symbol']['retrieval_localization']['recall_at_k'] == {
        'mean': 0.5, 'defined_cases': 25, 'undefined_no_gold_cases': 0}


@pytest.mark.parametrize('status', ['failed', 'unavailable'])
def test_failed_arms_are_empty_predictions_with_full_selected_denominator(matrix, status):
    matrix[1][0].update(status=status, pack=None)
    result = summarize(matrix)
    aggregate = result['aggregates']['lexical_symbol']
    assert result['cases'][0]['ranked_code_paths'] == []
    assert result['cases'][0]['localization']['recall_at_k'] == 0
    assert aggregate['selected_cases'] == 25
    assert aggregate['completed_cases'] == 24
    assert aggregate['completion_coverage'] == 24 / 25
    assert aggregate['retrieval_localization']['recall_at_k']['mean'] == 12 / 25
    assert result['contrasts'][0]['mean_recall_at_5_increment_all_selected'] == 0.5 / 25
    assert result['contrasts'][0]['mean_recall_at_5_increment_both_completed'] == 0


def test_empty_gold_explicitly_undefined_and_not_silent_row_drop(matrix):
    cid = matrix[0]['cases'][0]['case_id']
    matrix[2][cid]['files'] = []
    result = summarize(matrix)
    metric = result['aggregates']['lexical_symbol']['retrieval_localization']['recall_at_k']
    assert metric == {'mean': 0.5, 'defined_cases': 24, 'undefined_no_gold_cases': 1}
    assert result['aggregates']['lexical_symbol']['selected_cases'] == 25


def test_unique_cost_once_global_but_shared_reference_retained(matrix):
    result = summarize(matrix)
    assert result['global_unique_provider_calls'] == 25
    assert result['global_unique_usage']['tokens_charged_or_reserved'] == 750
    assert result['global_unique_usage']['micro_usd_charged_or_reserved'] == 100
    assert result['aggregates']['lexical_symbol']['owned_usage']['tokens_charged_or_reserved'] == 750
    assert result['aggregates']['plus_graph']['owned_usage']['tokens_charged_or_reserved'] == 0
    assert result['cases'][1]['referenced_usage']['tokens_charged_or_reserved'] == 30
    assert result['cases'][1]['owned_usage']['tokens_charged_or_reserved'] == 0
    assert result['global_unique_preparation_latency']['observed_seconds'] == pytest.approx(2.5)
    assert result['aggregates']['lexical_symbol']['owned_preparation_latency']['observations'] == 5
    assert result['aggregates']['plus_graph']['referenced_preparation_latency']['observations'] == 25


def test_resume_preparation_is_distinct_and_not_dropped(matrix):
    for row in matrix[1][3:5]:
        row.update(preparation_measurement_id='e' * 64, preparation_owner_arm_id='plus_verifier', preparation_seconds=0.4)
    result = summarize(matrix)
    assert result['global_unique_preparation_latency']['observations'] == 26
    assert result['global_unique_preparation_latency']['observed_seconds'] == pytest.approx(2.9)


def test_preparation_owner_must_actually_reference_that_measurement(matrix):
    matrix[1][1].update(preparation_measurement_id='e' * 64, preparation_owner_arm_id='plus_verifier')
    with pytest.raises(ValueError, match='no matching original owner'):
        summarize(matrix)


def test_unknown_bounds_and_missing_latency_kept_separate(matrix):
    first = matrix[3][0]
    receipt = next(iter(first['receipts'].values()))
    receipt.update(status='failed_bound', observed_input_tokens=None, observed_output_tokens=None,
                   tokens_charged_or_reserved=300, micro_usd_charged_or_reserved=40)
    first['latency_seconds'] = None
    result = summarize(matrix)
    usage = result['global_unique_usage']
    assert usage['settled_tokens'] == 24 * 30
    assert usage['unsettled_or_unknown_tokens_bound'] == 300
    assert usage['missing_observed_usage_reservations'] == 1
    assert result['global_provider_latency']['missing_observations'] == 1
    assert result['global_provider_latency']['observed_seconds'] == pytest.approx(4.8)


def test_unreceipted_intent_has_unknown_cost_count(matrix):
    matrix[3][0].update(status='intent', receipts={}, latency_seconds=None)
    result = summarize(matrix)
    assert result['global_calls_without_receipts'] == 1
    assert result['aggregates']['lexical_symbol']['owned_calls_without_receipts'] == 1
    assert result['global_unique_usage']['unique_reservations'] == 24


def test_counters_and_citations_are_model_assertions_not_quality(matrix):
    result = summarize(matrix)
    assert result['aggregates']['plus_counter_search']['model_cited_counter_coverage_completed_hypotheses'] == 1
    assert result['aggregates']['plus_graph']['model_cited_counter_coverage_completed_hypotheses'] == 0
    assert result['cases'][-1]['citations']['counter_semantic_correctness'] == 'unverified'
    assert result['semantic_quality'] == 'unreviewed'
    assert result['independent_human'] is False
    assert result['aggregates']['plus_counter_search']['counter_added_code_file_count'] == 25
    assert result['aggregates']['plus_counter_search']['counter_added_gold_file_hits'] == 0


def test_counter_appended_file_can_improve_recall18_while_top5_unchanged(matrix):
    cid = matrix[1][4]['case_id']
    paths = tuple(f'miss-{i}.py' for i in range(6))
    matrix[1][3]['pack'] = pack(cid, paths, history=True)
    matrix[1][4]['pack'] = pack(cid, paths + ('outside.py',), history=True, counter=True)
    result = summarize(matrix)
    before, after = result['cases'][3:5]
    assert before['localization']['recall_at_k'] == after['localization']['recall_at_k'] == 0
    assert before['retrieval_file_recall_at_18'] == 0
    assert after['retrieval_file_recall_at_18'] == 0.5
    assert after['counter_added_new_code_file_count'] == 1
    assert after['counter_added_new_gold_file_hits'] == 1
    assert after['counter_candidate_semantic_correctness'] == 'unverified'
    assert result['contrasts'][-1]['mean_recall_at_18_increment_all_selected'] == pytest.approx(0.5 / 25)


def test_counter_candidate_relabeling_as_confirmed_is_rejected(matrix):
    matrix[1][4]['pack']['refinement']['search']['counter_evidence_confirmed'] = True
    with pytest.raises(ValueError, match='unverified retrieval'):
        summarize(matrix)


def test_zero_history_coverage_is_uninformative(matrix):
    for row in matrix[0]['cases']:
        row['history_included_sources'] = 0
    result = summarize(matrix)
    assert result['history_contrast_informative'] is False
    assert any('uninformative' in text for text in result['limitations'])
    assert result['contrasts'][2]['mean_recall_at_5_increment_all_selected'] == 0


@pytest.mark.parametrize('mutation', [
    lambda data: data[1].pop(),
    lambda data: data[1].__setitem__(0, deepcopy(data[1][1])),
    lambda data: data[1][0].update(status='running'),
    lambda data: data[1][0].update(pack=None),
    lambda data: data[1][0]['pack']['case'].update(split='main_test'),
    lambda data: data[1][0].update(pipeline_latency_seconds=float('nan')),
    lambda data: data[1][0].update(preparation_seconds=True),
    lambda data: data[1][0].update(owned_call_ids=[]),
    lambda data: data[1][1].update(call_ids=[]),  # Unowned shared refs allowed; this case is valid, handled separately.
])
def test_bad_complete_matrix_rejected(matrix, mutation):
    mutation(matrix)
    if matrix[1][1]['call_ids'] == []:
        # A no-call arm can legitimately reuse a cached no-model stage.
        assert summarize(matrix)['global_unique_provider_calls'] == 25
    else:
        with pytest.raises((ValueError, KeyError, TypeError)):
            summarize(matrix)


@pytest.mark.parametrize('mutation', [
    lambda data: data[3].append(deepcopy(data[3][0])),
    lambda data: data[1][1].update(call_ids=[data[3][1]['call_id']]),
    lambda data: data[1][0].update(call_ids=[data[3][0]['call_id']] * 2),
    lambda data: data[1][1].update(owned_call_ids=[data[3][0]['call_id']]),
    lambda data: next(iter(data[3][0]['receipts'].values())).update(tokens_charged_or_reserved=-1),
    lambda data: next(iter(data[3][0]['receipts'].values())).update(observed_output_tokens=None),
    lambda data: next(iter(data[3][0]['receipts'].values())).update(status='free'),
])
def test_call_or_receipt_corruption_rejected(matrix, mutation):
    mutation(matrix)
    with pytest.raises((ValueError, KeyError, TypeError)):
        summarize(matrix)


def test_duplicate_reservation_across_calls_rejected(matrix):
    first = next(iter(matrix[3][0]['receipts'].values()))
    matrix[3][1]['receipts'] = {first['reservation_id']: deepcopy(first)}
    with pytest.raises(ValueError, match='more than one call'):
        summarize(matrix)


def test_rejects_bad_target_selection_and_path(matrix):
    matrix[2][matrix[0]['cases'][0]['case_id']]['files'] = ['../escape.py']
    with pytest.raises(ValueError):
        summarize(matrix)


def test_citation_and_history_replay_before_scores(matrix):
    matrix[1][0]['pack']['annotation']['hypotheses'][0]['support_ids'] = ['made-up']
    with pytest.raises(ValueError, match='unavailable evidence'):
        summarize(matrix)


def test_history_audit_rehash_cannot_hide_inconsistent_counts(matrix):
    audit = matrix[1][0]['pack']['historical_source_audit']
    audit['included_sources'] = 1
    audit['sha256'] = digest({key: value for key, value in audit.items() if key != 'sha256'})
    with pytest.raises(ValueError, match='inconsistent'):
        summarize(matrix)


@pytest.mark.parametrize('patch,expected', [
    ('diff --git a/one.py b/one.py\n--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n-old\n+new\n', ['one.py']),
    ('diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+new\n', ['new.py']),
    ('diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n', ['old.py']),
    ('diff --git a/image.bin b/image.bin\nBinary files a/image.bin and b/image.bin differ\n', ['image.bin']),
    ('diff --git a/file name.bin b/file name.bin\nBinary files differ\n', ['file name.bin']),
    ('diff --git "a/caf\\303\\251.bin" "b/caf\\303\\251.bin"\nBinary files differ\n', ['café.bin']),
    ('diff --git a/mode.py b/mode.py\nold mode 100644\nnew mode 100755\n', ['mode.py']),
    ('diff --git a/one.py b/one.py\n--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n--- fake.py\n+++ invented.py\n', ['one.py']),
    ('', []),
])
def test_all_patch_file_header_forms_preserve_denominator(patch, expected):
    assert scoring._patch_files(patch) == expected


@pytest.mark.parametrize('patch', ['Binary unsupported without file identity',
    'diff --git a/left b/right b/ambiguous\nBinary files differ\n',
    'diff --git a/../escape b/../escape\nBinary files differ\n'])
def test_unsupported_patch_identity_rejects_instead_of_dropping_files(patch):
    with pytest.raises(ValueError):
        scoring._patch_files(patch)


def fixture_wrapper(monkeypatch, tmp_path, matrix):
    from issueintel.benchmarks import component_ablation, dev_context
    spec, results, targets, calls = matrix
    state = {'registration': spec, 'directory': tmp_path / 'artifacts/experiments/component_ablation' / ('a' * 64),
             'seal': {'sha256': 'c' * 64}, 'results': results, 'calls': calls, 'stages': []}
    state['directory'].mkdir(parents=True)
    order = []
    def sealed(*args):
        order.append('sealed')
        return deepcopy(state)
    def authorization(*args, **kwargs):
        order.append('auth')
        return {'gold_scoring_authorized': True}
    def context(root, cid):
        order.append('public')
        return {'split': 'main_dev'}, SimpleNamespace(protocol='issue_time', files=[SimpleNamespace(path='hit.py')]), {}
    def gold(*args):
        order.append('gold')
        return deepcopy(targets), {'sha256': 'd' * 64}
    monkeypatch.setitem(sys.modules, 'issueintel.benchmarks.component_ablation_run', SimpleNamespace(load_sealed_inference=sealed))
    monkeypatch.setattr(component_ablation, 'verify_run_authorization', authorization)
    monkeypatch.setattr(dev_context, 'accepted_context', context)
    monkeypatch.setattr(scoring, '_gold_targets', gold)
    return state, order


def test_wrapper_replays_complete_seal_and_exact_auth_before_gold(monkeypatch, tmp_path, matrix):
    state, order = fixture_wrapper(monkeypatch, tmp_path, matrix)
    result = scoring.score_component_ablation(tmp_path, 'a' * 64, 'ignored-explicit-auth')
    assert order[:2] == ['sealed', 'auth']
    assert order.index('gold') == 28  # initial seal/auth, 25 public contexts, second seal
    assert order[-2:] == ['sealed', 'auth']
    report = json.loads(__import__('pathlib').Path(result['report_json']).read_bytes())
    assert report['inference_gold_used'] is False
    assert report['scoring_gold_used'] is True
    assert report['gold_source']['sha256'] == 'd' * 64
    assert report['sha256'] == digest({key: value for key, value in report.items() if key != 'sha256'})
    second = scoring.score_component_ablation(tmp_path, 'a' * 64, 'ignored-explicit-auth')
    assert second == result


def test_wrapper_denies_gold_without_matching_auth(monkeypatch, tmp_path, matrix):
    from issueintel.benchmarks import component_ablation
    state, order = fixture_wrapper(monkeypatch, tmp_path, matrix)
    monkeypatch.setattr(component_ablation, 'verify_run_authorization', lambda *args, **kwargs: {'gold_scoring_authorized': False})
    with pytest.raises(PermissionError):
        scoring.score_component_ablation(tmp_path, 'a' * 64, 'not-authorized')
    assert 'gold' not in order


def test_wrapper_denies_gold_on_invalid_inference_seal(monkeypatch, tmp_path, matrix):
    state, order = fixture_wrapper(monkeypatch, tmp_path, matrix)
    def deny(*args):
        raise ValueError('Incomplete matrix')
    monkeypatch.setitem(sys.modules, 'issueintel.benchmarks.component_ablation_run', SimpleNamespace(load_sealed_inference=deny))
    with pytest.raises(ValueError, match='Incomplete'):
        scoring.score_component_ablation(tmp_path, 'a' * 64, 'not-used')
    assert 'gold' not in order


def test_wrapper_detects_changed_inference_before_gold(monkeypatch, tmp_path, matrix):
    state, order = fixture_wrapper(monkeypatch, tmp_path, matrix)
    counter = 0
    def changed(*args):
        nonlocal counter
        counter += 1
        result = deepcopy(state)
        if counter > 1:
            result['seal']['sha256'] = 'e' * 64
        return result
    monkeypatch.setitem(sys.modules, 'issueintel.benchmarks.component_ablation_run', SimpleNamespace(load_sealed_inference=changed))
    with pytest.raises(ValueError, match='changed before'):
        scoring.score_component_ablation(tmp_path, 'a' * 64, 'explicit-auth')
    assert 'gold' not in order


def test_wrapper_preserves_inference_and_writes_only_new_content_id(monkeypatch, tmp_path, matrix):
    state, order = fixture_wrapper(monkeypatch, tmp_path, matrix)
    marker = state['directory'] / 'inference-sealed.json'
    marker.write_bytes(b'synthetic original inference bytes')
    first = scoring.score_component_ablation(tmp_path, 'a' * 64, 'explicit-auth')
    path = __import__('pathlib').Path(first['report_json'])
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='immutable'):
        scoring.score_component_ablation(tmp_path, 'a' * 64, 'explicit-auth')
    assert marker.read_bytes() == b'synthetic original inference bytes'


def fake_gold_reader(monkeypatch, matrix):
    from issueintel.benchmarks import dev_context, strict_dev
    spec = matrix[0]
    selected = [case['case_id'] for case in spec['cases']]
    reference = {'path': 'data/raw/synthetic.parquet', 'sha256': 'f' * 64}
    catalog = {'_members': {cid: {} for cid in selected}, '_authority': {'sources': [reference]}}
    rows = [{'instance_id': cid.removeprefix('verified:'),
        'patch': 'diff --git a/hit.py b/hit.py\n--- a/hit.py\n+++ b/hit.py\n@@ -1 +1 @@\n-old\n+new\n'
                 'diff --git a/outside.py b/outside.py\n--- a/outside.py\n+++ b/outside.py\n@@ -1 +1 @@\n-old\n+new\n'} for cid in selected]
    requests, checks = [], []
    def reader(path, **kwargs):
        requests.append((path, kwargs))
        return SimpleNamespace(to_pylist=lambda: deepcopy(rows))
    def checked(root, value):
        checks.append(deepcopy(value))
        return 'synthetic-validated-source'
    parquet = SimpleNamespace(read_table=reader)
    monkeypatch.setitem(sys.modules, 'pyarrow', SimpleNamespace(parquet=parquet))
    monkeypatch.setitem(sys.modules, 'pyarrow.parquet', parquet)
    monkeypatch.setattr(dev_context, 'development_catalog', lambda root: catalog)
    monkeypatch.setattr(strict_dev, '_checked', checked)
    available = {cid: ['hit.py'] for cid in selected}
    return spec, available, rows, requests, checks, catalog


def test_gold_reader_filters_exact_dev_patch_columns_and_rechecks_source(monkeypatch, matrix):
    spec, available, rows, requests, checks, _ = fake_gold_reader(monkeypatch, matrix)
    targets, reference = scoring._gold_targets('synthetic-root', spec, available)
    assert requests == [('synthetic-validated-source', {
        'columns': ['instance_id', 'patch'],
        'filters': [('instance_id', 'in', [case['case_id'].removeprefix('verified:') for case in spec['cases']])]
    })]
    assert checks == [reference, reference]
    first = targets[spec['cases'][0]['case_id']]
    assert first['files'] == ['hit.py', 'outside.py']
    assert first['available_files'] == ['hit.py']
    assert first['patch_sha256'] == sha256(rows[0]['patch'].encode()).hexdigest()


@pytest.mark.parametrize('mutate', [
    lambda rows, catalog: rows.pop(),
    lambda rows, catalog: rows.append(deepcopy(rows[0])),
    lambda rows, catalog: rows[0].update(instance_id='holdout-record'),
    lambda rows, catalog: catalog['_members'].pop(next(iter(catalog['_members']))),
    lambda rows, catalog: rows[0].update(patch=None),
])
def test_gold_reader_rejects_nonexact_selection(monkeypatch, matrix, mutate):
    spec, available, rows, _, _, catalog = fake_gold_reader(monkeypatch, matrix)
    mutate(rows, catalog)
    with pytest.raises(ValueError):
        scoring._gold_targets('synthetic-root', spec, available)
