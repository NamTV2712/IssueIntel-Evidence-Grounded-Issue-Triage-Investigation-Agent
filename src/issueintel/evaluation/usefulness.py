"""Offline, source-bound review of explicitly selected product proposals.

This module reads no files, repositories, environment, stores or providers.  Its
native validators establish consistency of included saved bytes, not the source
author's identity, Git availability, successful execution or semantic quality.
The filesystem adapter owns private storage and append-only import admission.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import unicodedata

from issueintel.annotations.public_inputs import model_case, model_evidence, model_hypotheses
from issueintel.runtime.io import digest


DIMENSIONS = ('specificity', 'grounding', 'discrimination', 'feasibility', 'useful_next_action')
RATING_FIELDS = ('blind_id', 'display_sha256', *DIMENSIONS, 'rationale')
_HASH = re.compile(r'[a-f0-9]{64}')
_BLIND = re.compile(r'[a-f0-9]{24}')
_REVIEW = re.compile(r'[a-f0-9]{32}')
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}')
_SELECTION_FIELDS = {'schema', 'purpose', 'protocol_id', 'language', 'slots'}
_SLOT_FIELDS = {'unit_id', 'condition_id', 'kind', 'status', 'reason',
                'source_job_id', 'pack', 'artifact'}
_ORGANIZER_FIELDS = {'schema', 'review_id', 'selection', 'blind_ids', 'order',
                     'slots', 'display_sha256', 'limitations', 'sha256'}
_DISPLAY_FIELDS = {'schema', 'review_id', 'language', 'rubric', 'slots', 'limitations', 'sha256'}
_CARD_FIELDS = {'blind_id', 'kind', 'status', 'context', 'proposals'}
_REVIEWER_FIELDS = {'schema', 'reviewer_id', 'kind', 'assistance', 'relationship',
                    'model_id', 'reviewed_at', 'declaration'}
_RECEIPT_FIELDS = {'schema', 'review_id', 'organizer_sha256', 'display_sha256',
                   'csv_utf8', 'csv_sha256', 'reviewer_utf8', 'reviewer_sha256',
                   'reviewer', 'rows', 'supersedes', 'correction_reason',
                   'limitations', 'sha256'}
_STATUSES = ('completed', 'empty', 'failed', 'unavailable')
_LABELS = {'', 'yes', 'no', 'uncertain'}
LIMITATIONS = (
    'Only explicitly selected saved product exports are included; selection coverage and preregistration are not established.',
    'Raw-byte hashes and native source validation establish included-byte consistency, not source authority, authorship or full Git replay.',
    'One review card represents one saved proposal bundle, not an independent case for each question or check.',
    'Completed and empty statuses are checked against native artifacts; failed and unavailable are organizer declarations, not measured job/provider failures or proven source absence.',
    'Condition, provider, model and local metadata are hidden; recognizable source wording can still reveal a condition, so perfect blinding is not claimed.',
    'Questions, checks and predictions are unverified proposals; no target code or proposed check is executed by this tool.',
    'Reviewer identity, independence and assistance are declarations, not authenticated or independently verified facts.',
    'Human, agent and model ratings remain separate; blanks are missing ratings, not negative labels or human validation.',
    'Descriptive ratings do not establish causal efficacy, improvement, inter-rater agreement or benchmark/holdout performance.',
)
_RUBRIC_TEXT = {
    'en': {
        'specificity': 'Are the proposals concrete and specific to this issue and saved hypotheses?',
        'grounding': 'Are the proposed distinctions and expectations justified by the supplied evidence, with unknowns retained?',
        'discrimination': 'Could the requested observation or distinct outcomes help distinguish the saved hypotheses?',
        'feasibility': 'Is at least one proposed next action feasible and sufficiently clear, including necessary controls?',
        'useful_next_action': 'Would this bundle help choose a useful next investigation action, without treating a proposal as a verified cause?',
    },
    'vi': {
        'specificity': 'Các đề xuất có cụ thể cho issue và các giả thuyết đã lưu không?',
        'grounding': 'Các khác biệt và dự đoán có căn cứ từ bằng chứng được cung cấp, đồng thời giữ rõ điều chưa biết không?',
        'discrimination': 'Quan sát được hỏi hoặc các kết quả khác nhau có thể giúp phân biệt các giả thuyết đã lưu không?',
        'feasibility': 'Có ít nhất một bước tiếp theo khả thi, đủ rõ và nêu các điều kiện kiểm soát cần thiết không?',
        'useful_next_action': 'Bộ đề xuất có giúp chọn một bước điều tra hữu ích mà không coi đề xuất là nguyên nhân đã xác minh không?',
    },
}


def _seal(value):
    return dict(value, sha256=digest(value))


def _fields(value, expected, name):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('Invalid ' + name + ' fields')


def _text(value, name, maximum=4000, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum
            or (not empty and not value.strip())
            or any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} and char not in '\t\n\r'
                   for char in value)):
        raise ValueError('Invalid ' + name)
    return value


def _match(value, pattern, name):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError('Invalid ' + name)


def _hash(value, name):
    _match(value.get('sha256') if isinstance(value, dict) else None, _HASH, name + ' hash')
    if value['sha256'] != digest({key: row for key, row in value.items() if key != 'sha256'}):
        raise ValueError(name + ' hash mismatch')


def _reference(value):
    _fields(value, {'path', 'sha256'}, 'selected source reference')
    path = _text(value['path'], 'selected relative JSON path', 1000)
    parts = path.split('/')
    if (len(parts) < 2 or parts[0] != 'artifacts' or not path.endswith('.json')
            or '\\' in path or ':' in path or any(part in {'', '.', '..'} for part in parts)
            or any(char in path for char in '\t\n\r')):
        raise ValueError('Selected source must be a workspace-relative artifacts JSON path')
    _match(value['sha256'], _HASH, 'selected raw-byte SHA256')


def validate_selection(selection):
    """Validate explicit selection metadata; perform no discovery or I/O."""
    _fields(selection, _SELECTION_FIELDS, 'usefulness selection')
    if selection['schema'] != 'usefulness_selection_v1':
        raise ValueError('Invalid usefulness selection schema')
    _text(selection['purpose'], 'review purpose', 2000)
    _match(selection['protocol_id'], _IDENTIFIER, 'review protocol ID')
    if selection['language'] not in ('en', 'vi'):
        raise ValueError('Review language must be en or vi')
    slots = selection['slots']
    if not isinstance(slots, list) or not 1 <= len(slots) <= 100:
        raise ValueError('Select between one and 100 proposal bundles')
    seen = set()
    refs = {}
    for slot in slots:
        _fields(slot, _SLOT_FIELDS, 'selected proposal slot')
        for field in ('unit_id', 'condition_id'):
            _match(slot[field], _IDENTIFIER, field)
        if slot['kind'] not in ('question', 'check') or slot['status'] not in _STATUSES:
            raise ValueError('Invalid selected proposal kind or status')
        identity = (slot['unit_id'], slot['condition_id'], slot['kind'])
        if identity in seen:
            raise ValueError('Duplicate unit, condition and proposal kind')
        seen.add(identity)
        _text(slot['reason'], 'selected status reason', 2000,
              empty=slot['status'] == 'completed')
        _match(slot['source_job_id'], _BLIND, 'selected source job ID')
        _reference(slot['pack'])
        if slot['status'] in ('completed', 'empty'):
            _reference(slot['artifact'])
        elif slot['artifact'] is not None:
            raise ValueError('Failed or unavailable slots cannot include an artifact')
        if slot['status'] == 'empty' and slot['kind'] != 'check':
            raise ValueError('Only a native empty check plan can have empty status')
        for ref in (slot['pack'], slot['artifact']):
            if ref is not None:
                if ref['path'] in refs and refs[ref['path']] != ref['sha256']:
                    raise ValueError('One selected path has conflicting raw-byte hashes')
                refs[ref['path']] = ref['sha256']
    return deepcopy(selection)


def _json_bytes(raw, name, *, maximum=16_000_000):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= maximum:
        raise ValueError('Invalid or oversized ' + name + ' bytes')
    try:
        text = raw.decode('utf-8')
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError('Duplicate JSON field')
                result[key] = value
            return result
        def invalid_constant(_):
            raise ValueError('Non-finite JSON number')
        result = json.loads(text.removeprefix('\ufeff'), object_pairs_hook=pairs,
                            parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError('Invalid ' + name + ' UTF-8 JSON') from None
    return result, text


def _product(pack):
    from issueintel.investigation.comparison import compare_packs
    from issueintel.repository.snapshot import validate_repo_path
    try:
        if (not isinstance(pack, dict) or not isinstance(pack.get('case'), dict)
                or 'split' in pack['case'] or 'instance_id' in pack['case']
                or not isinstance(pack['case'].get('case_id'), str)
                or pack['case']['case_id'].startswith(('verified:', 'live:', 'main_dev:', 'main_test:', 'temporal_candidate:'))
                or not isinstance(pack.get('snapshot'), dict)
                or pack['snapshot'].get('protocol') != 'benchmark_base'
                or not isinstance(pack.get('leakage_audit', {}), dict)
                or any(pack.get('leakage_audit', {}).get(field) is True for field in (
                    'strict_issue_time_verified', 'snapshot_issue_time_verified', 'original_query_verified'))):
            raise ValueError('Usefulness review requires an unverified product investigation')
        compare_packs(pack, pack)
        case = model_case(pack['case'])
        evidence = model_evidence(pack['evidence'])
        hypotheses = model_hypotheses((pack.get('annotation') or {}).get('hypotheses', []))
        if len(evidence) > 30 or len(hypotheses) > 5:
            raise ValueError('Selected product context exceeds review bounds')
        if (len(case['problem_statement']) > 100000
                or sum(len(row['text']) for row in evidence) > 150000):
            raise ValueError('Selected product text exceeds review bounds')
        _text(case['problem_statement'], 'selected issue description', 100000)
        for row in evidence:
            _text(row['id'], 'selected evidence ID', 200)
            _text(row.get('locator'), 'selected evidence locator', 2000)
            _text(row['text'], 'selected evidence text', 100000)
            if row['kind'] == 'code':
                locator = re.fullmatch(r'(.+):([1-9][0-9]*)-([1-9][0-9]*)', row['locator'])
                if locator is None or int(locator[2]) > int(locator[3]):
                    raise ValueError('Invalid selected code locator')
                validate_repo_path(locator[1])
        for row in hypotheses:
            for field, maximum in (('id', 200), ('statement', 8000), ('falsification_test', 8000)):
                _text(row[field], 'selected hypothesis ' + field, maximum)
        return case, evidence, hypotheses
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError('Invalid selected product source') from None


def _context(pack):
    case, evidence, hypotheses = _product(pack)
    return {
        'issue': case['problem_statement'],
        'repository': case.get('repository') or case.get('repo') or 'local_repository',
        'revision': {'requested': case.get('revision') or case.get('base_commit'),
                     'commit': pack['snapshot']['commit_sha']},
        'hypotheses': hypotheses,
        'evidence': [{field: row.get(field) for field in ('id', 'kind', 'locator', 'text', 'revision')}
                     for row in evidence],
    }


def _rubric(language):
    return {'schema': 'usefulness_rubric_v1', 'rating_unit': 'whole_saved_proposal_bundle',
            'scale': ['yes', 'no', 'uncertain', ''],
            'dimensions': [{'id': field, 'prompt': _RUBRIC_TEXT[language][field]}
                           for field in DIMENSIONS]}


def _identities(review_id, blind_ids, order, count):
    _match(review_id, _REVIEW, 'review ID')
    if not isinstance(blind_ids, list) or len(blind_ids) != count:
        raise ValueError('One blind ID is required for every selected bundle')
    for value in blind_ids:
        _match(value, _BLIND, 'blind review ID')
    if len(set(blind_ids)) != count:
        raise ValueError('Duplicate blind review ID')
    if (not isinstance(order, list) or len(order) != count
            or any(not isinstance(value, str) for value in order)
            or set(order) != set(blind_ids)):
        raise ValueError('Display order must permute all blind review IDs exactly once')


def build_review(selection, inputs, *, review_id, blind_ids, order):
    """Freeze selected native exports and a blinded, whole-bundle display."""
    from issueintel.investigation import adaptive_clarifications, check_plans

    selection = validate_selection(selection)
    _identities(review_id, blind_ids, order, len(selection['slots']))
    references = {ref['path']: ref['sha256'] for slot in selection['slots']
                  for ref in (slot['pack'], slot['artifact']) if ref is not None}
    if not isinstance(inputs, dict) or set(inputs) != set(references):
        raise ValueError('Inputs must contain exactly the selected source paths')
    if any(not isinstance(raw, bytes) for raw in inputs.values()) or sum(map(len, inputs.values())) > 128_000_000:
        raise ValueError('Selected source bytes exceed review bounds')
    parsed = {}
    for path, expected in references.items():
        if hashlib.sha256(inputs[path]).hexdigest() != expected:
            raise ValueError('Selected source raw-byte hash mismatch')
        parsed[path], _ = _json_bytes(inputs[path], 'selected source')
    cards, mappings = {}, []
    for slot, blind_id in zip(selection['slots'], blind_ids):
        pack = parsed[slot['pack']['path']]
        context = _context(pack)
        proposals = []
        if slot['artifact'] is not None:
            bundle = parsed[slot['artifact']['path']]
            module = adaptive_clarifications if slot['kind'] == 'question' else check_plans
            source = module.build_source(pack, slot['source_job_id'], slot['pack']['sha256'])
            if slot['kind'] == 'question':
                bundle = module.validate_bundle(bundle, source)
                language = selection['language']
                proposals = [{'id': row['id'], 'prompt': row['prompts'][language],
                              'purpose': row['purpose'][language], 'answer_use': row['answer_use'][language],
                              'hypothesis_ids': list(row['hypothesis_ids']), 'evidence_ids': list(row['evidence_ids'])}
                             for row in bundle['questions']]
            else:
                bundle = module.validate_plan(bundle, source)
                proposals = [{field: deepcopy(row[field]) for field in (
                    'id', 'procedure', 'assumptions', 'outcome_a', 'outcome_b', 'evidence_ids', 'predictions')}
                             for row in bundle['checks']]
            if bool(proposals) != (slot['status'] == 'completed'):
                raise ValueError('Selected proposal status differs from native artifact contents')
        card = {'blind_id': blind_id, 'kind': slot['kind'], 'status': slot['status'],
                'context': context, 'proposals': proposals}
        cards[blind_id] = card
        mappings.append({field: slot[field] for field in ('unit_id', 'condition_id', 'kind', 'status')}
                        | {'blind_id': blind_id, 'display_sha256': digest(card)})
    display = _seal({'schema': 'usefulness_display_v1', 'review_id': review_id,
                     'language': selection['language'], 'rubric': _rubric(selection['language']),
                     'slots': [cards[identifier] for identifier in order], 'limitations': list(LIMITATIONS)})
    organizer = _seal({'schema': 'usefulness_organizer_v1', 'review_id': review_id,
                       'selection': selection, 'blind_ids': list(blind_ids), 'order': list(order),
                       'slots': mappings, 'display_sha256': display['sha256'], 'limitations': list(LIMITATIONS)})
    validate_review(organizer, display)
    return {'organizer': organizer, 'display': display}


def validate_review(organizer, display):
    """Check frozen review bindings; source-byte reconstruction belongs to build."""
    _fields(organizer, _ORGANIZER_FIELDS, 'usefulness organizer')
    _fields(display, _DISPLAY_FIELDS, 'usefulness display')
    _hash(organizer, 'Usefulness organizer')
    _hash(display, 'Usefulness display')
    selection = validate_selection(organizer['selection'])
    _identities(organizer['review_id'], organizer['blind_ids'], organizer['order'], len(selection['slots']))
    if (organizer['schema'] != 'usefulness_organizer_v1'
            or display['schema'] != 'usefulness_display_v1'
            or display['review_id'] != organizer['review_id']
            or display['language'] != selection['language']
            or display['rubric'] != _rubric(selection['language'])
            or display['limitations'] != list(LIMITATIONS)
            or organizer['limitations'] != list(LIMITATIONS)
            or organizer['display_sha256'] != display['sha256']):
        raise ValueError('Frozen review identity, rubric or limitations changed')
    if (not isinstance(display['slots'], list) or len(display['slots']) != len(selection['slots'])
            or not isinstance(organizer['slots'], list) or len(organizer['slots']) != len(selection['slots'])):
        raise ValueError('Frozen review card inventory changed')
    by_id = {}
    for card, expected in zip(display['slots'], organizer['order']):
        _fields(card, _CARD_FIELDS, 'blind review card')
        if card['blind_id'] != expected:
            raise ValueError('Frozen blind review order changed')
        by_id[card['blind_id']] = card
    for mapping, slot, identifier in zip(organizer['slots'], selection['slots'], organizer['blind_ids']):
        expected = {field: slot[field] for field in ('unit_id', 'condition_id', 'kind', 'status')}
        expected.update(blind_id=identifier, display_sha256=digest(by_id[identifier]))
        if mapping != expected:
            raise ValueError('Frozen card mapping or display contents changed')
        card = by_id[identifier]
        if card['kind'] != slot['kind'] or card['status'] != slot['status']:
            raise ValueError('Blind card differs from its selected proposal slot')
        if (not isinstance(card['context'], dict) or set(card['context']) != {
                'issue', 'repository', 'revision', 'hypotheses', 'evidence'}
                or not isinstance(card['proposals'], list)
                or bool(card['proposals']) != (slot['status'] == 'completed')):
            raise ValueError('Invalid frozen card context or proposal inventory')
    return {'organizer': deepcopy(organizer), 'display': deepcopy(display)}


def _reviewer(raw):
    reviewer, text = _json_bytes(raw, 'reviewer declaration', maximum=32000)
    _fields(reviewer, _REVIEWER_FIELDS, 'reviewer declaration')
    if reviewer['schema'] != 'usefulness_reviewer_v1':
        raise ValueError('Invalid reviewer declaration schema')
    _match(reviewer['reviewer_id'], _IDENTIFIER, 'reviewer ID')
    if (reviewer['kind'] not in ('human', 'agent', 'model')
            or reviewer['assistance'] not in ('none', 'ai', 'unknown')
            or reviewer['relationship'] not in ('author', 'declared_independent', 'unknown')):
        raise ValueError('Invalid reviewer provenance declaration')
    requires_model = reviewer['kind'] in ('agent', 'model') or reviewer['assistance'] == 'ai'
    if reviewer['model_id'] is not None:
        _text(reviewer['model_id'], 'reviewer model ID', 200)
    elif requires_model:
        raise ValueError('Agent, model or AI-assisted review requires a model ID')
    if reviewer['kind'] == 'human' and reviewer['assistance'] == 'none' and reviewer['model_id'] is not None:
        raise ValueError('An unassisted human declaration cannot name an assisting model')
    timestamp = _text(reviewer['reviewed_at'], 'UTC review date', 40)
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', timestamp):
        raise ValueError('Reviewer date must be an explicit UTC timestamp')
    try:
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError('Reviewer date must use UTC')
    except ValueError:
        raise ValueError('Invalid UTC reviewer date') from None
    _text(reviewer['declaration'], 'reviewer declaration', 4000)
    return reviewer, text


def _ratings(raw, organizer, display):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 1_000_000:
        raise ValueError('Invalid or oversized ratings CSV bytes')
    try:
        text = raw.decode('utf-8')
        reader = csv.reader(io.StringIO(text.removeprefix('\ufeff'), newline=''), strict=True)
        if next(reader) != list(RATING_FIELDS):
            raise ValueError('Ratings CSV headers must match the frozen rubric exactly')
        rows, seen = {}, set()
        expected = {row['blind_id']: row for row in organizer['slots']}
        for values in reader:
            if len(values) != len(RATING_FIELDS):
                raise ValueError('Invalid ratings CSV column count')
            row = dict(zip(RATING_FIELDS, values))
            identifier = row['blind_id']
            if identifier in seen or identifier not in expected:
                raise ValueError('Duplicate or unknown blind review ID')
            seen.add(identifier)
            if row['display_sha256'] != expected[identifier]['display_sha256']:
                raise ValueError('Ratings CSV display hash changed')
            if any(row[field] not in _LABELS for field in DIMENSIONS):
                raise ValueError('Ratings must be yes, no, uncertain or blank')
            if expected[identifier]['status'] != 'completed' and any(row[field] for field in DIMENSIONS):
                raise ValueError('Noncompleted proposal bundles cannot receive quality labels')
            _text(row['rationale'], 'review rationale', 4000,
                  empty=not any(row[field] for field in DIMENSIONS))
            rows[identifier] = row
        if seen != set(expected):
            raise ValueError('Ratings CSV must contain every frozen review card exactly once')
    except (UnicodeError, csv.Error, StopIteration):
        raise ValueError('Invalid ratings UTF-8 CSV') from None
    return [rows[identifier] for identifier in organizer['order']], text


def rating_receipt(organizer, display, csv_bytes, reviewer_bytes, *, supersedes=None, correction_reason=''):
    """Validate a declared review without inferring human independence or truth."""
    validate_review(organizer, display)
    if supersedes is None:
        if correction_reason != '':
            raise ValueError('A correction reason requires an explicit superseded receipt')
    else:
        _match(supersedes, _HASH, 'superseded rating receipt SHA256')
        _text(correction_reason, 'rating correction reason', 2000)
    reviewer, reviewer_text = _reviewer(reviewer_bytes)
    rows, csv_text = _ratings(csv_bytes, organizer, display)
    return _seal({'schema': 'usefulness_rating_receipt_v1', 'review_id': organizer['review_id'],
                  'organizer_sha256': organizer['sha256'], 'display_sha256': display['sha256'],
                  'csv_utf8': csv_text, 'csv_sha256': hashlib.sha256(csv_bytes).hexdigest(),
                  'reviewer_utf8': reviewer_text, 'reviewer_sha256': hashlib.sha256(reviewer_bytes).hexdigest(),
                  'reviewer': reviewer, 'rows': rows, 'supersedes': supersedes,
                  'correction_reason': correction_reason, 'limitations': list(LIMITATIONS)})


def validate_rating_receipt(organizer, display, receipt):
    """Reconstruct all derived fields from retained exact UTF-8 input texts."""
    _fields(receipt, _RECEIPT_FIELDS, 'usefulness rating receipt')
    _hash(receipt, 'Usefulness rating receipt')
    if not isinstance(receipt['csv_utf8'], str) or not isinstance(receipt['reviewer_utf8'], str):
        raise ValueError('Rating receipt must retain exact UTF-8 input texts')
    try:
        expected = rating_receipt(organizer, display, receipt['csv_utf8'].encode('utf-8'),
                                  receipt['reviewer_utf8'].encode('utf-8'),
                                  supersedes=receipt['supersedes'], correction_reason=receipt['correction_reason'])
    except UnicodeError:
        raise ValueError('Invalid retained UTF-8 rating input') from None
    if receipt != expected:
        raise ValueError('Rating receipt differs from its frozen sources, declarations or labels')
    return deepcopy(receipt)


def _active_receipts(receipts):
    unique = {}
    for receipt in receipts:
        unique[receipt['sha256']] = receipt
    by_reviewer = defaultdict(dict)
    for sha, receipt in unique.items():
        by_reviewer[receipt['reviewer']['reviewer_id']][sha] = receipt
    active = []
    histories = []
    for reviewer_id, nodes in sorted(by_reviewer.items()):
        roots, children = [], {}
        for sha, receipt in nodes.items():
            parent = receipt['supersedes']
            if parent is None:
                roots.append(sha)
            else:
                if parent not in nodes:
                    raise ValueError('Rating correction has a missing or different-reviewer parent')
                if parent in children:
                    raise ValueError('Rating corrections cannot branch')
                children[parent] = sha
        if len(roots) != 1:
            raise ValueError('Each reviewer requires exactly one original receipt')
        chain, current = [], roots[0]
        while current is not None:
            if current in chain:
                raise ValueError('Rating correction cycle')
            chain.append(current)
            current = children.get(current)
        if len(chain) != len(nodes):
            raise ValueError('Disconnected or cyclic rating correction chain')
        leaf = nodes[chain[-1]]
        active.append(leaf)
        histories.append({'reviewer_id': reviewer_id, 'receipt_sha256': leaf['sha256'],
                          'history_sha256': chain})
    return active, histories


def _coverage(organizer):
    groups = {}
    for slot in organizer['slots']:
        key = (slot['condition_id'], slot['kind'])
        if key not in groups:
            groups[key] = {'condition_id': key[0], 'kind': key[1], 'selected': 0,
                           **{status: 0 for status in _STATUSES}}
        groups[key]['selected'] += 1
        groups[key][slot['status']] += 1
    return [groups[key] for key in sorted(groups)]


def score_reviews(organizer, display, receipts):
    """Describe active declared ratings, preserving failures and missing labels."""
    validate_review(organizer, display)
    if not isinstance(receipts, list) or len(receipts) > 1000:
        raise ValueError('Provide at most 1000 frozen rating receipts')
    validated = [validate_rating_receipt(organizer, display, receipt) for receipt in receipts]
    active, histories = _active_receipts(validated)
    coverage = _coverage(organizer)
    groups = []
    binary, uncertain = 0, 0
    for receipt in active:
        rows = {row['blind_id']: row for row in receipt['rows']}
        for scope in coverage:
            selected = [row for row in organizer['slots']
                        if row['condition_id'] == scope['condition_id'] and row['kind'] == scope['kind']]
            dimensions = {}
            for field in DIMENSIONS:
                counts = {'yes': 0, 'no': 0, 'uncertain': 0, 'missing': 0}
                for slot in selected:
                    if slot['status'] == 'completed':
                        label = rows[slot['blind_id']][field]
                        counts[label or 'missing'] += 1
                known = counts['yes'] + counts['no']
                binary += known
                uncertain += counts['uncertain']
                dimensions[field] = dict(counts,
                    known_rate=counts['yes'] / known if known else None,
                    observed_yield_bounds={
                        'lower': counts['yes'] / scope['selected'],
                        'upper': (counts['yes'] + counts['uncertain'] + counts['missing']) / scope['selected'],
                        'denominator': scope['selected'],
                    })
            groups.append({'reviewer': deepcopy(receipt['reviewer']),
                           'receipt_sha256': receipt['sha256'],
                           'condition_id': scope['condition_id'], 'kind': scope['kind'],
                           'coverage': deepcopy(scope), 'dimensions': dimensions})
    status = ('descriptive_declared_reviewer_ratings' if binary else
              'uncertain_only_no_binary_judgments' if uncertain else 'unreviewed_no_nonblank_ratings')
    return _seal({'schema': 'usefulness_scores_v1', 'review_id': organizer['review_id'],
                  'organizer_sha256': organizer['sha256'], 'display_sha256': display['sha256'],
                  'rubric_schema': 'usefulness_rubric_v1', 'coverage': coverage,
                  'active_receipts': histories, 'groups': groups, 'quality_status': status,
                  'independent_human_validation': 'not_established',
                  'inter_rater_agreement': 'not_computed', 'causal_efficacy': 'not_measured',
                  'observed_yield_definition': (
                      'Within each declared reviewer, condition and proposal kind, lower=yes/all selected bundles; '
                      'upper=(yes+uncertain+completed-but-blank)/all selected bundles. Empty, failed and unavailable '
                      'bundles produce no observed useful proposal and contribute zero to this yield; they are not '
                      'assigned negative quality labels. Known rate excludes uncertain and missing ratings.'),
                  'limitations': list(LIMITATIONS)})
