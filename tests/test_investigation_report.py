from copy import deepcopy
from hashlib import sha256
import json

import pytest

from issueintel.investigation.pipeline import investigate
from issueintel.investigation.report import (
    COMPACT_LIMIT, DESCRIPTION_LIMIT, EVIDENCE_LIMIT, REPORT_SCHEMA,
    build_report, render_markdown, validate_report,
)
from issueintel.runtime.io import canonical_json, digest
from test_pipeline import repository, case
from test_focused_retrieval import FakeModel


def hashed(text):
    return sha256(text.encode()).hexdigest()


def project(pack, **kwargs):
    return build_report(pack, source_pack_sha256=hashed(json.dumps(pack)), **kwargs)


def repaired(report):
    report['sha256'] = digest({key: value for key, value in report.items() if key != 'sha256'})
    return report


@pytest.fixture
def pack(repository):
    return investigate(case(repository), repository[0])


@pytest.fixture
def generated(repository):
    return investigate(case(repository), repository[0], complete_json=FakeModel(),
                       provider='synthetic', model='fixture')


def test_projection_deterministic_bound_and_immutable(pack):
    before = deepcopy(pack)
    report = project(pack, job_id='a' * 24)
    assert report == project(pack, job_id='a' * 24)
    assert pack == before
    assert validate_report(report) is report
    assert report['schema'] == 'investigation_report_v1'
    assert report['source'] == {
        'job_id': 'a' * 24, 'run_id': pack['run_id'], 'pack_sha256': hashed(json.dumps(pack)),
        'pack_hash_kind': 'serialized_pack_bytes', 'query_sha256': hashed(pack['case']['problem_statement']),
        'commit_sha': pack['snapshot']['commit_sha'], 'snapshot_sha256': pack['snapshot']['snapshot_sha256'],
        'protocol': 'benchmark_base', 'strict_issue_time_verified': False,
    }
    assert report['summary'] == {'text': pack['case']['problem_statement'], 'kind': 'issue_excerpt',
        'evidence_ids': [pack['evidence'][0]['id']], 'truncated': False}
    assert all(row['url'] is None for row in report['evidence'])
    assert all(row['execution_status'] == 'not_executed_by_issueintel' for row in report['next_checks'])
    assert report['findings'] == {'status': 'evidence_only', 'causal_status': 'unverified',
        'severity': 'unassessed', 'confidence_probability': None, 'confidence_method': None}


def test_numeric_confidence_and_severity_in_pack_never_promoted(pack):
    pack['confidence'] = {'probability': .86, 'method': 'unsupported'}
    pack['severity'] = 'critical'
    findings = project(pack)['findings']
    assert findings['confidence_probability'] is findings['confidence_method'] is None
    assert findings['severity'] == 'unassessed'


def test_model_review_separate_from_semantic_causal_and_human(generated):
    for review in generated['verification']['reviews']:
        review['assessment'] = 'supported'
    report = project(generated)
    assert report['findings']['status'] == 'hypotheses_unverified'
    assert report['abstention']['source'] == 'model_annotation_unverified'
    for row in report['hypotheses']:
        assert row['verification']['model_assessment'] == 'supported'
        assert row['verification']['citation_integrity'] == 'valid'
        assert row['verification']['semantic_support'] == 'unverified'
        assert row['verification']['independent_human'] is False
        check = next(check for check in report['next_checks'] if row['id'] in check['hypothesis_ids'])
        assert check['text'] == row['falsification_test']
        assert check['evidence_ids'] == list(dict.fromkeys(row['support_ids'] + row['counter_ids']))
    markdown = render_markdown(report)
    assert 'Model assessment: supported (unverified)' in markdown
    assert 'Citation integrity: valid. Semantic support: unverified. Independent human: no.' in markdown
    assert 'not found in the retrieved scope' in markdown
    assert '## Next checks' in markdown


def test_no_saved_critic_does_not_invent_one(generated):
    generated['verification'] = None
    report = project(generated)
    assert all(row['verification']['model_assessment'] is None for row in report['hypotheses'])
    assert 'Model assessment:' not in render_markdown(report)


def test_abstain_is_saved_model_answerability_not_root_cause(generated):
    generated['annotation']['abstention']['label'] = 'abstain'
    report = project(generated)
    assert report['findings']['status'] == 'abstained'
    assert report['findings']['causal_status'] == 'unverified'
    assert 'model_answerability_abstain' in report['unknowns']


@pytest.mark.parametrize('mutation', ['missing_evidence', 'wrong_snapshot', 'wrong_issue', 'bad_hash',
    'bad_locator', 'zero_line', 'reversed_lines', 'span_mismatch', 'absent_path', 'traversal'])
def test_corrupted_pack_denied_before_projection(pack, mutation):
    code = next(row for row in pack['evidence'] if row['kind'] == 'code')
    if mutation == 'missing_evidence': pack['evidence'] = pack['evidence'][1:]
    elif mutation == 'wrong_snapshot': code['provenance']['snapshot_sha256'] = '0' * 64
    elif mutation == 'wrong_issue': pack['case']['problem_statement'] = 'Changed query'
    elif mutation == 'bad_hash': code['sha256'] = '0' * 64
    elif mutation == 'bad_locator': code['locator'] = 'parser.py'
    elif mutation == 'zero_line': code['locator'] = 'parser.py:0-99999'
    elif mutation == 'reversed_lines': code['locator'] = 'parser.py:3-1'
    elif mutation == 'span_mismatch': code['locator'] = 'parser.py:1-99999'
    elif mutation == 'absent_path': code['locator'] = 'missing.py:1-' + str(len(code['text'].split('\n')))
    else: code['locator'] = '../parser.py:1-2'
    with pytest.raises(ValueError):
        project(pack)


@pytest.mark.parametrize('mutation', ['citation', 'critic_citation', 'critic_count', 'critic_kind', 'human', 'counter', 'support_overlap'])
def test_invalid_annotation_or_critic_denied(generated, mutation):
    hypothesis = generated['annotation']['hypotheses'][0]
    if mutation == 'citation': hypothesis['support_ids'] = ['missing']
    elif mutation == 'critic_citation': generated['verification']['reviews'][0]['evidence_ids'] = ['missing']
    elif mutation == 'critic_count': generated['verification']['reviews'] = []
    elif mutation == 'critic_kind': generated['verification']['kind'] = 'made_up'
    elif mutation == 'human': generated['verification']['independent_human'] = True
    elif mutation == 'counter': hypothesis['counter_status'] = 'found'
    else:
        hypothesis['counter_ids'] = list(hypothesis['support_ids'])
        hypothesis['counter_status'] = 'found'
    with pytest.raises(ValueError):
        project(generated)


@pytest.mark.parametrize('field,value', [('source_pack_sha256', 'bad'), ('source_pack_sha256', None),
    ('job_id', 'bad'), ('job_id', 'A' * 24), ('source_hash_kind', 'unknown')])
def test_source_binding_types_fail_closed(pack, field, value):
    values = {'source_pack_sha256': hashed(json.dumps(pack)), field: value}
    with pytest.raises(ValueError):
        build_report(pack, **values)


def test_canonical_hash_kind_and_byte_hash_are_not_conflated(pack):
    report = build_report(pack, source_pack_sha256=digest(pack), source_hash_kind='canonical_pack_json')
    assert 'Canonical pack JSON SHA256:' in render_markdown(report)
    assert 'Source pack bytes SHA256:' not in render_markdown(report)
    assert 'Source pack bytes SHA256:' in render_markdown(project(pack))


def test_bounded_excerpts_keep_full_hash_and_separate_excerpt_hash(pack):
    text = 'Observed parser issue. ' * 150
    pack['case']['problem_statement'] = text
    issue = next(row for row in pack['evidence'] if row['kind'] == 'issue')
    issue.update(text=text, sha256=hashed(text))
    report = project(pack)
    row = next(row for row in report['evidence'] if row['kind'] == 'issue')
    assert report['summary']['text'] == text[:DESCRIPTION_LIMIT]
    assert report['summary']['truncated'] is report['issue']['description_truncated'] is True
    assert row['text'] == text[:EVIDENCE_LIMIT] and row['text_truncated'] is True
    assert row['sha256'] == hashed(text)
    assert row['excerpt_sha256'] == hashed(text[:EVIDENCE_LIMIT]) != row['sha256']
    assert 'excerpt truncated' in render_markdown(report)


def github_pack(pack):
    pack['case'].update(repository='Owner/Repo', issue_url='https://github.com/Owner/Repo/issues/4')
    return pack


def test_safe_exact_revision_github_links_encode_code_path(pack):
    github_pack(pack)
    code = next(row for row in pack['evidence'] if row['kind'] == 'code')
    original = code['locator'].split(':')[0]
    path = 'src/a space[1]#日本.py'
    code['locator'] = code['locator'].replace(original, path)
    next(row for row in pack['snapshot']['files'] if row['path'] == original)['path'] = path
    report = project(pack)
    projected = next(row for row in report['evidence'] if row['id'] == code['id'])
    assert f"/blob/{pack['snapshot']['commit_sha']}/src/a%20space%5B1%5D%23%E6%97%A5%E6%9C%AC.py#L" in projected['url']
    assert report['issue']['issue_url'] == 'https://github.com/Owner/Repo/issues/4'


@pytest.mark.parametrize('url', ['http://github.com/Owner/Repo/issues/4',
    'https://github.com.evil/Owner/Repo/issues/4', 'https://user@github.com/Owner/Repo/issues/4',
    'https://github.com/Owner/Repo/issues/4?secret=1', 'https://github.com/Owner/Repo/issues/4#x'])
def test_noncanonical_issue_never_creates_github_links(pack, url):
    pack['case'].update(repository='Owner/Repo', issue_url=url)
    report = project(pack)
    assert report['issue']['issue_url'] is None
    assert all(row['url'] is None for row in report['evidence'])


def test_mismatched_issue_repository_denied(pack):
    pack['case'].update(repository='Other/Repo', issue_url='https://github.com/Owner/Repo/issues/4')
    with pytest.raises(ValueError, match='identities'):
        project(pack)


@pytest.mark.parametrize('mutation', ['hash', 'extra_field', 'confidence', 'causal', 'human', 'semantic',
    'summary', 'summary_ref', 'evidence_excerpt', 'url', 'revision', 'candidate', 'check_ref', 'check_text',
    'check_citations', 'missing_check', 'finding', 'unknowns', 'wrong_protocol'])
def test_rehashed_report_tampering_rejected(generated, mutation):
    github_pack(generated)
    report = project(generated)
    if mutation == 'hash': report['sha256'] = '0' * 64
    elif mutation == 'extra_field': report['unreviewed_field'] = True
    elif mutation == 'confidence': report['findings']['confidence_probability'] = .86
    elif mutation == 'causal': report['findings']['causal_status'] = 'verified'
    elif mutation == 'human': report['hypotheses'][0]['verification']['independent_human'] = True
    elif mutation == 'semantic': report['hypotheses'][0]['verification']['semantic_support'] = 'verified'
    elif mutation == 'summary': report['summary']['text'] = 'Invented conclusion'
    elif mutation == 'summary_ref': report['summary']['evidence_ids'] = [report['evidence'][1]['id']]
    elif mutation == 'evidence_excerpt': report['evidence'][0]['text'] = 'Changed'
    elif mutation == 'url': report['evidence'][1]['url'] = 'https://evil.test/payload'
    elif mutation == 'revision': report['evidence'][1]['revision'] = '0' * 40
    elif mutation == 'candidate': report['candidate_locations'][0]['end_line'] += 1
    elif mutation == 'check_ref': report['next_checks'][0]['hypothesis_ids'] = ['missing']
    elif mutation == 'check_text': report['next_checks'][0]['text'] = 'Different action'
    elif mutation == 'check_citations': report['next_checks'][0]['evidence_ids'] = []
    elif mutation == 'missing_check': report['next_checks'] = []
    elif mutation == 'finding': report['findings']['status'] = 'evidence_only'
    elif mutation == 'unknowns': report['unknowns'] = []
    else: report['source']['protocol'] = 'claimed_historical'
    if mutation != 'hash': repaired(report)
    with pytest.raises(ValueError):
        validate_report(report)
    with pytest.raises(ValueError):
        render_markdown(report)


def test_source_markdown_is_literal_and_mentions_neutralized(generated):
    hostile = '# Fake conclusion\n![secret](https://evil.test/image)\n<script>alert(1)</script>\n@everyone **verified**'
    generated['annotation']['hypotheses'][0]['statement'] = hostile
    generated['annotation']['hypotheses'][0]['falsification_test'] = hostile
    generated['recommended_steps'] = [hostile]
    generated['verification']['reviews'][0]['reason'] = hostile
    report = project(generated)
    markdown = render_markdown(report)
    assert '\n# Fake conclusion' not in markdown
    assert '![secret]' not in markdown
    assert '<script>' not in markdown
    assert '@everyone' not in markdown and '@\u200beveryone' in markdown
    assert '\\# Fake conclusion' in markdown and '&lt;script&gt;' in markdown
    assert report['hypotheses'][0]['statement'] == hostile


def test_compact_report_keeps_unknowns_and_refs_and_shortens_long_text(generated):
    report = project(generated)
    body = render_markdown(report, compact=True)
    assert len(body) <= COMPACT_LIMIT
    assert '## Next checks' in body and '## Limits' in body and '## Source binding' in body
    assert 'not executed by IssueIntel' in body
    generated['annotation']['hypotheses'][0]['statement'] = 'a' * 18000
    long_report = project(generated)
    compact = render_markdown(long_report, compact=True)
    assert len(compact) <= COMPACT_LIMIT
    assert 'verbatim excerpt truncated' in compact
    assert hashed('a' * 18000) in compact
    assert long_report['sha256'] in compact


def test_valid_unbounded_annotation_text_is_preserved_in_full_reports(generated):
    from jsonschema import Draft202012Validator
    statement = 'Distinct observed mechanism with details.\n' * 1400
    reason = 'Automatic reviewer reasoning remains unverified.\n' * 1300
    check = 'Check this proposed input without claiming execution.\n' * 1200
    generated['annotation']['hypotheses'][0].update(statement=statement, falsification_test=check)
    generated['verification']['reviews'][0]['reason'] = reason
    generated['annotation']['abstention']['reason'] = reason
    generated['recommended_steps'][0] = check
    report = project(generated)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert report['hypotheses'][0]['statement'] == statement
    assert report['hypotheses'][0]['verification']['model_reason'] == reason
    assert report['hypotheses'][0]['falsification_test'] == check
    assert report['abstention']['reason'] == reason
    assert any(row['text'] == check for row in report['next_checks'])
    full = render_markdown(report)
    assert 'verbatim excerpt truncated' not in full
    assert full.count('Distinct observed mechanism with details') == 1400
    compact = render_markdown(report, compact=True)
    assert len(compact) <= COMPACT_LIMIT
    assert 'verbatim excerpt truncated' in compact
    assert all(hashed(value) in compact for value in (statement, reason, check))
    assert report['sha256'] in compact and report['source']['pack_sha256'] in compact
    assert 'not executed by IssueIntel' in compact


def test_compact_many_long_checks_explicitly_omits_collection_detail(pack):
    pack['recommended_steps'] = [f'Proposed check {i}: ' + 'detail ' * 5000 for i in range(30)]
    report = project(pack)
    assert report['next_checks'][29]['text'] == pack['recommended_steps'][29]
    compact = render_markdown(report, compact=True)
    assert len(compact) <= COMPACT_LIMIT
    assert 'Additional proposed checks omitted: 20' in compact
    assert digest(report['next_checks']) in compact
    assert report['sha256'] in compact


def test_compact_detail_omission_explicit_and_preserves_full_binding(pack):
    code = next(row for row in pack['evidence'] if row['kind'] == 'code')
    # Many distinct spans are synthetic; hashes still bind each exact excerpt.
    for index in range(120):
        row = deepcopy(code)
        row['id'] = f'e_synthetic_{index}'
        start = index * 10 + 10
        row['locator'] = 'parser.py:' + str(start) + '-' + str(start + len(row['text'].split('\n')) - 1)
        pack['evidence'].append(row)
    report = project(pack)
    compact = render_markdown(report, compact=True)
    assert len(compact) <= COMPACT_LIMIT
    assert '**Presentation truncated:**' in compact
    assert report['sha256'] in compact and report['source']['pack_sha256'] in compact


def test_legacy_optional_product_fields_not_required(pack):
    for key in ('intake', 'refinement', 'stage_checkpoints', 'confidence'):
        pack.pop(key, None)
    report = project(pack)
    assert report['provenance'] == []
    assert 'IssueIntel investigation' in render_markdown(report)


def test_schema_is_json_serializable_and_declares_all_projection_fields(pack):
    report = project(pack)
    schema = json.loads(json.dumps(REPORT_SCHEMA))
    assert schema['$schema'].endswith('2020-12/schema')
    assert set(schema['required']) == set(report)
    assert set(schema['properties']['source']['required']) == set(report['source'])
    assert canonical_json(REPORT_SCHEMA) == canonical_json(schema)


def test_public_schema_conforms_to_draft_2020_and_validates_projection(pack, generated):
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(REPORT_SCHEMA)
    validator = Draft202012Validator(REPORT_SCHEMA)
    validator.validate(project(pack))
    validator.validate(project(generated))
    invalid = project(pack)
    invalid['findings']['confidence_probability'] = .86
    assert list(validator.iter_errors(invalid))
