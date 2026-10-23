"""Pure synthetic proposal reviews; no Git, dataset, credentials or real model."""
from copy import deepcopy
import csv
import hashlib
import io
import json

import pytest

from issueintel.evaluation.usefulness import (
    DIMENSIONS, LIMITATIONS, RATING_FIELDS, build_review, rating_receipt,
    score_reviews, validate_rating_receipt, validate_review, validate_selection,
)
from issueintel.investigation import adaptive_clarifications, check_plans
from issueintel.runtime.io import digest


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def raw_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def rehash(value):
    value['sha256'] = digest({key: row for key, row in value.items() if key != 'sha256'})
    return value


def product_pack():
    query = 'parser.parse_list crashes for a missing item'
    code = 'def parse_list(items):\n    return [item.strip() for item in items]'
    hypotheses = [
        {'id': 'h1', 'statement': 'A None item may reach strip without validation',
         'support_ids': ['code_1'], 'counter_ids': [], 'counter_status': 'not_found',
         'falsification_test': 'Compare a None item with an empty string'},
        {'id': 'h2', 'statement': 'The iterable itself may be None',
         'support_ids': ['code_1'], 'counter_ids': [], 'counter_status': 'not_found',
         'falsification_test': 'Compare None with a list containing None'},
        {'id': 'h3', 'statement': 'A numeric item may enter a text-only boundary',
         'support_ids': ['code_1'], 'counter_ids': [], 'counter_status': 'not_found',
         'falsification_test': 'Compare a numeric item with text'},
    ]
    provenance = {'kind': 'synthetic', 'method': 'offline_fixture'}
    return {
        'run_id': '9' * 24, 'stop_reason': 'fixture_complete',
        'case': {'case_id': 'local:synthetic', 'repository': r'C:\private\synthetic-repo',
                 'revision': 'HEAD', 'problem_statement': query},
        'snapshot': {'commit_sha': 'a' * 40, 'snapshot_sha256': 'b' * 64,
                     'protocol': 'benchmark_base', 'cutoff': None, 'availability': {},
                     'files': [{'path': 'parser.py'}]},
        'evidence': [
            {'id': 'issue_1', 'kind': 'issue', 'locator': 'local:synthetic',
             'text': query, 'sha256': raw_sha(query.encode()), 'revision': None,
             'available_at': None, 'provenance': {'kind': 'input',
                 'local_receipt': r'C:\private\receipt.json'}},
            {'id': 'code_1', 'kind': 'code', 'locator': 'parser.py:1-2',
             'text': code, 'sha256': raw_sha(code.encode()), 'revision': 'a' * 40,
             'available_at': None, 'provenance': {'kind': 'git_blob',
                 'snapshot_sha256': 'b' * 64, 'local_receipt': r'C:\private\git.json'}},
        ],
        'annotation': {'case_id': 'local:synthetic', 'hypotheses': hypotheses,
                       'evidence_ids': ['issue_1', 'code_1'],
                       'abstention': {'label': 'uncertain', 'reason': 'Not executed',
                                      'provenance': provenance},
                       'provenance': provenance, 'source_quality': {}},
        'leakage_audit': {'strict_issue_time_verified': False},
        'trace': [{'stage': 'local_only', 'path': r'C:\private\stage.json'}],
    }


def question():
    return {'id': 'probe:q1',
            'prompts': {'en': 'Does [None] fail differently from None itself?',
                        'vi': '[None] có lỗi khác đầu vào None không?'},
            'purpose': {'en': 'Distinguish the invalid item and invalid iterable.',
                        'vi': 'Phân biệt phần tử và iterable không hợp lệ.'},
            'answer_use': {'en': 'Inspect the relevant boundary; unknown leaves both hypotheses open.',
                           'vi': 'Xem biên tương ứng; chưa biết thì giữ cả hai giả thuyết.'},
            'hypothesis_ids': ['h1', 'h2'], 'evidence_ids': ['code_1']}


def check():
    return {'id': 'check_1', 'procedure': 'Compare [None] and None with unchanged controls.',
            'assumptions': 'Use the saved revision and hold other inputs constant.',
            'outcome_a': 'Failure occurs during item.strip.',
            'outcome_b': 'Failure occurs before iterating over items.', 'effort': 'low',
            'evidence_ids': ['code_1'], 'predictions': [
                {'hypothesis_id': identifier, 'outcome': outcome,
                 'reason': 'An unverified prediction from the supplied source.'}
                for identifier, outcome in (('h1', 'A'), ('h2', 'B'), ('h3', 'unknown'))]}


def selected_inputs():
    """Reusable root I/O fixture: five frozen slots across two conditions."""
    pack = product_pack()
    pack_path = 'artifacts/demo/runs/' + '1' * 24 + '/pack.json'
    raw_pack = encoded(pack)
    pack_ref = {'path': pack_path, 'sha256': raw_sha(raw_pack)}
    source_id = '1' * 24
    question_source = adaptive_clarifications.build_source(pack, source_id, pack_ref['sha256'])
    question_bundle = adaptive_clarifications.generate_questions(
        question_source, lambda *_a, **_kw: {'questions': [question()]}, model=adaptive_clarifications.MODEL)
    plan_source = check_plans.build_source(pack, source_id, pack_ref['sha256'])
    plan = check_plans.generate_plan(plan_source, lambda *_a, **_kw: {'checks': [check()]}, model=check_plans.MODEL)
    empty = check_plans.generate_plan(plan_source, lambda *_a, **_kw: {'checks': []}, model=check_plans.MODEL)
    inputs = {pack_path: raw_pack}
    slots = []
    for unit, condition, kind, status, bundle in (
        ('one', 'treatment', 'question', 'completed', question_bundle),
        ('one', 'treatment', 'check', 'completed', plan),
        ('two', 'treatment', 'check', 'empty', empty),
        ('three', 'treatment', 'question', 'failed', None),
        ('four', 'control', 'check', 'unavailable', None),
    ):
        artifact = None
        if bundle is not None:
            path = f'artifacts/synthetic/{unit}-{kind}.json'
            raw = encoded(bundle)
            inputs[path] = raw
            artifact = {'path': path, 'sha256': raw_sha(raw)}
        slots.append({'unit_id': unit, 'condition_id': condition, 'kind': kind, 'status': status,
                      'reason': '' if status == 'completed' else 'Synthetic ' + status,
                      'source_job_id': source_id, 'pack': deepcopy(pack_ref), 'artifact': artifact})
    return {'schema': 'usefulness_selection_v1', 'purpose': 'Test an offline declared review',
            'protocol_id': 'synthetic-v1', 'language': 'en', 'slots': slots}, inputs


def prepared_review(selection=None, inputs=None):
    if selection is None:
        selection, inputs = selected_inputs()
    identifiers = [f'{index:024x}' for index in range(1, len(selection['slots']) + 1)]
    return build_review(selection, inputs, review_id='a' * 32,
                        blind_ids=identifiers, order=list(reversed(identifiers)))


def reviewer(**changes):
    return {'schema': 'usefulness_reviewer_v1', 'reviewer_id': 'synthetic-reviewer',
            'kind': 'agent', 'assistance': 'none', 'relationship': 'author',
            'model_id': 'synthetic-fixture', 'reviewed_at': '2026-10-24T00:00:00Z',
            'declaration': 'Synthetic agent labels, not human validation.', **changes}


def reviewer_bytes(**changes):
    return encoded(reviewer(**changes))


def ratings_csv(review, *, label='', by_id=None, rationale='Synthetic review rationale'):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=RATING_FIELDS)
    writer.writeheader()
    lookup = {row['blind_id']: row for row in review['organizer']['slots']}
    for identifier in review['organizer']['order']:
        mapping = lookup[identifier]
        values = {field: label if mapping['status'] == 'completed' else '' for field in DIMENSIONS}
        row = {'blind_id': identifier, 'display_sha256': mapping['display_sha256'],
               **values, 'rationale': rationale if any(values.values()) else ''}
        row.update((by_id or {}).get(identifier, {}))
        writer.writerow(row)
    return out.getvalue().encode('utf-8')


def receipt(review, *, label='', who=None, parent=None, reason='', by_id=None):
    return rating_receipt(review['organizer'], review['display'],
                          ratings_csv(review, label=label, by_id=by_id),
                          reviewer_bytes(**(who or {})), supersedes=parent, correction_reason=reason)


def first_scope(report, condition='treatment', kind='check', reviewer_id='synthetic-reviewer'):
    return next(group for group in report['groups'] if group['condition_id'] == condition
                and group['kind'] == kind and group['reviewer']['reviewer_id'] == reviewer_id)


def test_build_native_whole_bundles_freezes_context_status_and_private_mapping_without_side_effects():
    selection, inputs = selected_inputs()
    original = deepcopy(selection), dict(inputs)
    review = prepared_review(selection, inputs)
    organizer, display = review['organizer'], review['display']
    assert organizer['selection'] == selection
    assert len(display['slots']) == 5
    assert organizer['slots'][0]['blind_id'] == '0' * 23 + '1'
    assert display['slots'][0]['blind_id'] == '0' * 23 + '5'
    assert all(digest(card) == next(row['display_sha256'] for row in organizer['slots']
               if row['blind_id'] == card['blind_id']) for card in display['slots'])
    text = json.dumps(display, ensure_ascii=False)
    for hidden in ('C:\\', 'private', 'local_receipt', 'source_job_id', 'condition_id',
                   'groq', 'gpt-oss', 'recommended_check_id', 'effort', 'annotation_sha256'):
        assert hidden not in text
    assert display['slots'][-1]['context']['repository'] == 'local_repository'
    assert display['slots'][-1]['context']['evidence'][1]['text'] == product_pack()['evidence'][1]['text']
    assert 'HEAD' in text and 'None' in text
    assert selection == original[0] and inputs == original[1]
    assert validate_review(organizer, display) == review


def test_selected_language_retains_exact_question_without_model_translation():
    selection, inputs = selected_inputs()
    selection['language'] = 'vi'
    review = prepared_review(selection, inputs)
    card = next(row for row in review['display']['slots'] if row['kind'] == 'question' and row['status'] == 'completed')
    assert card['proposals'][0]['prompt'] == question()['prompts']['vi']
    assert card['proposals'][0]['purpose'] == question()['purpose']['vi']
    assert review['display']['rubric']['dimensions'][0]['prompt'].startswith('Các đề xuất')


def test_private_paths_and_model_metadata_are_excluded_but_original_text_not_silently_redacted():
    selection, inputs = selected_inputs()
    review = prepared_review(selection, inputs)
    check_card = next(row for row in review['display']['slots'] if row['kind'] == 'check' and row['status'] == 'completed')
    assert set(check_card['proposals'][0]) == {'id', 'procedure', 'assumptions', 'outcome_a', 'outcome_b',
                                              'evidence_ids', 'predictions'}
    assert check_card['proposals'][0]['predictions'] == check()['predictions']
    assert len(check_card['context']['hypotheses']) == 3
    assert any('recognizable source wording' in limit for limit in review['display']['limitations'])


@pytest.mark.parametrize('change', [
    lambda value: value.update(extra=True),
    lambda value: value.update(schema='different'),
    lambda value: value.update(language='fr'),
    lambda value: value.update(protocol_id=''),
    lambda value: value.update(purpose=' '),
    lambda value: value.update(slots=[]),
    lambda value: value['slots'].append(deepcopy(value['slots'][0])),
    lambda value: value['slots'][0].update(kind='unknown'),
    lambda value: value['slots'][0].update(status='unknown'),
    lambda value: value['slots'][0].update(source_job_id='not-a-job'),
    lambda value: value['slots'][0].update(artifact=None),
    lambda value: value['slots'][0].update(status='failed'),
    lambda value: value['slots'][0].update(status='empty', reason='empty'),
    lambda value: value['slots'][2].update(reason=''),
    lambda value: value['slots'][0]['pack'].update(path='../outside.json'),
    lambda value: value['slots'][0]['pack'].update(path='data/benchmark/gold.json'),
    lambda value: value['slots'][0]['pack'].update(path='artifacts/a/../pack.json'),
    lambda value: value['slots'][0]['pack'].update(path='artifacts\\pack.json'),
    lambda value: value['slots'][0]['pack'].update(path='C:/artifacts/pack.json'),
    lambda value: value['slots'][0]['pack'].update(path='artifacts//pack.json'),
    lambda value: value['slots'][0]['pack'].update(sha256='wrong'),
])
def test_selection_rejects_ambiguous_scope_or_paths_before_loading_sources(change):
    selection, _ = selected_inputs()
    change(selection)
    with pytest.raises(ValueError):
        validate_selection(selection)


def test_same_path_cannot_assert_different_raw_hashes():
    selection, _ = selected_inputs()
    selection['slots'][0]['pack']['sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='conflicting'):
        validate_selection(selection)


@pytest.mark.parametrize('change', [
    lambda inputs: inputs.update({'artifacts/extra.json': b'{}'}),
    lambda inputs: inputs.pop(next(iter(inputs))),
    lambda inputs: inputs.update({next(iter(inputs)): inputs[next(iter(inputs))] + b'\n'}),
    lambda inputs: inputs.update({next(iter(inputs)): '{}'}),
])
def test_inputs_are_exact_selected_raw_bytes_without_discovery(change):
    selection, inputs = selected_inputs()
    change(inputs)
    with pytest.raises(ValueError):
        prepared_review(selection, inputs)


@pytest.mark.parametrize('change', [
    lambda pack: pack['case'].update(split='main_dev'),
    lambda pack: pack['case'].update(split=None),
    lambda pack: pack['case'].update(instance_id='synthetic__benchmark-1'),
    lambda pack: pack['case'].update(instance_id=None),
    lambda pack: pack['case'].update(case_id='verified:synthetic__benchmark-1'),
    lambda pack: pack['case'].update(case_id='live:synthetic__benchmark-1'),
    lambda pack: pack['case'].update(case_id='main_dev:synthetic__benchmark-1'),
    lambda pack: pack['case'].update(case_id='main_test:synthetic__benchmark-1'),
    lambda pack: pack['case'].update(case_id='temporal_candidate:synthetic__benchmark-1'),
    lambda pack: pack['snapshot'].update(protocol='issue_time'),
    lambda pack: pack['leakage_audit'].update(strict_issue_time_verified=True),
    lambda pack: pack['leakage_audit'].update(snapshot_issue_time_verified=True),
    lambda pack: pack['leakage_audit'].update(original_query_verified=True),
    lambda pack: pack['case'].update(patch='restricted'),
    lambda pack: pack['evidence'][0].update(human_rationale='restricted'),
    lambda pack: pack['annotation'].update(source_quality={'source_human': 'restricted'}),
    lambda pack: pack['evidence'][1].update(text='tampered'),
    lambda pack: pack['evidence'][1].update(locator=r'C:\private\parser.py:1-2'),
    lambda pack: pack['annotation']['hypotheses'][0].update(statement='x' * 8001),
])
def test_failed_slots_do_not_bypass_product_or_restricted_source_guards(change):
    selection, inputs = selected_inputs()
    selection['slots'] = [selection['slots'][3]]
    pack = product_pack()
    change(pack)
    raw = encoded(pack)
    selection['slots'][0]['pack']['sha256'] = raw_sha(raw)
    inputs = {selection['slots'][0]['pack']['path']: raw}
    with pytest.raises(ValueError):
        prepared_review(selection, inputs)


def test_failed_product_without_annotation_remains_in_selected_denominator():
    selection, _ = selected_inputs()
    selection['slots'] = [selection['slots'][3]]
    pack = product_pack()
    pack['annotation'] = None
    raw = encoded(pack)
    selection['slots'][0]['pack']['sha256'] = raw_sha(raw)
    review = prepared_review(selection, {selection['slots'][0]['pack']['path']: raw})
    assert review['display']['slots'][0]['context']['hypotheses'] == []
    report = score_reviews(review['organizer'], review['display'], [])
    assert report['coverage'][0] == {'condition_id': 'treatment', 'kind': 'question',
                                   'selected': 1, 'completed': 0, 'empty': 0, 'failed': 1, 'unavailable': 0}


def test_product_followup_identity_is_retained_without_claiming_ancestry_authenticity():
    selection, _ = selected_inputs()
    selection['slots'] = [selection['slots'][3]]
    pack = product_pack()
    pack['case']['case_id'] = 'followup:' + '3' * 24
    pack['annotation']['case_id'] = pack['case']['case_id']
    raw = encoded(pack)
    selection['slots'][0]['pack']['sha256'] = raw_sha(raw)
    review = prepared_review(selection, {selection['slots'][0]['pack']['path']: raw})
    assert review['display']['slots'][0]['status'] == 'failed'


@pytest.mark.parametrize('slot_index,new_status', [(1, 'empty'), (2, 'completed')])
def test_native_empty_and_completed_statuses_cannot_be_relabelled(slot_index, new_status):
    selection, inputs = selected_inputs()
    selection['slots'][slot_index].update(status=new_status, reason='Declared status')
    with pytest.raises(ValueError, match='status differs'):
        prepared_review(selection, inputs)


def test_rehashing_native_check_recommendation_cannot_silence_validation():
    selection, inputs = selected_inputs()
    slot = selection['slots'][1]
    artifact = json.loads(inputs[slot['artifact']['path']])
    artifact['comparison']['recommended_check_id'] = None
    rehash(artifact['comparison'])
    rehash(artifact)
    raw = encoded(artifact)
    slot['artifact']['sha256'] = raw_sha(raw)
    inputs[slot['artifact']['path']] = raw
    with pytest.raises(ValueError, match='comparison differs'):
        prepared_review(selection, inputs)


@pytest.mark.parametrize('field,value', [('blind_ids', ['f' * 24] * 5),
                                        ('order', ['f' * 24] * 5),
                                        ('review_id', 'bad')])
def test_blind_identity_and_order_must_be_frozen_unique_permutations(field, value):
    selection, inputs = selected_inputs()
    identifiers = [f'{index:024x}' for index in range(1, 6)]
    kwargs = {'review_id': 'a' * 32, 'blind_ids': identifiers, 'order': identifiers}
    kwargs[field] = value
    with pytest.raises(ValueError):
        build_review(selection, inputs, **kwargs)


def test_review_display_tamper_fails_even_if_outer_display_rehashed():
    review = prepared_review()
    review['display']['slots'][-1]['proposals'][0]['prompt'] = 'Changed question'
    rehash(review['display'])
    review['organizer']['display_sha256'] = review['display']['sha256']
    rehash(review['organizer'])
    with pytest.raises(ValueError, match='mapping or display'):
        validate_review(review['organizer'], review['display'])


def test_receipt_retains_exact_utf8_csv_and_declaration_idempotently_without_timestamp_or_human_claim():
    review = prepared_review()
    csv_bytes = b'\xef\xbb\xbf' + ratings_csv(review, label='yes')
    person = reviewer_bytes(declaration='Nhãn tổng hợp của agent, không phải kiểm người.')
    result = rating_receipt(review['organizer'], review['display'], csv_bytes, person)
    assert result['csv_utf8'].encode() == csv_bytes
    assert result['csv_sha256'] == raw_sha(csv_bytes)
    assert result['reviewer_utf8'].encode() == person
    assert result['reviewer_sha256'] == raw_sha(person)
    assert result == rating_receipt(review['organizer'], review['display'], csv_bytes, person)
    assert result == validate_rating_receipt(review['organizer'], review['display'], result)
    assert result['reviewer']['kind'] == 'agent'
    assert 'created_at' not in result
    assert result['limitations'] == list(LIMITATIONS)


@pytest.mark.parametrize('changes', [
    {'kind': 'unknown'}, {'assistance': 'unknown-ai'}, {'relationship': 'independent_verified'},
    {'model_id': None}, {'reviewer_id': ''}, {'reviewed_at': '2026-10-24'},
    {'reviewed_at': '2026-10-24T00:00:00+07:00'}, {'reviewed_at': '2026-20-24T00:00:00Z'},
    {'declaration': ''}, {'kind': 'human', 'assistance': 'ai', 'model_id': None},
    {'kind': 'human', 'assistance': 'none'},
])
def test_invalid_reviewer_provenance_never_becomes_implicit_human_validation(changes):
    review = prepared_review()
    with pytest.raises(ValueError):
        receipt(review, who=changes)


def test_unassisted_human_declaration_and_ai_assisted_human_remain_separate_unverified_provenance():
    review = prepared_review()
    human = receipt(review, label='yes', who={'reviewer_id': 'human-one', 'kind': 'human',
                    'model_id': None, 'relationship': 'declared_independent'})
    assisted = receipt(review, label='no', who={'reviewer_id': 'human-two', 'kind': 'human',
                       'assistance': 'ai', 'model_id': 'synthetic-assistant'})
    model = receipt(review, label='uncertain', who={'reviewer_id': 'model-one', 'kind': 'model'})
    report = score_reviews(review['organizer'], review['display'], [human, assisted, model])
    assert len(report['groups']) == 9
    assert {row['reviewer']['kind'] for row in report['groups']} == {'human', 'model'}
    assert report['independent_human_validation'] == 'not_established'
    assert report['inter_rater_agreement'] == 'not_computed'
    assert report['causal_efficacy'] == 'not_measured'
    assert first_scope(report, reviewer_id='human-one')['dimensions']['grounding']['known_rate'] == 1
    assert first_scope(report, reviewer_id='human-two')['dimensions']['grounding']['known_rate'] == 0


@pytest.mark.parametrize('changes', [
    {'display_sha256': 'f' * 64}, {'specificity': 'YES'}, {'specificity': ' yes'},
    {'useful_next_action': 'supported'}, {'rationale': ''},
])
def test_invalid_csv_labels_hashes_and_missing_rationale_rejected(changes):
    review = prepared_review()
    identifier = review['organizer']['blind_ids'][0]
    with pytest.raises(ValueError):
        receipt(review, label='yes', by_id={identifier: changes})


@pytest.mark.parametrize('index', [2, 3, 4])
def test_noncompleted_cards_cannot_be_given_quality_labels(index):
    review = prepared_review()
    identifier = review['organizer']['blind_ids'][index]
    with pytest.raises(ValueError, match='Noncompleted'):
        receipt(review, by_id={identifier: {'grounding': 'no', 'rationale': 'Not a proposal'}})


@pytest.mark.parametrize('mode', ['duplicate', 'missing', 'unknown', 'extra_column', 'duplicate_header', 'renamed_header'])
def test_csv_must_have_exact_inventory_and_columns(mode):
    review = prepared_review()
    parsed = list(csv.reader(io.StringIO(ratings_csv(review).decode())))
    if mode == 'duplicate':
        parsed.append(parsed[-1])
    elif mode == 'missing':
        parsed.pop()
    elif mode == 'unknown':
        parsed[-1][0] = 'f' * 24
    elif mode == 'extra_column':
        parsed[-1].append('extra')
    elif mode == 'duplicate_header':
        parsed[0][-1] = parsed[0][-2]
    else:
        parsed[0][0] = 'changed'
    out = io.StringIO(newline='')
    csv.writer(out).writerows(parsed)
    with pytest.raises(ValueError):
        rating_receipt(review['organizer'], review['display'], out.getvalue().encode(), reviewer_bytes())


@pytest.mark.parametrize('change', [
    lambda result: result['rows'][0].update(rationale='tampered'),
    lambda result: result['reviewer'].update(kind='human', model_id=None),
    lambda result: result.update(csv_sha256='f' * 64),
    lambda result: result.update(organizer_sha256='f' * 64),
    lambda result: result.update(limitations=[]),
])
def test_receipt_reconstruction_detects_rehashed_derived_field_changes(change):
    review = prepared_review()
    result = receipt(review, label='yes')
    change(result)
    rehash(result)
    with pytest.raises(ValueError):
        validate_rating_receipt(review['organizer'], review['display'], result)


def test_blank_only_review_preserves_failure_empty_denominators_without_quality_measurement():
    review = prepared_review()
    result = receipt(review)
    report = score_reviews(review['organizer'], review['display'], [result])
    assert report['quality_status'] == 'unreviewed_no_nonblank_ratings'
    check_scope = first_scope(report)
    assert check_scope['coverage'] == {'condition_id': 'treatment', 'kind': 'check',
            'selected': 2, 'completed': 1, 'empty': 1, 'failed': 0, 'unavailable': 0}
    assert check_scope['dimensions']['grounding'] == {'yes': 0, 'no': 0, 'uncertain': 0, 'missing': 1,
            'known_rate': None, 'observed_yield_bounds': {'lower': 0, 'upper': .5, 'denominator': 2}}
    absent = first_scope(report, condition='control')
    assert absent['coverage']['unavailable'] == 1
    assert absent['dimensions']['grounding']['missing'] == 0
    assert absent['dimensions']['grounding']['observed_yield_bounds'] == {'lower': 0, 'upper': 0, 'denominator': 1}


def test_unknown_ratings_remain_distinct_from_blank_and_negative_and_no_automatic_pairing():
    review = prepared_review()
    result = receipt(review, label='uncertain')
    report = score_reviews(review['organizer'], review['display'], [result])
    assert report['quality_status'] == 'uncertain_only_no_binary_judgments'
    values = first_scope(report)['dimensions']['useful_next_action']
    assert values == {'yes': 0, 'no': 0, 'uncertain': 1, 'missing': 0, 'known_rate': None,
                      'observed_yield_bounds': {'lower': 0, 'upper': .5, 'denominator': 2}}
    assert 'paired_contrasts' not in report and 'winner' not in report


def test_success_labels_and_multiple_reviewers_are_not_pooled_as_extra_cases():
    review = prepared_review()
    result = receipt(review, label='yes')
    second = receipt(review, label='no', who={'reviewer_id': 'second-agent'})
    report = score_reviews(review['organizer'], review['display'], [result, second, result])
    assert len(report['active_receipts']) == 2
    assert len(report['groups']) == 6
    assert sum(row['selected'] for row in report['coverage']) == 5
    check_scope = first_scope(report)
    assert check_scope['dimensions']['grounding']['known_rate'] == 1
    assert check_scope['dimensions']['grounding']['observed_yield_bounds'] == {'lower': .5, 'upper': .5, 'denominator': 2}
    assert first_scope(report, reviewer_id='second-agent')['dimensions']['grounding']['known_rate'] == 0


def test_correction_chains_are_order_independent_explicit_and_only_latest_leaf_is_active():
    review = prepared_review()
    original = receipt(review, label='yes')
    corrected = receipt(review, label='no', parent=original['sha256'], reason='Correct a synthetic review error')
    newest = receipt(review, label='uncertain', parent=corrected['sha256'], reason='Keep uncertainty explicit')
    report = score_reviews(review['organizer'], review['display'], [newest, original, corrected, original])
    assert report['active_receipts'] == [{'reviewer_id': 'synthetic-reviewer',
            'receipt_sha256': newest['sha256'],
            'history_sha256': [original['sha256'], corrected['sha256'], newest['sha256']]}]
    assert first_scope(report)['dimensions']['grounding']['uncertain'] == 1
    assert report == score_reviews(review['organizer'], review['display'], [original, corrected, newest])


@pytest.mark.parametrize('mode', ['two_roots', 'branch', 'missing_parent', 'different_reviewer'])
def test_ambiguous_correction_graphs_rejected(mode):
    review = prepared_review()
    original = receipt(review, label='yes')
    corrected = receipt(review, label='no', parent=original['sha256'], reason='Synthetic correction')
    if mode == 'two_roots':
        receipts = [original, receipt(review, label='no')]
    elif mode == 'branch':
        receipts = [original, corrected, receipt(review, label='uncertain', parent=original['sha256'], reason='A branch')]
    elif mode == 'missing_parent':
        receipts = [corrected]
    else:
        receipts = [original, receipt(review, label='no', who={'reviewer_id': 'different-reviewer'},
                                    parent=original['sha256'], reason='Wrong reviewer')]
    with pytest.raises(ValueError):
        score_reviews(review['organizer'], review['display'], receipts)


def test_corrections_require_parent_and_nonblank_reason():
    review = prepared_review()
    with pytest.raises(ValueError, match='requires an explicit'):
        receipt(review, reason='Unbound correction')
    with pytest.raises(ValueError, match='correction reason'):
        receipt(review, parent='a' * 64)


def test_no_receipts_has_coverage_and_no_quality_claim():
    review = prepared_review()
    result = score_reviews(review['organizer'], review['display'], [])
    assert result['groups'] == [] and result['active_receipts'] == []
    assert result['quality_status'] == 'unreviewed_no_nonblank_ratings'
    assert sum(row['selected'] for row in result['coverage']) == 5


def test_malformed_duplicate_json_and_invalid_utf8_are_not_silently_normalized():
    review = prepared_review()
    with pytest.raises(ValueError, match='reviewer declaration UTF-8 JSON'):
        rating_receipt(review['organizer'], review['display'], ratings_csv(review),
                       b'{"schema":"one","schema":"two"}')
    with pytest.raises(ValueError, match='UTF-8 CSV'):
        rating_receipt(review['organizer'], review['display'], b'\xff', reviewer_bytes())


def test_many_proposals_still_produce_one_card_and_one_observation_per_selected_bundle():
    selection, inputs = selected_inputs()
    pack = json.loads(inputs[selection['slots'][0]['pack']['path']])
    source = adaptive_clarifications.build_source(pack, '1' * 24, selection['slots'][0]['pack']['sha256'])
    proposals = [question()]
    for index in range(2, 5):
        proposal = question()
        proposal['id'] = f'probe:q{index}'
        proposal['prompts'] = {language: f'{index}: {text}' for language, text in proposal['prompts'].items()}
        proposals.append(proposal)
    bundle = adaptive_clarifications.generate_questions(source, lambda *_a, **_kw: {'questions': proposals},
                                                        model=adaptive_clarifications.MODEL)
    raw = encoded(bundle)
    ref = selection['slots'][0]['artifact']
    ref['sha256'] = raw_sha(raw)
    inputs[ref['path']] = raw
    review = prepared_review(selection, inputs)
    assert len(next(card for card in review['display']['slots'] if card['kind'] == 'question'
                    and card['status'] == 'completed')['proposals']) == 4
    report = score_reviews(review['organizer'], review['display'], [receipt(review, label='yes')])
    question_scope = first_scope(report, kind='question')
    assert question_scope['coverage']['completed'] == 1
    assert question_scope['dimensions']['useful_next_action']['yes'] == 1
