"""Synthetic paired product review; no saved private data or provider calls."""
from copy import deepcopy
import csv
import io
import json

import pytest

from issueintel.evaluation import usefulness_pairs as core
from issueintel.investigation import adaptive_clarifications, check_plans, clarifications
from issueintel.investigation.report import build_report
from issueintel.runtime.io import digest
from test_usefulness import product_pack, question, check, encoded, raw_sha, reviewer_bytes, rehash


def selected_inputs(*, native_status='completed', language='en', pack=None,
                    unit_id='shared-case', source_job_id='1' * 24, path_prefix='artifacts/synthetic'):
    pack = product_pack() if pack is None else pack
    pack_raw = encoded(pack)
    reference = {'path': path_prefix + '/pack.json', 'sha256': raw_sha(pack_raw)}
    inputs, pairs = {reference['path']: pack_raw}, []
    for kind, baseline, native in (
        ('question', 'intake_questions_v1', 'adaptive_clarifications_v1'),
        ('check', 'saved_falsification_v1', 'product_check_plan_v1')):
        status = native_status
        if status == 'empty' and kind == 'question':
            status = 'unavailable'
        artifact = None
        if status in ('completed', 'empty'):
            if kind == 'question':
                source = adaptive_clarifications.build_source(pack, source_job_id, reference['sha256'])
                bundle = adaptive_clarifications.generate_questions(source,
                    lambda *_a, **_kw: {'questions': [question()]}, model=adaptive_clarifications.MODEL)
            else:
                source = check_plans.build_source(pack, source_job_id, reference['sha256'])
                bundle = check_plans.generate_plan(source,
                    lambda *_a, **_kw: {'checks': [] if status == 'empty' else [check()]}, model=check_plans.MODEL)
            raw = encoded(bundle)
            artifact = {'path': f'{path_prefix}/{kind}.json', 'sha256': raw_sha(raw)}
            inputs[artifact['path']] = raw
        count = (len(clarifications.questions_for(pack['case']['problem_statement'])['questions']) if kind == 'question'
            else len(build_report(pack, source_pack_sha256=reference['sha256'], job_id=source_job_id)['next_checks'][:3]))
        pairs.append({'unit_id': unit_id, 'kind': kind, 'source_job_id': source_job_id,
            'pack': deepcopy(reference), 'variants': [
                {'condition_id': 'baseline', 'variant': baseline, 'status': 'completed' if count else 'empty',
                 'reason': '' if count else 'No native proposals', 'artifact': None},
                {'condition_id': 'native', 'variant': native, 'status': status,
                 'reason': '' if status == 'completed' else 'Synthetic ' + status, 'artifact': artifact}]})
    return {'schema': 'usefulness_pair_selection_v2', 'purpose': 'Synthetic paired proposed action review',
        'protocol_id': 'paired-synthetic-v2', 'language': language, 'pairs': pairs}, inputs


def four_case_inputs():
    """Four distinct frozen product cases, each with question/check proposals."""
    pairs, inputs = [], {}
    for index in range(1, 5):
        pack = product_pack()
        case_id = f'local:synthetic-{index}'
        query = pack['case']['problem_statement'] + f' Synthetic issue {index}.'
        pack['case'].update(case_id=case_id, problem_statement=query)
        pack['annotation']['case_id'] = case_id
        pack['evidence'][0].update(text=query, locator=case_id, sha256=raw_sha(query.encode()))
        selected, source_inputs = selected_inputs(pack=pack, unit_id=f'case-{index}',
            source_job_id=f'{index:024x}', path_prefix=f'artifacts/synthetic/{index}')
        pairs.extend(selected['pairs'])
        inputs.update(source_inputs)
    selected['pairs'] = pairs
    return selected, inputs


def prepared_review(selection=None, inputs=None, *, orientations=None):
    if selection is None:
        selection, inputs = selected_inputs()
    identifiers = [f'{index:024x}' for index in range(1, len(selection['pairs']) + 1)]
    return core.build_review(selection, inputs, review_id='a' * 32, blind_ids=identifiers,
        order=list(reversed(identifiers)), orientations=orientations or [False] * len(identifiers))


def ratings_csv(review, *, label='', preference='', changes=None):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=core.RATING_FIELDS, lineterminator='\n')
    writer.writeheader()
    for mapping in review['organizer']['pairs']:
        row = {'blind_id': mapping['blind_id'], 'display_sha256': mapping['display_sha256']}
        for side in ('A', 'B'):
            if mapping['alternatives'][side]['status'] == 'completed':
                row.update({side + '_' + field: label for field in core.DIMENSIONS})
                row[side + '_rationale'] = 'Synthetic declared reasoning' if label else ''
        if all(variant['status'] == 'completed' for variant in mapping['alternatives'].values()):
            row.update(preference=preference, preference_rationale='Synthetic comparison' if preference else '')
        row.update((changes or {}).get(mapping['blind_id'], {}))
        writer.writerow(row)
    return stream.getvalue().encode('utf-8')


def receipt(review, *, label='', preference='', changes=None, who=None, parent=None, reason=''):
    return core.rating_receipt(review['organizer'], review['display'],
        ratings_csv(review, label=label, preference=preference, changes=changes), reviewer_bytes(**(who or {})),
        supersedes=parent, correction_reason=reason)


def test_baseline_is_exact_bilingual_intake_without_llm_or_fabricated_fields():
    pack = product_pack()
    before = deepcopy(pack)
    result = core.build_intake_baseline(pack, '1' * 24, '2' * 64)
    expected = clarifications.questions_for(pack['case']['problem_statement'])
    assert result['questions'] == expected['questions'] and result['intake'] == expected
    assert result['provenance'] == {'kind': 'deterministic_derivation',
        'method': 'clarifications.questions_for_v1', 'model_calls': 0}
    assert result['origin_hypothesis_provenance'] == pack['annotation']['provenance']
    assert result['source']['annotation_sha256'] == digest(pack['annotation'])
    assert result == core.validate_baseline(result, pack, '1' * 24, '2' * 64)
    assert pack == before
    for language in ('en', 'vi'):
        review = prepared_review(*selected_inputs(language=language))
        card = next(row for row in review['display']['pairs'] if row['kind'] == 'question')
        proposals = card['alternatives']['A']['proposals']
        assert [row['prompt'] for row in proposals] == [row['prompts'][language] for row in expected['questions']]
        assert all(row['purpose'] is None and row['answer_use'] is None
            and row['hypothesis_ids'] == [] and row['evidence_ids'] == [] for row in proposals)


@pytest.mark.parametrize('query,count', [
    ('A vague issue', 4),
    ('Actual result is error. Expected result is success. Steps to reproduce: example input. Python version 3.11 on Windows.', 0),
])
def test_intake_baseline_preserves_zero_to_four_questions(query, count):
    pack = product_pack()
    pack['case']['problem_statement'] = query
    pack['evidence'][0].update(text=query, sha256=raw_sha(query.encode()))
    result = core.build_intake_baseline(pack, '1' * 24, '2' * 64)
    assert len(result['questions']) == count


def test_saved_check_baseline_uses_first_three_report_order_after_native_deduplication():
    pack = product_pack()
    hypotheses = pack['annotation']['hypotheses']
    hypotheses[1]['falsification_test'] = hypotheses[0]['falsification_test']
    pack['recommended_steps'] = ['Saved additional check', 'Another saved check', 'Beyond cap']
    source_sha = raw_sha(encoded(pack))
    report = build_report(pack, source_pack_sha256=source_sha, job_id='1' * 24)
    baseline = core.build_saved_falsification_baseline(pack, '1' * 24, source_sha)
    assert baseline['checks'] == [{**row, 'reference_role': 'hypothesis_source_link'} for row in report['next_checks'][:3]]
    assert [row['text'] for row in baseline['checks']] == [hypotheses[0]['falsification_test'],
        hypotheses[2]['falsification_test'], 'Saved additional check']
    assert baseline['checks'][0]['hypothesis_ids'] == ['h1', 'h2']
    assert baseline['source']['report_sha256'] == report['sha256']
    review = prepared_review(*selected_inputs(pack=pack))
    card = next(row for row in review['display']['pairs'] if row['kind'] == 'check')
    proposal = card['alternatives']['A']['proposals'][0]
    assert proposal['procedure'] == report['next_checks'][0]['text']
    assert proposal['evidence_ids'] == report['next_checks'][0]['evidence_ids']
    assert proposal['reference_role'] == 'hypothesis_source_link'
    assert proposal['assumptions'] is None and proposal['outcome_a'] is None and proposal['outcome_b'] is None
    assert proposal['predictions'] == []


def test_original_llm_provenance_remains_separate_from_deterministic_derivation():
    pack = product_pack()
    pack['annotation']['provenance'] = {'kind': 'llm', 'method': 'synthetic-only',
        'provider': 'synthetic', 'model': 'synthetic-only'}
    baseline = core.build_saved_falsification_baseline(pack, '1' * 24, '2' * 64)
    assert baseline['provenance']['kind'] == 'deterministic_derivation'
    assert baseline['origin_hypothesis_provenance'] == pack['annotation']['provenance']


@pytest.mark.parametrize('schema', ['intake', 'check'])
def test_rehashed_baseline_edits_cannot_replace_exact_derivation(schema):
    pack = product_pack()
    if schema == 'intake':
        result = core.build_intake_baseline(pack, '1' * 24, '2' * 64)
        result['questions'][0]['prompts']['en'] = 'Invented prompt'
    else:
        result = core.build_saved_falsification_baseline(pack, '1' * 24, '2' * 64)
        result['checks'][0]['evidence_ids'] = ['issue_1']
    rehash(result)
    with pytest.raises(ValueError, match='exact saved source derivation'):
        core.validate_baseline(result, pack, '1' * 24, '2' * 64)


def test_pair_has_one_common_source_context_and_frozen_orientation_without_method_metadata():
    selection, inputs = selected_inputs()
    originals = deepcopy(inputs)
    review = prepared_review(selection, inputs, orientations=[False, True])
    assert inputs == originals
    assert review == prepared_review(selection, inputs, orientations=[False, True])
    for card in review['display']['pairs']:
        assert set(card) == {'blind_id', 'kind', 'context', 'alternatives'}
        assert card['context']['hypotheses'] == product_pack()['annotation']['hypotheses']
        assert len(card['context']['evidence']) == 2
        assert card['context']['repository'] == 'local_repository'
        for variant in card['alternatives'].values():
            assert set(variant) == {'status', 'proposals'}
    serialized = json.dumps(review['display'])
    for hidden in ('"condition_id"', '"variant"', '"provider"', '"model"', '"effort"', '"rank"', 'C:\\private'):
        assert hidden not in serialized
    check_mapping = next(row for row in review['organizer']['pairs'] if row['kind'] == 'check')
    assert check_mapping['alternatives']['A']['condition_id'] == 'native'
    score = core.score_reviews(review['organizer'], review['display'], [receipt(review, preference='A')])
    assert score['case_clusters'] == {'count': 1, 'unit_ids': ['shared-case']}
    assert score['selected_pairs'] == 2
    assert {row['kind']: row['counts']['baseline'] for row in score['paired_preferences']} == {'check': 0, 'question': 1}


@pytest.mark.parametrize('change', [
    lambda value: value.update(schema='usefulness_selection_v1'),
    lambda value: value.update(extra=True),
    lambda value: value.update(language='fr'),
    lambda value: value.update(pairs=[]),
    lambda value: value['pairs'].append(deepcopy(value['pairs'][0])),
    lambda value: value['pairs'][1].update(source_job_id='2' * 24),
    lambda value: value['pairs'][0]['pack'].update(path='data/gold.json'),
    lambda value: value['pairs'][0]['pack'].update(path='artifacts/../source.json'),
    lambda value: value['pairs'][0]['variants'].pop(),
    lambda value: value['pairs'][0]['variants'][0].update(variant='invented'),
    lambda value: value['pairs'][0]['variants'][0].update(status='failed', reason='Failure'),
    lambda value: value['pairs'][0]['variants'][0].update(artifact={'path': 'artifacts/a.json', 'sha256': '2' * 64}),
    lambda value: value['pairs'][0]['variants'][1].update(status='empty', reason='None'),
    lambda value: value['pairs'][0]['variants'][1].update(status='failed', reason='Failure'),
    lambda value: value['pairs'][0]['variants'][1].update(condition_id='baseline'),
])
def test_ambiguous_pair_scope_and_references_rejected(change):
    selection, _ = selected_inputs()
    change(selection)
    with pytest.raises(ValueError):
        core.validate_selection(selection)


@pytest.mark.parametrize('mode', ['same_source', 'path_alias', 'same_pack_different_job', 'same_job_different_pack'])
def test_one_source_cannot_inflate_case_clusters_under_another_unit(mode):
    selection, inputs = selected_inputs()
    duplicate = deepcopy(selection['pairs'][0])
    duplicate['unit_id'] = 'another-case'
    if mode == 'path_alias':
        duplicate['pack']['path'] = 'artifacts/alias/pack.json'
        inputs[duplicate['pack']['path']] = inputs[selection['pairs'][0]['pack']['path']]
    elif mode == 'same_pack_different_job':
        duplicate['source_job_id'] = '2' * 24
    elif mode == 'same_job_different_pack':
        duplicate['pack'] = {'path': 'artifacts/another/pack.json', 'sha256': 'f' * 64}
    selection['pairs'].append(duplicate)
    with pytest.raises(ValueError, match='single case cluster'):
        core.validate_selection(selection)
    with pytest.raises(ValueError, match='single case cluster'):
        prepared_review(selection, inputs)


def test_same_unit_question_and_check_may_reference_identical_pack_bytes_at_alias_paths():
    selection, inputs = selected_inputs()
    original = selection['pairs'][0]['pack']['path']
    alias = 'artifacts/alias/same-pack.json'
    selection['pairs'][1]['pack']['path'] = alias
    inputs[alias] = inputs[original]
    review = prepared_review(selection, inputs)
    assert core.score_reviews(review['organizer'], review['display'], [])['case_clusters']['count'] == 1


def test_four_clusters_require_four_distinct_pack_bytes_and_source_jobs():
    selection, inputs = four_case_inputs()
    assert len({pair['pack']['sha256'] for pair in selection['pairs']}) == 4
    assert len({pair['source_job_id'] for pair in selection['pairs']}) == 4
    review = prepared_review(selection, inputs)
    report = core.score_reviews(review['organizer'], review['display'], [receipt(review, label='yes', preference='tie')])
    assert report['selected_pairs'] == 8
    assert report['case_clusters'] == {'count': 4, 'unit_ids': ['case-1', 'case-2', 'case-3', 'case-4']}
    assert all(scope['selected'] == 4 and scope['case_clusters'] == 4 for scope in report['coverage'])
    assert all(group['case_clusters'] == 4 and group['eligible_pairs'] == 4 for group in report['paired_preferences'])


@pytest.mark.parametrize('change', [
    lambda pack: pack['case'].update(split='main_dev'),
    lambda pack: pack['case'].update(split=None),
    lambda pack: pack['case'].update(instance_id=None),
    lambda pack: pack['case'].update(case_id='verified:synthetic-1'),
    lambda pack: pack['case'].update(case_id='live:synthetic-1'),
    lambda pack: pack['snapshot'].update(protocol='issue_time'),
    lambda pack: pack['leakage_audit'].update(original_query_verified=True),
    lambda pack: pack['case'].update(gold='forbidden'),
    lambda pack: pack['annotation'].update(source_quality={'source_human': 'forbidden'}),
    lambda pack: pack['evidence'][1].update(text='tampered'),
])
def test_failed_native_does_not_bypass_product_source_admission(change):
    selection, inputs = selected_inputs(native_status='failed')
    pack = product_pack()
    change(pack)
    raw = encoded(pack)
    for pair in selection['pairs']:
        pair['pack']['sha256'] = raw_sha(raw)
    inputs[selection['pairs'][0]['pack']['path']] = raw
    with pytest.raises(ValueError):
        prepared_review(selection, inputs)


@pytest.mark.parametrize('mode', ['extra', 'missing', 'hash', 'text', 'duplicate_json'])
def test_exact_raw_sources_required(mode):
    selection, inputs = selected_inputs()
    path = selection['pairs'][0]['pack']['path']
    if mode == 'extra':
        inputs['artifacts/extra.json'] = b'{}'
    elif mode == 'missing':
        inputs.pop(path)
    elif mode == 'hash':
        inputs[path] += b'\n'
    elif mode == 'text':
        inputs[path] = inputs[path].decode()
    else:
        inputs[path] = b'{"schema":1,"schema":2}'
        for pair in selection['pairs']:
            pair['pack']['sha256'] = raw_sha(inputs[path])
    with pytest.raises(ValueError):
        prepared_review(selection, inputs)


@pytest.mark.parametrize('orientations', [[], [False], [False, 0], [False, 'yes']])
def test_orientation_is_explicit_boolean_and_never_rerandomized(orientations):
    selection, inputs = selected_inputs()
    with pytest.raises(ValueError, match='orientation'):
        core.build_review(selection, inputs, review_id='a' * 32,
            blind_ids=['1' * 24, '2' * 24], order=['1' * 24, '2' * 24], orientations=orientations)


def test_native_validator_rejects_rehashed_recommendation_and_source_changes():
    selection, inputs = selected_inputs()
    artifact = selection['pairs'][1]['variants'][1]['artifact']
    bundle = json.loads(inputs[artifact['path']])
    bundle['comparison']['recommended_check_id'] = None
    rehash(bundle['comparison'])
    rehash(bundle)
    raw = encoded(bundle)
    inputs[artifact['path']] = raw
    artifact['sha256'] = raw_sha(raw)
    with pytest.raises(ValueError, match='comparison differs'):
        prepared_review(selection, inputs)


@pytest.mark.parametrize('native_status', ['empty', 'failed', 'unavailable'])
def test_empty_failure_and_unavailable_keep_denominators_without_forced_negative(native_status):
    review = prepared_review(*selected_inputs(native_status=native_status))
    report = core.score_reviews(review['organizer'], review['display'], [receipt(review)])
    scope = next(row for row in report['coverage'] if row['kind'] == 'check' and row['condition_id'] == 'native')
    assert scope[native_status] == 1 and scope['selected'] == 1 and scope['case_clusters'] == 1
    group = next(row for row in report['groups'] if row['kind'] == 'check' and row['condition_id'] == 'native')
    assert group['dimensions']['grounding'] == {'yes': 0, 'no': 0, 'uncertain': 0, 'missing': 0,
        'known_rate': None, 'observed_yield_bounds': {'lower': 0, 'upper': 0, 'denominator': 1}}
    assert all(row['counts']['ineligible'] == 1 for row in report['paired_preferences'])
    assert report['quality_status'] == 'unreviewed_no_nonblank_ratings'
    blind_id = review['organizer']['blind_ids'][1]
    with pytest.raises(ValueError, match='both alternatives completed'):
        receipt(review, changes={blind_id: {'preference': 'A', 'preference_rationale': 'Invalid comparison'}})
    with pytest.raises(ValueError, match='Noncompleted'):
        receipt(review, changes={blind_id: {'B_grounding': 'no', 'B_rationale': 'Do not force a negative'}})


def test_derived_empty_baseline_is_recomputed_and_not_relabelled():
    pack = product_pack()
    query = 'Actual result is error. Expected result is success. Steps to reproduce: example input. Python version 3.11 on Windows.'
    pack['case']['problem_statement'] = query
    pack['evidence'][0].update(text=query, sha256=raw_sha(query.encode()))
    selection, inputs = selected_inputs(pack=pack)
    review = prepared_review(selection, inputs)
    card = next(row for row in review['display']['pairs'] if row['kind'] == 'question')
    assert card['alternatives']['A'] == {'status': 'empty', 'proposals': []}
    selection['pairs'][0]['variants'][0]['status'] = 'completed'
    with pytest.raises(ValueError, match='status differs'):
        prepared_review(selection, inputs)


@pytest.mark.parametrize('change', [
    {'A_grounding': 'YES'}, {'B_grounding': ' yes'}, {'A_rationale': ''},
    {'preference': 'baseline'}, {'preference_rationale': ''}, {'display_sha256': 'f' * 64},
])
def test_invalid_quality_preference_hash_and_rationale_rejected(change):
    review = prepared_review()
    with pytest.raises(ValueError):
        receipt(review, label='yes', preference='tie', changes={review['organizer']['blind_ids'][0]: change})


def test_exact_rating_bytes_and_reviewer_declaration_are_retained_without_human_authentication():
    review = prepared_review()
    raw = b'\xef\xbb\xbf' + ratings_csv(review, label='uncertain', preference='uncertain')
    who = reviewer_bytes(declaration='Synthetic agent review, not human validation.')
    result = core.rating_receipt(review['organizer'], review['display'], raw, who)
    assert result['csv_utf8'].encode() == raw and result['csv_sha256'] == raw_sha(raw)
    assert result['reviewer_utf8'].encode() == who and result['reviewer_sha256'] == raw_sha(who)
    assert result == core.validate_rating_receipt(review['organizer'], review['display'], result)
    assert result['reviewer']['kind'] == 'agent'
    assert result == core.rating_receipt(review['organizer'], review['display'], raw, who)
    report = core.score_reviews(review['organizer'], review['display'], [result])
    assert report['independent_human_validation'] == 'not_established'
    assert report['causal_efficacy'] == 'not_measured'
    assert report['inter_rater_agreement'] == 'not_computed'


@pytest.mark.parametrize('mode', ['duplicate', 'missing', 'unknown', 'header', 'extra_column', 'utf8'])
def test_csv_inventory_and_exact_header_rejected(mode):
    review = prepared_review()
    rows = list(csv.reader(io.StringIO(ratings_csv(review).decode())))
    if mode == 'duplicate':
        rows.append(rows[-1])
    elif mode == 'missing':
        rows.pop()
    elif mode == 'unknown':
        rows[-1][0] = 'f' * 24
    elif mode == 'header':
        rows[0][-1] = rows[0][-2]
    elif mode == 'extra_column':
        rows[-1].append('extra')
    stream = io.StringIO(newline='')
    csv.writer(stream).writerows(rows)
    raw = stream.getvalue().encode() if mode != 'utf8' else b'\xff'
    with pytest.raises(ValueError):
        core.rating_receipt(review['organizer'], review['display'], raw, reviewer_bytes())


def test_correction_leaf_is_active_reviewers_stay_separate_and_cards_do_not_become_cases():
    review = prepared_review()
    first = receipt(review, label='yes', preference='A')
    corrected = receipt(review, label='no', preference='B', parent=first['sha256'], reason='Synthetic correction')
    human = receipt(review, label='uncertain', preference='tie', who={'reviewer_id': 'declared-human',
        'kind': 'human', 'assistance': 'none', 'model_id': None, 'relationship': 'declared_independent'})
    report = core.score_reviews(review['organizer'], review['display'], [corrected, human, first, first])
    assert len(report['active_receipts']) == 2 and len(report['groups']) == 8
    assert report['case_clusters']['count'] == 1 and report['selected_pairs'] == 2
    assert report['independent_human_validation'] == 'not_established'
    assert all(row['dimensions']['grounding']['no'] == 1 for row in report['groups']
        if row['reviewer']['reviewer_id'] == 'synthetic-reviewer')
    assert report == core.score_reviews(review['organizer'], review['display'], [first, human, corrected])
    branch = receipt(review, label='uncertain', parent=first['sha256'], reason='Invalid branch')
    with pytest.raises(ValueError, match='branch'):
        core.score_reviews(review['organizer'], review['display'], [first, corrected, branch])
    with pytest.raises(ValueError, match='missing or different-reviewer'):
        core.score_reviews(review['organizer'], review['display'], [corrected])


def test_rehashed_receipt_and_display_derived_tampering_rejected():
    review = prepared_review()
    result = receipt(review, label='yes')
    result['rows'][0]['A_grounding'] = 'no'
    rehash(result)
    with pytest.raises(ValueError, match='retained exact sources'):
        core.validate_rating_receipt(review['organizer'], review['display'], result)
    review['display']['pairs'][0]['alternatives']['A']['proposals'][0]['procedure'] = 'Changed'
    rehash(review['display'])
    review['organizer']['display_sha256'] = review['display']['sha256']
    rehash(review['organizer'])
    with pytest.raises(ValueError, match='mapping or display'):
        core.validate_review(review['organizer'], review['display'])


@pytest.mark.parametrize('mode', ['prompt', 'provenance', 'source', 'role'])
def test_frozen_baseline_replay_rejects_changes_even_with_fresh_outer_hashes(mode):
    review = prepared_review()
    index = 0 if mode != 'role' else 1
    derived = review['organizer']['derivations'][index]['alternatives']['A']
    if mode == 'prompt':
        derived['questions'][0]['prompts']['en'] = 'Fabricated baseline'
    elif mode == 'provenance':
        derived['provenance']['model_calls'] = 1
    elif mode == 'source':
        derived['source']['source_pack_sha256'] = 'f' * 64
    else:
        derived['checks'][0]['reference_role'] = 'procedure_grounding'
    rehash(derived)
    rehash(review['organizer'])
    with pytest.raises(ValueError):
        core.validate_review(review['organizer'], review['display'])


def test_empty_saved_check_baseline_without_hypotheses_remains_a_selected_bundle():
    pack = product_pack()
    pack['annotation'] = None
    selection, inputs = selected_inputs(native_status='failed', pack=pack)
    review = prepared_review(selection, inputs)
    card = next(row for row in review['display']['pairs'] if row['kind'] == 'check')
    assert card['context']['hypotheses'] == []
    assert card['alternatives']['A'] == {'status': 'empty', 'proposals': []}
    report = core.score_reviews(review['organizer'], review['display'], [receipt(review)])
    scope = next(row for row in report['coverage'] if row['kind'] == 'check' and row['condition_id'] == 'baseline')
    assert scope['selected'] == 1 and scope['empty'] == 1


def test_blank_and_uncertain_are_distinct_from_no_and_no_receipts_do_not_claim_quality():
    review = prepared_review()
    report = core.score_reviews(review['organizer'], review['display'], [])
    assert report['groups'] == [] and report['paired_preferences'] == []
    assert report['quality_status'] == 'unreviewed_no_nonblank_ratings'
    blank = core.score_reviews(review['organizer'], review['display'], [receipt(review)])
    uncertain = core.score_reviews(review['organizer'], review['display'], [receipt(review, label='uncertain')])
    assert blank['groups'][0]['dimensions']['grounding']['missing'] == 1
    assert uncertain['groups'][0]['dimensions']['grounding']['uncertain'] == 1
    assert uncertain['quality_status'] == 'uncertain_only_no_binary_judgments'
    only_preference = core.score_reviews(review['organizer'], review['display'], [receipt(review, preference='uncertain')])
    assert only_preference['quality_status'] == 'uncertain_only_no_binary_judgments'


def test_native_empty_check_remains_valid_and_original_bytes_and_v1_are_unchanged():
    from issueintel.evaluation import usefulness
    from test_usefulness import prepared_review as v1_review
    original = v1_review()
    review = prepared_review(*selected_inputs(native_status='empty'))
    assert next(row for row in review['display']['pairs'] if row['kind'] == 'check')['alternatives']['B']['status'] == 'empty'
    assert usefulness.validate_review(original['organizer'], original['display']) == original
    assert v1_review() == original
