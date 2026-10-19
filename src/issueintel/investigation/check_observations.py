"""Append-only user reports against exact saved discriminating check plans.

This store never executes checks or certifies an outcome. Its caller authenticates
the plan job and product ancestry; self-consistent hashes alone do not prove that
authority. The original report journal and inference artifacts remain untouched.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unicodedata

from issueintel.runtime.io import digest
from .check_plans import validate_plan, output_schema, render_markdown as render_plan
from .hypothesis_review import _safe, _quoted
from .report import _check_shape


LIMIT = 100
OUTCOMES = ('not_run', 'A', 'B', 'other', 'inconclusive')
FLAGS = {'provenance': 'user_report_unverified', 'executed_by_issueintel': False,
         'causal_verified': False, 'fix_verified': False, 'independent_human': False,
         'evaluation_performed': False}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def _text(maximum, minimum=0):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum}


_SHA = {'type': 'string', 'pattern': '[a-f0-9]{64}'}
_JOB = {'type': 'string', 'pattern': '[a-f0-9]{24}'}
_NULL = {'type': 'null'}
_REVISION = {'type': 'string', 'pattern': '(?:[a-f0-9]{40}|[a-f0-9]{64})'}
_OPTIONAL_ID = {'anyOf': [{'type': 'string', 'pattern': r'observation_[1-9][0-9]*'}, _NULL]}
OBSERVATION_SCHEMA = _object({
    'check_id': {'type': 'string', 'enum': ['check_1', 'check_2', 'check_3']},
    'outcome': {'type': 'string', 'enum': list(OUTCOMES)},
    'conditions': _text(2000), 'observed_result': _text(4000),
    'interpretation': _text(2000),
    'tested_commit_sha': {'anyOf': [_REVISION, _NULL]}, 'supersedes': _OPTIONAL_ID,
})
_SOURCE = _object({'plan_job_id': _JOB, 'plan_sha256': _SHA, 'source_job_id': _JOB,
    'source_pack_sha256': _SHA, 'report_sha256': _SHA, 'query_sha256': _SHA,
    'snapshot_sha256': _SHA, 'commit_sha': _REVISION, 'annotation_sha256': _SHA,
    'source_projection_sha256': _SHA})
_EVENT = _object({
    'schema': {'const': 'manual_check_plan_observation_v1'}, 'source': _SOURCE,
    'revision': {'type': 'integer', 'minimum': 1}, 'parent_sha256': _SHA,
    'observation_id': {'type': 'string', 'pattern': r'observation_[1-9][0-9]*'},
    'check_binding': _object({'id': OBSERVATION_SCHEMA['properties']['check_id'], 'sha256': _SHA}),
    'observation': OBSERVATION_SCHEMA, 'recorded_at': _text(50, 1),
    **{key: {'const': value} for key, value in FLAGS.items()}, 'sha256': _SHA,
})


def _seal(state):
    return dict(state, sha256=digest(state))


def initial_journal(plan, plan_job_id):
    """Bind the full plan and each immutable input, not positional report checks."""
    _check_shape(plan_job_id, _JOB, 'plan_job_id')
    if not isinstance(plan, dict) or 'source' not in plan:
        raise ValueError('A saved product check plan is required')
    plan = validate_plan(plan, plan['source'])
    source = plan['source']
    binding = {key: source[key] for key in ('source_job_id', 'source_pack_sha256',
        'report_sha256', 'query_sha256', 'snapshot_sha256', 'commit_sha', 'annotation_sha256')}
    binding.update(plan_job_id=plan_job_id, plan_sha256=plan['sha256'],
                   source_projection_sha256=source['sha256'])
    _check_shape(binding, _SOURCE, 'journal.source')
    return _seal({'schema': 'supplemental_check_observation_journal_v1', 'source': binding,
        'checks': deepcopy(plan['checks']), 'revision': 0, 'parent_sha256': None,
        'events': [], **FLAGS})


def _check_binding(check):
    # Includes outcome labels, procedure, controls, predictions and their reasons.
    return {'id': check['id'], 'sha256': digest(check)}


def _validate_observation(observation, state):
    _check_shape(observation, OBSERVATION_SCHEMA, 'observation')
    for field in ('conditions', 'observed_result', 'interpretation'):
        if any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} and char not in '\t\r\n'
               for char in observation[field]):
            raise ValueError('Manual observation contains unsupported control characters')
    check = next((row for row in state['checks'] if row['id'] == observation['check_id']), None)
    if check is None:
        raise ValueError('Choose an exact check from the saved plan')
    if observation['outcome'] == 'not_run':
        if (observation['observed_result'].strip() or observation['tested_commit_sha'] is not None
                or not observation['interpretation'].strip()):
            raise ValueError('An unrun check needs a reason and cannot claim observations or a tested commit')
    elif any(not observation[field].strip() for field in ('conditions', 'observed_result')):
        raise ValueError('Record conditions and observed results for a reported check')
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


def replay_journal(plan, plan_job_id, events):
    state = initial_journal(plan, plan_job_id)
    if type(events) is not list or len(events) > LIMIT:
        raise ValueError('Manual check observation history limit exceeded')
    for event in events:
        _check_shape(event, _EVENT, 'manual_event')
        if (event['sha256'] != digest({key: value for key, value in event.items() if key != 'sha256'})
                or event['source'] != state['source']
                or event['revision'] != state['revision'] + 1
                or event['observation_id'] != f"observation_{event['revision']}"
                or event['parent_sha256'] != state['sha256']):
            raise ValueError('Manual check observation source or history integrity changed')
        try:
            timestamp = datetime.fromisoformat(event['recorded_at'])
            if timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
                raise ValueError('UTC required')
        except (ValueError, OverflowError):
            raise ValueError('Manual check observation timestamp must have UTC offset') from None
        check = _validate_observation(event['observation'], state)
        if event['check_binding'] != _check_binding(check):
            raise ValueError('Manual check observation full check binding changed')
        state = _apply(state, event)
    return state


def validate_journal(journal, plan, plan_job_id):
    if type(journal) is not dict:
        raise ValueError('Invalid manual check observation journal')
    expected = replay_journal(plan, plan_job_id, journal.get('events'))
    schema = _object({'schema': {'const': 'supplemental_check_observation_journal_v1'},
        'source': _SOURCE, 'checks': output_schema(plan['source'])['properties']['checks'],
        'revision': {'type': 'integer', 'minimum': 0},
        'parent_sha256': {'anyOf': [_SHA, _NULL]},
        'events': {'type': 'array', 'items': _EVENT, 'maxItems': LIMIT},
        **{key: {'const': value} for key, value in FLAGS.items()}, 'sha256': _SHA})
    _check_shape(journal, schema, 'journal')
    # Canonical JSON also distinguishes booleans from numbers in nested values.
    if digest(journal) != digest(expected):
        raise ValueError('Manual check observation journal differs from its source or events')
    return journal


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manual observation JSON field')
        result[key] = value
    return result


class CheckObservationStore:
    """Dedicated table namespaced by plan job; original report journal is intact."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS supplemental_check_observations (plan_job_id TEXT NOT NULL, revision INTEGER NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(plan_job_id,revision))')
            connection.commit()

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def _load(self, connection, plan, plan_job_id):
        rows = connection.execute('SELECT revision,sha256,payload FROM supplemental_check_observations WHERE plan_job_id=? ORDER BY revision LIMIT ?',
                                  (plan_job_id, LIMIT + 1)).fetchall()
        events = []
        for revision, checksum, payload in rows:
            if type(payload) is not str or len(payload.encode('utf-8')) > 64000:
                raise ValueError('Manual check observation event too large')
            event = json.loads(payload, object_pairs_hook=_json_object)
            if (type(event) is not dict or type(event.get('revision')) is not int
                    or event['revision'] != revision or event.get('sha256') != checksum):
                raise ValueError('Manual check observation storage integrity changed')
            events.append(event)
        return replay_journal(plan, plan_job_id, events)

    def load(self, plan, plan_job_id):
        initial_journal(plan, plan_job_id)
        with closing(self.connect()) as connection:
            return self._load(connection, plan, plan_job_id)

    def append(self, plan, plan_job_id, request):
        if type(request) is not dict or set(request) != {'expected_sha256', 'observation'}:
            raise ValueError('An exact journal version and manual observation are required')
        request = deepcopy(request)
        _check_shape(request['expected_sha256'], _SHA, 'expected_sha256')
        initial_journal(plan, plan_job_id)
        with closing(self.connect()) as connection:
            try:
                connection.execute('BEGIN IMMEDIATE')
                state = self._load(connection, plan, plan_job_id)
                if request['expected_sha256'] != state['sha256']:
                    raise ValueError('Journal changed; reload before saving')
                if state['revision'] >= LIMIT:
                    raise ValueError('Manual check observation limit reached')
                check = _validate_observation(request['observation'], state)
                event = _seal({'schema': 'manual_check_plan_observation_v1', 'source': state['source'],
                    'revision': state['revision'] + 1, 'parent_sha256': state['sha256'],
                    'observation_id': f"observation_{state['revision'] + 1}",
                    'check_binding': _check_binding(check), 'observation': request['observation'],
                    'recorded_at': datetime.now(timezone.utc).isoformat(), **FLAGS})
                payload = json.dumps(event, ensure_ascii=False, allow_nan=False)
                if len(payload.encode('utf-8')) > 64000:
                    raise ValueError('Manual check observation event too large')
                connection.execute('INSERT INTO supplemental_check_observations VALUES (?,?,?,?)',
                    (plan_job_id, event['revision'], event['sha256'], payload))
                result = self._load(connection, plan, plan_job_id)
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise


def render_markdown(journal, plan):
    """Full saved plan and all reports/corrections; never a winning hypothesis."""
    if type(journal) is not dict or type(journal.get('source')) is not dict:
        raise ValueError('Invalid manual check observation journal')
    validate_journal(journal, plan, journal['source'].get('plan_job_id'))
    lines = [render_plan(plan), '\n---\n', '# Manual discriminating check observations', '',
        'User reports are unverified. A/B are user-selected labels against the exact saved plan, not verified predictions.',
        'IssueIntel did not execute these checks, verify a cause or certify a fix. These records are not independent human benchmark labels.',
        'Tested commits are user-supplied; they are not resolved or assumed to match the source commit.',
        'Recorded timestamps are storage times, not proof of execution. Corrections retain the full original history.', '',
        'Plan job: ' + journal['source']['plan_job_id'],
        'Source commit: ' + journal['source']['commit_sha'],
        'Journal revision: ' + str(journal['revision']),
        'Journal SHA256: ' + journal['sha256'], '']
    superseded = {event['observation']['supersedes'] for event in journal['events']}
    if not journal['events']:
        lines += ['No manual observations recorded; this does not mean any check was executed.', '']
    for event in journal['events']:
        observation = event['observation']
        lines += ['## ' + event['observation_id'] + (' (superseded)' if event['observation_id'] in superseded else ''),
            'Saved check: ' + _safe(observation['check_id']),
            'Full saved check SHA256: ' + event['check_binding']['sha256'],
            'Recorded at (not execution proof): ' + event['recorded_at'],
            'User-reported outcome: ' + observation['outcome'],
            'User-reported tested commit: ' + (observation['tested_commit_sha'] or 'not supplied; not inferred from source'),
            'Corrects: ' + (observation['supersedes'] or 'none')]
        for label, key in [('Conditions', 'conditions'), ('Observed result', 'observed_result'),
                           ('User interpretation / reason not run', 'interpretation')]:
            lines += [label + ':', _quoted(observation[key]) if observation[key] else '> not supplied']
        lines += ['Event SHA256: ' + event['sha256'], '']
    return '\n'.join(lines).rstrip() + '\n'
