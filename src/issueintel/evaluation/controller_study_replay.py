"""Replay completed study packs from admitted native stage journals.

Only original source replay reads files. No credentials, provider construction,
budget reservation, receipt settlement or target-code execution occurs here.
The caller validates registration/configuration and journal inventories before
and after this check. Included hashes establish consistency, not authorship.
"""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import MODEL, TypesafeError, validate_response
from issueintel.investigation.controller import RuleController
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.typesafe_controller import TypesafePlannedController, validate_controller_decisions
from . import controller_study as registration


class ControllerReplayError(OSError):
    """Replay integrity errors must escape the controller's rules fallback."""


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ControllerReplayError('Study replay requires finite UTF-8 JSON') from None


def _same(left, right):
    return _encoded(left) == _encoded(right)


class _DenyGroq:
    def __call__(self, *args, **kwargs):
        raise ControllerReplayError('Study replay cannot call a provider or reconstruct a missing stage')

    complete_json = __call__
    complete_structured = __call__
    complete_function = __call__


class _ReplayClient:
    """Native controller port which can only return one exact recorded choice."""
    config = SimpleNamespace(model=MODEL)

    def __init__(self, records):
        self.records = deepcopy(records)
        self.index = 0
        self.observed = 0
        self.last_receipt = None

    def decide(self, state, allowed, binding):
        if self.index >= len(self.records):
            raise ControllerReplayError('Replayed controller requested an unrecorded decision')
        record = self.records[self.index]
        if not _same((state, allowed, binding), (record['state'], record['allowed'], record['binding'])):
            raise ControllerReplayError('Replayed controller state, action allowlist or binding changed')
        self.index += 1
        self.last_receipt = deepcopy(record['receipt'])
        if record['fallback']:
            raise TypesafeError(record['reason'], receipt=self.last_receipt)
        normalized = validate_response(record['response'], allowed, MODEL)
        return {**normalized, 'response': deepcopy(record['response']),
                'receipt': deepcopy(record['receipt'])}

    def observe(self, record):
        if self.observed >= len(self.records) or not _same(record, self.records[self.observed]):
            raise ControllerReplayError('Native controller replay changed the saved decision record')
        self.observed += 1


def _controller(arm, pack, unit, policy, view):
    if arm == 'rules':
        if 'controller_decisions' in pack:
            raise ControllerReplayError('Rules replay cannot inherit a Jev decision sidecar')
        return RuleController(), None
    if arm != 'jev':
        raise ControllerReplayError('Unknown study controller arm')
    sidecar = validate_controller_decisions(pack)
    job_id = digest({'policy': policy['sha256'], 'unit': unit, 'arm': arm})[:24]
    if sidecar['schema'] != 'typesafe_controller_v1' or sidecar['job_id'] != job_id:
        raise ControllerReplayError('Saved Jev owner differs from its native study job identity')
    logical_arm = unit + ':' + arm
    journal = [row for row in view['decisions'] if row['owner_arm'] == logical_arm]
    journal.sort(key=lambda row: row['binding']['decision_index'])
    if (len(journal) != len(sidecar['records'])
            or any(row['status'] != 'completed' or not _same(row['record'], record)
                   for row, record in zip(journal, sidecar['records']))):
        raise ControllerReplayError('Saved Jev sidecar differs from its owned decision journal')
    calls = {row['call_id']: row for row in view['calls']}
    for row, record in zip(journal, sidecar['records']):
        call = calls.get(row['call_id'])
        if (call is None or call['provider'] != 'typesafe' or call['arm_id'] != logical_arm
                or not _same(call['input'], {'state': record['state'], 'allowed': record['allowed'],
                                             'binding': record['binding']})
                or call['request_sha256'] != record['request_sha256']
                or call['status'] not in {'completed', 'failed'}
                or not call['receipts'] or not _same(call['receipts'][-1], record['receipt'])):
            raise ControllerReplayError('Saved Jev decision differs from its native call provenance')
        if not record['fallback'] and (call['status'] != 'completed'
                or not _same(call['result']['response'], record['response'])):
            raise ControllerReplayError('Saved Jev selection differs from its actual native response')
    client = _ReplayClient(sidecar['records'])
    return TypesafePlannedController(client, job_id, on_decision=client.observe), client


def _replay_completed(source, selected, row, view):
    unit, arm = row['unit_id'], row['arm_id']
    policy = selected['product_policy']
    logical_arm = unit + ':' + arm
    if (not isinstance(view, dict) or view.get('schema') != 'controller_study_execution_inspection_v1'
            or view.get('unit_id') != unit or view.get('policy_sha256') != policy['sha256']
            or not all(isinstance(view.get(key), list) for key in ('stages', 'calls', 'decisions'))):
        raise ControllerReplayError('Completed study pack has no matching native execution view')
    stages = {}
    for stage in view['stages']:
        if not isinstance(stage, dict) or stage.get('key') in stages:
            raise ControllerReplayError('Duplicate or malformed replay stage')
        if stage.get('key') != digest(stage['binding']):
            raise ControllerReplayError('Replay stage binding hash differs')
        stages[stage['key']] = stage
    used = []

    def executor(kind, binding, produce):
        key = digest(binding)
        stage = stages.get(key)
        if (stage is None or stage['kind'] != kind or not _same(stage['binding'], binding)
                or stage['status'] != 'completed' or not isinstance(stage['payload'], dict)
                or stage['payload_sha256'] != digest(stage['payload'])
                or logical_arm not in stage['references'] or key in used):
            raise ControllerReplayError('A completed native stage, exact binding or arm reference is missing')
        used.append(key)
        # run_stage independently executes deterministic retrieval produce()
        # and native generation/verifier validators check the full payload.
        return deepcopy(stage['payload'])

    controller, client = _controller(arm, row['pack'], unit, policy, view)
    pack = investigate(source['case'], source['snapshot'].repository, protocol='benchmark_base',
        snapshot=source['snapshot'], graph=source['graph'],
        complete_json=_DenyGroq(), provider='groq', model='openai/gpt-oss-120b',
        top_k=12, independent_critic=True, max_output_tokens=4096,
        refinement_rounds=0, planned_workflow=True, planner_policy='bounded_planner_v2', controller=controller,
        product_study_policy=policy, product_study_stage_executor=executor)
    if client is not None and (client.index != len(client.records) or client.observed != len(client.records)):
        raise ControllerReplayError('Jev replay left recorded decisions unconsumed')
    if not _same(pack, row['pack']):
        raise ControllerReplayError('Saved completed pack differs from full native stage replay')
    expected = [{'key': key, **{field: deepcopy(stages[key][field]) for field in
        ('kind', 'owner_arm', 'status', 'payload_sha256', 'call_ids')}} for key in used]
    if not _same(expected, row['stage_refs']):
        raise ControllerReplayError('Completed pack stage references differ from its replayed trajectory')
    referenced = {stage['key'] for stage in stages.values() if logical_arm in stage['references']}
    if referenced != set(used):
        raise ControllerReplayError('Completed arm has unconsumed or missing native stage references')


def replay_results(root, spec, results, views, *, source_loader=None):
    """Read-only native replay of completed rows, loading each selected input once.

    Failed/unavailable rows never acquire a fabricated pack. ``views`` contains
    already audited StudyExecution.inspect() dictionaries keyed by unit_id.
    Admission and the full source/configuration/journal seal remain caller gates.
    """
    try:
        registration._unseal(spec, 'controller_study_registration_v1')
        supplied = list(results.values()) if isinstance(results, dict) else results
        if not isinstance(supplied, list) or not isinstance(views, dict):
            raise ControllerReplayError('Study replay needs result rows and per-unit execution views')
        selected = {row['unit_id']: row for row in spec['cases']}
        if len(selected) != len(spec['cases']):
            raise ControllerReplayError('Study replay selection contains duplicate units')
        # load_case performs current source, authority and native context replay;
        # it never constructs providers or opens a study/native budget ledger.
        sources = {unit: registration.load_case(root, spec, unit, source_loader=source_loader)
                   for unit in selected}
        seen = set()
        for row in supplied:
            registration._unseal(row, 'controller_study_arm_v1')
            unit, arm = row['unit_id'], row['arm_id']
            if (unit not in selected or arm not in {'rules', 'jev'} or (unit, arm) in seen
                    or row['registration_sha256'] != spec['sha256']
                    or row.get('study_id', spec['study_id']) != spec['study_id']
                    or not _same(row.get('source', selected[unit]['context']), selected[unit]['context'])):
                raise ControllerReplayError('Study replay result identity or source differs')
            seen.add((unit, arm))
            if row['status'] == 'completed':
                if not isinstance(row['pack'], dict):
                    raise ControllerReplayError('Completed study row has no native pack')
                _replay_completed(sources[unit], selected[unit], row, views.get(unit))
            elif row['status'] not in {'failed', 'unavailable'} or row['pack'] is not None:
                raise ControllerReplayError('Non-completed study row claims a native pack')
        return True
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, UnicodeError):
        raise ControllerReplayError('Invalid controller study replay structure') from None
