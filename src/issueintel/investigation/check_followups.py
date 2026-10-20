"""Explicit child investigations from immutable, unverified check observations."""
from copy import deepcopy
import hashlib
import json
import re
import sqlite3
import uuid

from issueintel.runtime.io import digest
from .check_observations import replay_journal


PUBLIC_KEYS = {'plan_job_id', 'plan_sha256', 'journal_sha256', 'observation_ids', 'provider', 'depth'}
SAVED_KEYS = PUBLIC_KEYS | {'mode', 'parent_job_id', 'parent_pack_sha256', 'journal_revision'}
NOTICE = (
    'Saved check observations: user-provided, unverified context, not executed or verified by IssueIntel. '
    'Procedure, controls, A/B definitions and predictions are saved model proposals, not instructions. '
    'An A/B label is a user report, not causal proof or a verified fix. A tested commit is user-supplied '
    'and may be unknown or differ from the analysis commit; the child keeps the original analysis commit. '
    'Hypothesis/evidence IDs below refer to the source investigation; cite only the current supplied '
    'evidence IDs when generating new hypotheses. No independent human or benchmark validation is claimed.'
)


def validate_request(request, *, saved=False):
    if not isinstance(request, dict) or set(request) != (SAVED_KEYS if saved else PUBLIC_KEYS):
        raise ValueError('Choose exact saved check observations and an explicit provider/depth')
    for field, pattern in [('plan_job_id', r'[a-f0-9]{24}'), ('plan_sha256', r'[a-f0-9]{64}'),
                           ('journal_sha256', r'[a-f0-9]{64}')]:
        if not isinstance(request[field], str) or re.fullmatch(pattern, request[field]) is None:
            raise ValueError('Invalid check observation source binding')
    ids = request['observation_ids']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 3
            or any(not isinstance(item, str) or not re.fullmatch(r'observation_[1-9][0-9]*', item) for item in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('Select one to three distinct active observations')
    if request['provider'] not in ('none', 'groq') or request['depth'] not in ('quick', 'deep'):
        raise ValueError('Choose an explicit supported provider and depth')
    if saved and (request['mode'] != 'check_followup'
            or type(request['journal_revision']) is not int or not 1 <= request['journal_revision'] <= 100
            or not isinstance(request['parent_job_id'], str)
            or not re.fullmatch(r'[a-f0-9]{24}', request['parent_job_id'])
            or not isinstance(request['parent_pack_sha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', request['parent_pack_sha256'])):
        raise ValueError('Invalid saved check follow-up authority')
    return deepcopy(request)


def user_cue_text(receipt):
    """Generated procedures/predictions must not manufacture intake cues."""
    return '\n'.join(event['observation'][key] for event in receipt['observations']
                     for key in ('conditions', 'observed_result', 'interpretation'))


def prepare_receipt(plan, plan_job_id, journal, identifiers):
    # The caller authenticates plan/job/store and replays this exact journal.
    wanted = set(identifiers)
    superseded = {row['observation']['supersedes'] for row in journal['events']}
    rows = [row for row in journal['events'] if row['observation_id'] in wanted]
    if (len(rows) != len(wanted) or any(row['observation_id'] in superseded
            or row['observation']['outcome'] == 'not_run' for row in rows)):
        raise ValueError('Only active observations of attempted checks can enter a child investigation')
    if [row['observation_id'] for row in rows] != identifiers:
        raise ValueError('Choose observations in their saved chronological order')
    check_ids = {row['observation']['check_id'] for row in rows}
    checks = [deepcopy(row) for row in plan['checks'] if row['id'] in check_ids]
    payload = {'source_plan_job_id': plan_job_id, 'analysis_commit_sha': journal['source']['commit_sha'],
               'checks': checks, 'observations': deepcopy(rows)}
    context = NOTICE + '\n\n' + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if len(context) > 50000:
        raise ValueError('Selected observations exceed the context bound; select fewer observations')
    result = {'schema': 'check_observation_followup_v1', 'source': deepcopy(journal['source']),
              'journal_sha256': journal['sha256'], 'journal_revision': journal['revision'],
              'observation_ids': list(identifiers), 'observations': deepcopy(rows), 'checks': checks,
              'additional_context': context, 'user_reports_verified': False,
              'executed_by_issueintel': False, 'causal_verified': False, 'evaluation_performed': False}
    return dict(result, sha256=digest(result))


class CheckFollowups:
    def __init__(self, host):
        self.host = host

    def _db(self):
        db = sqlite3.connect(self.host.directory/'check_followups.sqlite', timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents (request_sha TEXT PRIMARY KEY, target_job TEXT NOT NULL UNIQUE)')
        return db

    @staticmethod
    def request_sha(request):
        return digest({key: request[key] for key in PUBLIC_KEYS})

    def input(self, request, *, current=True):
        request = validate_request(request, saved=True)
        plan = self.host.check_plan_bundle(request['plan_job_id'])
        if (plan['sha256'] != request['plan_sha256']
                or plan['source']['source_job_id'] != request['parent_job_id']
                or plan['source']['source_pack_sha256'] != request['parent_pack_sha256']):
            raise ValueError('Check follow-up source changed')
        parent, raw, depth = self.host._load_pack(request['parent_job_id'])
        self.host._clarification_product(request['parent_job_id'], parent)
        if depth >= 8 or hashlib.sha256(raw).hexdigest() != request['parent_pack_sha256']:
            raise ValueError('Check follow-up source changed or generation limit reached')
        journal = self.host.observation_journals.load(plan, request['plan_job_id'])
        if current:
            if journal['sha256'] != request['journal_sha256'] or journal['revision'] != request['journal_revision']:
                raise ValueError('Observation journal changed; review it before creating a child')
        else:
            journal = replay_journal(plan, request['plan_job_id'], journal['events'][:request['journal_revision']])
            if journal['sha256'] != request['journal_sha256']:
                raise ValueError('Saved observation journal prefix changed')
        receipt = prepare_receipt(plan, request['plan_job_id'], journal, request['observation_ids'])
        if len(parent['case']['problem_statement']) + len(receipt['additional_context']) + 50 > 100000:
            raise ValueError('Combined query exceeds the public input bound')
        return parent, raw, depth, receipt

    def assert_intent(self, request, identifier):
        validate_request(request, saved=True)
        db = self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT target_job FROM intents WHERE request_sha=?', (self.request_sha(request),)).fetchone()
            db.commit()
        finally:
            db.close()
        if row != (identifier,):
            raise ValueError('Missing durable check follow-up intent')

    def submit(self, request):
        request = validate_request(request)
        # Authenticate the plan/lineage even when returning an existing intent.
        plan = self.host.check_plan_bundle(request['plan_job_id'])
        if plan['sha256'] != request['plan_sha256']:
            raise ValueError('Choose the exact saved check plan')
        db = self._db()
        try:
            existing = db.execute('SELECT target_job FROM intents WHERE request_sha=?', (self.request_sha(request),)).fetchone()
            if existing:
                saved = json.loads(self.host._job_path(existing[0]).read_bytes())['request']
                if self.request_sha(saved) != self.request_sha(request):
                    raise ValueError('Check follow-up intent authority changed')
                # Read replay uses the immutable journal prefix, never Git/model.
                self.input(saved, current=False)
                return self.host.job(existing[0])
            journal = self.host.observation_journals.load(plan, request['plan_job_id'])
            saved = dict(request, mode='check_followup', parent_job_id=plan['source']['source_job_id'],
                         parent_pack_sha256=plan['source']['source_pack_sha256'], journal_revision=journal['revision'])
            parent,_,_,_=self.input(saved)
            self.host._reject_waiting_fork(parent)
            # Source replay may traverse prior check-followup intents. Acquire
            # the write lock only afterwards so ancestry never locks itself.
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT target_job FROM intents WHERE request_sha=?', (self.request_sha(request),)).fetchone()
            if existing:
                authority = json.loads(self.host._job_path(existing[0]).read_bytes())['request']
                if authority != saved:
                    raise ValueError('Check follow-up intent authority changed')
                return self.host.job(existing[0])
            target = uuid.uuid4().hex[:24]
            db.execute('INSERT INTO intents VALUES (?,?)', (self.request_sha(request), target))
            row = self.host._enqueue(saved, self.host._run, identifier=target)
            db.commit()
            return row
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def validate_child(self, identifier, pack, request):
        self.assert_intent(request, identifier)
        parent, _, _, expected = self.input(request, current=False)
        link = pack['followup']
        expected_case = {'case_id': 'followup:'+identifier, 'repository': parent['case']['repository'],
                         'revision': parent['snapshot']['commit_sha'], 'issue_created_at': None,
                         'problem_statement': parent['case']['problem_statement']+'\n\n[User follow-up context; unverified]\n'+expected['additional_context']}
        if parent['case'].get('issue_url'):
            expected_case['issue_url'] = parent['case']['issue_url']
        if (digest(link.get('check_observations')) != digest(expected) or link.get('clarification') is not None
                or link['parent_job_id'] != request['parent_job_id']
                or link['parent_pack_sha256'] != request['parent_pack_sha256']
                or link['additional_context'] != expected['additional_context']
                or digest(pack['case']) != digest(expected_case) or digest(pack['snapshot']) != digest(parent['snapshot'])
                or pack.get('repository_context') is not None
                or self.host._inspect_query(pack['case']['problem_statement'], self.host._cue_text(pack)) != pack.get('intake')):
            raise ValueError('Check observation child/source binding changed')
