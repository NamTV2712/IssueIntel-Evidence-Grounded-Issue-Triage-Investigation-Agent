"""Fresh common critic for a closed, frozen product controller comparison.

The native product modules are imported from the parent checkout by an isolated
CLI bootstrap. Only this adapter and its pure projection helper come from the
new checkout. All judge state belongs to a separate child with one owner per
parent seal; no parent record, source or receipt is repaired or rewritten.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, time as daytime, timedelta, timezone
from hashlib import sha256
import argparse
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

from . import common_grounding as common
from . import controller_study as registry
from . import controller_study_run as parent_run
from .controller_study_execution import StageJournal
from .controller_study_replay import replay_results
from issueintel.investigation.providers import GroqProvider, _input_token_bound, failure_metadata
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import load_local_env
from issueintel.runtime import study_budget as allowance


PLAN_SCHEMA = 'controller_grounding_plan_v1'
AUTH_SCHEMA = 'controller_grounding_authorization_v1'
SCOPE = 'closed_product_controller_common_grounding_v1'
_HEX = re.compile('[a-f0-9]{64}')
_CLOSED = {'settled', 'failed_bound', 'missing_usage_bound'}
_FALSE_FLAGS = ('gold_read', 'holdout_or_live_access', 'jev_spending_authorized',
                'new_freeze_authorized', 'bulk_annotation_restart_authorized')
_CAPS = {'max_combined_groq_calls': 90, 'max_combined_typesafe_calls': 20,
         'max_combined_micro_usd': 1935360, 'max_typesafe_micro_usd': 53760}


def _sealed(value, schema):
    registry._unseal(value, schema)
    return value


def _read_only(path):
    path = allowance._safe(path)
    if not path.is_file():
        raise allowance.StudyBudgetStop('Missing existing study index')
    # SQLite mode=ro can still create WAL/SHM auxiliaries. These are closed
    # immutable study artifacts, so admit only checkpointed rollback-journal
    # databases and never recover or checkpoint them here.
    with path.open('rb') as stream:
        header = stream.read(100)
    if (len(header) != 100 or header[:16] != b'SQLite format 3\x00'
            or header[18:20] != b'\x01\x01'
            or any(path.with_name(path.name + suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))):
        raise allowance.StudyBudgetStop('Read-only study audit requires a closed checkpointed database')
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise allowance.StudyBudgetStop('Study index integrity check failed')
        db.execute('BEGIN')
    except BaseException:
        db.close()
        raise
    return db


def _budget_read(directory, policy):
    """Replay existing events through query-only SQLite, never recover an index."""
    budget = object.__new__(allowance.StudyBudgetLedger)
    budget.policy = deepcopy(policy)
    budget.providers, budget.arms = allowance._validate_policy(budget.policy)
    budget.policy_sha256 = allowance._digest(budget.policy)
    budget.directory = allowance._safe(directory)
    budget.events_directory = budget.directory / 'events'
    budget.path = budget.directory / 'index.sqlite'
    db = _read_only(budget.path)
    try:
        state = budget._load(db)
    finally:
        db.close()
    snapshot = {'schema': 'study_budget_inspection_v1', 'policy_sha256': budget.policy_sha256,
        'locked': state['locked'], 'stop_reasons': list(state['stop_reasons']),
        'event_count': len(state['events']), **budget._totals(state),
        'calls': deepcopy(list(state['calls'].values())), 'is_provider_invoice': False,
        'automatic_retry': False,
        'integrity_scope': 'event/index byte consistency; not authorship or coordinated rollback protection'}
    return budget, snapshot


def _native_receipts(path):
    """Use the native low-effort receipt contract, including unknown directions."""
    path = allowance._safe(path)
    if not path.exists():
        return {}
    db = _read_only(path)
    try:
        keys = ('created_at', 'model', 'input_bound', 'output_bound',
            'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved', 'status',
            'price_date', 'observed_input_tokens', 'observed_output_tokens')
        rows = db.execute('SELECT c.id,c.created_at,c.model,c.input_bound,c.output_bound,'
            'c.charged_tokens,c.charged_micro_usd,c.status,c.price_date,x.input_tokens,x.output_tokens '
            'FROM calls c LEFT JOIN call_context x ON x.id=c.id LIMIT 1001').fetchall()
        if len(rows) > 1000:
            raise allowance.StudyBudgetStop('Native judge receipt inventory exceeds its bound')
        return {row[0]: dict(zip(keys, row[1:]), reservation_id=row[0]) for row in rows}
    finally:
        db.close()


def _offline_source(root, row, pack):
    from issueintel.investigation.question_jobs import replay_snapshot
    from issueintel.repository.graph import build_graph
    path = registry.safe_path(root, 'artifacts/demo/jobs/' + row['source_job_id'] + '.json',
                              artifacts=True, exists=True)
    snapshot = replay_snapshot(pack)
    return {'case': pack['case'], 'snapshot': snapshot, 'graph': build_graph(snapshot),
        'authority': {'path': path.relative_to(root).as_posix(),
            'sha256': sha256(registry.read_bytes(path)).hexdigest(), 'mode': 'github'}}


def _journal_read(root, directory, selected, budget):
    unit = selected['unit_id']
    folder = registry.safe_path(root, directory / 'execution' / unit,
                                artifacts=True, exists=True, directory=True)
    journal = object.__new__(StageJournal)
    journal.root, journal.study_dir = root, directory
    journal.policy, journal.unit_id, journal.budget = selected['product_policy'], unit, budget
    journal.directory, journal.events_dir, journal.raw_dir = folder, folder / 'events', folder / 'raw'
    journal.path = folder / 'index.sqlite'
    journal.identity = {'schema': 'controller_study_execution_identity_v1', 'unit_id': unit,
        'study_directory': str(directory), 'policy_sha256': selected['product_policy']['sha256'],
        'budget_policy_sha256': budget.policy_sha256}
    db = _read_only(journal.path)
    try:
        state = journal._load(db)
    finally:
        db.close()
    return {'schema': 'controller_study_execution_inspection_v1', 'unit_id': unit,
        'policy_sha256': selected['product_policy']['sha256'],
        **{kind: list(state[kind].values()) for kind in ('stages', 'calls', 'decisions')},
        'stage_refs': [{'key': row['key'], **{field: row[field] for field in
            ('kind', 'owner_arm', 'status', 'payload_sha256', 'call_ids')}}
            for row in state['stages'].values() if unit + ':rules' in row['references']]}


def _isolation(root):
    expected = root / 'src/issueintel/evaluation'
    if (Path(registry.__file__).resolve() != expected / 'controller_study.py'
            or Path(parent_run.__file__).resolve() != expected / 'controller_study_run.py'):
        raise allowance.StudyBudgetStop('Common judge must import native code from the exact frozen parent root')


def _native_parent(root, identifier):
    """Load a fully closed parent without writable ledger or Workbench objects."""
    _isolation(root)
    spec, directory = registry.load_study(root, identifier, replay_sources=False)
    before = registry.inventory(root, directory, exclude_names={'execution.lock'})
    saved = registry.read_json(root, directory / 'inference-sealed.json')
    _sealed(saved, 'controller_study_inference_seal_v1')
    budget, snapshot = _budget_read(directory / 'budget', spec['budget_policy'])
    views = {row['unit_id']: _journal_read(root, directory, row, budget) for row in spec['cases']}
    if parent_run._interrupted(views, snapshot):
        raise allowance.StudyBudgetStop('Parent has pending, unknown or locked work')
    owned = {row['call_id']: row for row in snapshot['calls']}
    journal = {row['call_id']: row for view in views.values() for row in view['calls']}
    if len(journal) != sum(len(view['calls']) for view in views.values()) or set(owned) != set(journal):
        raise allowance.StudyBudgetStop('Parent journal ownership inventory changed')
    for identifier, row in journal.items():
        call = owned[identifier]
        if (any(row[key] != call[key] for key in ('arm_id', 'provider', 'stage', 'request_sha256'))
                or row['status'] != call['outcome'] or not row['receipts']
                or row['receipts'][-1] != call['receipt']
                or row['request_sha256'] != registry.digest(row['request'])
                or row['status'] == 'completed' and registry.digest(row['result']) != call['outcome_sha256']):
            raise allowance.StudyBudgetStop('Parent call differs from its owned native journal')
    for provider in ('groq', 'typesafe'):
        expected = {call['receipt']['reservation_id']: call['receipt']
                    for call in owned.values() if call['provider'] == provider and call['receipt'] is not None}
        if provider == 'groq':
            actual = _native_receipts(directory / 'native/groq.sqlite')
        else:
            db = _read_only(directory / 'native/typesafe.sqlite')
            try:
                rows = db.execute('SELECT id,receipt FROM typesafe_calls LIMIT 1001').fetchall()
                if len(rows) > 1000:
                    raise allowance.StudyBudgetStop('Parent Typesafe receipt inventory exceeds its bound')
                actual = {rid: json.loads(raw) for rid, raw in rows}
            finally:
                db.close()
        if actual != expected:
            raise allowance.StudyBudgetStop('Parent native ledger has foreign or differing receipts')
    results = parent_run._results(root, directory, spec, snapshot)
    replay_results(root, spec, results, views, source_loader=_offline_source)
    class FrozenBudget:
        def inspect(self):
            return deepcopy(snapshot)
    if parent_run._seal_run(root, directory, spec, FrozenBudget(), results, views) != saved:
        raise allowance.StudyBudgetStop('Full parent inference seal no longer matches native replay')
    after = registry.inventory(root, directory, exclude_names={'execution.lock'})
    if after != before:
        raise allowance.StudyBudgetStop('Parent ledger or inventory changed during read-only admission')
    return {'registration': spec, 'seal': saved, 'results': list(results.values()),
            'budget_snapshot': snapshot, 'inventory': before, 'native_replay_verified': True}


def _parent(root, identifier, parent_loader=None):
    state = (parent_loader or _native_parent)(root, identifier)
    spec, seal = state['registration'], state['seal']
    _sealed(spec, 'controller_study_registration_v1')
    _sealed(seal, 'controller_study_inference_seal_v1')
    cases = [row['unit_id'] for row in spec['cases']]
    rows = state['results']
    expected = {(unit, arm) for unit in cases for arm in ('rules', 'jev')}
    if (spec['study_id'] != identifier or len(cases) != 5 or len(set(cases)) != 5
            or spec['protocol']['arms'] != ['rules', 'jev'] or state.get('native_replay_verified') is not True
            or seal.get('study_id') != identifier or seal.get('registration_sha256') != spec['sha256']
            or seal.get('gold_read') is not False or seal.get('quality_claim') is not False
            or len(rows) != 10 or {(row['unit_id'], row['arm_id']) for row in rows} != expected
            or seal.get('arm_results') != {row['unit_id'] + ':' + row['arm_id']: row['sha256'] for row in rows}):
        raise allowance.StudyBudgetStop('Common judge requires the exact full five-case native parent seal')
    for row in rows:
        _sealed(row, 'controller_study_arm_v1')
        if (row['registration_sha256'] != spec['sha256'] or row['status'] not in {'completed', 'failed', 'unavailable'}
                or (row['status'] == 'completed') != isinstance(row.get('pack'), dict)):
            raise allowance.StudyBudgetStop('Parent contains unfinished or foreign arm results')
    snapshot = state['budget_snapshot']
    if snapshot['locked'] or any(call['outcome'] in {'pending', 'unknown'}
            or call['receipt'] is None or call['receipt']['status'] not in _CLOSED for call in snapshot['calls']):
        raise allowance.StudyBudgetStop('Parent paid outcomes must be closed before common assessment')
    return state


def _sources():
    rows = []
    for path in sorted({Path(__file__).resolve(), Path(common.__file__).resolve()}):
        raw = registry.read_bytes(allowance._safe(path), maximum=4_000_000)
        rows.append({'path': str(path), 'sha256': sha256(raw).hexdigest()})
    return rows


def _wire(request):
    return {'model': 'openai/gpt-oss-120b', 'messages': deepcopy(request['messages']),
        'max_completion_tokens': 2048, 'temperature': 0, 'reasoning_effort': 'low',
        'include_reasoning': False, 'stream': False,
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'issueintel_response', 'strict': True, 'schema': deepcopy(request['schema'])}}}


def _config(root, directory):
    config = RuntimeConfig.load(root)
    if config.model != 'openai/gpt-oss-120b' or config.reasoning_effort != 'low':
        raise allowance.StudyBudgetStop('Common product judge requires the frozen native low-effort model')
    return replace(config, max_output_tokens=2048, ledger_file=directory / 'native/groq.sqlite')


def _descriptor(config):
    return {'model': config.model, 'reasoning_effort': config.reasoning_effort,
        'max_output_tokens': config.max_output_tokens,
        'price_file': str(config.price_file), 'ledger_file': 'native/groq.sqlite',
        'policy': {key: str(value) if type(value).__module__ == 'decimal' else value
                   for key, value in vars(config.policy).items()}}


def grounding_directory(root, identifier):
    if not isinstance(identifier, str) or not _HEX.fullmatch(identifier):
        raise ValueError('Use the exact parent inference seal ID')
    return registry.safe_path(root, Path('artifacts/controller-grounding') / identifier, artifacts=True)


def _parent_usage(state):
    calls = state['budget_snapshot']['calls']
    return {'groq_calls': sum(row['provider'] == 'groq' for row in calls),
        'typesafe_calls': sum(row['provider'] == 'typesafe' for row in calls),
        'micro_usd': sum(row['receipt']['micro_usd_charged_or_reserved'] for row in calls),
        'typesafe_micro_usd': sum(row['receipt']['micro_usd_charged_or_reserved'] for row in calls
                                  if row['provider'] == 'typesafe')}


def _make_plan(root, state):
    spec, parent_seal = state['registration'], state['seal']
    identifier = parent_seal['sha256']
    directory = grounding_directory(root, identifier)
    # Plan JSON sorts object keys. Native tokenizer counts the rendered schema,
    # so bind that same canonical key order before computing the exact bound.
    projected = json.loads(json.dumps(common.make_units(spec['cases'], state['results']),
        ensure_ascii=False, sort_keys=True, allow_nan=False))
    if len(projected['units']) > 10 or len(_selected(projected, 1)) > 2:
        raise allowance.StudyBudgetStop('Common judge exceeds the registered ten-input/two-pilot limit')
    config = _config(root, directory)
    descriptor, usage = _descriptor(config), _parent_usage(state)
    residual_calls = min(10, _CAPS['max_combined_groq_calls'] - usage['groq_calls'])
    residual_usd = _CAPS['max_combined_micro_usd'] - usage['micro_usd']
    price_ref = {'path': str(config.price_file), 'sha256': sha256(registry.read_bytes(config.price_file)).hexdigest(),
                 'checked_at': config.price().checked_at}
    call_bounds = {}
    for unit in projected['units']:
        incoming = _input_token_bound(unit['request']['messages'], unit['request']['schema'])
        if incoming + 2048 > 131072:
            raise allowance.StudyBudgetStop('A frozen common critic request exceeds native context')
        call_bounds[unit['unit_id']] = {'input_bound': incoming,
            'micro_usd_bound': config.price().micro_usd(incoming, 2048),
            'wire_sha256': registry.digest(_wire(unit['request']))}
    eligible_budget = (residual_calls > 0 and residual_usd > 0 and
        usage['typesafe_calls'] <= 20 and usage['typesafe_micro_usd'] <= 53760)
    budget = None
    if projected['units'] and eligible_budget:
        budget = {'schema': 'study_budget_policy_v1', 'study_id': identifier,
            'config_sha256': registry.digest(descriptor),
            'providers': [{'provider': 'groq', 'model': config.model,
                'config_sha256': registry.digest(descriptor), 'price_sha256': price_ref['sha256'],
                'price_date': price_ref['checked_at'], 'max_attempts': residual_calls}],
            'arms': [{'arm_id': unit['unit_id'], 'max_micro_usd': call_bounds[unit['unit_id']]['micro_usd_bound'],
                      'max_attempts': 1} for unit in projected['units']],
            'max_owned_attempts': residual_calls, 'max_owned_micro_usd': residual_usd}
    return registry.seal({'schema': PLAN_SCHEMA, 'scope': SCOPE, 'study_id': identifier,
        'parent_study_id': spec['study_id'], 'parent_registration_sha256': spec['sha256'],
        'parent_inference_seal_sha256': parent_seal['sha256'],
        'parent_source_fingerprint': spec['source_fingerprint'], 'parent_runtime': spec['runtime'],
        'parent_inventory': state['inventory'], 'parent_budget_snapshot_sha256': registry.digest(state['budget_snapshot']),
        'parent_usage': usage, 'combined_caps': dict(_CAPS), 'adapter_sources': _sources(),
        'judge_config': dict(common.JUDGE_CONFIG), 'native_config': descriptor, 'price': price_ref,
        **projected, 'call_bounds': call_bounds, 'budget_policy': budget,
        'fresh_common_judge': True, 'reuses_in_treatment_critic': False,
        'gold_read': False, 'independent_human': False, 'quality_claim': False})


def _selected(plan, limit):
    cases = set(plan['case_ids'][:limit])
    return [unit for unit in plan['units'] if cases.intersection(unit['case_ids'])]


def create_plan(root, parent_study_id, *, parent_loader=None):
    root = Path(root).resolve()
    state = _parent(root, parent_study_id, parent_loader)
    plan = _make_plan(root, state)
    directory = grounding_directory(root, plan['study_id'])
    owner = registry.safe_path(root, Path('artifacts/controller-grounding/owners') /
                               (plan['study_id'] + '.json'), artifacts=True)
    identity = registry.seal({'schema': 'controller_grounding_owner_v1', 'study_id': plan['study_id'],
        'plan_sha256': plan['sha256'], 'parent_study_id': parent_study_id,
        'parent_seal_sha256': plan['parent_inference_seal_sha256']})
    if owner.exists():
        if registry.read_json(root, owner) != identity or not (directory / 'plan.json').is_file():
            raise allowance.StudyBudgetStop('This parent seal already has a different or incomplete common-judge owner')
        if registry.read_json(root, directory / 'plan.json') != plan:
            raise allowance.StudyBudgetStop('Existing common-judge plan cannot be replaced to reset its budget')
    else:
        if directory.exists():
            raise allowance.StudyBudgetStop('Unowned common-judge directory already exists')
        registry.write_new(root, owner, identity)
        registry.write_new(root, directory / 'plan.json', plan)
    return plan


def load_plan(root, identifier, *, parent_loader=None):
    root = Path(root).resolve()
    directory = grounding_directory(root, identifier)
    plan = registry.read_json(root, directory / 'plan.json')
    _sealed(plan, PLAN_SCHEMA)
    state = _parent(root, plan['parent_study_id'], parent_loader)
    if plan['study_id'] != identifier or plan != _make_plan(root, state):
        raise allowance.StudyBudgetStop('Frozen parent, source, config, price or common-judge projection changed')
    owner = registry.read_json(root, Path('artifacts/controller-grounding/owners') / (identifier + '.json'))
    _sealed(owner, 'controller_grounding_owner_v1')
    if owner != registry.seal({'schema': 'controller_grounding_owner_v1', 'study_id': identifier,
            'plan_sha256': plan['sha256'], 'parent_study_id': plan['parent_study_id'],
            'parent_seal_sha256': plan['parent_inference_seal_sha256']}):
        raise allowance.StudyBudgetStop('Common-judge single-parent ownership changed')
    return plan, directory, state


def _authority_value(plan, value, *, limit, clock=None, active=True):
    if type(limit) is not int or limit not in (1, 5):
        raise ValueError('Run the first-case common-judge pilot or the registered five-case matrix')
    fields = {'schema', 'scope', 'study_id', 'plan_sha256', 'parent_study_id',
        'parent_registration_sha256', 'parent_inference_seal_sha256',
        'parent_source_fingerprint_sha256', 'parent_runtime_sha256', 'adapter_sources_sha256',
        'case_ids', 'unit_ids', 'authorized_case_count', 'phase', 'max_new_judge_calls',
        'execution_authorized', 'groq_spending_authorized', 'author_request', 'date', 'expires_at',
        *_FALSE_FLAGS, *_CAPS}
    if (not isinstance(value, dict) or set(value) != fields or value['schema'] != AUTH_SCHEMA
            or value['scope'] != SCOPE or type(value['authorized_case_count']) is not int
            or value['authorized_case_count'] != limit
            or value['phase'] != ('first_case_pilot' if limit == 1 else 'registered_matrix')
            or any(value[key] != plan[key] for key in ('study_id', 'parent_study_id',
                'parent_registration_sha256', 'parent_inference_seal_sha256', 'case_ids'))
            or value['plan_sha256'] != plan['sha256']
            or value['parent_source_fingerprint_sha256'] != plan['parent_source_fingerprint']['sha256']
            or value['parent_runtime_sha256'] != plan['parent_runtime']['sha256']
            or value['adapter_sources_sha256'] != registry.digest(plan['adapter_sources'])
            or value['unit_ids'] != [row['unit_id'] for row in plan['units']]
            or type(value['max_new_judge_calls']) is not int
            or value['max_new_judge_calls'] != len(_selected(plan, limit))
            or any(type(value[key]) is not int or value[key] != bound for key, bound in _CAPS.items())
            or value['execution_authorized'] is not True or value['groq_spending_authorized'] is not True
            or any(value[key] is not False for key in _FALSE_FLAGS)
            or not isinstance(value['author_request'], str) or not value['author_request'].strip()
            or len(value['author_request']) > 8000):
        raise ValueError('Exact separate common-judge phase authority and retained combined caps are required')
    try:
        approved = date.fromisoformat(value['date'])
        expiry = datetime.fromisoformat(value['expires_at'])
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        first = datetime.combine(approved, daytime(), timezone.utc)
        if (now.tzinfo is None or expiry.tzinfo is None or expiry.utcoffset() != timedelta(0)
                or approved.isoformat() != value['date'] or approved > now.astimezone(timezone.utc).date()
                or expiry <= first or active and expiry <= now
                or expiry > first + timedelta(days=2)):
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Common-judge authority requires an unexpired bounded UTC decision') from None
    return value


def verify_authorization(root, plan, path, *, limit, clock=None):
    value = registry.read_json(root, registry.safe_path(root, path, artifacts=True, exists=True), maximum=32000)
    return _authority_value(plan, value, limit=limit, clock=clock)


def _retained_intents(root, directory, plan):
    """Validate historical declarations without requiring them to remain live."""
    folder, intents = directory / 'execution-intents', {}
    if not folder.exists():
        return intents
    registry.safe_path(root, folder, artifacts=True, exists=True, directory=True)
    fields = {'schema', 'plan_sha256', 'authority', 'authority_sha256',
              'requested_cases', 'pilot_gate_sha256', 'gold_read', 'sha256'}
    for path in folder.iterdir():
        value = registry.read_json(root, path, maximum=32000)
        _sealed(value, 'controller_grounding_execution_intent_v1')
        if (set(value) != fields or path.name != value['sha256'] + '.json'
                or value['plan_sha256'] != plan['sha256']
                or value['authority_sha256'] != registry.digest(value['authority'])
                or value['gold_read'] is not False):
            raise allowance.StudyBudgetStop('Retained common-judge execution authority changed')
        _authority_value(plan, value['authority'], limit=value['requested_cases'], active=False)
        if ((value['requested_cases'] == 1 and value['pilot_gate_sha256'] is not None)
                or (value['requested_cases'] == 5 and
                    (not isinstance(value['pilot_gate_sha256'], str)
                     or not _HEX.fullmatch(value['pilot_gate_sha256'])))):
            raise allowance.StudyBudgetStop('Retained common-judge phase gate changed')
        intents[value['sha256']] = value
    return intents


def _inventory(root, directory, plan):
    files = registry.inventory(root, directory, maximum_files=5000, maximum_bytes=128_000_000,
                               exclude_names={'execution.lock'})
    if 'execution.lock' in files and files.pop('execution.lock') != {'bytes': 1}:
        raise allowance.StudyBudgetStop('Invalid common-judge lease metadata')
    units = {row['unit_id'] for row in plan['units']}
    for name in files:
        parts = Path(name).parts
        allowed = ((len(parts) == 1 and name in {'plan.json', 'pilot-gate.json', 'inference-sealed.json'})
            or parts[0] == 'budget'
            or len(parts) == 2 and parts[0] == 'native' and parts[1] in
                {'groq.sqlite', 'groq.sqlite-journal', 'groq.sqlite-wal', 'groq.sqlite-shm'}
            or len(parts) == 3 and parts[0] == 'units' and parts[1] in units
                and parts[2] in {'intent.json', 'outcome.json'}
            or len(parts) == 2 and parts[0] in {'execution-intents', 'scores'}
                and parts[1].endswith('.json') and _HEX.fullmatch(parts[1][:-5]))
        if not allowed:
            raise allowance.StudyBudgetStop('Unknown common-judge inventory entry')
        if len(parts) == 2 and parts[0] == 'scores':
            value = registry.read_json(root, directory / name)
            _sealed(value, 'controller_grounding_score_v1')
            if value['sha256'] != parts[1][:-5] or value.get('plan_sha256') != plan['sha256']:
                raise allowance.StudyBudgetStop('Retained common-judge score identity changed')
    _retained_intents(root, directory, plan)
    return files


def _snapshot(directory, plan):
    path = directory / 'budget'
    if not path.exists():
        if (directory / 'units').exists() or (directory / 'native').exists():
            raise allowance.StudyBudgetStop('Owned judge work has lost its joint budget')
        return None, None
    if plan['budget_policy'] is None:
        raise allowance.StudyBudgetStop('Judge budget exists without a positive residual allowance')
    return _budget_read(path, plan['budget_policy'])


def _records(root, directory, plan, snapshot):
    units = {row['unit_id']: row for row in plan['units']}
    calls = {row['call_id']: row for row in snapshot['calls']} if snapshot else {}
    records, observed = {}, set()
    phases = _retained_intents(root, directory, plan)
    folder = directory / 'units'
    if folder.exists():
        for path in folder.iterdir():
            if not path.is_dir() or path.name not in units:
                raise allowance.StudyBudgetStop('Unknown or moved common-judge unit directory')
            intent = registry.read_json(root, path / 'intent.json')
            _sealed(intent, 'controller_grounding_unit_intent_v1')
            unit, uid = units[path.name], path.name
            call = calls.get(intent.get('call_id'))
            bound = plan['call_bounds'][uid]
            phase = phases.get(intent.get('execution_intent_sha256'))
            if (set(intent) != {'schema', 'plan_sha256', 'unit_id', 'call_id', 'request_sha256',
                    'execution_intent_sha256',
                    'wire_sha256', 'input_bound', 'micro_usd_bound', 'sha256'}
                    or intent['plan_sha256'] != plan['sha256'] or intent['unit_id'] != uid
                    or intent['request_sha256'] != common.digest(unit['request'])
                    or any(intent[key] != bound[key] for key in ('wire_sha256', 'input_bound', 'micro_usd_bound'))
                    or call is None or call['call_id'] in observed or call['arm_id'] != uid
                    or call['stage'] != 'fresh_common_critic:' + intent['execution_intent_sha256']
                    or call['request_sha256'] != bound['wire_sha256']
                    or call['input_bound'] != bound['input_bound'] or call['output_bound'] != 2048
                    or call['micro_usd_bound'] != bound['micro_usd_bound']
                    or phase is None or uid not in {row['unit_id'] for row in _selected(plan, phase['requested_cases'])}):
                raise allowance.StudyBudgetStop('Common-judge intent lost its exact native call owner')
            observed.add(call['call_id'])
            receipt = call['receipt']
            result = {'schema': 'controller_grounding_unit_v1', 'plan_sha256': plan['sha256'],
                'unit_id': uid, 'call_id': call['call_id'], 'request_sha256': common.digest(unit['request']),
                'execution_intent_sha256': intent['execution_intent_sha256'],
                'status': 'intent', 'receipts': {receipt['reservation_id']: receipt} if receipt else {},
                'response': None, 'verification': None, 'failure': None,
                'latency_seconds': None, 'micro_usd_bound': bound['micro_usd_bound']}
            outcome = path / 'outcome.json'
            if outcome.exists():
                saved = registry.read_json(root, outcome)
                _sealed(saved, 'controller_grounding_unit_v1')
                if (set(saved) != set(result) | {'sha256'}
                        or any(saved[key] != result[key] for key in ('schema', 'plan_sha256', 'unit_id',
                            'call_id', 'request_sha256', 'receipts', 'micro_usd_bound', 'execution_intent_sha256'))
                        or saved['status'] not in {'reviewed', 'failed'}
                        or type(saved['latency_seconds']) not in (int, float)
                        or not math.isfinite(saved['latency_seconds']) or saved['latency_seconds'] < 0):
                    raise allowance.StudyBudgetStop('Closed common-judge result ownership or timing changed')
                if saved['status'] == 'reviewed':
                    if (call['outcome'] != 'completed' or saved['failure'] is not None
                            or call['outcome_sha256'] != registry.digest(saved['response'])
                            or common.validate_response(unit, saved['response']) != saved['verification']):
                        raise allowance.StudyBudgetStop('Common model assessment differs from its actual native response')
                elif (call['outcome'] not in {'failed', 'unknown'} or saved['verification'] is not None
                      or saved['response'] is not None or not isinstance(saved['failure'], dict)):
                    raise allowance.StudyBudgetStop('Failed common judge claims successful or fabricated work')
                result = saved
            elif call['outcome'] not in {'pending', 'unknown'}:
                raise allowance.StudyBudgetStop('A closed paid call has lost its terminal native result; no reconstruction')
            records[uid] = result
    if set(calls) != observed:
        raise allowance.StudyBudgetStop('A paid common judge has an orphan or missing unit intent')
    expected = {row['receipt']['reservation_id']: row['receipt'] for row in calls.values()
                if row['receipt'] is not None}
    if _native_receipts(directory / 'native/groq.sqlite') != expected:
        raise allowance.StudyBudgetStop('Native judge ledger has an orphan or changed owned reservation')
    gate_path = directory / 'pilot-gate.json'
    matrices = [row for row in phases.values() if row['requested_cases'] == 5]
    if gate_path.exists() or matrices:
        expected_gate = _pilot_gate(plan, records)
        if (not gate_path.exists() or registry.read_json(root, gate_path) != expected_gate
                or any(row['pilot_gate_sha256'] != expected_gate['sha256'] for row in matrices)):
            raise allowance.StudyBudgetStop('Retained matrix pilot gate changed or was removed')
    common.summarize(plan, records)  # Native wire/verification and receipt validation.
    return records


def _has_unknown(snapshot, records):
    return (snapshot is not None and (snapshot['locked'] or any(row['outcome'] in {'pending', 'unknown'}
            or row['receipt'] is None or row['receipt']['status'] not in _CLOSED for row in snapshot['calls']))
            or any(row['status'] == 'intent' for row in records.values()))


def _combined(plan, snapshot, candidate=0):
    calls = snapshot['calls'] if snapshot else []
    spent = sum(row['receipt']['micro_usd_charged_or_reserved'] if row['receipt'] is not None
                else row['micro_usd_bound'] for row in calls)
    usage = plan['parent_usage']
    if (len(calls) + bool(candidate) > min(10, 90 - usage['groq_calls'])
            or usage['micro_usd'] + spent + candidate > 1935360
            or usage['typesafe_calls'] > 20 or usage['typesafe_micro_usd'] > 53760):
        raise allowance.StudyBudgetStop('Parent inference plus common judge exceeds the retained approved combined cap')
    return {'groq_calls': usage['groq_calls'] + len(calls), 'typesafe_calls': usage['typesafe_calls'],
        'micro_usd': usage['micro_usd'] + spent, 'typesafe_micro_usd': usage['typesafe_micro_usd'],
        'judge_micro_usd': spent, 'is_provider_invoice': False}


def _pilot_gate(plan, records):
    selected = _selected(plan, 1)
    if not selected or any(records.get(row['unit_id'], {}).get('status') != 'reviewed' for row in selected):
        raise allowance.StudyBudgetStop('Every first-case common unit must complete before matrix expansion')
    return registry.seal({'schema': 'controller_grounding_pilot_gate_v1',
        'plan_sha256': plan['sha256'], 'units': {row['unit_id']: records[row['unit_id']]['sha256'] for row in selected}})


def _study_seal(root, directory, plan, records, snapshot):
    if set(records) != {row['unit_id'] for row in plan['units']} or _has_unknown(snapshot, records):
        raise allowance.StudyBudgetStop('Full common-judge scoring needs every selected unit terminal with closed paid usage')
    files = _inventory(root, directory, plan)
    stable = {name: value for name, value in files.items() if name != 'inference-sealed.json'
              and not name.startswith('scores/') and not name.endswith(('-wal', '-shm', '-journal'))}
    return registry.seal({'schema': 'controller_grounding_seal_v1', 'plan_sha256': plan['sha256'],
        'parent_seal_sha256': plan['parent_inference_seal_sha256'], 'files': stable,
        'records': {uid: row['sha256'] for uid, row in records.items()},
        'budget_snapshot_sha256': registry.digest(snapshot), 'gold_read': False,
        'combined_usage': _combined(plan, snapshot), 'quality_claim': False})


def _publish_same(root, path, value):
    if path.exists():
        if registry.read_json(root, path) != value:
            raise allowance.StudyBudgetStop('A terminal common-judge artifact cannot be replaced')
    else:
        registry.write_new(root, path, value)


def run(root, identifier, authorization_path, *, limit=1, parent_loader=None,
        provider_factory=None, load_environment=False, env_loader=None, clock=None):
    root = Path(root).resolve()
    plan, directory, state = load_plan(root, identifier, parent_loader=parent_loader)
    authority = verify_authorization(root, plan, authorization_path, limit=limit, clock=clock)
    admitted = deepcopy(authority)
    stopped = None
    with parent_run._lease(root, directory):
        plan_again = load_plan(root, identifier, parent_loader=parent_loader)[0]
        if plan_again != plan:
            raise allowance.StudyBudgetStop('Common-judge plan changed before admission')
        _inventory(root, directory, plan)
        _, snapshot = _snapshot(directory, plan)
        records = _records(root, directory, plan, snapshot)
        if _has_unknown(snapshot, records):
            raise allowance.StudyBudgetStop('Unknown or interrupted common judge cannot be retried')
        _combined(plan, snapshot)
        gate = _pilot_gate(plan, records) if limit == 5 else None
        selected = _selected(plan, limit)
        remaining = [unit for unit in selected if unit['unit_id'] not in records]
        activate_phase = bool(remaining) or limit == 5 and not (directory / 'inference-sealed.json').exists()
        phase_intent = None
        if activate_phase:
            if remaining and plan['budget_policy'] is None:
                raise allowance.StudyBudgetStop('No residual approved allowance remains for a common judge')
            if verify_authorization(root, plan, authorization_path, limit=limit, clock=clock) != admitted:
                raise allowance.StudyBudgetStop('Common-judge authority changed during admission')
            if gate is not None:
                _publish_same(root, directory / 'pilot-gate.json', gate)
            phase_intent = registry.seal({'schema': 'controller_grounding_execution_intent_v1',
                'plan_sha256': plan['sha256'], 'authority': authority,
                'authority_sha256': registry.digest(authority), 'requested_cases': limit,
                'pilot_gate_sha256': gate['sha256'] if gate else None, 'gold_read': False})
            _publish_same(root, directory / 'execution-intents' / (phase_intent['sha256'] + '.json'), phase_intent)
        if remaining and load_environment:
            if verify_authorization(root, plan, authorization_path, limit=limit, clock=clock) != admitted:
                raise allowance.StudyBudgetStop('Common-judge authority changed before credentials')
            if load_plan(root, identifier, parent_loader=parent_loader)[0] != plan:
                raise allowance.StudyBudgetStop('Frozen parent or judge source changed before credentials')
            (env_loader or load_local_env)(root / '.env')
        budget = allowance.StudyBudgetLedger(directory / 'budget', plan['budget_policy']) if remaining else None
        config = _config(root, directory)

        def guard():
            if load_plan(root, identifier, parent_loader=parent_loader)[0] != plan:
                raise allowance.StudyBudgetStop('Frozen parent or judge source changed during the common pass')
            if verify_authorization(root, plan, authorization_path, limit=limit, clock=clock) != admitted:
                raise allowance.StudyBudgetStop('Common-judge phase authority changed')
            if budget is not None:
                current = budget.inspect()
                if current['locked']:
                    raise allowance.StudyBudgetStop('Common-judge joint ledger is locked')
                _inventory(root, directory, plan)
                _records(root, directory, plan, current)
                _combined(plan, current)

        for unit in remaining:
            guard()
            uid = unit['unit_id']
            bound = plan['call_bounds'][uid]
            current = budget.inspect()
            _combined(plan, current, bound['micro_usd_bound'])
            if len(current['calls']) + 1 > admitted['max_new_judge_calls']:
                raise allowance.StudyBudgetStop('The next unique judge exceeds its phase call allowance')
            provider = plan['budget_policy']['providers'][0]
            call_id = budget.reserve(uid, 'fresh_common_critic:' + phase_intent['sha256'], bound['wire_sha256'], 'groq',
                config.model, provider['config_sha256'], provider['price_sha256'],
                bound['input_bound'], 2048, bound['micro_usd_bound'])
            intent = registry.seal({'schema': 'controller_grounding_unit_intent_v1',
                'plan_sha256': plan['sha256'], 'unit_id': uid, 'call_id': call_id,
                'execution_intent_sha256': phase_intent['sha256'],
                'request_sha256': common.digest(unit['request']), **bound})
            registry.write_new(root, directory / 'units' / uid / 'intent.json', intent)
            begun = time.monotonic()
            client = None
            response, verification, failure, status = None, None, None, 'reviewed'

            def receipt(value):
                actual = _native_receipts(config.ledger_file).get(value['reservation_id'])
                if actual != value:
                    raise allowance.StudyBudgetStop('Common receipt differs from its actual native reservation')
                budget.publish_receipt(call_id, value)
                guard()

            try:
                guard()  # Durable joint owner and native intent precede construction.
                client = (provider_factory or GroqProvider)(config, call_context=None, on_receipt=receipt)
                if (getattr(client, 'model', None) != 'openai/gpt-oss-120b'
                        or getattr(client, 'config', None) != config):
                    raise allowance.StudyBudgetStop('Common judge factory changed the pinned native configuration')
                guard()
                response = client.complete_structured(deepcopy(unit['request']['messages']),
                    max_output_tokens=2048, schema=deepcopy(unit['request']['schema']))
                guard()
                verification = common.validate_response(unit, response)
                owned = next(row for row in budget.inspect()['calls'] if row['call_id'] == call_id)
                if owned['receipt'] is None or owned['receipt']['status'] not in {'settled', 'missing_usage_bound'}:
                    raise allowance.StudyBudgetStop('Returned common judge lacks its closed owned receipt')
                budget.finish(call_id, outcome_sha256=registry.digest(response))
            except BaseException as error:
                # Native calls may stop between reservation and callback. Audit
                # the actual existing ledger, never construct or refund it here.
                reservation = getattr(client, 'last_reservation', None)
                if reservation:
                    native = _native_receipts(config.ledger_file).get(reservation)
                    if native is not None:
                        try:
                            budget.publish_receipt(call_id, native)
                        except (OSError, ValueError):
                            stopped = 'source_accounting_or_unknown_outcome_no_automatic_retry'
                owned = next(row for row in budget.inspect()['calls'] if row['call_id'] == call_id)
                if owned['outcome'] == 'completed':
                    raise allowance.StudyBudgetStop('Paid result closed before durable publication; no reconstruction') from error
                uncertain = (isinstance(error, allowance.StudyBudgetStop) or not isinstance(error, Exception)
                    or owned['receipt'] is None or owned['receipt']['status'] not in _CLOSED
                    or budget.inspect()['locked'] or stopped is not None)
                budget.finish(call_id, 'unknown' if uncertain else 'failed',
                              reason='common_judge_integrity_or_unknown' if uncertain else 'common_judge_failed')
                status, failure = 'failed', failure_metadata(error)
                response, verification = None, None
                if uncertain:
                    stopped = 'source_accounting_or_unknown_outcome_no_automatic_retry'
                if not isinstance(error, Exception):
                    raise
            owned = next(row for row in budget.inspect()['calls'] if row['call_id'] == call_id)
            native = owned['receipt']
            record = registry.seal({'schema': 'controller_grounding_unit_v1',
                'plan_sha256': plan['sha256'], 'unit_id': uid, 'call_id': call_id,
                'execution_intent_sha256': phase_intent['sha256'],
                'request_sha256': common.digest(unit['request']), 'status': status,
                'receipts': {native['reservation_id']: native} if native else {},
                'response': response, 'verification': verification, 'failure': failure,
                'latency_seconds': round(max(0.0, time.monotonic() - begun), 6),
                'micro_usd_bound': bound['micro_usd_bound']})
            registry.write_new(root, directory / 'units' / uid / 'outcome.json', record)
            records[uid] = record
            if stopped:
                break
        load_plan(root, identifier, parent_loader=parent_loader)
        _, snapshot = _snapshot(directory, plan)
        records = _records(root, directory, plan, snapshot)
        if limit == 5 and not stopped and set(records) == {unit['unit_id'] for unit in plan['units']}:
            _publish_same(root, directory / 'inference-sealed.json', _study_seal(root, directory, plan, records, snapshot))
        return _progress(plan, directory, records, snapshot, stopped)


def _progress(plan, directory, records, snapshot, stopped=None):
    return {'schema': 'controller_grounding_progress_v1', 'study_id': plan['study_id'],
        'plan_sha256': plan['sha256'], 'selected_cases': 5, 'selected_arm_rows': 10,
        'unique_units': len(plan['units']), 'owned_calls': len(records),
        'reviewed_units': sum(row['status'] == 'reviewed' for row in records.values()),
        'failed_units': sum(row['status'] == 'failed' for row in records.values()),
        'unknown_work': bool(_has_unknown(snapshot, records)),
        'inference_sealed': (directory / 'inference-sealed.json').exists(),
        'combined_usage': _combined(plan, snapshot), 'stop_reason': stopped,
        'parent_inference_unchanged': True, 'gold_read': False, 'quality_claim': False}


def inspect(root, identifier, *, parent_loader=None):
    root = Path(root).resolve()
    plan, directory, _ = load_plan(root, identifier, parent_loader=parent_loader)
    _inventory(root, directory, plan)
    _, snapshot = _snapshot(directory, plan)
    records = _records(root, directory, plan, snapshot)
    if (directory / 'inference-sealed.json').exists():
        if registry.read_json(root, directory / 'inference-sealed.json') != _study_seal(root, directory, plan, records, snapshot):
            raise allowance.StudyBudgetStop('Common-judge full seal changed')
    return {**_progress(plan, directory, records, snapshot), 'provider_constructed': False,
            'environment_loaded': False}


def score(root, identifier, *, parent_loader=None):
    root = Path(root).resolve()
    plan, directory, _ = load_plan(root, identifier, parent_loader=parent_loader)
    saved = registry.read_json(root, directory / 'inference-sealed.json')
    _, snapshot = _snapshot(directory, plan)
    records = _records(root, directory, plan, snapshot)
    if saved != _study_seal(root, directory, plan, records, snapshot):
        raise allowance.StudyBudgetStop('Common-judge full inference seal differs before scoring')
    result = registry.seal({'schema': 'controller_grounding_score_v1', 'study_id': identifier,
        'plan_sha256': plan['sha256'], 'parent_seal_sha256': plan['parent_inference_seal_sha256'],
        'assessment': common.summarize(plan, records), 'combined_usage': _combined(plan, snapshot),
        'gold_read': False, 'quality_claim': False})
    load_plan(root, identifier, parent_loader=parent_loader)
    _, final_snapshot = _snapshot(directory, plan)
    final_records = _records(root, directory, plan, final_snapshot)
    if _study_seal(root, directory, plan, final_records, final_snapshot) != saved:
        raise allowance.StudyBudgetStop('Common judge changed during gold-free scoring')
    _publish_same(root, directory / 'scores' / (result['sha256'] + '.json'), result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--mode', choices=('preview', 'run', 'inspect', 'score'), required=True)
    parser.add_argument('--parent-study')
    parser.add_argument('--study')
    parser.add_argument('--authorization')
    parser.add_argument('--limit', type=int, choices=(1, 5), default=1)
    args = parser.parse_args(argv)
    if args.mode == 'preview':
        if not args.parent_study or args.study or args.authorization:
            parser.error('Preview requires --parent-study only')
        plan = create_plan(args.root, args.parent_study)
        result = {'schema': 'controller_grounding_preview_v1', 'study_id': plan['study_id'],
            'plan_sha256': plan['sha256'], 'parent_study_id': plan['parent_study_id'],
            'case_ids': plan['case_ids'], 'unique_units': len(plan['units']),
            'pilot_unique_units': len(_selected(plan, 1)), 'parent_usage': plan['parent_usage'],
            'combined_caps': plan['combined_caps'], 'authority_granted': False,
            'provider_constructed': False, 'gold_read': False}
    elif not args.study or args.parent_study:
        parser.error('Saved common-judge operations require --study only')
    elif args.mode == 'run':
        if not args.authorization:
            parser.error('Run requires --authorization')
        result = run(args.root, args.study, args.authorization, limit=args.limit, load_environment=True)
    elif args.authorization:
        parser.error('Inspect and score do not accept spending authority')
    else:
        result = inspect(args.root, args.study) if args.mode == 'inspect' else score(args.root, args.study)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0
