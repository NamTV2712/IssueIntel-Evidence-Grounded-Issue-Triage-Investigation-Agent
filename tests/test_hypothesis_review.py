"""Saved-source hypothesis comparisons; all sources and models are synthetic."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from issueintel.investigation.hypothesis_review import (
    LIMITATIONS, REVIEW_SCHEMA, build_hypothesis_review, render_markdown,
    validate_hypothesis_review,
)
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.report import build_report, validate_report
from issueintel.runtime.io import digest
from test_focused_retrieval import FakeModel, fixture_repo


def seal(value):
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    return value


def project(pack):
    return build_report(pack, job_id='a' * 24,
                        source_pack_sha256=sha256(json.dumps(pack).encode()).hexdigest())


@pytest.fixture
def generated(fixture_repo):
    case, path = fixture_repo
    return investigate(case, path, complete_json=FakeModel(), provider='synthetic', model='fixture')


@pytest.fixture
def report(generated):
    return project(generated)


@pytest.fixture
def comparison_report(generated):
    generated['case'].update(repository='Owner/Repo', issue_url='https://github.com/Owner/Repo/issues/7')
    code = next(row['id'] for row in generated['evidence'] if row['kind'] == 'code')
    issue = next(row['id'] for row in generated['evidence'] if row['kind'] == 'issue')
    left, right, third = generated['annotation']['hypotheses']
    left.update(support_ids=[code], counter_ids=[issue], counter_status='found',
                falsification_test='Inspect each branch with the same input.')
    right.update(support_ids=[issue], counter_ids=[code], counter_status='found')
    third.update(support_ids=[issue, code], counter_ids=[], counter_status='not_found',
                 falsification_test=left['falsification_test'])
    for row, assessment in zip(generated['verification']['reviews'], ('contradicted', 'supported', 'uncertain')):
        row.update(assessment=assessment, reason='Saved synthetic critique; runtime untested.', evidence_ids=[code])
    return project(generated)


def test_projection_is_deterministic_source_bound_and_does_not_mutate_report(report):
    before = deepcopy(report)
    result = build_hypothesis_review(report)
    assert result == build_hypothesis_review(report)
    assert report == before
    assert validate_hypothesis_review(result, report) is result
    assert result['source'] == dict(report['source'], report_sha256=report['sha256'])
    assert result['model_calls'] == 0 and result['evaluation_performed'] is False
    assert result['independent_human'] is False and result['correctness'] == 'unverified'
    assert result['limitations'] == LIMITATIONS
    assert [row['id'] for row in result['hypotheses']] == [row['id'] for row in report['hypotheses']]
    result['hypotheses'][0]['support_ids'].append('changed')
    result['checks'][0]['text'] = 'changed'
    result['evidence'][0]['text'] = 'changed'
    assert report == before


def test_pairs_are_exact_id_intersections_and_keep_saved_order(comparison_report):
    result = build_hypothesis_review(comparison_report)
    left, right, third = result['hypotheses']
    code = next(row['id'] for row in result['evidence'] if row['kind'] == 'code')
    issue = next(row['id'] for row in result['evidence'] if row['kind'] == 'issue')
    assert [(row['left_hypothesis_id'], row['right_hypothesis_id']) for row in result['pairs']] == [
        (left['id'], right['id']), (left['id'], third['id']), (right['id'], third['id'])]
    first = result['pairs'][0]
    assert first['shared_support_ids'] == first['shared_counter_ids'] == []
    assert first['left_support_right_counter_ids'] == [code]
    assert first['left_counter_right_support_ids'] == [issue]
    assert result['pairs'][1]['shared_support_ids'] == [code]
    assert result['pairs'][1]['shared_check_ids'] == left['check_ids'] == third['check_ids']
    assert result['pairs'][2]['shared_support_ids'] == [issue]
    assert result['pairs'][2]['shared_check_ids'] == []
    assert result['checks'] == comparison_report['next_checks']
    assert all(row in comparison_report['evidence'] for row in result['evidence'])


def test_literal_gaps_do_not_become_truth_or_clear_missing_counter(comparison_report):
    # A supported automatic critique still cannot establish missing counter-evidence.
    third = comparison_report['hypotheses'][2]
    third['verification']['model_assessment'] = 'supported'
    seal(comparison_report)
    result = build_hypothesis_review(comparison_report)
    left, right, third = result['hypotheses']
    assert left['gap_codes'] == ['model_review_contradicted']
    assert left['citation_integrity'] == 'valid' and left['semantic_support'] == 'unverified'
    assert left['model_review']['assessment'] == 'contradicted'
    assert right['gap_codes'] == ['no_code_support_citation']
    assert third['gap_codes'] == ['missing_counter_in_retrieved_scope']
    assert result['correctness'] == 'unverified' and not result['independent_human']


def test_missing_critic_is_not_fabricated_as_disagreement(generated):
    generated['verification'] = None
    result = build_hypothesis_review(project(generated))
    for row in result['hypotheses']:
        assert row['model_review'] == {'assessment': None, 'reason': None, 'evidence_ids': [], 'independent_human': False}
        assert row['gap_codes'] == ['missing_counter_in_retrieved_scope', 'model_review_not_recorded']


def test_equal_source_text_does_not_merge_distinct_source_ids(comparison_report):
    original = next(row for row in comparison_report['evidence'] if row['kind'] == 'code')
    distinct = deepcopy(original)
    distinct.update(id='different_source', locator='other.py:' + original['locator'].split(':')[-1])
    distinct['url'] = distinct['url'].replace('/parser.py#', '/other.py#')
    comparison_report['evidence'].append(distinct)
    comparison_report['candidate_locations'].append({
        'path': 'other.py', 'start_line': int(distinct['locator'].split(':')[1].split('-')[0]),
        'end_line': int(distinct['locator'].split('-')[-1]), 'evidence_ids': [distinct['id']]})
    third = comparison_report['hypotheses'][2]
    third['support_ids'] = [distinct['id']]
    # Give this hypothesis its own saved check so each linked check keeps exact citations.
    shared = next(row for row in comparison_report['next_checks'] if third['id'] in row['hypothesis_ids'])
    shared['hypothesis_ids'].remove(third['id'])
    shared['evidence_ids'] = list(dict.fromkeys(comparison_report['hypotheses'][0]['support_ids'] + comparison_report['hypotheses'][0]['counter_ids']))
    third['falsification_test'] = 'Inspect the other saved source.'
    comparison_report['next_checks'].append({
        'id': 'different_check', 'text': third['falsification_test'], 'kind': 'proposed_check',
        'execution_status': 'not_executed_by_issueintel', 'hypothesis_ids': [third['id']],
        'evidence_ids': [distinct['id']]})
    seal(comparison_report)
    result = build_hypothesis_review(comparison_report)
    assert result['pairs'][1]['shared_support_ids'] == []
    assert result['pairs'][1]['shared_check_ids'] == []
    assert {row['id'] for row in result['evidence']} >= {original['id'], distinct['id']}


def test_only_referenced_evidence_is_included(report):
    result = build_hypothesis_review(report)
    cited = {eid for row in result['hypotheses'] for eid in row['support_ids'] + row['counter_ids'] + row['model_review']['evidence_ids']}
    cited.update(eid for row in result['checks'] for eid in row['evidence_ids'])
    assert [row['id'] for row in result['evidence']] == [row['id'] for row in report['evidence'] if row['id'] in cited]
    assert not any(row['kind'] == 'issue' for row in result['evidence'])


def test_offline_empty_state_has_no_invented_hypotheses_or_pairs(fixture_repo):
    case, path = fixture_repo
    report = project(investigate(case, path))
    result = build_hypothesis_review(report)
    assert result['hypotheses'] == result['pairs'] == result['evidence'] == []
    # Offline reports still contain a saved general inspection step. Retain it
    # literally without claiming it belongs to an invented hypothesis.
    assert result['checks'] == report['next_checks']
    assert all(row['hypothesis_ids'] == row['evidence_ids'] == [] for row in result['checks'])
    assert validate_hypothesis_review(result, report) is result
    markdown = render_markdown(result)
    assert 'No hypotheses were generated' in markdown and 'No hypothesis pairs' in markdown
    assert result['source']['pack_sha256'] in markdown


@pytest.mark.parametrize('mutation', [
    'pair_deleted', 'pair_order', 'pair_role', 'new_reference', 'gap_removed', 'invented_gap',
    'check_removed', 'check_text', 'statement', 'model_reason', 'source_pack', 'source_report',
    'source_snapshot', 'source_query', 'source_job', 'human', 'semantic', 'confidence',
    'limits', 'extra_evidence',
])
def test_resealed_review_cannot_replace_its_source_report(comparison_report, mutation):
    result = build_hypothesis_review(comparison_report)
    if mutation == 'pair_deleted': result['pairs'].pop()
    elif mutation == 'pair_order': result['pairs'].reverse()
    elif mutation == 'pair_role': result['pairs'][0]['left_support_right_counter_ids'] = []
    elif mutation == 'new_reference': result['hypotheses'][0]['support_ids'] = ['missing']
    elif mutation == 'gap_removed': result['hypotheses'][0]['gap_codes'] = []
    elif mutation == 'invented_gap': result['hypotheses'][0]['gap_codes'] = ['model_review_unsupported']
    elif mutation == 'check_removed': result['checks'] = []
    elif mutation == 'check_text': result['checks'][0]['text'] = 'Unrecorded next step'
    elif mutation == 'statement': result['hypotheses'][0]['statement'] = 'Different causal claim'
    elif mutation == 'model_reason': result['hypotheses'][0]['model_review']['reason'] = 'Invented critique'
    elif mutation == 'source_pack': result['source']['pack_sha256'] = 'f' * 64
    elif mutation == 'source_report': result['source']['report_sha256'] = 'f' * 64
    elif mutation == 'source_snapshot': result['source']['snapshot_sha256'] = 'f' * 64
    elif mutation == 'source_query': result['source']['query_sha256'] = 'f' * 64
    elif mutation == 'source_job': result['source']['job_id'] = 'f' * 24
    elif mutation == 'human': result['independent_human'] = True
    elif mutation == 'semantic': result['hypotheses'][0]['semantic_support'] = 'verified'
    elif mutation == 'confidence': result['confidence'] = .99
    elif mutation == 'limits': result['limitations'] = []
    else:
        row = deepcopy(result['evidence'][0]); row['id'] = 'uncited'
        result['evidence'].append(row)
    seal(result)
    with pytest.raises(ValueError):
        validate_hypothesis_review(result, comparison_report)


@pytest.mark.parametrize('mutation', ['query', 'revision', 'excerpt', 'citation', 'url', 'locator'])
def test_corrupt_source_report_is_rejected_before_projection(report, mutation):
    code = next(row for row in report['evidence'] if row['kind'] == 'code')
    if mutation == 'query': report['source']['query_sha256'] = '0' * 64
    elif mutation == 'revision': code['revision'] = '0' * 40
    elif mutation == 'excerpt': code['text'] = 'unrecorded'
    elif mutation == 'citation': report['hypotheses'][0]['support_ids'] = ['missing']
    elif mutation == 'url': code['url'] = 'https://evil.invalid/payload'
    else: code['locator'] = '../escape.py:1-2'
    seal(report)
    with pytest.raises(ValueError):
        build_hypothesis_review(report)


def test_projection_and_export_do_not_read_files_execute_or_use_network(report, monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('Saved comparison attempted IO, execution or a model call')
    with monkeypatch.context() as patch:
        patch.setattr('builtins.open', denied)
        patch.setattr('socket.socket', denied)
        patch.setattr('subprocess.Popen', denied)
        patch.setattr('issueintel.investigation.pipeline.investigate', denied)
        result = build_hypothesis_review(report)
        validate_hypothesis_review(result, report)
        markdown = render_markdown(result)
    assert result['source']['report_sha256'] in markdown


def test_markdown_is_useful_source_bound_and_renders_untrusted_text_literally(comparison_report):
    malicious = '<script>alert(1)</script> [click](javascript:alert(1)) @everyone | forge'
    comparison_report['hypotheses'][0]['statement'] = malicious
    comparison_report['hypotheses'][0]['verification']['model_reason'] = malicious
    seal(comparison_report)
    result = build_hypothesis_review(comparison_report)
    markdown = render_markdown(result)
    assert '<script>' not in markdown and '[click](javascript:' not in markdown and '@everyone' not in markdown
    assert '&lt;script&gt;' in markdown and '\\|' in markdown
    assert 'Saved checks — proposed, not executed' in markdown
    assert 'Different asserted roles' in markdown or 'different asserted roles' in markdown
    assert 'not a verified semantic contradiction' in markdown
    assert 'same input' in markdown
    assert comparison_report['source']['commit_sha'] in markdown
    assert comparison_report['source']['query_sha256'] in markdown
    assert comparison_report['source']['pack_sha256'] in markdown
    assert comparison_report['sha256'] in markdown and result['sha256'] in markdown
    assert '/blob/' + comparison_report['source']['commit_sha'] + '/' in markdown


def test_forged_standalone_evidence_link_is_not_rendered(comparison_report):
    result = build_hypothesis_review(comparison_report)
    next(row for row in result['evidence'] if row['kind'] == 'code')['url'] = 'https://evil.invalid/payload'
    seal(result)
    with pytest.raises(ValueError):
        render_markdown(result)


def test_runtime_contract_matches_json_schema(comparison_report):
    import jsonschema
    result = build_hypothesis_review(comparison_report)
    jsonschema.Draft202012Validator.check_schema(REVIEW_SCHEMA)
    jsonschema.validate(result, REVIEW_SCHEMA)
    assert validate_report(comparison_report) is comparison_report


@pytest.mark.parametrize('field,value', [
    ('model_calls', False), ('model_calls', 0.0),
    ('evaluation_performed', 0), ('independent_human', 0),
    ('source.strict_issue_time_verified', 0),
    ('hypotheses.0.model_review.independent_human', 0),
    ('evidence.0.text_truncated', 0),
])
def test_resealed_equal_python_values_cannot_change_json_types(report, field, value):
    result = build_hypothesis_review(report)
    parts = field.split('.')
    target = result
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    target[parts[-1]] = value
    seal(result)
    with pytest.raises(ValueError):
        validate_hypothesis_review(result, report)
    with pytest.raises(ValueError):
        render_markdown(result)


def test_five_hypotheses_include_all_ten_unordered_pairs(comparison_report):
    for suffix in ('fourth', 'fifth'):
        hypothesis = deepcopy(comparison_report['hypotheses'][0])
        hypothesis['id'] = suffix
        comparison_report['hypotheses'].append(hypothesis)
        check = next(row for row in comparison_report['next_checks']
                     if row['text'] == hypothesis['falsification_test'])
        check['hypothesis_ids'].append(suffix)
    seal(comparison_report)
    result = build_hypothesis_review(comparison_report)
    ids = [row['id'] for row in result['hypotheses']]
    assert [(row['left_hypothesis_id'], row['right_hypothesis_id']) for row in result['pairs']] == [
        (left, right) for i, left in enumerate(ids) for right in ids[i + 1:]]
    assert len(result['pairs']) == 10
    assert validate_hypothesis_review(result, comparison_report) is result


@pytest.mark.parametrize('mutation', ['duplicate_hypothesis', 'duplicate_evidence', 'duplicate_check',
                                     'duplicate_citation', 'check_reference', 'model_reference',
                                     'counter_status', 'invented_model_evidence'])
def test_standalone_exports_preserve_reference_closure(comparison_report, mutation):
    result = build_hypothesis_review(comparison_report)
    if mutation == 'duplicate_hypothesis':
        result['hypotheses'].append(deepcopy(result['hypotheses'][0]))
    elif mutation == 'duplicate_evidence':
        result['evidence'].append(deepcopy(result['evidence'][0]))
    elif mutation == 'duplicate_check':
        result['checks'].append(deepcopy(result['checks'][0]))
    elif mutation == 'duplicate_citation':
        result['hypotheses'][0]['support_ids'] *= 2
    elif mutation == 'check_reference':
        result['checks'][0]['hypothesis_ids'] = ['missing']
    elif mutation == 'model_reference':
        result['hypotheses'][0]['model_review']['evidence_ids'] = ['missing']
    elif mutation == 'counter_status':
        result['hypotheses'][0]['counter_status'] = 'not_found'
    else:
        result['hypotheses'][0]['model_review'].update(assessment=None, reason=None)
    seal(result)
    with pytest.raises(ValueError):
        render_markdown(result)


def test_markdown_keeps_all_untrusted_fields_inert_and_links_only_pinned_sources(comparison_report):
    payload = 'https://evil.invalid www.evil.invalid\n# title\n```sh\n@team <img src=x> | [x](https://evil.invalid)'
    hypothesis = comparison_report['hypotheses'][0]
    hypothesis['statement'] = payload
    hypothesis['verification']['model_reason'] = payload
    old_check = hypothesis['falsification_test']
    for row in comparison_report['hypotheses']:
        if row['falsification_test'] == old_check:
            row['falsification_test'] = payload
    next(row for row in comparison_report['next_checks'] if row['text'] == old_check)['text'] = payload
    code = next(row for row in comparison_report['evidence'] if row['kind'] == 'code')
    # Keep a valid saved excerpt with the same number of code lines. This is a
    # synthetic report, not a mutation of the source repository or a real pack.
    code['text'] = '<script> https://evil.invalid @team\n# comment'
    code['sha256'] = code['excerpt_sha256'] = sha256(code['text'].encode()).hexdigest()
    seal(comparison_report)
    review = build_hypothesis_review(comparison_report)
    markdown = render_markdown(review)
    assert 'https://evil.invalid' not in markdown
    assert 'www.evil.invalid' not in markdown
    assert '@team' not in markdown and '<img' not in markdown and '<script>' not in markdown
    assert '> \\# title' in markdown and '> \\`\\`\\`sh' in markdown
    assert '&lt;img src=x&gt;' in markdown and '&lt;script&gt;' in markdown
    assert '](' + code['url'] + ')' in markdown
    assert '/blob/' + review['source']['commit_sha'] + '/parser.py#' in markdown
