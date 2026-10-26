"""Owned, finite rules/Jev comparisons over registered original product inputs."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
import math
import os
import re
import time

from . import controller_study as registry
from .controller_study_execution import StudyExecution, SavedSharedFailure
from .controller_study_score import summarize_study, validate_results
from .controller_study_replay import replay_results
from issueintel.investigation.controller import RuleController
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.planner import validate_planned
from issueintel.investigation.typesafe_controller import TypesafePlannedController, validate_controller_decisions
from issueintel.investigation.providers import failure_metadata, ProviderError
from issueintel.annotations.models import AnnotationError
from issueintel.runtime.io import load_local_env
from issueintel.runtime.study_budget import StudyBudgetLedger, StudyBudgetStop


ARMS = ('rules', 'jev')
ACCESS_FLAGS = ('gold_read', 'holdout_access_authorized', 'live_access_authorized',
                'new_freeze_authorized', 'bulk_annotation_restart_authorized')
AUTH_FIELDS = {'schema', 'study_id', 'registration_sha256', 'source_fingerprint_sha256',
    'runtime_sha256', 'budget_policy_sha256', 'unit_ids', 'authorized_case_count', 'phase',
    'max_groq_attempts', 'max_typesafe_attempts', 'max_joint_micro_usd',
    'max_typesafe_micro_usd', 'execution_authorized', 'groq_spending_authorized',
    'typesafe_spending_authorized', 'author_request', 'date', 'expires_at', *ACCESS_FLAGS}
_CEILINGS = {1: (18, 4, 387072, 10752), 5: (90, 20, 1935360, 53760)}
_HEX = re.compile('[a-f0-9]{64}')
_CLOSED = {'settled', 'failed_bound', 'missing_usage_bound'}


def _utc(clock=None):
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None:
        raise ValueError('Study clock must retain its timezone')
    return value.astimezone(timezone.utc)


def verify_authorization(root, spec, path, *, limit, clock=None):
    """Validate a separate finite declaration, never manufacture human consent."""
    if type(limit) is not int or limit not in (1, 5):
        raise ValueError('Run the paired pilot or the registered five-case matrix')
    path = registry.safe_path(root, path, artifacts=True, exists=True)
    value = registry.read_json(root, path, maximum=32000)
    if not isinstance(value, dict) or set(value) != AUTH_FIELDS:
        raise ValueError('Need exact study-specific Groq and Typesafe authority')
    count = value['authorized_case_count']
    if type(count) is not int or count not in (1, 5) or limit != count:
        raise ValueError('Requested pilot or matrix phase differs from study authority')
    if (value['schema'] != 'controller_study_authorization_v1'
            or value['study_id'] != spec['study_id'] or value['registration_sha256'] != spec['sha256']
            or value['source_fingerprint_sha256'] != spec['source_fingerprint']['sha256']
            or value['runtime_sha256'] != spec['runtime']['sha256']
            or value['budget_policy_sha256'] != registry.digest(spec['budget_policy'])
            or value['unit_ids'] != [row['unit_id'] for row in spec['cases']]
            or value['phase'] != ('first_case_pilot' if count == 1 else 'registered_matrix')
            or any(value[key] is not True for key in
                   ('execution_authorized', 'groq_spending_authorized', 'typesafe_spending_authorized'))
            or any(value[key] is not False for key in ACCESS_FLAGS)
            or not isinstance(value['author_request'], str) or not value['author_request'].strip()
            or len(value['author_request']) > 8000):
        raise ValueError('Study authority differs from the frozen scope or separate provider approvals')
    fields = ('max_groq_attempts', 'max_typesafe_attempts', 'max_joint_micro_usd', 'max_typesafe_micro_usd')
    if any(type(value[key]) is not int or not 0 < value[key] <= ceiling
           for key, ceiling in zip(fields, _CEILINGS[count])):
        raise ValueError('Missing or excessive finite study authority bounds')
    try:
        approved = date.fromisoformat(value['date'])
        expiry = datetime.fromisoformat(value['expires_at'])
        now = _utc(clock)
        if (approved.isoformat() != value['date'] or expiry.tzinfo is None
                or expiry.utcoffset() != timedelta(0) or approved > now.date()
                or expiry <= now or expiry > datetime.combine(approved, daytime(), timezone.utc) + timedelta(days=2)):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Study authority is expired or has an invalid bounded UTC window') from None
    return value


@contextmanager
def _lease(root, directory):
    path = registry.safe_path(root, directory / 'execution.lock', artifacts=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'1'); stream.flush(); os.fsync(stream.fileno())
        elif stream.tell() != 1:
            raise StudyBudgetStop('Invalid controller study lease length')
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StudyBudgetStop('Another controller study process owns this execution lease') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _check_seal(value, schema):
    if (not isinstance(value, dict) or value.get('schema') != schema
            or value.get('sha256') != registry.digest({key: item for key, item in value.items() if key != 'sha256'})):
        raise StudyBudgetStop('Controller study journal seal or schema differs')


def _inventory(root, directory):
    """Every namespace has an owner; incidental notes cannot conceal work."""
    files = registry.inventory(root, directory, exclude_names={'execution.lock'})
    if 'execution.lock' in files:
        if files.pop('execution.lock') != {'bytes': 1}:
            raise StudyBudgetStop('Invalid controller study lease metadata')
    for name in files:
        parts = Path(name).parts
        allowed = ((len(parts) == 1 and name in {'registration.json', 'selection.json',
                    'execution.lock', 'pilot-gate.json', 'inference-sealed.json'})
            or parts[0] in {'budget', 'execution'}
            or (len(parts) == 2 and parts[0] in {'arms', 'execution-intents', 'scores'} and parts[1].endswith('.json'))
            or (len(parts) == 2 and parts[0] == 'native' and
                any(parts[1] == base + suffix for base in ('groq.sqlite', 'typesafe.sqlite')
                    for suffix in ('', '-wal', '-shm', '-journal'))))
        if not allowed:
            raise StudyBudgetStop('Unknown controller study inventory entry')
        if len(parts) == 2 and parts[0] in {'arms', 'execution-intents', 'scores'}:
            if not _HEX.fullmatch(parts[1][:-5]):
                raise StudyBudgetStop('Unknown controller study metadata owner')
            value = registry.read_json(root, directory / name)
            schemas = {'arms': 'controller_study_arm_v1',
                'execution-intents': 'controller_study_execution_intent_v1',
                'scores': 'controller_study_scores_v1'}
            _check_seal(value, schemas[parts[0]])
            if value.get('study_id') != directory.name:
                raise StudyBudgetStop('Controller study metadata belongs to another registration')
            if parts[0] != 'arms' and value['sha256'] != parts[1][:-5]:
                raise StudyBudgetStop('Controller study metadata filename changed')
            if parts[0] == 'execution-intents' and (value.get('gold_read') is not False
                    or value.get('quality_claim') is not False
                    or type(value.get('requested_case_count')) is not int
                    or value['requested_case_count'] not in (1, 5)
                    or value.get('authority_sha256') != registry.digest(value.get('authority'))):
                raise StudyBudgetStop('Invalid retained controller study execution intent')
            if parts[0] == 'scores' and not (directory / 'inference-sealed.json').is_file():
                raise StudyBudgetStop('Scores cannot precede the full native inference seal')
    return files


def _results(root, directory, spec, snapshot):
    units = {row['unit_id']: row for row in spec['cases']}
    result = {}
    folder = directory / 'arms'
    if not folder.exists():
        return result
    registry.safe_path(root, folder, artifacts=True, exists=True, directory=True)
    for path in folder.iterdir():
        row = registry.read_json(root, path)
        _check_seal(row, 'controller_study_arm_v1')
        key = row.get('unit_id'), row.get('arm_id')
        if (key[0] not in units or key[1] not in ARMS or key in result
                or row.get('study_id') != spec['study_id'] or row.get('registration_sha256') != spec['sha256']
                or path.name != registry.digest(list(key)) + '.json' or row.get('source') != units[key[0]]['context']):
            raise StudyBudgetStop('Saved study result owner or source changed')
        logical = key[0] + ':' + key[1]
        referenced = [call['call_id'] for call in snapshot['calls']
                      if any(ref['arm_id'] == logical for ref in call['references'])]
        owned = [call['call_id'] for call in snapshot['calls'] if call['arm_id'] == logical]
        if row.get('call_ids') != referenced or row.get('owned_call_ids') != owned:
            raise StudyBudgetStop('Saved result hid owned or shared study costs')
        if row['status'] == 'completed':
            if row.get('failure') is not None or not isinstance(row.get('pack'), dict):
                raise StudyBudgetStop('Saved completion lacks its full native pack')
            validate_planned(row['pack'])
            if key[1] == 'jev':
                validate_controller_decisions(row['pack'])
        elif row['status'] not in {'failed', 'unavailable'} or row.get('pack') is not None or not isinstance(row.get('failure'), dict):
            raise StudyBudgetStop('Saved failed study result changed')
        result[key] = row
    validate_results(spec, list(result.values()))
    return result


def _execution_views(root, spec, directory, budget, configs, *, active=None):
    calls = budget.inspect()['calls']
    found = {}
    folder = directory / 'execution'
    allowed = {row['unit_id'] for row in spec['cases']}
    if folder.exists() and any(not path.is_dir() or path.name not in allowed for path in folder.iterdir()):
        raise StudyBudgetStop('Unknown or moved study execution journal')
    for row in spec['cases']:
        unit = row['unit_id']
        has_calls = any(call['arm_id'] in {unit + ':' + arm for arm in ARMS} for call in calls)
        path = folder / unit
        if not path.exists():
            if has_calls:
                raise StudyBudgetStop('A paid unit journal was removed')
            continue
        execution = active if active is not None and active.unit_id == unit else StudyExecution(
            root, directory, unit, 'rules', row['product_policy'], *configs, budget, lambda: None,
            inspection_only=True)
        found[unit] = execution.inspect()
    recorded = [call['call_id'] for view in found.values() for call in view['calls']]
    if len(recorded) != len(set(recorded)) or set(recorded) != {call['call_id'] for call in calls}:
        raise StudyBudgetStop('A paid call lacks one exact unit journal')
    if not found:
        StudyExecution.audit_native_inventory(configs, calls)
    return found


def _interrupted(views, snapshot):
    return (snapshot['locked']
        or any(call['outcome'] in {'pending', 'unknown'} for call in snapshot['calls'])
        or any(row['status'] == 'pending' for view in views.values()
               for kind in ('stages', 'decisions') for row in view[kind]))


def _gate(spec, results, snapshot, views):
    first = spec['cases'][0]['unit_id']
    selected = [results.get((first, arm)) for arm in ARMS]
    if any(row is None or row['status'] != 'completed' for row in selected):
        raise StudyBudgetStop('Both first-case native arms must complete before matrix expansion')
    if snapshot['locked'] or any(call['outcome'] in {'pending', 'unknown'}
            or call['receipt'] is None or call['receipt']['status'] not in _CLOSED for call in snapshot['calls']):
        raise StudyBudgetStop('Pilot paid outcomes must be closed before matrix expansion')
    records = selected[1]['pack'].get('controller_decisions', {}).get('records', [])
    if not any(record.get('reason') == 'selected' and record.get('fallback') is False for record in records):
        raise StudyBudgetStop('Pilot needs an actual native Jev selection; fallback alone is not treatment acceptance')
    return registry.seal({'schema': 'controller_study_pilot_gate_v1',
        'study_id': spec['study_id'], 'registration_sha256': spec['sha256'],
        'unit_id': first, 'arms': [row['sha256'] for row in selected],
        'pilot_calls_sha256': registry.digest([call for call in snapshot['calls']
            if call['arm_id'] in {first + ':' + arm for arm in ARMS}]),
        'execution_sha256': registry.digest(views[first]), 'genuine_jev_selection': True,
        'gold_read': False, 'quality_claim': False})


def _phase_bound(authority, snapshot, provider, candidate):
    if provider not in {'groq', 'typesafe'} or type(candidate) is not int or candidate <= 0:
        raise StudyBudgetStop('Invalid next-call phase bound')
    calls = snapshot['calls']
    costs = [call['receipt']['micro_usd_charged_or_reserved'] if call['receipt'] is not None
             else call['micro_usd_bound'] for call in calls]
    typesafe = sum(cost for call, cost in zip(calls, costs) if call['provider'] == 'typesafe')
    if (sum(call['provider'] == provider for call in calls) + 1 > authority['max_' + provider + '_attempts']
            or sum(costs) + candidate > authority['max_joint_micro_usd']
            or typesafe + (candidate if provider == 'typesafe' else 0) > authority['max_typesafe_micro_usd']):
        raise StudyBudgetStop('Exact next call exceeds the separate phase authority')


def _seal_run(root, directory, spec, budget, results, views):
    snapshot = budget.inspect()
    if len(results) != 10 or snapshot['locked'] or any(call['outcome'] in {'pending', 'unknown'}
            or call['receipt'] is None or call['receipt']['status'] not in _CLOSED for call in snapshot['calls']):
        raise StudyBudgetStop('A full terminal ten-cell comparison with closed owned usage is required')
    if _interrupted(views, snapshot):
        raise StudyBudgetStop('Unfinished native stages or decisions cannot be sealed')
    files = _inventory(root, directory)
    stable = {name: sha for name, sha in files.items()
              if name != 'inference-sealed.json' and not name.startswith('scores/')
              and not name.endswith(('.sqlite-wal', '.sqlite-shm', '.sqlite-journal'))}
    return registry.seal({'schema': 'controller_study_inference_seal_v1', 'study_id': spec['study_id'],
        'registration_sha256': spec['sha256'], 'files': stable,
        'arm_results': {unit + ':' + arm: row['sha256'] for (unit, arm), row in results.items()},
        'budget_snapshot_sha256': registry.digest(snapshot),
        'executions_sha256': registry.digest(views), 'gold_read': False, 'quality_claim': False})


def run_study(root, study_id, authorization_path, *, limit=1, source_loader=None,
              provider_factories=None, load_environment=False, env_loader=None, clock=None):
    root = Path(root).resolve()
    spec, directory = registry.load_study(root, study_id, source_loader=source_loader)
    authority = verify_authorization(root, spec, authorization_path, limit=limit, clock=clock)
    original_authority = deepcopy(authority)
    selected = {row['unit_id'] for row in spec['cases'][:limit]}
    stopped = None
    with _lease(root, directory):
        if registry.load_study(root, study_id, source_loader=source_loader, replay_sources=False)[0] != spec:
            raise StudyBudgetStop('Registration changed before execution admission')
        _inventory(root, directory)
        configs = registry.runtime_configs(root, spec)
        budget = StudyBudgetLedger(directory / 'budget', spec['budget_policy'])
        views = _execution_views(root, spec, directory, budget, configs)
        results = _results(root, directory, spec, budget.inspect())
        if _interrupted(views, budget.inspect()):
            raise StudyBudgetStop('Interrupted or unknown study work requires explicit audit, never automatic retry')
        replay_results(root, spec, results, views, source_loader=source_loader)
        if limit == 5:
            gate = _gate(spec, results, budget.inspect(), views)
            path = directory / 'pilot-gate.json'
            if not path.exists():
                registry.write_new(root, path, gate)
            elif registry.read_json(root, path) != gate:
                raise StudyBudgetStop('Original paired pilot gate changed')
        remaining = [(row['unit_id'], arm) for row in spec['cases'][:limit] for arm in ARMS
                     if (row['unit_id'], arm) not in results]
        intent = registry.seal({'schema': 'controller_study_execution_intent_v1', 'study_id': study_id,
            'registration_sha256': spec['sha256'], 'authority': authority,
            'authority_sha256': registry.digest(authority), 'requested_case_count': limit,
            'gold_read': False, 'quality_claim': False})
        if remaining:
            if (verify_authorization(root, spec, authorization_path, limit=limit, clock=clock) != original_authority
                    or registry.load_study(root, study_id, replay_sources=False)[0] != spec):
                raise StudyBudgetStop('Phase authority or source changed during admission')
            _publish_same(root, directory / 'execution-intents' / (intent['sha256'] + '.json'), intent)
        if remaining and load_environment:
            if verify_authorization(root, spec, authorization_path, limit=limit, clock=clock) != original_authority:
                raise StudyBudgetStop('Phase authority changed before loading credentials')
            (env_loader or load_local_env)(root / '.env')
        for index, row in enumerate(spec['cases'][:limit]):
            unit = row['unit_id']
            order = ARMS if index % 2 == 0 else tuple(reversed(ARMS))
            todo = [arm for arm in order if (unit, arm) not in results]
            if not todo:
                continue
            started = time.monotonic()
            context = registry.load_case(root, spec, unit, source_loader=source_loader)
            preparation = round(time.monotonic() - started, 6)
            measurement = registry.digest([study_id, unit, time.time_ns()])
            for arm in todo:
                active = {}

                def fresh():
                    try:
                        if registry.load_study(root, study_id, replay_sources=False)[0] != spec:
                            raise ValueError('Registered study changed')
                        if verify_authorization(root, spec, authorization_path, limit=limit, clock=clock) != original_authority:
                            raise ValueError('Separate phase authority changed')
                        if unit not in selected:
                            raise ValueError('Current source is outside this phase')
                        registry.load_case(root, spec, unit, source_loader=source_loader)
                        _inventory(root, directory)
                        current = _results(root, directory, spec, budget.inspect())
                        if ({key: value['sha256'] for key, value in current.items()} !=
                                {key: value['sha256'] for key, value in results.items()}):
                            raise ValueError('Previously admitted completed results changed')
                        _execution_views(root, spec, directory, budget, configs, active=active.get('execution'))
                    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
                        raise StudyBudgetStop('Current source, journal or separate phase authority changed') from error

                def admit(provider, bound):
                    fresh()
                    _phase_bound(original_authority, budget.inspect(), provider, bound)

                execution = StudyExecution(root, directory, unit, arm, row['product_policy'],
                    *configs, budget, fresh, provider_factories=provider_factories, admit_call=admit)
                active['execution'] = execution
                fresh()
                controller = (RuleController() if arm == 'rules' else TypesafePlannedController(
                    execution.typesafe_client, execution.job_id, on_decision=execution.on_decision))
                begun = time.monotonic()
                pack, status, failure = None, 'completed', None
                try:
                    pack = investigate(context['case'], Path(context['snapshot'].repository),
                        snapshot=context['snapshot'], graph=context['graph'], protocol='benchmark_base',
                        provider='groq', model='openai/gpt-oss-120b', complete_json=execution.groq,
                        top_k=12, independent_critic=True, max_output_tokens=4096,
                        refinement_rounds=0, planned_workflow=True, planner_policy='bounded_planner_v2',
                        controller=controller, product_study_policy=row['product_policy'],
                        product_study_stage_executor=execution.stage)
                    validate_planned(pack)
                    if arm == 'jev':
                        validate_controller_decisions(pack)
                    fresh()
                except (ValueError, OSError, RuntimeError, AnnotationError) as error:
                    status = 'unavailable' if isinstance(error, SavedSharedFailure) else 'failed'
                    failure = failure_metadata(error)
                    pack = None
                    if isinstance(error, StudyBudgetStop) or not isinstance(error, (AnnotationError, ProviderError, SavedSharedFailure)):
                        stopped = 'source_storage_or_unknown_outcome_no_automatic_retry'
                elapsed = round(time.monotonic() - begun, 6)
                result = registry.seal({'schema': 'controller_study_arm_v1', 'study_id': study_id,
                    'registration_sha256': spec['sha256'], 'unit_id': unit, 'arm_id': arm,
                    'source': row['context'], 'status': status, 'pack': pack, 'failure': failure,
                    'stage_refs': execution.stage_refs, 'call_ids': execution.call_ids,
                    'owned_call_ids': execution.owned_call_ids, 'pipeline_latency_seconds': elapsed,
                    'preparation_seconds': preparation, 'preparation_measurement_id': measurement,
                    'preparation_owner_arm_id': todo[0],
                    'stop_reason': pack['stop_reason'] if pack is not None else stopped})
                registry.write_new(root, directory / 'arms' / (registry.digest([unit, arm]) + '.json'), result)
                results[(unit, arm)] = result
                if stopped:
                    break
            if stopped:
                break
        registry.load_study(root, study_id, source_loader=source_loader)
        views = _execution_views(root, spec, directory, budget, configs)
        results = _results(root, directory, spec, budget.inspect())
        replay_results(root, spec, results, views, source_loader=source_loader)
        if not stopped and len(results) == 10:
            sealed = _seal_run(root, directory, spec, budget, results, views)
            _publish_same(root, directory / 'inference-sealed.json', sealed)
        return {'schema': 'controller_study_progress_v1', 'study_id': study_id,
            'selected_cases': 5, 'terminal_arms': len(results),
            'completed_arms': sum(row['status'] == 'completed' for row in results.values()),
            'inference_sealed': (directory / 'inference-sealed.json').exists(),
            'owned_calls': budget.inspect()['owned_attempts'], 'stop_reason': stopped,
            'gold_read': False, 'quality_claim': False}


def inspect_run(root, study_id, *, source_loader=None):
    spec, directory = registry.load_study(root, study_id, source_loader=source_loader)
    files = _inventory(root, directory)
    if not (directory / 'budget').exists():
        if set(files) - {'registration.json', 'selection.json'}:
            raise StudyBudgetStop('Executed study metadata has lost its joint ledger')
        return {'schema': 'controller_study_run_inspection_v1', 'study_id': study_id,
                'selected_cases': 5, 'terminal_arms': 0, 'owned_calls': 0,
                'inference_sealed': False, 'provider_constructed': False, 'gold_read': False}
    budget = StudyBudgetLedger(directory / 'budget', spec['budget_policy'])
    configs = registry.runtime_configs(root, spec)
    views = _execution_views(root, spec, directory, budget, configs)
    snapshot = budget.inspect()
    results = _results(root, directory, spec, snapshot)
    replay_results(root, spec, results, views, source_loader=source_loader)
    if (directory / 'inference-sealed.json').exists():
        if registry.read_json(root, directory / 'inference-sealed.json') != _seal_run(
                root, directory, spec, budget, results, views):
            raise StudyBudgetStop('Saved controller study inference seal changed')
    return {'schema': 'controller_study_run_inspection_v1', 'study_id': study_id,
        'selected_cases': 5, 'terminal_arms': len(results),
        'completed_arms': sum(row['status'] == 'completed' for row in results.values()),
        'owned_calls': snapshot['owned_attempts'], 'ledger_locked': snapshot['locked'],
        'interrupted_work': bool(_interrupted(views, snapshot)),
        'unit_journals': len(views), 'inference_sealed': (directory / 'inference-sealed.json').exists(),
        'provider_constructed': False, 'gold_read': False}


def score_study(root, study_id, *, source_loader=None):
    spec, directory = registry.load_study(root, study_id, source_loader=source_loader)
    saved = registry.read_json(root, directory / 'inference-sealed.json')
    if not (directory / 'budget').is_dir():
        raise StudyBudgetStop('Sealed comparison has no existing joint ledger')
    budget = StudyBudgetLedger(directory / 'budget', spec['budget_policy'])
    views = _execution_views(root, spec, directory, budget, registry.runtime_configs(root, spec))
    snapshot = budget.inspect()
    results = _results(root, directory, spec, snapshot)
    replay_results(root, spec, results, views, source_loader=source_loader)
    expected = _seal_run(root, directory, spec, budget, results, views)
    if saved != expected:
        raise StudyBudgetStop('Full native comparison seal changed before scoring')
    calls = [call for view in views.values() for call in view['calls']]
    score = summarize_study(spec, list(results.values()), snapshot, calls)
    registry.load_study(root, study_id, source_loader=source_loader)
    if _seal_run(root, directory, spec, budget, _results(root, directory, spec, budget.inspect()),
            _execution_views(root, spec, directory, budget, registry.runtime_configs(root, spec))) != saved:
        raise StudyBudgetStop('Comparison changed during gold-free scoring')
    _publish_same(root, directory / 'scores' / (score['sha256'] + '.json'), score)
    return score


def _publish_same(root, path, value):
    if path.exists():
        if registry.read_json(root, path) != value:
            raise StudyBudgetStop('An immutable study artifact already has different bytes')
    else:
        registry.write_new(root, path, value)
