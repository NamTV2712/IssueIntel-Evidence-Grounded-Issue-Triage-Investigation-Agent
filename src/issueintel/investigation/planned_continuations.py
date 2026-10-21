"""Explicit, source-bound Planned answers with one durable child per pause.

Reading a saved continuation only replays private bytes and the recorded policy.
Admission additionally reads the current policy and the original Git snapshot;
no provider or configuration is constructed by this service.
"""
from copy import deepcopy
from collections import Counter
import hashlib
import json
import re
import sqlite3
import uuid

from issueintel.runtime.io import digest
from .clarifications import validate_answers


PUBLIC_KEYS = {'source_pack_sha256', 'workflow_plan_sha256', 'waiting_sha256',
               'policy_sha256', 'answers', 'language', 'confirmation'}
SAVED_KEYS = (PUBLIC_KEYS - {'confirmation'}) | {'mode', 'provider', 'depth', 'source_job_id'}
CONFIRMATION = 'continue_planned_with_api_cost'
LINK_SCHEMA = 'product_planned_continuation_v1'


def _hex(value, length=64):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{' + str(length) + '}', value) is not None


def validate_request(request, *, saved=False):
    if type(request) is not dict or set(request) != (SAVED_KEYS if saved else PUBLIC_KEYS):
        raise ValueError('Choose exact source-bound Planned questions and explicit continuation consent')
    if (any(not _hex(request[field]) for field in ('source_pack_sha256', 'workflow_plan_sha256',
                                                 'waiting_sha256', 'policy_sha256'))
            or request['language'] not in ('vi', 'en')):
        raise ValueError('Invalid Planned continuation binding or language')
    result = deepcopy(request)
    result['answers'] = validate_answers(result['answers'])
    if saved:
        if (request['mode'] != 'planned_continuation' or request['provider'] != 'groq'
                or request['depth'] != 'planned' or not _hex(request['source_job_id'], 24)):
            raise ValueError('Invalid saved Planned continuation authority')
    elif request['confirmation'] != CONFIRMATION:
        raise ValueError('Explicit cost consent is required for Planned continuation')
    return result


class PlannedContinuations:
    def __init__(self, host):
        self.host = host

    def _db(self):
        db = sqlite3.connect(self.host.directory / 'planned_continuations.sqlite', timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents ('
                   'source_job TEXT PRIMARY KEY, request_sha TEXT NOT NULL, '
                   'target_job TEXT NOT NULL UNIQUE)')
        return db

    def _intent(self, identifier):
        db = self._db()
        try:
            return db.execute('SELECT request_sha,target_job FROM intents WHERE source_job=?',
                              (identifier,)).fetchone()
        finally:
            db.close()

    def _child_authority(self, identifier, request):
        saved = json.loads(self.host._job_path(identifier).read_bytes())
        if (saved.get('id') != identifier or saved.get('mode') != 'planned_continuation'
                or saved.get('provider') != 'groq' or saved.get('depth') != 'planned'
                or digest(saved.get('request')) != digest(request)):
            raise ValueError('Saved Planned continuation child authority changed')

    def assert_intent(self, request, identifier):
        request = validate_request(request, saved=True)
        # The dispatch transaction is authoritative. Waiting for it also prevents
        # a newly started worker from observing its own uncommitted intent.
        db = self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT request_sha,target_job FROM intents WHERE source_job=?',
                             (request['source_job_id'],)).fetchone()
            db.commit()
        finally:
            db.close()
        if row != (digest(request), identifier):
            raise ValueError('Missing durable Planned continuation intent')
        self._child_authority(identifier, request)

    def _original_request(self, request, pack):
        from .workbench import validate_request as canonical_request
        from issueintel.integrations.github import github_input
        if digest(canonical_request(request)) != digest(request):
            raise ValueError('Original Planned source request is not canonical')
        if (request['mode'] not in {'local', 'github', 'example'} or request['provider'] != 'groq'
                or request['depth'] != 'planned' or request.get('controller', 'rules') != 'rules'):
            raise ValueError('Planned continuation requires rules and an original product source')
        case = pack['case']
        if request['mode'] == 'local':
            expected = {'case_id': 'local:' + digest(request)[:16],
                        'repository': request['repository'].strip(),
                        'revision': request['revision'].strip(),
                        'problem_statement': request['issue_text'], 'issue_created_at': None}
            if digest(case) != digest(expected):
                raise ValueError('Local Planned source differs from its saved request')
        elif request['mode'] == 'github':
            source, actual = github_input(request['issue_url']), github_input(case.get('issue_url', ''))
            if (set(case) != {'case_id', 'issue_url', 'repository', 'revision', 'problem_statement', 'issue_created_at'}
                    or case['case_id'] != actual.issue_url or actual.repository.casefold() != source.repository.casefold()
                    or actual.issue_number != source.issue_number
                    or case['repository'].casefold() != source.repository.casefold()
                    or case['revision'] != request['revision']):
                raise ValueError('GitHub Planned source differs from its saved request')
        elif (digest(case) != digest({'case_id': 'synthetic:parse-list', 'repository': 'synthetic/example',
                'revision': pack['snapshot']['commit_sha'], 'issue_created_at': None,
                'problem_statement': 'parser.parse_list fails on None input. Inspect how missing values are handled.'})):
            raise ValueError('Example Planned source differs from its fixed product input')

    def _source(self, identifier, *, admission=False):
        from .planned_waiting import SCHEMA, POLICY, policy_sha256, validate_plan, continuation_remaining, recovery_call_debit
        parent, raw, depth = self.host._load_pack(identifier)
        row = json.loads(self.host._job_path(identifier).read_bytes())
        request = row['request']
        if (row.get('id') != identifier or row.get('mode') != request.get('mode')
                or row.get('provider') != request.get('provider') or row.get('depth') != request.get('depth')
                or row.get('status') != 'completed' or request.get('provider') != 'groq'
                or request.get('depth') != 'planned' or depth >= 8
                or any(key in parent for key in ('followup', 'context_selection', 'resumed_from', 'finalized_from',
                                                'controller_decisions'))
                or parent['snapshot']['protocol'] != 'benchmark_base'
                or any(key in parent['case'] for key in ('split', 'instance_id'))
                or any(parent['leakage_audit'].get(key) is not False for key in
                       ('gold_in_inference', 'strict_issue_time_verified', 'snapshot_issue_time_verified',
                        'original_query_verified'))):
            raise ValueError('No supported product Planned pause is available')
        plan = parent.get('workflow_plan') or {}
        if (plan.get('schema') != SCHEMA or plan.get('policy') != POLICY
                or plan.get('provider') != 'groq' or plan.get('model') != 'openai/gpt-oss-120b'
                or plan.get('stop_reason') != 'waiting_for_user' or parent.get('stop_reason') != 'waiting_for_user'
                or plan.get('verifier_passes') != 0 or parent.get('verification', {}).get('kind') != 'structural'
                or not isinstance(plan.get('waiting'), dict) or not parent.get('annotation')):
            raise ValueError('Planned source is not waiting before verification')
        if request.get('mode') == 'planned_continuation':
            validate_request(request, saved=True)
            self.validate_child(identifier, parent, request)
        elif request.get('mode')=='stage_resume':
            # _load_pack authenticated intent, source prefix, new tail and the
            # recovered pack. Account all attempts, including failed originals;
            # successful logical stages alone cannot authorize further calls.
            self.host.stage_recoveries.validate_child(identifier,parent,request)
            if 'continued_from' in parent:raise ValueError('Unexpected recovered continuation lineage')
            validate_plan(parent)
        else:
            if 'continued_from' in parent:
                raise ValueError('Unexpected Planned continuation lineage')
            self._original_request(request, parent)
            validate_plan(parent)
        # Counts belong to this job; inherited totals are validated by the pure
        # plan replay. A new paid continuation cannot inherit unknown accounting.
        expected_calls = Counter({'seed_generation': 1 + plan['seed_annotation']['provenance']['repair_count']})
        for step in plan['rounds']:
            if step['output_annotation'] is not None:
                expected_calls['planned_generation_' + str(step['round'])] += (
                    1 + step['output_annotation']['provenance']['repair_count'])
        receipts = row.get('usage_receipts')
        recovered=request.get('mode')=='stage_resume'
        if recovered:
            source,_,_,_=self.host.stage_recoveries.source(request['source_job_id'])
            prior_receipts=source.get('usage_receipts')
            attempts=source.get('pipeline_attempts',[])+row.get('pipeline_attempts',[])
            if (source.get('usage_tracking') is not True or not isinstance(prior_receipts,dict)
                    or row.get('usage_tracking') is not True or not isinstance(receipts,dict)
                    or set(prior_receipts)&set(receipts)
                    or any(not isinstance(r,dict) for r in [*prior_receipts.values(),*receipts.values()])):
                raise ValueError('Unknown or duplicated recovered Planned receipts')
            receipts={**prior_receipts,**receipts}
            attempt_counts=Counter(attempt['stage'] for attempt in attempts)
            if (len(receipts)!=plan['provider_calls']+recovery_call_debit(parent)
                    or len(receipts)!=len(attempts)
                    or Counter(r.get('stage') for r in receipts.values())!=attempt_counts
                    or any(attempt_counts[stage]<count for stage,count in expected_calls.items())
                    or any(stage not in expected_calls for stage in attempt_counts)):
                raise ValueError('Recovered Planned receipts differ from owned attempts')
        if (row.get('usage_tracking') is not True or not isinstance(receipts, dict)
                or (not recovered and len(receipts) != plan['provider_calls'])
                or any(not isinstance(receipt, dict) for receipt in receipts.values())
                or (not recovered and Counter(receipt.get('stage') for receipt in receipts.values()) != expected_calls)
                or any(not isinstance(receipt, dict) or receipt.get('reservation_id') != key
                       or receipt.get('model') != plan['model']
                       or receipt.get('status') not in {'settled', 'missing_usage_bound', 'failed_bound'}
                       for key, receipt in receipts.items())):
            raise ValueError('Planned source call receipts differ from its recorded local calls')
        for stage, expected in expected_calls.items():
            states = [receipt['status'] for receipt in receipts.values() if receipt['stage'] == stage]
            success=any(state in {'settled','missing_usage_bound'} for state in states)
            if recovered and not success or not recovered and 'failed_bound' in states and (expected!=2 or not success):
                raise ValueError('Failed Planned calls require their completed, recorded schema repair')
        binding = {'source_job_id': identifier, 'source_pack_sha256': hashlib.sha256(raw).hexdigest(),
                   'workflow_plan_sha256': plan['sha256'], 'waiting_sha256': plan['waiting']['sha256'],
                   'policy_sha256': plan['policy_sha256']}
        if admission:
            if binding['policy_sha256'] != policy_sha256():
                raise ValueError('Planned policy changed; saved pause cannot authorize new calls')
            from .question_jobs import replay_snapshot
            replay_snapshot(parent)
            if continuation_remaining(parent)['provider_calls']<3:
                raise ValueError('Recovered Planned attempts exhausted the continuation budget')
        return parent, raw, binding, continuation_remaining(parent)

    def preview(self, identifier):
        from .planned_waiting import policy_sha256
        try:
            parent, _, binding, remaining = self._source(identifier)
            intent = self._intent(identifier)
            child = None
            if intent:
                saved = json.loads(self.host._job_path(intent[1]).read_bytes())['request']
                if (saved.get('source_job_id') != identifier
                        or digest({key: saved.get(key) for key in binding}) != digest(binding)):
                    raise ValueError('Planned continuation belongs to another source pause')
                self.assert_intent(saved, intent[1])
                if digest(saved) != intent[0]:
                    raise ValueError('Planned continuation intent changed')
                child = self.host.job(intent[1])
            current = binding['policy_sha256'] == policy_sha256()
            enough=remaining['provider_calls']>=3
            return dict(binding, available=current and enough, reason=(None if current and enough else
                        'planned_policy_changed' if not current else 'planned_attempt_budget_exhausted'),
                        waiting=deepcopy(parent['workflow_plan']['waiting']), remaining=remaining,
                        continuation_job=child)
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            return {'source_job_id': identifier, 'available': False,
                    'reason': 'no_valid_product_planned_pause', 'continuation_job': None}

    def input(self, request, identifier=None, *, admission=True):
        from .planned_waiting import build_continuation
        request = validate_request(request, saved=True)
        if identifier is not None:
            self.assert_intent(request, identifier)
        parent, raw, binding, _ = self._source(request['source_job_id'], admission=admission)
        if digest(binding) != digest({key: request[key] for key in binding}):
            raise ValueError('Planned source, waiting questions or policy binding changed')
        built = build_continuation(parent, request['answers'], request['language'])
        link = {'schema': LINK_SCHEMA, 'parent_job_id': request['source_job_id'],
                'parent_pack_sha256': binding['source_pack_sha256'],
                'parent_plan_sha256': binding['workflow_plan_sha256'],
                'waiting_sha256': binding['waiting_sha256'], 'policy_sha256': binding['policy_sha256'],
                'answers_receipt': built['receipt'], 'cue_text': built['cue_text'],
                'answered_fields': built['answered_fields']}
        link['sha256'] = digest(link)
        return parent, raw, built['case'], built['receipt'], link

    def submit(self, identifier, request):
        public = validate_request(request)
        saved = {key: value for key, value in public.items() if key != 'confirmation'}
        saved.update(mode='planned_continuation', provider='groq', depth='planned', source_job_id=identifier)
        validate_request(saved, saved=True)
        # Authenticate source bytes even for duplicate submission, but a replay
        # of an existing intent does not need Git, config, a key or today's policy.
        self.input(saved, admission=False)
        existing = self._intent(identifier)
        if existing:
            if existing[0] != digest(saved):
                raise ValueError('A different Planned continuation already owns this pause')
            self.assert_intent(saved, existing[1])
            return self.host.job(existing[1])
        self.input(saved, admission=True)
        db = self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT request_sha,target_job FROM intents WHERE source_job=?',
                                  (identifier,)).fetchone()
            if existing:
                if existing[0] != digest(saved):
                    raise ValueError('A different Planned continuation already owns this pause')
                self._child_authority(existing[1], saved)
                db.commit()
                return self.host.job(existing[1])
            target = uuid.uuid4().hex[:24]
            db.execute('INSERT INTO intents VALUES (?,?,?)', (identifier, digest(saved), target))
            job = self.host._enqueue(saved, self.host._run, identifier=target)
            db.commit()
            return job
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def validate_child(self, identifier, pack, request):
        from .planned_waiting import validate_plan, continued_context
        self.assert_intent(request, identifier)
        parent, _, case, _, link = self.input(request, admission=False)
        if (digest(pack.get('continued_from')) != digest(link) or digest(pack.get('case')) != digest(case)
                or digest(pack.get('snapshot')) != digest(parent['snapshot'])
                or digest(pack.get('repository_context')) != digest(continued_context(parent, case))
                or any(key in pack for key in ('followup', 'resumed_from', 'finalized_from', 'context_selection',
                                              'controller_decisions'))):
            raise ValueError('Planned continuation child differs from its source-bound answers')
        validate_plan(pack, parent=parent)
        return pack
