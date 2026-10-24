"""Frozen paired reviews of saved product proposals and deterministic baselines.

Pure data only: no files, credentials, dataset, Git or provider are opened here.
The v1 review and native question/check validators retain their original contracts.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
import hashlib
import io

from issueintel.runtime.io import digest
from . import usefulness as shared


DIMENSIONS = shared.DIMENSIONS
RATING_FIELDS = ('blind_id', 'display_sha256',
    *(side + '_' + field for side in ('A', 'B') for field in (*DIMENSIONS, 'rationale')),
    'preference', 'preference_rationale')
VARIANTS = ('intake_questions_v1', 'saved_falsification_v1',
            'adaptive_clarifications_v1', 'product_check_plan_v1')
_KINDS = {'question': ('intake_questions_v1', 'adaptive_clarifications_v1'),
          'check': ('saved_falsification_v1', 'product_check_plan_v1')}
_STATUSES = ('completed', 'empty', 'failed', 'unavailable')
_LABELS = {'', 'yes', 'no', 'uncertain'}
_PREFERENCES = {'', 'A', 'B', 'tie', 'uncertain'}
_ORGANIZER_FIELDS = {'schema', 'review_id', 'selection', 'blind_ids', 'order',
    'orientations', 'pairs', 'derivations', 'display_sha256', 'limitations', 'sha256'}
_DISPLAY_FIELDS = {'schema', 'review_id', 'language', 'rubric', 'pairs', 'limitations', 'sha256'}
LIMITATIONS = (
    *shared.LIMITATIONS,
    'Both alternatives share exactly one frozen product pack, issue, hypotheses and evidence; no Main Dev case is relabelled as product.',
    'Intake questions are exactly clarifications.questions_for(query), with zero model calls; saved check baselines are the first three deduplicated report.next_checks in native order.',
    'Deterministic derivation does not make the original hypothesis or saved falsification wording non-model output; origin provenance is retained separately.',
    'Missing purposes, assumptions, outcomes and predictions remain null or empty; an unrecorded field is not automatically a negative usefulness judgment.',
    'Saved check baseline evidence references are hypothesis_source_link references, not evidence grounding the proposed procedure.',
    'A/B orientation and card order are frozen once; condition, variant, provider, model, rank and effort metadata remain organizer-only.',
    'Paired preferences require two completed proposal bundles; empty, failed and unavailable alternatives retain their denominators without quality labels.',
    'Repeated cards and question/check kinds from one selected unit form one case cluster, not independent cases; descriptive differences are not causal efficacy or benchmark results.',
)


def validate_selection(selection):
    """Admit an explicit product-only paired design without source discovery."""
    shared._fields(selection, {'schema', 'purpose', 'protocol_id', 'language', 'pairs'}, 'paired selection')
    if selection['schema'] != 'usefulness_pair_selection_v2':
        raise ValueError('Invalid paired selection schema')
    shared._text(selection['purpose'], 'paired review purpose', 2000)
    shared._match(selection['protocol_id'], shared._IDENTIFIER, 'paired protocol ID')
    if selection['language'] not in ('en', 'vi'):
        raise ValueError('Paired review language must be en or vi')
    pairs = selection['pairs']
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 100:
        raise ValueError('Select between one and 100 proposal pairs')
    seen, units, references, conditions = set(), {}, {}, {}
    source_jobs, source_packs = {}, {}
    for pair in pairs:
        shared._fields(pair, {'unit_id', 'kind', 'source_job_id', 'pack', 'variants'}, 'selected pair')
        shared._match(pair['unit_id'], shared._IDENTIFIER, 'paired unit ID')
        shared._match(pair['source_job_id'], shared._BLIND, 'paired source job ID')
        if not isinstance(pair['kind'], str) or pair['kind'] not in _KINDS:
            raise ValueError('Invalid paired proposal kind')
        identity = (pair['unit_id'], pair['kind'])
        if identity in seen:
            raise ValueError('Duplicate unit and proposal kind')
        seen.add(identity)
        shared._reference(pair['pack'])
        source_identity = (pair['source_job_id'], pair['pack']['sha256'])
        if pair['unit_id'] in units and units[pair['unit_id']] != source_identity:
            raise ValueError('One case cluster must use exactly the same source pack and job')
        units[pair['unit_id']] = source_identity
        for identity, index in ((pair['source_job_id'], source_jobs), (pair['pack']['sha256'], source_packs)):
            if identity in index and index[identity] != pair['unit_id']:
                raise ValueError('One source job or raw pack must belong to a single case cluster')
            index[identity] = pair['unit_id']
        variants = pair['variants']
        if not isinstance(variants, list) or len(variants) != 2:
            raise ValueError('Each pair requires exactly two variants')
        names, ids = set(), set()
        for variant in variants:
            shared._fields(variant, {'condition_id', 'variant', 'status', 'reason', 'artifact'}, 'selected variant')
            shared._match(variant['condition_id'], shared._IDENTIFIER, 'paired condition ID')
            if (not isinstance(variant['variant'], str) or variant['variant'] not in _KINDS[pair['kind']]
                    or not isinstance(variant['status'], str) or variant['status'] not in _STATUSES):
                raise ValueError('Invalid paired variant or status')
            names.add(variant['variant'])
            ids.add(variant['condition_id'])
            shared._text(variant['reason'], 'paired status reason', 2000,
                         empty=variant['status'] == 'completed')
            baseline = variant['variant'] == _KINDS[pair['kind']][0]
            if baseline:
                if variant['artifact'] is not None or variant['status'] not in ('completed', 'empty'):
                    raise ValueError('Deterministic baselines are derived from the pack with completed or empty status')
            elif variant['status'] in ('completed', 'empty'):
                shared._reference(variant['artifact'])
                if variant['status'] == 'empty' and pair['kind'] != 'check':
                    raise ValueError('Native adaptive questions cannot be empty')
            elif variant['artifact'] is not None:
                raise ValueError('Failed or unavailable native variants cannot include an artifact')
            scope = (variant['condition_id'], pair['kind'])
            if scope in conditions and conditions[scope] != variant['variant']:
                raise ValueError('One condition and kind cannot name different variants')
            conditions[scope] = variant['variant']
        if names != set(_KINDS[pair['kind']]) or len(ids) != 2:
            raise ValueError('Pair one deterministic baseline and one native variant under distinct conditions')
        for reference in (pair['pack'], *(variant['artifact'] for variant in variants)):
            if reference is not None:
                path, fingerprint = reference['path'], reference['sha256']
                if path in references and references[path] != fingerprint:
                    raise ValueError('One paired path has conflicting raw hashes')
                references[path] = fingerprint
    return deepcopy(selection)


def _source_binding(pack, source_job_id, source_pack_sha256):
    shared._product(pack)
    shared._match(source_job_id, shared._BLIND, 'baseline source job ID')
    shared._match(source_pack_sha256, shared._HASH, 'baseline raw source hash')
    return {'source_job_id': source_job_id, 'source_pack_sha256': source_pack_sha256,
        'query_sha256': hashlib.sha256(pack['case']['problem_statement'].encode('utf-8')).hexdigest(),
        'snapshot_sha256': pack['snapshot']['snapshot_sha256'],
        'commit_sha': pack['snapshot']['commit_sha'], 'annotation_sha256': digest(pack.get('annotation'))}


def _origin(pack):
    return deepcopy((pack.get('annotation') or {}).get('provenance'))


def build_intake_baseline(pack, source_job_id, source_pack_sha256):
    """Derive the unchanged bilingual 0–4 intake templates with no model call."""
    from issueintel.investigation.clarifications import questions_for
    source = _source_binding(pack, source_job_id, source_pack_sha256)
    intake = questions_for(pack['case']['problem_statement'])
    return shared._seal({'schema': 'intake_questions_v1', 'source': source,
        'questions': deepcopy(intake['questions']), 'intake': intake,
        'provenance': {'kind': 'deterministic_derivation',
            'method': 'clarifications.questions_for_v1', 'model_calls': 0},
        'origin_hypothesis_provenance': _origin(pack), 'limitations': list(LIMITATIONS)})


def build_saved_falsification_baseline(pack, source_job_id, source_pack_sha256):
    """Keep the first three native deduplicated report checks, including links."""
    from issueintel.investigation.report import build_report
    source = _source_binding(pack, source_job_id, source_pack_sha256)
    report = build_report(pack, source_pack_sha256=source_pack_sha256, job_id=source_job_id)
    source['report_sha256'] = report['sha256']
    checks = [{**deepcopy(row), 'reference_role': 'hypothesis_source_link'}
              for row in report['next_checks'][:3]]
    return shared._seal({'schema': 'saved_falsification_v1', 'source': source, 'checks': checks,
        'provenance': {'kind': 'deterministic_derivation',
            'method': 'report.next_checks_first_three_v1', 'model_calls': 0},
        'origin_hypothesis_provenance': _origin(pack), 'limitations': list(LIMITATIONS)})


def validate_baseline(bundle, pack, source_job_id, source_pack_sha256):
    """Rebuild a baseline; rehashing edited questions or links cannot admit it."""
    if not isinstance(bundle, dict) or bundle.get('schema') not in _KINDS['question'][:1] + _KINDS['check'][:1]:
        raise ValueError('Invalid deterministic baseline schema')
    builder = build_intake_baseline if bundle['schema'] == 'intake_questions_v1' else build_saved_falsification_baseline
    expected = builder(pack, source_job_id, source_pack_sha256)
    if bundle != expected:
        raise ValueError('Deterministic baseline differs from exact saved source derivation')
    return expected


def _baseline_proposals(bundle, kind, language):
    if kind == 'question':
        return [{'id': row['id'], 'prompt': row['prompts'][language],
            'purpose': None, 'answer_use': None, 'hypothesis_ids': [], 'evidence_ids': []}
            for row in bundle['questions']]
    return [{'id': row['id'], 'procedure': row['text'], 'assumptions': None,
        'outcome_a': None, 'outcome_b': None, 'predictions': [],
        'hypothesis_ids': deepcopy(row['hypothesis_ids']), 'evidence_ids': deepcopy(row['evidence_ids']),
        'reference_role': 'hypothesis_source_link'} for row in bundle['checks']]


def _proposals(pair, variant, pack, parsed, language):
    from issueintel.investigation import adaptive_clarifications, check_plans
    name = variant['variant']
    source_job, source_sha = pair['source_job_id'], pair['pack']['sha256']
    if name == 'intake_questions_v1':
        bundle = build_intake_baseline(pack, source_job, source_sha)
        proposals = _baseline_proposals(bundle, pair['kind'], language)
    elif name == 'saved_falsification_v1':
        bundle = build_saved_falsification_baseline(pack, source_job, source_sha)
        proposals = _baseline_proposals(bundle, pair['kind'], language)
    elif variant['artifact'] is None:
        return [], None
    else:
        bundle = parsed[variant['artifact']['path']]
        if name == 'adaptive_clarifications_v1':
            source = adaptive_clarifications.build_source(pack, source_job, source_sha)
            bundle = adaptive_clarifications.validate_bundle(bundle, source)
            proposals = [{'id': row['id'], 'prompt': row['prompts'][language],
                'purpose': row['purpose'][language], 'answer_use': row['answer_use'][language],
                'hypothesis_ids': deepcopy(row['hypothesis_ids']), 'evidence_ids': deepcopy(row['evidence_ids'])}
                for row in bundle['questions']]
        else:
            source = check_plans.build_source(pack, source_job, source_sha)
            bundle = check_plans.validate_plan(bundle, source)
            proposals = [{**{key: deepcopy(row[key]) for key in (
                'id', 'procedure', 'assumptions', 'outcome_a', 'outcome_b', 'predictions', 'evidence_ids')},
                'hypothesis_ids': [prediction['hypothesis_id'] for prediction in row['predictions']],
                'reference_role': 'proposed_check_citation'} for row in bundle['checks']]
    if bool(proposals) != (variant['status'] == 'completed'):
        raise ValueError('Paired status differs from exact derived or native artifact contents')
    return proposals, bundle if name in ('intake_questions_v1', 'saved_falsification_v1') else None


def _rubric(language):
    result = shared._rubric(language)
    result.update(schema='usefulness_pair_rubric_v2', rating_unit='whole_proposal_bundle_within_frozen_pair',
        preference_scale=['A', 'B', 'tie', 'uncertain', ''],
        preference_prompt=('Which completed bundle would better help choose the next investigation action?'
            if language == 'en' else 'Bộ đề xuất hoàn tất nào giúp chọn bước điều tra tiếp theo tốt hơn?'),
        missing_field_instruction=('An unrecorded field alone does not require a no rating.' if language == 'en'
            else 'Một trường chưa được ghi không tự buộc phải chấm no.'))
    return result


def _orientations(orientations, count):
    if not isinstance(orientations, list) or len(orientations) != count or any(type(row) is not bool for row in orientations):
        raise ValueError('One frozen boolean orientation is required for each pair')


def _mapping(pair, identifier, orientation, card):
    variants = list(reversed(pair['variants'])) if orientation else pair['variants']
    return {'unit_id': pair['unit_id'], 'kind': pair['kind'], 'blind_id': identifier,
        'display_sha256': digest(card), 'alternatives': {side: {
            key: variant[key] for key in ('condition_id', 'variant', 'status')}
            for side, variant in zip(('A', 'B'), variants)}}


def build_review(selection, inputs, *, review_id, blind_ids, order, orientations):
    """Freeze paired common-context cards; True reverses selected variant order."""
    selection = validate_selection(selection)
    count = len(selection['pairs'])
    shared._identities(review_id, blind_ids, order, count)
    _orientations(orientations, count)
    references = {reference['path']: reference['sha256'] for pair in selection['pairs']
        for reference in (pair['pack'], *(variant['artifact'] for variant in pair['variants']))
        if reference is not None}
    if not isinstance(inputs, dict) or set(inputs) != set(references):
        raise ValueError('Paired inputs must contain exactly the selected source paths')
    if any(not isinstance(raw, bytes) for raw in inputs.values()) or sum(map(len, inputs.values())) > 128_000_000:
        raise ValueError('Selected paired source bytes exceed review bounds')
    parsed = {}
    for path, expected in references.items():
        if hashlib.sha256(inputs[path]).hexdigest() != expected:
            raise ValueError('Paired raw-byte source hash mismatch')
        parsed[path], _ = shared._json_bytes(inputs[path], 'selected paired source')
    cards, mappings, derivations = {}, [], []
    for pair, identifier, orientation in zip(selection['pairs'], blind_ids, orientations):
        pack = parsed[pair['pack']['path']]
        context = shared._context(pack)
        alternatives, derived = {}, {}
        variants = list(reversed(pair['variants'])) if orientation else pair['variants']
        for side, variant in zip(('A', 'B'), variants):
            proposals, baseline = _proposals(pair, variant, pack, parsed, selection['language'])
            alternatives[side] = {'status': variant['status'], 'proposals': proposals}
            derived[side] = baseline
        card = {'blind_id': identifier, 'kind': pair['kind'], 'context': context, 'alternatives': alternatives}
        cards[identifier] = card
        mappings.append(_mapping(pair, identifier, orientation, card))
        derivations.append({'blind_id': identifier, 'alternatives': derived})
    display = shared._seal({'schema': 'usefulness_pair_display_v2', 'review_id': review_id,
        'language': selection['language'], 'rubric': _rubric(selection['language']),
        'pairs': [cards[identifier] for identifier in order], 'limitations': list(LIMITATIONS)})
    organizer = shared._seal({'schema': 'usefulness_pair_organizer_v2', 'review_id': review_id,
        'selection': selection, 'blind_ids': list(blind_ids), 'order': list(order), 'orientations': list(orientations),
        'pairs': mappings, 'derivations': derivations, 'display_sha256': display['sha256'],
        'limitations': list(LIMITATIONS)})
    validate_review(organizer, display)
    return {'organizer': organizer, 'display': display}


def _validate_frozen_baseline(bundle, pair, context, alternative, language):
    """Replay the baseline presentation; full raw pack replay remains with build."""
    from issueintel.investigation.clarifications import questions_for
    kind = pair['kind']
    collection = 'questions' if kind == 'question' else 'checks'
    fields = {'schema', 'source', collection, 'provenance', 'origin_hypothesis_provenance', 'limitations', 'sha256'}
    if kind == 'question':
        fields.add('intake')
    shared._fields(bundle, fields, 'frozen baseline')
    shared._hash(bundle, 'Paired deterministic derivation')
    method = 'clarifications.questions_for_v1' if kind == 'question' else 'report.next_checks_first_three_v1'
    if (bundle['schema'] != _KINDS[kind][0]
            or bundle['provenance'] != {'kind': 'deterministic_derivation', 'method': method, 'model_calls': 0}
            or bundle['limitations'] != list(LIMITATIONS)):
        raise ValueError('Frozen baseline provenance or limitations changed')
    source_keys = {'source_job_id', 'source_pack_sha256', 'query_sha256', 'snapshot_sha256',
                   'commit_sha', 'annotation_sha256'}
    if kind == 'check':
        source_keys.add('report_sha256')
    shared._fields(bundle['source'], source_keys, 'frozen baseline source')
    source = bundle['source']
    if (source['source_job_id'] != pair['source_job_id']
            or source['source_pack_sha256'] != pair['pack']['sha256']
            or source['query_sha256'] != hashlib.sha256(context['issue'].encode('utf-8')).hexdigest()
            or source['commit_sha'] != context['revision']['commit']):
        raise ValueError('Frozen baseline differs from the paired source binding')
    for field in source_keys - {'source_job_id', 'commit_sha'}:
        shared._match(source[field], shared._HASH, 'frozen baseline ' + field)
    if kind == 'question':
        expected = questions_for(context['issue'])
        if bundle['intake'] != expected or bundle['questions'] != expected['questions']:
            raise ValueError('Frozen baseline differs from exact intake templates')
    else:
        checks = bundle['checks']
        if not isinstance(checks, list) or len(checks) > 3:
            raise ValueError('Frozen saved check baseline exceeds three checks')
        hypotheses = context['hypotheses']
        initial = list(dict.fromkeys(row['falsification_test'] for row in hypotheses))[:3]
        for index, row in enumerate(checks, 1):
            shared._fields(row, {'id', 'text', 'kind', 'execution_status', 'hypothesis_ids',
                'evidence_ids', 'reference_role'}, 'frozen saved report check')
            shared._text(row['text'], 'frozen saved report procedure', 100000)
            linked = [hypothesis for hypothesis in hypotheses if hypothesis['falsification_test'] == row['text']]
            if (row['id'] != 'check_' + str(index) or row['kind'] != 'proposed_check'
                    or row['execution_status'] != 'not_executed_by_issueintel'
                    or row['reference_role'] != 'hypothesis_source_link'
                    or row['hypothesis_ids'] != [hypothesis['id'] for hypothesis in linked]
                    or row['evidence_ids'] != list(dict.fromkeys(eid for hypothesis in linked
                        for eid in hypothesis['support_ids'] + hypothesis['counter_ids']))):
                raise ValueError('Frozen baseline check source links changed')
        if [row['text'] for row in checks[:len(initial)]] != initial:
            raise ValueError('Frozen baseline differs from saved hypothesis check order')
    if alternative['proposals'] != _baseline_proposals(bundle, kind, language):
        raise ValueError('Frozen baseline presentation differs from its deterministic derivation')


def validate_review(organizer, display):
    """Check frozen presentation/mapping seals; the adapter replays raw sources."""
    shared._fields(organizer, _ORGANIZER_FIELDS, 'paired organizer')
    shared._fields(display, _DISPLAY_FIELDS, 'paired display')
    shared._hash(organizer, 'Paired organizer')
    shared._hash(display, 'Paired display')
    selection = validate_selection(organizer['selection'])
    count = len(selection['pairs'])
    shared._identities(organizer['review_id'], organizer['blind_ids'], organizer['order'], count)
    _orientations(organizer['orientations'], count)
    if (organizer['schema'] != 'usefulness_pair_organizer_v2'
            or display['schema'] != 'usefulness_pair_display_v2'
            or display['review_id'] != organizer['review_id'] or display['language'] != selection['language']
            or display['rubric'] != _rubric(selection['language'])
            or display['limitations'] != list(LIMITATIONS) or organizer['limitations'] != list(LIMITATIONS)
            or organizer['display_sha256'] != display['sha256']):
        raise ValueError('Frozen paired identity, rubric or limitations changed')
    for value in (organizer['pairs'], organizer['derivations'], display['pairs']):
        if not isinstance(value, list) or len(value) != count:
            raise ValueError('Frozen pair inventory changed')
    by_id = {}
    for card, expected in zip(display['pairs'], organizer['order']):
        shared._fields(card, {'blind_id', 'kind', 'context', 'alternatives'}, 'paired blind card')
        if card['blind_id'] != expected:
            raise ValueError('Frozen paired card order changed')
        shared._fields(card['context'], {'issue', 'repository', 'revision', 'hypotheses', 'evidence'}, 'paired common context')
        shared._fields(card['alternatives'], {'A', 'B'}, 'paired alternatives')
        by_id[expected] = card
    for pair, identifier, orientation, mapping, derived in zip(selection['pairs'], organizer['blind_ids'],
            organizer['orientations'], organizer['pairs'], organizer['derivations']):
        card = by_id[identifier]
        if mapping != _mapping(pair, identifier, orientation, card) or card['kind'] != pair['kind']:
            raise ValueError('Frozen paired mapping or display contents changed')
        shared._fields(derived, {'blind_id', 'alternatives'}, 'paired deterministic derivations')
        shared._fields(derived['alternatives'], {'A', 'B'}, 'paired derivation alternatives')
        if derived['blind_id'] != identifier:
            raise ValueError('Frozen paired derivation order changed')
        for side in ('A', 'B'):
            alternative = card['alternatives'][side]
            shared._fields(alternative, {'status', 'proposals'}, 'paired proposal bundle')
            if (alternative['status'] != mapping['alternatives'][side]['status']
                    or not isinstance(alternative['proposals'], list)
                    or bool(alternative['proposals']) != (alternative['status'] == 'completed')):
                raise ValueError('Invalid frozen paired proposal status or inventory')
            baseline = mapping['alternatives'][side]['variant'] == _KINDS[pair['kind']][0]
            bundle = derived['alternatives'][side]
            if baseline:
                if not isinstance(bundle, dict) or bundle.get('schema') != _KINDS[pair['kind']][0]:
                    raise ValueError('Missing frozen deterministic derivation')
                _validate_frozen_baseline(bundle, pair, card['context'], alternative, selection['language'])
            elif bundle is not None:
                raise ValueError('Native output cannot be labelled deterministic derivation')
    return {'organizer': deepcopy(organizer), 'display': deepcopy(display)}


def _ratings(raw, organizer):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 2_000_000:
        raise ValueError('Invalid or oversized paired ratings CSV bytes')
    try:
        text = raw.decode('utf-8')
        reader = csv.reader(io.StringIO(text.removeprefix('\ufeff'), newline=''), strict=True)
        if next(reader) != list(RATING_FIELDS):
            raise ValueError('Paired ratings CSV headers must match the frozen rubric exactly')
        expected = {pair['blind_id']: pair for pair in organizer['pairs']}
        rows, seen = {}, set()
        for values in reader:
            if len(values) != len(RATING_FIELDS):
                raise ValueError('Invalid paired ratings CSV column count')
            row = dict(zip(RATING_FIELDS, values))
            identifier = row['blind_id']
            if identifier in seen or identifier not in expected:
                raise ValueError('Duplicate or unknown paired blind ID')
            seen.add(identifier)
            pair = expected[identifier]
            if row['display_sha256'] != pair['display_sha256']:
                raise ValueError('Paired ratings display hash changed')
            for side in ('A', 'B'):
                labels = [row[side + '_' + field] for field in DIMENSIONS]
                if any(label not in _LABELS for label in labels):
                    raise ValueError('Paired ratings must be yes, no, uncertain or blank')
                if pair['alternatives'][side]['status'] != 'completed' and any(labels):
                    raise ValueError('Noncompleted paired bundles cannot receive quality labels')
                shared._text(row[side + '_rationale'], 'paired review rationale', 4000, empty=not any(labels))
            if row['preference'] not in _PREFERENCES:
                raise ValueError('Paired preference must be A, B, tie, uncertain or blank')
            if row['preference'] and any(pair['alternatives'][side]['status'] != 'completed' for side in ('A', 'B')):
                raise ValueError('Paired preference requires both alternatives completed')
            shared._text(row['preference_rationale'], 'paired preference rationale', 4000, empty=not row['preference'])
            rows[identifier] = row
        if seen != set(expected):
            raise ValueError('Paired ratings must include every frozen card exactly once')
    except (UnicodeError, csv.Error, StopIteration):
        raise ValueError('Invalid paired ratings UTF-8 CSV') from None
    return [rows[identifier] for identifier in organizer['order']], text


def rating_receipt(organizer, display, csv_bytes, reviewer_bytes, *, supersedes=None, correction_reason=''):
    """Retain exact declared judgments and reviewer provenance without inference."""
    validate_review(organizer, display)
    if supersedes is None:
        if correction_reason != '':
            raise ValueError('A correction reason requires an explicit superseded receipt')
    else:
        shared._match(supersedes, shared._HASH, 'superseded paired rating SHA256')
        shared._text(correction_reason, 'paired correction reason', 2000)
    reviewer, reviewer_text = shared._reviewer(reviewer_bytes)
    rows, csv_text = _ratings(csv_bytes, organizer)
    return shared._seal({'schema': 'usefulness_pair_rating_receipt_v2', 'review_id': organizer['review_id'],
        'organizer_sha256': organizer['sha256'], 'display_sha256': display['sha256'],
        'csv_utf8': csv_text, 'csv_sha256': hashlib.sha256(csv_bytes).hexdigest(),
        'reviewer_utf8': reviewer_text, 'reviewer_sha256': hashlib.sha256(reviewer_bytes).hexdigest(),
        'reviewer': reviewer, 'rows': rows, 'supersedes': supersedes, 'correction_reason': correction_reason,
        'limitations': list(LIMITATIONS)})


def validate_rating_receipt(organizer, display, receipt):
    shared._fields(receipt, shared._RECEIPT_FIELDS, 'paired rating receipt')
    shared._hash(receipt, 'Paired rating receipt')
    if not isinstance(receipt['csv_utf8'], str) or not isinstance(receipt['reviewer_utf8'], str):
        raise ValueError('Paired rating receipt must retain exact UTF-8 texts')
    try:
        expected = rating_receipt(organizer, display, receipt['csv_utf8'].encode('utf-8'),
            receipt['reviewer_utf8'].encode('utf-8'), supersedes=receipt['supersedes'],
            correction_reason=receipt['correction_reason'])
    except UnicodeError:
        raise ValueError('Invalid retained paired rating UTF-8') from None
    if receipt != expected:
        raise ValueError('Paired rating receipt differs from its retained exact sources')
    return deepcopy(receipt)


def _coverage(organizer):
    groups = {}
    for pair in organizer['pairs']:
        for variant in pair['alternatives'].values():
            key = (variant['condition_id'], pair['kind'])
            group = groups.setdefault(key, {'condition_id': key[0], 'kind': key[1],
                'variant': variant['variant'], 'selected': 0, **{status: 0 for status in _STATUSES}, 'unit_ids': []})
            group['selected'] += 1
            group[variant['status']] += 1
            if pair['unit_id'] not in group['unit_ids']:
                group['unit_ids'].append(pair['unit_id'])
    return [dict(groups[key], case_clusters=len(groups[key]['unit_ids'])) for key in sorted(groups)]


def _dimension_counts(labels, denominator):
    counts = {'yes': 0, 'no': 0, 'uncertain': 0, 'missing': 0}
    for label in labels:
        counts[label or 'missing'] += 1
    known = counts['yes'] + counts['no']
    return dict(counts, known_rate=counts['yes'] / known if known else None,
        observed_yield_bounds={'lower': counts['yes'] / denominator,
            'upper': (counts['yes'] + counts['uncertain'] + counts['missing']) / denominator,
            'denominator': denominator})


def score_reviews(organizer, display, receipts):
    """Describe declared paired judgments, preserving status and case clusters."""
    validate_review(organizer, display)
    if not isinstance(receipts, list) or len(receipts) > 1000:
        raise ValueError('Provide at most 1000 paired rating receipts')
    validated = [validate_rating_receipt(organizer, display, receipt) for receipt in receipts]
    active, histories = shared._active_receipts(validated)
    coverage = _coverage(organizer)
    groups, preferences = [], []
    binary, uncertain, definite_preferences, uncertain_preferences = 0, 0, 0, 0
    for receipt in active:
        rows = {row['blind_id']: row for row in receipt['rows']}
        for scope in coverage:
            selected = [(pair, side) for pair in organizer['pairs'] for side in ('A', 'B')
                if pair['kind'] == scope['kind'] and pair['alternatives'][side]['condition_id'] == scope['condition_id']]
            dimensions = {}
            for field in DIMENSIONS:
                labels = [rows[pair['blind_id']][side + '_' + field] for pair, side in selected
                          if pair['alternatives'][side]['status'] == 'completed']
                dimensions[field] = _dimension_counts(labels, scope['selected'])
                binary += dimensions[field]['yes'] + dimensions[field]['no']
                uncertain += dimensions[field]['uncertain']
            groups.append({'reviewer': deepcopy(receipt['reviewer']), 'receipt_sha256': receipt['sha256'],
                'condition_id': scope['condition_id'], 'kind': scope['kind'], 'variant': scope['variant'],
                'coverage': deepcopy(scope), 'dimensions': dimensions})
        contrasts = defaultdict(list)
        for pair in organizer['pairs']:
            baseline_name = _KINDS[pair['kind']][0]
            baseline_side = next(side for side in ('A', 'B') if pair['alternatives'][side]['variant'] == baseline_name)
            native_side = 'B' if baseline_side == 'A' else 'A'
            key = (pair['kind'], pair['alternatives'][baseline_side]['condition_id'], pair['alternatives'][native_side]['condition_id'])
            contrasts[key].append((pair, baseline_side, native_side))
        for key, selected in sorted(contrasts.items()):
            counts = {'baseline': 0, 'native': 0, 'tie': 0, 'uncertain': 0, 'missing': 0, 'ineligible': 0}
            for pair, baseline_side, native_side in selected:
                if any(pair['alternatives'][side]['status'] != 'completed' for side in ('A', 'B')):
                    counts['ineligible'] += 1
                    continue
                label = rows[pair['blind_id']]['preference']
                condition = 'baseline' if label == baseline_side else 'native' if label == native_side else label or 'missing'
                counts[condition] += 1
                definite_preferences += label in ('A', 'B', 'tie')
                uncertain_preferences += label == 'uncertain'
            preferences.append({'reviewer': deepcopy(receipt['reviewer']), 'receipt_sha256': receipt['sha256'],
                'kind': key[0], 'baseline_condition_id': key[1], 'native_condition_id': key[2],
                'selected_pairs': len(selected), 'case_clusters': len({pair['unit_id'] for pair, _, _ in selected}),
                'eligible_pairs': len(selected) - counts['ineligible'], 'counts': counts})
    units = sorted({pair['unit_id'] for pair in organizer['pairs']})
    status = ('descriptive_declared_reviewer_ratings' if binary or definite_preferences else
              'uncertain_only_no_binary_judgments' if uncertain or uncertain_preferences else 'unreviewed_no_nonblank_ratings')
    return shared._seal({'schema': 'usefulness_pair_scores_v2', 'review_id': organizer['review_id'],
        'organizer_sha256': organizer['sha256'], 'display_sha256': display['sha256'],
        'rubric_schema': 'usefulness_pair_rubric_v2', 'coverage': coverage,
        'selected_pairs': len(organizer['pairs']), 'case_clusters': {'count': len(units), 'unit_ids': units},
        'active_receipts': histories, 'groups': groups, 'paired_preferences': preferences,
        'quality_status': status, 'independent_human_validation': 'not_established',
        'inter_rater_agreement': 'not_computed', 'causal_efficacy': 'not_measured',
        'case_cluster_inference': 'not_computed',
        'observed_yield_definition': ('For each declared reviewer, condition and kind, lower=yes/all selected bundles; '
            'upper=(yes+uncertain+completed-but-blank)/all selected bundles. Empty, failed and unavailable retain '
            'the denominator without negative quality labels. Known rates exclude uncertain and missing labels. '
            'Preferences are descriptive counts after unblinding frozen orientation; cards and reviewers are not independent cases.'),
        'limitations': list(LIMITATIONS)})
