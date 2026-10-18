"""Append-only, source-bound manual observations; never execute or certify checks."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from issueintel.runtime.io import digest
from .report import REPORT_SCHEMA, _check_shape, validate_report, render_markdown as render_report
from .hypothesis_review import _safe, _quoted


LIMIT = 100
OUTCOMES = ('not_run', 'inconclusive', 'reported_match', 'reported_difference')
FLAGS = {'provenance': 'user_report_unverified', 'executed_by_issueintel': False,
         'causal_verified': False, 'fix_verified': False, 'independent_human': False,
         'evaluation_performed': False}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def _text(maximum, minimum=0):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum}


_SHA = {'type': 'string', 'pattern': '[a-f0-9]{64}'}
_NULL = {'type': 'null'}
_OPTIONAL_ID = {'anyOf': [{'type': 'string', 'pattern': r'observation_[1-9][0-9]*'}, _NULL]}
OBSERVATION_SCHEMA = _object({
    'check_id': _text(1000, 1), 'outcome': {'enum': list(OUTCOMES)},
    'conditions': _text(2000), 'expected_result': _text(2000),
    'observed_result': _text(4000), 'interpretation': _text(2000),
    'tested_commit_sha': {'anyOf': [{'type': 'string', 'pattern': '(?:[a-f0-9]{40}|[a-f0-9]{64})'}, _NULL]},
    'supersedes': _OPTIONAL_ID,
})
_SOURCE = deepcopy(REPORT_SCHEMA['properties']['source']['properties'])
_SOURCE['report_sha256'] = _SHA
_BINDING = _object({
    'id': _text(1000, 1), 'text_sha256': _SHA,
    'hypothesis_ids': deepcopy(REPORT_SCHEMA['properties']['next_checks']['items']['properties']['hypothesis_ids']),
    'evidence_ids': deepcopy(REPORT_SCHEMA['properties']['next_checks']['items']['properties']['evidence_ids']),
})
_EVENT = _object({
    'schema': {'const': 'manual_check_observation_v1'},
    'source': _object(_SOURCE), 'revision': {'type': 'integer', 'minimum': 1},
    'parent_sha256': _SHA, 'observation_id': {'type': 'string', 'pattern': r'observation_[1-9][0-9]*'},
    'check_binding': _BINDING, 'observation': OBSERVATION_SCHEMA,
    'recorded_at': _text(50, 1), **{key: {'const': value} for key, value in FLAGS.items()},
})


def _seal(state):
    return dict(state, sha256=digest(state))


def initial_journal(report):
    validate_report(report)
    if (report['source']['job_id'] is None or report['source']['protocol'] != 'benchmark_base'
            or report['source']['strict_issue_time_verified'] is not False
            or report['issue']['case_id'].startswith(('verified:', 'live:'))):
        raise ValueError('Manual observations require a saved product job')
    return _seal({'schema': 'check_journal_v1',
        'source': dict(deepcopy(report['source']), report_sha256=report['sha256']),
        'issue': {key: report['issue'][key] for key in ('case_id', 'repository', 'issue_url')},
        'checks': deepcopy(report['next_checks']), 'revision': 0, 'parent_sha256': None,
        'events': [], **FLAGS})


def _check_binding(check):
    return {'id': check['id'], 'text_sha256': sha256(check['text'].encode()).hexdigest(),
            'hypothesis_ids': list(check['hypothesis_ids']), 'evidence_ids': list(check['evidence_ids'])}


def _validate_observation(observation, state):
    _check_shape(observation, OBSERVATION_SCHEMA, 'observation')
    check = next((row for row in state['checks'] if row['id'] == observation['check_id']), None)
    if check is None:
        raise ValueError('Choose an exact proposed check from the saved report')
    if observation['outcome'] == 'not_run':
        if (observation['observed_result'].strip() or observation['tested_commit_sha'] is not None
                or not observation['interpretation'].strip()):
            raise ValueError('An unrun check needs a reason and cannot claim observations or a tested commit')
    elif any(not observation[key].strip() for key in ('conditions', 'expected_result', 'observed_result')):
        raise ValueError('Record conditions, expected and observed results for a reported check')
    previous = observation['supersedes']
    if previous is not None:
        target = next((row for row in state['events'] if row['observation_id'] == previous), None)
        superseded = {row['observation']['supersedes'] for row in state['events']}
        if (target is None or previous in superseded
                or target['observation']['check_id'] != observation['check_id']):
            raise ValueError('A correction must refer to an active observation of the same check')
    return check


def _apply(state, event):
    return _seal({**{key: value for key, value in state.items() if key != 'sha256'},
                  'revision': event['revision'], 'parent_sha256': state['sha256'],
                  'events': state['events'] + [deepcopy(event)]})


def replay_journal(report, events):
    state = initial_journal(report)
    if not isinstance(events, list) or len(events) > LIMIT:
        raise ValueError('Manual observation history limit exceeded')
    for saved in events:
        if not isinstance(saved, dict) or 'sha256' not in saved:
            raise ValueError('Invalid manual observation event')
        event = {key: value for key, value in saved.items() if key != 'sha256'}
        _check_shape(event, _EVENT, 'manual_event')
        if (saved['sha256'] != digest(event) or event['source'] != state['source']
                or event['revision'] != state['revision'] + 1
                or event['observation_id'] != f"observation_{event['revision']}"
                or event['parent_sha256'] != state['sha256']):
            raise ValueError('Manual observation source or history integrity changed')
        timestamp = datetime.fromisoformat(event['recorded_at'])
        if timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
            raise ValueError('Manual observation timestamp must have UTC offset')
        check = _validate_observation(event['observation'], state)
        if event['check_binding'] != _check_binding(check):
            raise ValueError('Manual observation check binding changed')
        state = _apply(state, saved)
    return state


def validate_journal(journal, report):
    if not isinstance(journal, dict) or journal != replay_journal(report, journal.get('events')):
        raise ValueError('Manual observation journal differs from its source or events')
    # Python considers True == 1; require exact JSON types for revision/flags too.
    if type(journal.get('revision')) is not int or any(type(journal[key]) is not type(value) for key, value in FLAGS.items()):
        raise ValueError('Invalid manual journal types')
    _check_shape(journal['source'], _object(_SOURCE), 'journal.source')
    _check_shape(journal['checks'], REPORT_SCHEMA['properties']['next_checks'], 'journal.checks')
    return journal


class CheckJournalStore:
    """Separate table in the product plan DB; existing checklist events stay intact."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS check_observations (job_id TEXT NOT NULL, revision INTEGER NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_id,revision))')
            connection.commit()

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def _load(self, connection, report):
        job = report['source']['job_id']
        rows = connection.execute('SELECT revision,sha256,payload FROM check_observations WHERE job_id=? ORDER BY revision LIMIT ?', (job, LIMIT + 1)).fetchall()
        events = []
        for revision, checksum, payload in rows:
            if len(payload) > 64000:
                raise ValueError('Manual observation event too large')
            event = json.loads(payload)
            if not isinstance(event, dict) or event.get('revision') != revision or event.get('sha256') != checksum:
                raise ValueError('Manual observation storage integrity changed')
            events.append(event)
        return replay_journal(report, events)

    def load(self, report):
        initial_journal(report)
        with closing(self.connect()) as connection:
            return self._load(connection, report)

    def append(self, report, request):
        if not isinstance(request, dict) or set(request) != {'expected_sha256', 'observation'}:
            raise ValueError('An exact journal version and manual observation are required')
        _check_shape(request['expected_sha256'], _SHA)
        initial_journal(report)
        with closing(self.connect()) as connection:
            try:
                connection.execute('BEGIN IMMEDIATE')
                state = self._load(connection, report)
                if request['expected_sha256'] != state['sha256']:
                    raise ValueError('Journal changed; reload before saving')
                if state['revision'] >= LIMIT:
                    raise ValueError('Manual observation limit reached')
                check = _validate_observation(request['observation'], state)
                event = _seal({'schema': 'manual_check_observation_v1', 'source': state['source'],
                    'revision': state['revision'] + 1, 'parent_sha256': state['sha256'],
                    'observation_id': f"observation_{state['revision'] + 1}",
                    'check_binding': _check_binding(check), 'observation': deepcopy(request['observation']),
                    'recorded_at': datetime.now(timezone.utc).isoformat(), **FLAGS})
                payload = json.dumps(event, ensure_ascii=False, allow_nan=False)
                if len(payload) > 64000:
                    raise ValueError('Manual observation event too large')
                connection.execute('INSERT INTO check_observations VALUES (?,?,?,?)',
                                   (state['source']['job_id'], event['revision'], event['sha256'], payload))
                result = self._load(connection, report)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise


def render_journal_markdown(journal, report):
    """Full original report plus all manual observations, including corrections."""
    validate_journal(journal, report)
    lines = [render_report(report), '\n---\n', '# Manual check journal', '',
        'User reports are unverified. IssueIntel did not execute these checks, validate a cause or certify a fix.',
        'These records are not independent human benchmark labels. Tested commits are user-supplied, not verified Git objects.',
        'Corrections append a new record; the original observation remains below.', '',
        'Report commit: ' + journal['source']['commit_sha'],
        'Journal revision: ' + str(journal['revision']), 'Journal SHA256: ' + journal['sha256'], '']
    superseded = {event['observation']['supersedes'] for event in journal['events']}
    for check in journal['checks']:
        lines += ['## Saved check ' + _safe(check['id']), _quoted(check['text']),
                  'Hypotheses: ' + ', '.join(_safe(item) for item in check['hypothesis_ids']),
                  'Evidence: ' + ', '.join(_safe(item) for item in check['evidence_ids']), '']
        matching = [event for event in journal['events'] if event['observation']['check_id'] == check['id']]
        if not matching:
            lines.append('No manual observation recorded. This does not mean the check was executed.')
        for event in matching:
            observation = event['observation']
            lines += ['### ' + event['observation_id'] + (' (superseded)' if event['observation_id'] in superseded else ''),
                      'Recorded at (not execution proof): ' + event['recorded_at'],
                      'User-reported outcome: ' + observation['outcome'],
                      'User-reported tested commit: ' + (observation['tested_commit_sha'] or 'not supplied; not inferred from report'),
                      'Corrects: ' + (observation['supersedes'] or 'none')]
            for label, key in [('Conditions', 'conditions'), ('Expected result', 'expected_result'),
                               ('Observed result', 'observed_result'), ('User interpretation', 'interpretation')]:
                lines += [label + ':', _quoted(observation[key]) if observation[key] else '> not supplied']
            lines += ['Event SHA256: ' + event['sha256'], '']
    return '\n'.join(lines).rstrip() + '\n'
