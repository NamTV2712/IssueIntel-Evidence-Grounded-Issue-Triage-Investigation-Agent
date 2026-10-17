"""A source-bound presentation contract shared by local and public reports.

Projection is deterministic: it does not summarize with a model, execute tests,
read a repository, or promote model reviews into causal/human verification.
"""
from __future__ import annotations

from hashlib import sha256
import html
import json
import re
from urllib.parse import quote

from issueintel.repository.snapshot import validate_repo_path
from issueintel.runtime.io import digest


SCHEMA = 'investigation_report_v1'
DESCRIPTION_LIMIT = 800
EVIDENCE_LIMIT = 1200
COMPACT_LIMIT = 16000
_HASH = r'[a-f0-9]{64}'
_REVISION = r'(?:[a-f0-9]{40}|[a-f0-9]{64})'
_ID = {'type': 'string', 'minLength': 1}
_TEXT = {'type': 'string', 'minLength': 1}
_SHA = {'type': 'string', 'pattern': '^' + _HASH + '$'}
_NULL = {'type': 'null'}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def _array(items, maximum=200):
    return {'type': 'array', 'items': items, 'maxItems': maximum}


def _nullable(item):
    return {'anyOf': [item, _NULL]}


_IDS = _array(_ID)
_URL = _nullable({'type': 'string', 'minLength': 1, 'maxLength': 4000})
_VERIFICATION = _object({
    'citation_integrity': {'const': 'valid'}, 'semantic_support': {'const': 'unverified'},
    'model_assessment': _nullable({'enum': ['supported', 'unsupported', 'contradicted', 'uncertain']}),
    'model_reason': _nullable(_TEXT), 'model_evidence_ids': _IDS,
    'independent_human': {'const': False},
})
_HYPOTHESIS = _object({'id': _ID, 'statement': _TEXT, 'support_ids': _IDS,
    'counter_ids': _IDS, 'counter_status': {'enum': ['found', 'not_found']},
    'verification': _VERIFICATION, 'falsification_test': _TEXT})
_PROVENANCE = _object({'kind': {'enum': ['followup', 'clarification', 'resume', 'context_selection',
    'repository_context', 'focused_retrieval', 'planned_workflow']}, 'details': {'type': 'string', 'minLength': 1, 'maxLength': 100000}})

# This is an ordinary JSON Schema document and can be exported by any interface.
# The small validator below keeps the zero-dependency runtime intact.
REPORT_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'https://issueintel.local/schemas/investigation_report_v1',
    'title': 'IssueIntel source-bound investigation report',
    **_object({
        'schema': {'const': SCHEMA},
        'source': _object({'job_id': _nullable({'type': 'string', 'pattern': '^[a-f0-9]{24}$'}),
            'run_id': {'type': 'string', 'pattern': '^[a-f0-9]{24}$'}, 'pack_sha256': _SHA,
            'pack_hash_kind': {'enum': ['serialized_pack_bytes', 'canonical_pack_json']},
            'query_sha256': _SHA, 'commit_sha': {'type': 'string', 'pattern': '^' + _REVISION + '$'},
            'snapshot_sha256': _SHA, 'protocol': {'enum': ['benchmark_base', 'issue_time']},
            'strict_issue_time_verified': {'type': 'boolean'}}),
        'issue': _object({'case_id': _ID, 'repository': _TEXT, 'issue_url': _URL,
            'description_excerpt': {'type': 'string', 'minLength': 1, 'maxLength': DESCRIPTION_LIMIT},
            'description_truncated': {'type': 'boolean'}}),
        'execution': _object({'status': {'const': 'completed'}, 'stop_reason': _ID}),
        'findings': _object({'status': {'enum': ['evidence_only', 'hypotheses_unverified', 'abstained']},
            'causal_status': {'const': 'unverified'}, 'severity': {'const': 'unassessed'},
            'confidence_probability': _NULL, 'confidence_method': _NULL}),
        'summary': _object({'text': {'type': 'string', 'minLength': 1, 'maxLength': DESCRIPTION_LIMIT},
            'kind': {'const': 'issue_excerpt'}, 'evidence_ids': _IDS, 'truncated': {'type': 'boolean'}}),
        'evidence': _array(_object({'id': _ID, 'kind': {'enum': ['issue', 'code', 'repository_context']},
            'locator': _TEXT, 'text': {'type': 'string', 'minLength': 1, 'maxLength': EVIDENCE_LIMIT},
            'text_truncated': {'type': 'boolean'}, 'excerpt_sha256': _SHA, 'sha256': _SHA,
            'revision': _nullable({'type': 'string', 'pattern': '^' + _REVISION + '$'}), 'url': _URL})),
        'candidate_locations': _array(_object({'path': _TEXT, 'start_line': {'type': 'integer', 'minimum': 1},
            'end_line': {'type': 'integer', 'minimum': 1}, 'evidence_ids': _IDS})),
        'hypotheses': _array(_HYPOTHESIS, 5),
        'next_checks': _array(_object({'id': _ID, 'text': _TEXT, 'kind': {'const': 'proposed_check'},
            'execution_status': {'const': 'not_executed_by_issueintel'},
            'hypothesis_ids': _IDS, 'evidence_ids': _IDS})),
        'unknowns': _array(_ID),
        'abstention': _nullable(_object({'label': {'enum': ['unverified', 'answerable', 'abstain', 'uncertain']},
            'reason': _TEXT, 'source': {'const': 'model_annotation_unverified'}})),
        'limitations': _array(_TEXT), 'provenance': _array(_PROVENANCE, 12), 'sha256': _SHA,
    }),
}


def _hash(text):
    return sha256(text.encode('utf-8')).hexdigest()


def _check_shape(value, schema, path='report'):
    if 'anyOf' in schema:
        for option in schema['anyOf']:
            try:
                _check_shape(value, option, path)
                return
            except ValueError:
                pass
        raise ValueError(f'Invalid {path}')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        raise ValueError(f'Invalid {path}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'Invalid {path}')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'boolean': bool, 'null': type(None)}
    if 'type' in schema and type(value) is not types[schema['type']]:
        raise ValueError(f'Invalid {path} type')
    if schema.get('type') == 'object':
        if set(value) != set(schema['required']):
            raise ValueError(f'Invalid {path} fields')
        for key, item in value.items():
            _check_shape(item, schema['properties'][key], path + '.' + key)
    elif schema.get('type') == 'array':
        if len(value) > schema.get('maxItems', 200):
            raise ValueError(f'Invalid {path} size')
        for index, item in enumerate(value):
            _check_shape(item, schema['items'], f'{path}[{index}]')
    elif schema.get('type') == 'string':
        if (not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', float('inf'))
                or '\x00' in value or ('pattern' in schema and not re.fullmatch(schema['pattern'], value))):
            raise ValueError(f'Invalid {path} text')
    elif schema.get('type') == 'integer' and value < schema.get('minimum', value):
        raise ValueError(f'Invalid {path} integer')


def _github_origin(issue):
    url = issue.get('issue_url')
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/[1-9][0-9]*', url or '')
    if not match or any(part in {'.', '..'} for part in match[1].split('/')):
        return None
    repository = issue.get('repository', '')
    if repository.startswith('https://github.com/'):
        repository = repository[len('https://github.com/'):]
        if repository.endswith('.git'):
            repository = repository[:-4]
    if repository.casefold() != match[1].casefold():
        raise ValueError('Issue and report repository identities differ')
    return 'https://github.com/' + match[1]


def _code_locator(locator):
    match = re.fullmatch(r'(.+):([1-9][0-9]*)-([1-9][0-9]*)', locator)
    if not match or int(match[2]) > int(match[3]):
        raise ValueError('Invalid report code locator')
    validate_repo_path(match[1])
    return match[1], int(match[2]), int(match[3])


def _code_url(origin, revision, locator):
    path, start, end = _code_locator(locator)
    return f'{origin}/blob/{revision}/{quote(path, safe="/")}#L{start}-L{end}' if origin else None


def _context_url(origin, value):
    if not origin:
        return None
    pattern = re.escape(origin) + r'/(?:issues/[1-9][0-9]*(?:#issuecomment-[1-9][0-9]*)?|pull/[1-9][0-9]*(?:/files|#(?:issuecomment-[1-9][0-9]*|discussion_r[1-9][0-9]*))?|commit/[a-f0-9]{40,64})'
    if not isinstance(value, str) or not re.fullmatch(pattern, value, re.IGNORECASE):
        raise ValueError('Repository context link escaped the reviewed repository')
    return value


def _provenance(pack):
    rows = []
    # Workbench validates lineage against parent exports before calling this
    # projection. Keep only bounded descriptive facts, never filesystem paths.
    if pack.get('followup'):
        link = pack['followup']
        details = {key: link[key] for key in ('parent_job_id', 'parent_pack_sha256',
            'context_sha256', 'historical_query_verified') if key in link}
        rows.append({'kind': 'followup', 'details': json.dumps(details, ensure_ascii=False, sort_keys=True)})
        if link.get('clarification'):
            receipt = link['clarification']
            details = {key: receipt[key] for key in ('questions_sha256', 'sha256', 'language', 'answers') if key in receipt}
            if link.get('question_job_id'):
                details['question_job_id'] = link['question_job_id']
            rows.append({'kind': 'clarification', 'details': json.dumps(details, ensure_ascii=False, sort_keys=True)})
    if pack.get('resumed_from'):
        link = pack['resumed_from']
        details = {key: link[key] for key in ('source_job_id', 'checkpoint_sha256',
            'generation_reused') if key in link}
        rows.append({'kind': 'resume', 'details': json.dumps(details, ensure_ascii=False, sort_keys=True)})
    if pack.get('context_selection'):
        link = pack['context_selection']
        details = {key: link[key] for key in ('parent_job_id', 'parent_pack_sha256',
            'context_sha256', 'historical_verified') if key in link}
        rows.append({'kind': 'context_selection', 'details': json.dumps(details, ensure_ascii=False, sort_keys=True)})
    if pack.get('repository_context'):
        context = pack['repository_context']
        rows.append({'kind': 'repository_context', 'details': json.dumps({
            'sources': len(context['sources']), 'sha256': context['sha256'],
            'historical_verified': False}, sort_keys=True)})
    if pack.get('refinement'):
        refined = pack['refinement']
        rows.append({'kind': 'focused_retrieval', 'details': json.dumps({
            'retrieval_rounds': refined['retrieval_rounds'], 'maximum_rounds': 1,
            'generation_passes': refined['generation_passes'],
            'additional_excerpts': len(refined.get('search', {}).get('added_ids', []))}, sort_keys=True)})
    if pack.get('workflow_plan'):
        plan=pack['workflow_plan']
        details={key:plan[key] for key in ('policy','sha256','routing','limits','generation_passes','verifier_passes','stop_reason')}
        details['completed_retrieval_rounds']=sum(step.get('search') is not None for step in plan['rounds'])
        rows.append({'kind':'planned_workflow','details':json.dumps(details,ensure_ascii=False,sort_keys=True)})
    return rows


def build_report(pack, *, source_pack_sha256, job_id=None, source_hash_kind='serialized_pack_bytes'):
    """Project a validated saved pack; the caller binds its exact raw bytes.

    ``source_pack_sha256`` hashes the saved serialization, not canonical JSON.
    Interfaces reading an export must verify that receipt before calling here.
    A standalone in-memory caller can supply the hash of its own serialization.
    """
    from .comparison import compare_packs
    from .verifier import validate_critique
    from issueintel.annotations.models import Annotation

    compare_packs(pack, pack)
    if 'workflow_plan' in pack:
        from .planner import validate_planned
        validate_planned(pack)
    case, snapshot = pack['case'], pack['snapshot']
    origin = _github_origin(case)
    issue_records = [row for row in pack['evidence'] if row['kind'] == 'issue']
    annotation = pack.get('annotation')
    if annotation:
        Annotation.from_dict(annotation).validate_evidence(pack['evidence'])
    hypotheses = (annotation or {}).get('hypotheses', [])
    reviews = {}
    verification = pack.get('verification') or {}
    if 'reviews' in verification:
        if (not annotation or verification.get('kind') != 'llm_separate_pass'
                or verification.get('independent_human') is not False):
            raise ValueError('Model reviews require saved hypotheses and a declared automatic separate pass')
        reviews = {row['hypothesis_id']: row for row in validate_critique(
            {'reviews': verification['reviews']}, annotation, pack['evidence'])}
    evidence, candidates = [], []
    for row in pack['evidence']:
        text = row['text'][:EVIDENCE_LIMIT]
        url = None
        if row['kind'] == 'code':
            path, start, end = _code_locator(row['locator'])
            if end - start + 1 != len(row['text'].split('\n')):
                raise ValueError('Report code locator differs from the full evidence line span')
            if path not in {file['path'] for file in snapshot['files']}:
                raise ValueError('Report code evidence path is absent from the snapshot')
            url = _code_url(origin, snapshot['commit_sha'], row['locator'])
            candidates.append({'path': path, 'start_line': start, 'end_line': end,
                               'evidence_ids': [row['id']]})
        elif row['kind'] == 'issue':
            url = case.get('issue_url') if origin else None
        elif row['kind'] == 'repository_context':
            url = _context_url(origin, row['provenance']['source_url'])
        evidence.append({'id': row['id'], 'kind': row['kind'], 'locator': row['locator'],
            'text': text, 'text_truncated': len(text) != len(row['text']),
            'excerpt_sha256': _hash(text), 'sha256': row['sha256'],
            'revision': row['revision'], 'url': url})
    projected_hypotheses = []
    for row in hypotheses:
        review = reviews.get(row['id'])
        projected_hypotheses.append({key: row[key] if key not in {'support_ids', 'counter_ids'} else list(row[key])
            for key in ('id', 'statement', 'support_ids', 'counter_ids', 'counter_status', 'falsification_test')})
        projected_hypotheses[-1]['verification'] = {
            'citation_integrity': 'valid', 'semantic_support': 'unverified',
            'model_assessment': review['assessment'] if review else None,
            'model_reason': review['reason'] if review else None,
            'model_evidence_ids': list(review['evidence_ids']) if review else [], 'independent_human': False}
    checks = []
    proposed = [row['falsification_test'] for row in hypotheses]
    proposed.extend(pack.get('recommended_steps', []))
    for text in dict.fromkeys(proposed):
        linked = [row for row in hypotheses if row['falsification_test'] == text]
        checks.append({'id': 'check_' + str(len(checks) + 1), 'text': text,
            'kind': 'proposed_check', 'execution_status': 'not_executed_by_issueintel',
            'hypothesis_ids': [row['id'] for row in linked],
            'evidence_ids': list(dict.fromkeys(eid for row in linked for eid in row['support_ids'] + row['counter_ids']))})
    abstention = None
    if annotation:
        abstention = {key: annotation['abstention'][key] for key in ('label', 'reason')}
        abstention['source'] = 'model_annotation_unverified'
    unknowns = ['runtime_not_executed', 'causal_unverified', 'no_independent_human',
                'unassessed_severity', 'uncalibrated_confidence']
    if any(row['counter_status'] == 'not_found' for row in hypotheses):
        unknowns.append('counter_evidence_not_found_in_retrieved_scope')
    if not hypotheses:
        unknowns.append('model_hypotheses_not_generated')
    if abstention and abstention['label'] in {'abstain', 'uncertain'}:
        unknowns.append('model_answerability_' + abstention['label'])
    description = case['problem_statement'][:DESCRIPTION_LIMIT]
    result = {'schema': SCHEMA,
        'source': {'job_id': job_id, 'run_id': pack['run_id'], 'pack_sha256': source_pack_sha256,
            'pack_hash_kind': source_hash_kind,
            'query_sha256': _hash(case['problem_statement']), 'commit_sha': snapshot['commit_sha'],
            'snapshot_sha256': snapshot['snapshot_sha256'], 'protocol': snapshot['protocol'],
            'strict_issue_time_verified': pack.get('leakage_audit', {}).get('strict_issue_time_verified', False)},
        'issue': {'case_id': case['case_id'], 'repository': case['repository'],
            'issue_url': case.get('issue_url') if origin else None,
            'description_excerpt': description, 'description_truncated': len(description) != len(case['problem_statement'])},
        'execution': {'status': 'completed', 'stop_reason': pack['stop_reason']},
        'findings': {'status': ('abstained' if abstention and abstention['label'] == 'abstain'
                     else 'hypotheses_unverified' if hypotheses else 'evidence_only'),
            'causal_status': 'unverified', 'severity': 'unassessed',
            'confidence_probability': None, 'confidence_method': None},
        'summary': {'text': description, 'kind': 'issue_excerpt',
            'evidence_ids': [issue_records[0]['id']], 'truncated': len(description) != len(case['problem_statement'])},
        'evidence': evidence, 'candidate_locations': candidates, 'hypotheses': projected_hypotheses,
        'next_checks': checks, 'unknowns': unknowns, 'abstention': abstention,
        'limitations': list(dict.fromkeys([
            'The summary is an exact issue excerpt, not an independently verified observation.',
            'Candidate locations are retrieved code, not confirmed affected files.',
            'Citation integrity and model critique do not establish causal truth.',
            'No independent human validation is claimed.',
            'Suggested checks were not executed by IssueIntel; user checklist states are separate.',
            'Unknown severity and uncalibrated probability remain unassessed/null.',
            *pack.get('leakage_audit', {}).get('limitations', []),
        ])), 'provenance': _provenance(pack)}
    result['sha256'] = digest(result)
    validate_report(result)
    return result


def validate_report(report):
    """Check shape, digest, reference closure, source binding and truth levels."""
    _check_shape(report, REPORT_SCHEMA)
    if digest({key: value for key, value in report.items() if key != 'sha256'}) != report['sha256']:
        raise ValueError('Report content hash mismatch')
    source = report['source']
    evidence = {row['id']: row for row in report['evidence']}
    hypotheses = {row['id']: row for row in report['hypotheses']}
    if len(evidence) != len(report['evidence']) or len(hypotheses) != len(report['hypotheses']):
        raise ValueError('Ambiguous report IDs')
    issues = [row for row in evidence.values() if row['kind'] == 'issue']
    summary = report['summary']
    if (len(issues) != 1 or summary['evidence_ids'] != [issues[0]['id']]
            or issues[0]['sha256'] != source['query_sha256'] or issues[0]['revision'] is not None
            or summary['text'] != report['issue']['description_excerpt']
            or summary['truncated'] != report['issue']['description_truncated']
            or summary['text'] != issues[0]['text'][:DESCRIPTION_LIMIT]
            or summary['truncated'] != (issues[0]['text_truncated'] or len(issues[0]['text']) > DESCRIPTION_LIMIT)):
        raise ValueError('Report issue summary binding failed')
    origin = _github_origin(report['issue'])
    if report['issue']['issue_url'] is not None and not origin:
        raise ValueError('Report issue link is not canonical')
    for row in evidence.values():
        if _hash(row['text']) != row['excerpt_sha256'] or (not row['text_truncated'] and row['excerpt_sha256'] != row['sha256']):
            raise ValueError('Report evidence excerpt hash mismatch')
        if row['kind'] == 'code':
            if row['revision'] != source['commit_sha'] or row['url'] != _code_url(origin, source['commit_sha'], row['locator']):
                raise ValueError('Report code revision or link binding failed')
            _, start, end = _code_locator(row['locator'])
            lines = len(row['text'].split('\n'))
            if (not row['text_truncated'] and end - start + 1 != lines) or end - start + 1 < lines:
                raise ValueError('Report code excerpt span is inconsistent')
        elif row['kind'] == 'issue':
            if row['url'] != report['issue']['issue_url']:
                raise ValueError('Report issue link binding failed')
        elif row['revision'] is not None or row['url'] != _context_url(origin, row['url']):
            raise ValueError('Report repository context binding failed')
    def refs(values, known):
        if len(values) != len(set(values)) or any(value not in known for value in values):
            raise ValueError('Report references unavailable or duplicate IDs')
    for row in hypotheses.values():
        refs(row['support_ids'], evidence); refs(row['counter_ids'], evidence)
        refs(row['verification']['model_evidence_ids'], evidence)
        if (not row['support_ids'] or set(row['support_ids']) & set(row['counter_ids'])
                or bool(row['counter_ids']) != (row['counter_status'] == 'found')):
            raise ValueError('Report hypothesis citations are inconsistent')
        review = row['verification']
        if ((review['model_assessment'] is None) != (review['model_reason'] is None)
                or (review['model_assessment'] is None and review['model_evidence_ids'])
                or (review['model_assessment'] in {'supported', 'contradicted'} and not review['model_evidence_ids'])):
            raise ValueError('Invalid report model assessment')
    expected_candidates = []
    for row in evidence.values():
        if row['kind'] == 'code':
            path, start, end = _code_locator(row['locator'])
            expected_candidates.append({'path': path, 'start_line': start, 'end_line': end, 'evidence_ids': [row['id']]})
    if expected_candidates != report['candidate_locations']:
        raise ValueError('Report candidate locations differ from code evidence')
    if len({row['id'] for row in report['next_checks']}) != len(report['next_checks']):
        raise ValueError('Ambiguous next check IDs')
    for row in report['next_checks']:
        refs(row['hypothesis_ids'], hypotheses); refs(row['evidence_ids'], evidence)
        linked = [hypotheses[hid] for hid in row['hypothesis_ids']]
        if any(item['falsification_test'] != row['text'] for item in linked):
            raise ValueError('Report next check does not match its hypothesis')
        expected = list(dict.fromkeys(eid for item in linked for eid in item['support_ids'] + item['counter_ids']))
        if row['evidence_ids'] != expected:
            raise ValueError('Report next check citations differ from its hypotheses')
    for hypothesis in hypotheses.values():
        if not any(hypothesis['id'] in row['hypothesis_ids'] and row['text'] == hypothesis['falsification_test'] for row in report['next_checks']):
            raise ValueError('Report hypothesis has no proposed check')
    status = ('abstained' if report['abstention'] and report['abstention']['label'] == 'abstain'
              else 'hypotheses_unverified' if hypotheses else 'evidence_only')
    if report['findings']['status'] != status:
        raise ValueError('Report finding status differs from saved model output')
    required_unknowns = {'runtime_not_executed', 'causal_unverified', 'no_independent_human',
                         'unassessed_severity', 'uncalibrated_confidence'}
    if not required_unknowns <= set(report['unknowns']):
        raise ValueError('Report omitted required uncertainty disclosures')
    return report


def _safe(text, limit=None):
    """Render source text literally, including links, HTML and GitHub mentions."""
    text = str(text)
    omitted = limit is not None and len(text) > limit
    full_hash = _hash(text) if omitted else None
    text = html.escape(text[:limit] if omitted else text, quote=False)
    text = re.sub(r'([\\`*_{}\[\]()#!|>+\-.])', r'\\\1', text)
    text = text.replace('@', '@\u200b').replace('\r', '')
    if omitted:
        text += f' [verbatim excerpt truncated; full text SHA256: {full_hash}]'
    return text


def _quote(text, limit=None):
    return '\n'.join('> ' + line for line in _safe(text, limit).split('\n'))


def _markdown(report, *, compact, detail_limit=None, text_limit=None):
    safe = lambda text: _safe(text, text_limit)
    quoted = lambda text: _quote(text, text_limit)
    references = lambda values: safe(', '.join(values))
    source = report['source']
    lines = ['# IssueIntel investigation', '',
        f"Case: {safe(report['issue']['case_id'])}",
        f"Run completed; finding status: **{report['findings']['status']}**; causal correctness: **unverified**.",
        f"Stop: {safe(report['execution']['stop_reason'])}", '',
        '## Summary — issue excerpt', '', quoted(report['summary']['text']),
        'Source: ' + references(report['summary']['evidence_ids']) +
        ('; excerpt truncated.' if report['summary']['truncated'] else '.'), '',
        '## Verification and unknowns', '',
        '- Citation integrity was checked against the saved pack; this does not prove semantic support or causal truth.',
        '- Model assessments below are an automatic separate critique, not independent human validation.',
        '- Runtime/tests: not executed by IssueIntel. Severity: unassessed. Confidence probability: unknown (not calibrated).',
        '- Unknowns: ' + references(report['unknowns']),
    ]
    if report['abstention']:
        lines += ['', 'Model answerability: ' + safe(report['abstention']['label']), quoted(report['abstention']['reason'])]
    if report['hypotheses']:
        lines += ['', '## Competing hypotheses (automatic, correctness unverified)', '']
        for row in report['hypotheses']:
            lines += ['### ' + safe(row['id']), quoted(row['statement']),
                'Support: ' + references(row['support_ids']),
                'Counter: ' + (references(row['counter_ids']) or 'not found in the retrieved scope'),
                'Citation integrity: valid. Semantic support: unverified. Independent human: no.']
            review = row['verification']
            if review['model_assessment']:
                lines += ['Model assessment: ' + safe(review['model_assessment']) + ' (unverified).',
                          quoted(review['model_reason']),
                          'Model review citations: ' + (references(review['model_evidence_ids']) or 'none')]
    lines += ['', '## Next checks — proposed, not executed', '']
    check_limit = 10 if compact and text_limit is not None else None
    for row in report['next_checks'][:check_limit]:
        lines += ['- ' + safe(row['id']) + ': ' + safe(row['text']).replace('\n', '\n  '),
                  '  Hypotheses: ' + (references(row['hypothesis_ids']) or 'none') +
                  '; evidence: ' + (references(row['evidence_ids']) or 'none') + '.']
    if check_limit is not None and len(report['next_checks']) > check_limit:
        lines += [f"Additional proposed checks omitted: {len(report['next_checks']) - check_limit}. "
                  f"Full next_checks collection SHA256: {digest(report['next_checks'])}."]
    lines += ['', '## Candidate locations — retrieved, impact unconfirmed', '']
    for row in report['candidate_locations'][:detail_limit]:
        lines.append('- ' + safe(f"{row['path']}:{row['start_line']}-{row['end_line']}") + ' — ' + references(row['evidence_ids']))
    lines += ['', '## Evidence', '']
    for row in report['evidence'][:detail_limit]:
        label = safe(row['id'])
        if row['url']:
            label = '[' + label + '](' + row['url'] + ')'
        lines += ['- ' + label + ' — ' + safe(row['locator']) + ' (' + row['kind'] + ')',
                  '  Full evidence SHA256: ' + row['sha256']]
        if row['kind'] == 'repository_context':
            lines.append('  current repository context; historical availability and causal correctness unverified.')
        if not compact:
            lines += [quoted(row['text']), 'Excerpt SHA256: ' + row['excerpt_sha256'] +
                      ('; excerpt truncated.' if row['text_truncated'] else '; complete evidence excerpt.')]
    names = {'followup': 'Follow-up provenance', 'clarification': 'Guided clarification answers',
        'resume': 'Explicit verifier continuation', 'context_selection': 'User-selected repository context',
        'repository_context': 'Current repository context', 'focused_retrieval': 'Bounded hypothesis-focused retrieval',
        'planned_workflow': 'Planned investigation workflow'}
    for row in report['provenance']:
        lines += ['', '## ' + names[row['kind']], '',
                  quoted(row['details']) if not compact else 'Details preserved in the full source-bound report; user/model context remains unverified.']
    if detail_limit is not None:
        lines += ['', '**Presentation truncated:** candidate/evidence detail omitted to fit the comment bound. '
                  'Review the full JSON/Markdown report identified by the hashes below.']
    if text_limit is not None:
        lines += ['', '**Verbatim excerpts shortened for presentation:** omitted text is retained in the full report. '
                  'Per-field SHA256 markers hash the original text, not its displayed prefix.']
    lines += ['', '## Limits', '', *['- ' + safe(item) for item in report['limitations']],
        '', '## Source binding', '', 'Snapshot: ' + source['commit_sha'],
        'Snapshot protocol: ' + source['protocol'] + '; saved strict issue-time flag: ' + str(source['strict_issue_time_verified']).lower() + '.',
        'Snapshot SHA256: ' + source['snapshot_sha256'], 'Query SHA256: ' + source['query_sha256'],
        ('Source pack bytes SHA256: ' if source['pack_hash_kind'] == 'serialized_pack_bytes'
         else 'Canonical pack JSON SHA256: ') + source['pack_sha256'],
        'Report SHA256: ' + report['sha256'],
        'Run: ' + source['run_id'] + '; job: ' + (source['job_id'] or 'not provided')]
    return '\n'.join(lines) + '\n'


def render_markdown(report, *, compact=False):
    """Render literal source text; compact output has a hard reviewable bound."""
    validate_report(report)
    body = _markdown(report, compact=compact)
    if compact and len(body) > COMPACT_LIMIT:
        for limit in (800, 400, 160, 64):
            body = _markdown(report, compact=True, detail_limit=0, text_limit=limit)
            if len(body) <= COMPACT_LIMIT:
                break
    if compact and len(body) > COMPACT_LIMIT:
        raise ValueError('Report exceeds the compact reviewable size bound')
    return body
