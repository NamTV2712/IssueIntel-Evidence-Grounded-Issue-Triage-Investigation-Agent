"""Source-bound comparison of saved hypotheses, without inference or ranking.

The view describes citation roles and recorded gaps.  Neither source reuse nor
different asserted roles establishes semantic agreement, contradiction or truth.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import html
import re

from issueintel.runtime.io import digest
from .report import (
    REPORT_SCHEMA, _check_shape, _code_locator, _code_url, _context_url,
    _github_origin, validate_report,
)


SCHEMA = 'hypothesis_review_v1'
GAP_CODES = (
    'missing_counter_in_retrieved_scope', 'no_code_support_citation',
    'model_review_not_recorded', 'model_review_unsupported',
    'model_review_contradicted', 'model_review_uncertain',
)
LIMITATIONS = [
    'Citation roles are saved model assertions, not established semantic support or causal truth.',
    'A shared source is one saved evidence record; reuse does not prove agreement or independence.',
    'Different asserted roles for a shared source are not a verified semantic contradiction.',
    'Missing counter-evidence refers only to the retrieved scope and does not establish that a hypothesis is true.',
    'Model critique is automatic, may share model bias, and is not independent human validation.',
    'Saved order and inventory counts are not confidence, quality rankings or a selected cause.',
    'Saved checks are proposals, not executed tests; a shared check does not establish its ability to distinguish hypotheses.',
]
_GAP_TEXT = {
    'missing_counter_in_retrieved_scope': 'No counter citation was recorded in the retrieved scope. Inspect the saved proposed check; absence of a counter citation does not support the cause.',
    'no_code_support_citation': 'The saved support IDs contain no code citation. Inspect the cited issue or repository context before treating the claim as code-supported.',
    'model_review_not_recorded': 'No automatic model assessment was recorded. Valid citation references do not establish semantic support.',
    'model_review_unsupported': 'The automatic critic marked this claim unsupported. Inspect its reason and citations alongside the original support.',
    'model_review_contradicted': 'The automatic critic marked this claim contradicted. Inspect its reason and citations; this is not a verified contradiction.',
    'model_review_uncertain': 'The automatic critic remained uncertain. Inspect the saved reason and proposed check before drawing a conclusion.',
}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def _array(items, maximum=200):
    return {'type': 'array', 'items': items, 'maxItems': maximum}


_PROPERTIES = REPORT_SCHEMA['properties']
_ID = {'type': 'string', 'minLength': 1}
_IDS = _array(_ID)
_TEXT = {'type': 'string', 'minLength': 1}
_SHA = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
_NULL = {'type': 'null'}
_SOURCE = deepcopy(_PROPERTIES['source']['properties'])
_SOURCE['report_sha256'] = _SHA
REVIEW_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'https://issueintel.local/schemas/hypothesis_review_v1',
    'title': 'IssueIntel saved hypothesis comparison',
    **_object({
        'schema': {'const': SCHEMA}, 'source': _object(_SOURCE),
        'issue': _object({key: deepcopy(_PROPERTIES['issue']['properties'][key])
                          for key in ('case_id', 'repository', 'issue_url')}),
        'hypotheses': _array(_object({
            'id': _ID, 'statement': _TEXT, 'support_ids': _IDS,
            'counter_ids': _IDS, 'counter_status': {'enum': ['found', 'not_found']},
            'citation_integrity': {'const': 'valid'},
            'semantic_support': {'const': 'unverified'},
            'model_review': _object({
                'assessment': {'anyOf': [{'enum': ['supported', 'unsupported', 'contradicted', 'uncertain']}, _NULL]},
                'reason': {'anyOf': [_TEXT, _NULL]}, 'evidence_ids': _IDS,
                'independent_human': {'const': False},
            }),
            'check_ids': _IDS,
            'gap_codes': _array({'enum': list(GAP_CODES)}, len(GAP_CODES)),
        }), 5),
        'pairs': _array(_object({
            'left_hypothesis_id': _ID, 'right_hypothesis_id': _ID,
            'shared_support_ids': _IDS, 'shared_counter_ids': _IDS,
            'left_support_right_counter_ids': _IDS,
            'left_counter_right_support_ids': _IDS, 'shared_check_ids': _IDS,
        }), 10),
        'checks': deepcopy(_PROPERTIES['next_checks']),
        'evidence': deepcopy(_PROPERTIES['evidence']),
        'correctness': {'const': 'unverified'},
        'independent_human': {'const': False},
        'evaluation_performed': {'const': False}, 'model_calls': {'const': 0},
        'limitations': _array(_TEXT, len(LIMITATIONS)), 'sha256': _SHA,
    }),
}


def _gaps(hypothesis, evidence):
    result = []
    if hypothesis['counter_status'] == 'not_found':
        result.append('missing_counter_in_retrieved_scope')
    if not any(evidence[eid]['kind'] == 'code' for eid in hypothesis['support_ids']):
        result.append('no_code_support_citation')
    assessment = hypothesis['model_review']['assessment']
    if assessment is None:
        result.append('model_review_not_recorded')
    elif assessment != 'supported':
        result.append('model_review_' + assessment)
    return result


def _pairs(hypotheses, evidence, checks):
    pairs = []
    def intersection(left, right, inventory):
        common = set(left) & set(right)
        return [row['id'] for row in inventory if row['id'] in common]
    for index, left in enumerate(hypotheses):
        for right in hypotheses[index + 1:]:
            pairs.append({
                'left_hypothesis_id': left['id'], 'right_hypothesis_id': right['id'],
                'shared_support_ids': intersection(left['support_ids'], right['support_ids'], evidence),
                'shared_counter_ids': intersection(left['counter_ids'], right['counter_ids'], evidence),
                'left_support_right_counter_ids': intersection(left['support_ids'], right['counter_ids'], evidence),
                'left_counter_right_support_ids': intersection(left['counter_ids'], right['support_ids'], evidence),
                'shared_check_ids': intersection(left['check_ids'], right['check_ids'], checks),
            })
    return pairs


def _project(report):
    hypotheses = []
    for saved in report['hypotheses']:
        verification = saved['verification']
        row = {key: deepcopy(saved[key]) for key in (
            'id', 'statement', 'support_ids', 'counter_ids', 'counter_status')}
        row.update(citation_integrity='valid', semantic_support='unverified',
                   model_review={'assessment': verification['model_assessment'],
                                 'reason': verification['model_reason'],
                                 'evidence_ids': list(verification['model_evidence_ids']),
                                 'independent_human': False},
                   check_ids=[check['id'] for check in report['next_checks']
                              if row['id'] in check['hypothesis_ids']])
        row['gap_codes'] = _gaps(row, {item['id']: item for item in report['evidence']})
        hypotheses.append(row)
    cited = {eid for row in hypotheses for eid in (
        row['support_ids'] + row['counter_ids'] + row['model_review']['evidence_ids'])}
    cited.update(eid for check in report['next_checks'] for eid in check['evidence_ids'])
    evidence = [deepcopy(row) for row in report['evidence'] if row['id'] in cited]
    checks = deepcopy(report['next_checks'])
    result = {
        'schema': SCHEMA,
        'source': dict(deepcopy(report['source']), report_sha256=report['sha256']),
        'issue': {key: report['issue'][key] for key in ('case_id', 'repository', 'issue_url')},
        'hypotheses': hypotheses, 'pairs': _pairs(hypotheses, evidence, checks),
        'checks': checks, 'evidence': evidence, 'correctness': 'unverified',
        'independent_human': False, 'evaluation_performed': False, 'model_calls': 0,
        'limitations': list(LIMITATIONS),
    }
    result['sha256'] = digest(result)
    return result


def build_hypothesis_review(report):
    """Derive a fresh view from an already source-bound report without IO."""
    validate_report(report)
    result = _project(report)
    _validate_projection(result)
    return result


def validate_hypothesis_review(review, report):
    """Replay the whole projection against its report, including derived gaps.

    A repaired self-hash cannot substitute for a different source report.  The
    caller still owns checking the report against its exact saved pack bytes.
    """
    validate_report(report)
    _validate_projection(review)
    if review != _project(report):
        raise ValueError('Hypothesis review differs from its source report')
    return review


def _validate_projection(review):
    """Check a standalone export's shape, self-hash and internal closure.

    This protects presentation but does not authenticate the external report;
    use validate_hypothesis_review with that report to verify source binding.
    """
    _check_shape(review, REVIEW_SCHEMA, 'hypothesis_review')
    if digest({key: value for key, value in review.items() if key != 'sha256'}) != review['sha256']:
        raise ValueError('Hypothesis review content hash mismatch')
    if review['limitations'] != LIMITATIONS:
        raise ValueError('Hypothesis review omitted its comparison limits')
    evidence = {row['id']: row for row in review['evidence']}
    hypotheses = {row['id']: row for row in review['hypotheses']}
    checks = {row['id']: row for row in review['checks']}
    if (len(evidence) != len(review['evidence']) or len(hypotheses) != len(review['hypotheses'])
            or len(checks) != len(review['checks'])):
        raise ValueError('Ambiguous hypothesis review IDs')
    origin = _github_origin(review['issue'])
    if review['issue']['issue_url'] is not None and not origin:
        raise ValueError('Hypothesis review issue link is not canonical')
    for row in evidence.values():
        hashed = sha256(row['text'].encode('utf-8')).hexdigest()
        if (hashed != row['excerpt_sha256']
                or (not row['text_truncated'] and hashed != row['sha256'])):
            raise ValueError('Hypothesis review evidence excerpt hash mismatch')
        if row['kind'] == 'code':
            _, start, end = _code_locator(row['locator'])
            count = len(row['text'].split('\n'))
            if (row['revision'] != review['source']['commit_sha']
                    or row['url'] != _code_url(origin, row['revision'], row['locator'])
                    or (not row['text_truncated'] and end - start + 1 != count)
                    or end - start + 1 < count):
                raise ValueError('Hypothesis review code source binding failed')
        elif row['kind'] == 'issue':
            if (row['sha256'] != review['source']['query_sha256'] or row['revision'] is not None
                    or row['url'] != review['issue']['issue_url']):
                raise ValueError('Hypothesis review issue evidence binding failed')
        elif row['revision'] is not None or row['url'] != _context_url(origin, row['url']):
            raise ValueError('Hypothesis review repository context binding failed')
    def refs(values, known):
        if len(values) != len(set(values)) or any(value not in known for value in values):
            raise ValueError('Hypothesis review references duplicate or missing IDs')
    cited = set()
    for row in hypotheses.values():
        for key in ('support_ids', 'counter_ids'):
            refs(row[key], evidence)
            cited.update(row[key])
        model_review = row['model_review']
        refs(model_review['evidence_ids'], evidence)
        cited.update(model_review['evidence_ids'])
        if (not row['support_ids'] or set(row['support_ids']) & set(row['counter_ids'])
                or bool(row['counter_ids']) != (row['counter_status'] == 'found')):
            raise ValueError('Hypothesis review citation roles are inconsistent')
        if ((model_review['assessment'] is None) != (model_review['reason'] is None)
                or (model_review['assessment'] is None and model_review['evidence_ids'])
                or (model_review['assessment'] in {'supported', 'contradicted'} and not model_review['evidence_ids'])):
            raise ValueError('Hypothesis review model assessment is inconsistent')
        expected_checks = [check['id'] for check in review['checks'] if row['id'] in check['hypothesis_ids']]
        if not expected_checks or row['check_ids'] != expected_checks or row['gap_codes'] != _gaps(row, evidence):
            raise ValueError('Hypothesis review gaps or proposed checks differ from saved fields')
    for check in checks.values():
        refs(check['hypothesis_ids'], hypotheses)
        refs(check['evidence_ids'], evidence)
        cited.update(check['evidence_ids'])
        expected = list(dict.fromkeys(eid for hid in check['hypothesis_ids']
                                     for eid in hypotheses[hid]['support_ids'] + hypotheses[hid]['counter_ids']))
        if check['evidence_ids'] != expected:
            raise ValueError('Hypothesis review check citations are inconsistent')
    if set(evidence) != cited:
        raise ValueError('Hypothesis review includes uncited evidence')
    if review['pairs'] != _pairs(review['hypotheses'], review['evidence'], review['checks']):
        raise ValueError('Hypothesis review pairs differ from saved citation roles')
    return review


def _safe(value):
    text = html.escape(str(value), quote=False).replace('\r', '')
    # Escape colon as well as Markdown syntax so bare source URLs cannot become
    # automatic GFM links. Only the validated evidence URL is emitted as a link.
    return re.sub(r'([\\`*_{}\[\]()#!|>+\-.:])', r'\\\1', text).replace('@', '@\u200b')


def _quoted(value):
    return '\n'.join('> ' + line for line in _safe(value).split('\n'))


def render_markdown(review):
    """Render a literal, self-contained handoff; never read sources or run tests.

    Standalone rendering checks internal integrity.  Interfaces must first use
    validate_hypothesis_review(review, report) for the external source binding.
    """
    _validate_projection(review)
    refs = lambda values: ', '.join(_safe(value) for value in values) or 'none'
    source = review['source']
    lines = ['# IssueIntel hypothesis comparison', '',
        'Repository: ' + _safe(review['issue']['repository']),
        'Case: ' + _safe(review['issue']['case_id']),
        'Saved order is not a confidence ranking. Causal correctness: **unverified**.',
        'Model calls for this view: 0. Independent human validation: no.', '',
        '## Hypotheses and recorded gaps', '',
        '| Hypothesis | Support IDs | Counter IDs | Model review | Saved checks | Recorded gaps |',
        '| --- | --- | --- | --- | --- | --- |',
    ]
    for row in review['hypotheses']:
        lines.append('| ' + ' | '.join([
            _safe(row['id']), refs(row['support_ids']), refs(row['counter_ids']),
            _safe(row['model_review']['assessment'] or 'not recorded'),
            refs(row['check_ids']), refs(row['gap_codes']),
        ]) + ' |')
    if not review['hypotheses']:
        lines += ['', 'No hypotheses were generated in this saved report; no hypothesis comparison is inferred.']
    for row in review['hypotheses']:
        lines += ['', '### ' + _safe(row['id']), _quoted(row['statement']),
                  'Citation integrity: valid. Semantic support: unverified.']
        lines += ['- ' + _safe(_GAP_TEXT[code]) for code in row['gap_codes']]
        if row['model_review']['reason'] is not None:
            lines += ['Automatic model review (unverified): ' + _safe(row['model_review']['assessment']),
                      _quoted(row['model_review']['reason']),
                      'Model review evidence: ' + refs(row['model_review']['evidence_ids'])]
    lines += ['', '## Source reuse and different asserted roles', '',
        'These are exact intersections of saved IDs, not verified agreement or semantic contradiction.']
    if not review['pairs']:
        lines += ['', 'No hypothesis pairs are available.']
    for pair in review['pairs']:
        lines += ['', '### ' + _safe(pair['left_hypothesis_id']) + ' / ' + _safe(pair['right_hypothesis_id']),
            '- Shared support IDs: ' + refs(pair['shared_support_ids']),
            '- Shared counter IDs: ' + refs(pair['shared_counter_ids']),
            '- Left support / right counter IDs: ' + refs(pair['left_support_right_counter_ids']),
            '- Left counter / right support IDs: ' + refs(pair['left_counter_right_support_ids']),
            '- Shared saved check IDs: ' + refs(pair['shared_check_ids'])]
    lines += ['', '## Saved checks — proposed, not executed', '']
    for check in review['checks']:
        lines += ['### ' + _safe(check['id']), _quoted(check['text']),
                  'Hypotheses: ' + refs(check['hypothesis_ids']) + '; evidence: ' + refs(check['evidence_ids']) + '.', '']
    if not review['checks']:
        lines.append('No proposed checks were saved.')
    lines += ['', '## Referenced evidence', '']
    for row in review['evidence']:
        label = _safe(row['id'])
        if row['url']:
            label = '[' + label + '](' + row['url'] + ')'
        lines += ['- ' + label + ' — ' + _safe(row['locator']) + ' (' + row['kind'] + ')',
                  '  Revision: ' + (row['revision'] or 'not a pinned code revision'),
                  '  Full evidence SHA256: ' + row['sha256'],
                  _quoted(row['text']), 'Excerpt SHA256: ' + row['excerpt_sha256'] +
                  ('; excerpt truncated.' if row['text_truncated'] else '; complete evidence excerpt.')]
    if not review['evidence']:
        lines.append('No evidence is cited by hypotheses or saved checks.')
    lines += ['', '## Limits', '', *['- ' + _safe(text) for text in review['limitations']],
              '', '## Source binding', '',
              'Commit: ' + source['commit_sha'],
              'Snapshot SHA256: ' + source['snapshot_sha256'],
              'Query SHA256: ' + source['query_sha256'],
              ('Source pack bytes SHA256: ' if source['pack_hash_kind'] == 'serialized_pack_bytes'
               else 'Canonical pack JSON SHA256: ') + source['pack_sha256'],
              'Report SHA256: ' + source['report_sha256'],
              'Comparison SHA256: ' + review['sha256'],
              'Run: ' + source['run_id'] + '; job: ' + (source['job_id'] or 'not provided')]
    return '\n'.join(lines) + '\n'
