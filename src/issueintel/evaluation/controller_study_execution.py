"""Owned native calls and exact, pair-scoped product stage journals.

This adapter supplies accounting and persistence, not a new investigation
algorithm. The product pipeline still constructs and validates every stage.
Raw provider responses and public inputs are private artifacts. A response is
never sufficient to reconstruct an interrupted native stage or retry a call.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from io import BytesIO

from issueintel.runtime.io import digest
from issueintel.runtime.study_budget import StudyBudgetStop, _safe


MAX_EVENT_BYTES = 16_000_000
MAX_EVENTS = 4000
_UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')
_CALL = re.compile(r'[a-f0-9]{32}')
_TERMINAL = {'settled', 'failed_bound', 'missing_usage_bound', 'breach'}


class SavedSharedFailure(OSError):
    """The exact stage already failed with closed audit costs; do not retry."""


def _encode(value):
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        raise StudyBudgetStop('Study execution requires finite JSON') from None


def _read(path, maximum=MAX_EVENT_BYTES):
    path = _safe(path)
    info = path.stat()
    if not path.is_file() or info.st_size > maximum:
        raise StudyBudgetStop('Missing or oversized study execution record')
    with path.open('rb') as stream:
        raw = stream.read(info.st_size + 1)
    if len(raw) != info.st_size:
        raise StudyBudgetStop('Study execution record changed during bounded read')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise StudyBudgetStop('Invalid study execution record') from None
    if raw != _encode(value):
        raise StudyBudgetStop('Study execution record bytes changed')
    return value


def _write(path, raw):
    with _safe(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _failure(error):
    from issueintel.investigation.providers import failure_metadata
    from issueintel.runtime.typesafe import TypesafeError
    if isinstance(error, TypesafeError):
        # Native reasons are machine codes; upstream exception text is private.
        from issueintel.investigation.typesafe_controller import _REASONS
        return {'error_type': 'TypesafeError',
                'reason': error.reason if error.reason in _REASONS else 'client_error'}
    value = failure_metadata(error)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', value['error_type']):
        value['error_type'] = 'Exception'
    return value


class StageJournal:
    """Immutable event files anchored in SQLite, shared only within one case.

    Full native stage bindings are the cache key. They deliberately contain no
    controller or arm, while retaining the complete case, policy and inputs.
    SQLite also detects deletion of a previously indexed last event. A crash
    after fsync replays the event, never the provider operation.
    """

    def __init__(self, root, study_dir, unit_id, policy, budget):
        from issueintel.investigation.product_study import validate_policy
        self.root = _safe(root)
        self.study_dir = _safe(study_dir)
        if (not self.study_dir.is_relative_to(self.root / 'artifacts')
                or not isinstance(unit_id, str) or not _UNIT.fullmatch(unit_id)):
            raise StudyBudgetStop('Study execution must stay in private workspace artifacts')
        try:
            self.policy = validate_policy(policy)
        except (ValueError, OSError):
            raise StudyBudgetStop('Study execution policy changed') from None
        self.unit_id, self.budget = unit_id, budget
        if self.policy['study_id'] != budget.policy['study_id']:
            raise StudyBudgetStop('Stage journal and joint budget study differ')
        self.directory = _safe(self.study_dir / 'execution' / unit_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_dir = _safe(self.directory / 'events')
        self.raw_dir = _safe(self.directory / 'raw')
        self.events_dir.mkdir(exist_ok=True)
        self.raw_dir.mkdir(exist_ok=True)
        self.path = _safe(self.directory / 'index.sqlite')
        self.identity = {'schema': 'controller_study_execution_identity_v1',
            'unit_id': unit_id, 'study_directory': str(self.study_dir),
            'policy_sha256': policy['sha256'], 'budget_policy_sha256': budget.policy_sha256}
        with self._connect() as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger')")}
            if names - {'meta', 'events'}:
                raise StudyBudgetStop('Unexpected study execution index schema')
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,json TEXT NOT NULL)')
            expected = {'identity_sha256': digest(self.identity)}
            actual = dict(db.execute('SELECT key,value FROM meta'))
            if actual and actual != expected:
                raise StudyBudgetStop('Study execution index identity changed')
            if not actual:
                if db.execute('SELECT 1 FROM events LIMIT 1').fetchone():
                    raise StudyBudgetStop('Study execution index identity missing')
                db.executemany('INSERT INTO meta VALUES (?,?)', expected.items())
            identity_path = self.directory / 'identity.json'
            if identity_path.exists():
                if _read(identity_path) != self.identity:
                    raise StudyBudgetStop('Study execution journal moved or rebound')
            else:
                if any(self.events_dir.iterdir()) or any(self.raw_dir.iterdir()):
                    raise StudyBudgetStop('Study execution identity missing')
                _write(identity_path, _encode(self.identity))
            self._load(db)

    @contextmanager
    def _connect(self):
        db = None
        try:
            _safe(self.directory)
            _safe(self.path)
            db = sqlite3.connect(self.path, timeout=30)
            db.execute('PRAGMA busy_timeout=30000')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise StudyBudgetStop('Study execution index integrity failure')
            with db:
                yield db
        except sqlite3.Error:
            raise StudyBudgetStop('Study execution index unavailable') from None
        finally:
            if db is not None:
                db.close()

    def _apply(self, state, kind, data):
        stages, calls, decisions = state['stages'], state['calls'], state['decisions']
        if kind == 'stage_started':
            key = data['key']
            if key in stages or key != digest(data['binding']):
                raise StudyBudgetStop('A study stage cannot restart or change binding')
            from issueintel.investigation.product_study import stage_binding
            binding = data['binding']
            if stage_binding(self.policy, data['kind'], binding['inputs']) != binding:
                raise StudyBudgetStop('Stage binding differs from the native policy')
            stages[key] = {**deepcopy(data), 'status': 'pending', 'payload': None,
                'payload_sha256': None, 'failure': None, 'call_ids': [],
                'references': [data['owner_arm']]}
        elif kind == 'call_started':
            identifier = data['call_id']
            if identifier in calls or not _CALL.fullmatch(identifier):
                raise StudyBudgetStop('Duplicate study call owner')
            if data['request_sha256'] != digest(data['request']):
                raise StudyBudgetStop('Study native call body changed')
            key = data['stage_key']
            if data['provider'] == 'groq':
                if key not in stages or stages[key]['status'] != 'pending':
                    raise StudyBudgetStop('Groq call has no active stage')
                stages[key]['call_ids'].append(identifier)
            elif data['provider'] == 'typesafe':
                if key in decisions:
                    raise StudyBudgetStop('A controller decision cannot retry')
                decisions[key] = {'key': key, 'owner_arm': data['arm_id'],
                    'binding': deepcopy(data['input']['binding']), 'call_id': identifier,
                    'record': None, 'status': 'pending'}
            else:
                raise StudyBudgetStop('Unexpected study provider')
            calls[identifier] = {**deepcopy(data), 'status': 'pending', 'result': None,
                'failure': None, 'latency_seconds': None, 'receipts': [], 'raw': []}
        elif kind == 'receipt':
            row = calls[data['call_id']]
            if row['receipts'] and row['receipts'][-1] == data['receipt']:
                raise StudyBudgetStop('Duplicate journal receipt event')
            if row['receipts'] and row['receipts'][-1]['status'] in _TERMINAL:
                raise StudyBudgetStop('Terminal study receipt changed')
            row['receipts'].append(deepcopy(data['receipt']))
        elif kind == 'raw':
            calls[data['call_id']]['raw'].append(deepcopy(data['raw']))
        elif kind == 'call_finished':
            row = calls[data['call_id']]
            if row['status'] != 'pending' or data['status'] not in {'completed', 'failed', 'unknown'}:
                raise StudyBudgetStop('A closed study call cannot change')
            row.update({k: deepcopy(data[k]) for k in ('status', 'result', 'failure', 'latency_seconds')})
        elif kind == 'stage_finished':
            row = stages[data['key']]
            if row['status'] != 'pending' or data['status'] not in {'completed', 'failed'}:
                raise StudyBudgetStop('A closed study stage cannot change')
            if data['status'] == 'completed' and (not isinstance(data['payload'], dict)
                    or data['payload_sha256'] != digest(data['payload'])):
                raise StudyBudgetStop('Full native stage payload is missing')
            row.update({k: deepcopy(data[k]) for k in ('status', 'payload', 'payload_sha256', 'failure')})
        elif kind == 'stage_reference':
            row = stages[data['key']]
            if row['status'] == 'pending' or data['arm_id'] in row['references']:
                raise StudyBudgetStop('Invalid shared stage reference')
            row['references'].append(data['arm_id'])
        elif kind == 'decision_finished':
            row = decisions[data['key']]
            if row['status'] != 'pending' or calls[row['call_id']]['status'] == 'pending':
                raise StudyBudgetStop('Unclosed or duplicate controller decision')
            row.update(status='completed', record=deepcopy(data['record']))
        else:
            raise StudyBudgetStop('Unknown study execution event')

    def _load(self, db):
        if dict(db.execute('SELECT key,value FROM meta')) != {'identity_sha256': digest(self.identity)}:
            raise StudyBudgetStop('Study execution index identity changed')
        names = {(r[0], r[1]) for r in db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger')")}
        if names != {('meta', 'table'), ('events', 'table')}:
            raise StudyBudgetStop('Unexpected study execution index schema')
        for path in self.directory.iterdir():
            _safe(path)
            if path.name in {'events', 'raw'}:
                if not path.is_dir():
                    raise StudyBudgetStop('Invalid study execution directory')
            elif path.name not in {'identity.json', 'index.sqlite', 'index.sqlite-journal'} or not path.is_file():
                raise StudyBudgetStop('Unexpected study execution inventory')
        if _read(self.directory / 'identity.json') != self.identity:
            raise StudyBudgetStop('Study execution identity changed')
        paths = sorted(self.events_dir.iterdir())
        indexed = db.execute('SELECT seq,sha256,json FROM events ORDER BY seq').fetchall()
        if len(paths) > MAX_EVENTS or len(indexed) > len(paths):
            raise StudyBudgetStop('Study execution events missing or over bound')
        state = {'stages': {}, 'calls': {}, 'decisions': {}, 'events': []}
        previous = None
        for seq, path in enumerate(paths, 1):
            event = _read(path)
            if (set(event) != {'schema', 'seq', 'previous_sha256', 'identity_sha256', 'kind', 'data', 'sha256'}
                    or event['schema'] != 'controller_study_execution_event_v1'
                    or type(event['seq']) is not int or event['seq'] != seq
                    or event['identity_sha256'] != digest(self.identity)
                    or event['previous_sha256'] != previous
                    or event['sha256'] != digest({k: v for k, v in event.items() if k != 'sha256'})
                    or path.name != f'{seq:08d}-{event["sha256"]}.json'):
                raise StudyBudgetStop('Study execution journal chain changed')
            encoded = _encode(event).decode()
            if seq <= len(indexed) and indexed[seq - 1] != (seq, event['sha256'], encoded):
                raise StudyBudgetStop('Study execution event differs from its indexed bytes')
            try:
                self._apply(state, event['kind'], event['data'])
            except (KeyError, ValueError, TypeError, AttributeError):
                raise StudyBudgetStop('Invalid study execution transition') from None
            if seq > len(indexed):
                db.execute('INSERT INTO events VALUES (?,?,?)', (seq, event['sha256'], encoded))
            state['events'].append(event)
            previous = event['sha256']
        expected_raw = set()
        for call in state['calls'].values():
            for row in call['raw']:
                expected_raw.add(row['path'])
                path = _safe(self.raw_dir / row['path'])
                if (path.parent != self.raw_dir or not path.is_file()
                        or path.stat().st_size != row['bytes'] or row['bytes'] > 4_000_001
                        or sha256(path.read_bytes()).hexdigest() != row['sha256']):
                    raise StudyBudgetStop('Study raw response changed or missing')
        if {p.name for p in self.raw_dir.iterdir()} != expected_raw:
            raise StudyBudgetStop('Unowned study raw response inventory')
        return state

    def append(self, kind, data):
        with self._connect() as db:
            state = self._load(db)
            if len(state['events']) >= MAX_EVENTS:
                raise StudyBudgetStop('Study execution event bound exhausted')
            event = {'schema': 'controller_study_execution_event_v1',
                'seq': len(state['events']) + 1,
                'previous_sha256': state['events'][-1]['sha256'] if state['events'] else None,
                'identity_sha256': digest(self.identity), 'kind': kind, 'data': deepcopy(data)}
            event['sha256'] = digest(event)
            self._apply(state, kind, data)
            raw = _encode(event)
            if len(raw) > MAX_EVENT_BYTES:
                raise StudyBudgetStop('Study execution record exceeds private file bound')
            _write(self.events_dir / f'{event["seq"]:08d}-{event["sha256"]}.json', raw)
            db.execute('INSERT INTO events VALUES (?,?,?)', (event['seq'], event['sha256'], raw.decode()))

    def state(self):
        with self._connect() as db:
            return self._load(db)

    def raw(self, call_id, payload):
        if not isinstance(payload, bytes) or len(payload) > 4_000_001:
            raise StudyBudgetStop('Study raw response exceeds its native bound')
        state = self.state()
        ordinal = len(state['calls'][call_id]['raw']) + 1
        checksum = sha256(payload).hexdigest()
        name = f'{call_id}-{ordinal}-{checksum}.bin'
        row = {'path': name, 'sha256': checksum, 'bytes': len(payload)}
        # Raw and its reference share a transaction. A crash between them leaves
        # an orphan that admission rejects, never a recoverable stage payload.
        with self._connect() as db:
            state = self._load(db)
            if len(state['events']) >= MAX_EVENTS:
                raise StudyBudgetStop('Study execution event bound exhausted')
            _write(self.raw_dir / name, payload)
            event = {'schema': 'controller_study_execution_event_v1',
                'seq': len(state['events']) + 1,
                'previous_sha256': state['events'][-1]['sha256'] if state['events'] else None,
                'identity_sha256': digest(self.identity), 'kind': 'raw',
                'data': {'call_id': call_id, 'raw': row}}
            event['sha256'] = digest(event)
            raw = _encode(event)
            _write(self.events_dir / f'{event["seq"]:08d}-{event["sha256"]}.json', raw)
            db.execute('INSERT INTO events VALUES (?,?,?)', (event['seq'], event['sha256'], raw.decode()))

    def stage(self, kind, binding, produce):
        """Pipeline stage-executor contract, with the currently bound owner."""
        execution = getattr(self, '_execution', None)
        if execution is None:
            raise StudyBudgetStop('Stage journal has no bound execution owner')
        return execution._stage(kind, binding, produce)


class _CapturedResponse:
    def __init__(self, response, capture):
        self.response, self.capture = response, capture

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        return self.response.__exit__(*args)

    def read(self, maximum=-1):
        try:
            value = self.response.read(maximum)
        except Exception as error:
            partial = getattr(error, 'partial', None)
            if isinstance(partial, bytes) and partial:
                self.capture(partial)
            raise
        self.capture(value)
        return value


class _OwnedGroq:
    name = 'groq'

    def __init__(self, execution):
        self.execution = execution

    def __call__(self, messages, max_output_tokens=2048):
        return self.complete_json(messages, max_output_tokens)

    def complete_structured(self, messages, *, max_output_tokens=2048, schema):
        return self.complete_json(messages, max_output_tokens, schema=schema)

    def complete_function(self, messages, *, max_output_tokens=2048, schema):
        return self.complete_json(messages, max_output_tokens, schema=schema, function_call=True)

    def complete_json(self, messages, max_output_tokens=2048, *, schema=None, function_call=False):
        from issueintel.investigation.providers import _input_token_bound
        ex = self.execution
        if ex._active_stage is None:
            raise StudyBudgetStop('Study Groq calls require a current native stage')
        ex._guard()
        if (type(max_output_tokens) is not int or not 0 < max_output_tokens <= 4096
                or type(function_call) is not bool):
            raise StudyBudgetStop('Study Groq output request changed')
        body = {'model': ex.groq_config.model, 'messages': deepcopy(messages),
            'max_completion_tokens': max_output_tokens, 'temperature': 0,
            'reasoning_effort': 'low', 'include_reasoning': False,
            'response_format': {'type': 'json_object'}, 'stream': False}
        if schema is not None:
            body['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'issueintel_response', 'strict': True, 'schema': deepcopy(schema)}}
        if function_call:
            body.pop('response_format')
            body['tools'] = [{'type': 'function', 'function': {'name': 'issueintel_response',
                'description': 'Submit one response object for local validation; the external program decides whether to execute any action.',
                'parameters': deepcopy(schema)}}]
            body['tool_choice'] = {'type': 'function', 'function': {'name': 'issueintel_response'}}
            body['parallel_tool_calls'] = False
        incoming = _input_token_bound(messages, schema)
        price = ex.groq_config.price()
        return ex._call('groq', body, {'messages': deepcopy(messages),
            'max_output_tokens': max_output_tokens, 'schema': deepcopy(schema),
            'function_call': function_call}, incoming, max_output_tokens,
            price.micro_usd(incoming, max_output_tokens),
            lambda client: client.complete_json(messages, max_output_tokens,
                                                schema=schema, function_call=function_call))


class _OwnedTypesafe:
    def __init__(self, execution):
        self.execution, self.config = execution, execution.typesafe_config
        self.last_receipt = None

    def decide(self, state, allowed, binding):
        from issueintel.runtime.typesafe import build_request, INPUT_BOUND, MICRO_BOUND
        ex = self.execution
        if ex._active_stage is not None:
            raise StudyBudgetStop('Controller decisions must occur between native stages')
        if binding.get('job_id') != ex.job_id:
            raise StudyBudgetStop('Controller decision owner changed')
        body = build_request(state, allowed, self.config.model)
        self.last_receipt = None
        try:
            return ex._call('typesafe', body, {'state': deepcopy(state),
                'allowed': deepcopy(allowed), 'binding': deepcopy(binding)},
                INPUT_BOUND, 0, MICRO_BOUND,
                lambda client: client.decide(state, allowed, binding))
        finally:
            self.last_receipt = deepcopy(ex._last_typesafe_receipt)


class StudyExecution:
    """Native pipeline capabilities for one arm; owns no independent planner.

    Inspection mode can audit consistent interrupted journals while withholding
    call, stage and decision-publication capabilities. It does not repair them.
    """

    def __init__(self, root, study_dir, unit_id, arm_id, policy, groq_config,
                 typesafe_config, budget, guard, *, provider_factories=None, admit_call=None,
                 inspection_only=False):
        if arm_id not in {'rules', 'jev'} or not callable(guard) or type(inspection_only) is not bool:
            raise StudyBudgetStop('Invalid study arm or fresh authority guard')
        self._inspection_only = inspection_only
        self.unit_id, self.arm_id = unit_id, arm_id
        self.logical_arm_id = f'{unit_id}:{arm_id}'
        self.policy, self.budget = deepcopy(policy), budget
        self.groq_config, self.typesafe_config = groq_config, typesafe_config
        self.guard, self.provider_factories = guard, dict(provider_factories or {})
        if admit_call is not None and not callable(admit_call):
            raise StudyBudgetStop('Invalid study call admission callback')
        self.admit_call = admit_call
        self._active_stage = None
        self._integrity_failure = False
        self._last_typesafe_receipt = None
        self.journal = StageJournal(root, study_dir, unit_id, policy, budget)
        self.journal._execution = self
        self.job_id = digest({'policy': policy['sha256'], 'unit': unit_id, 'arm': arm_id})[:24]
        self.groq = _OwnedGroq(self)
        self.typesafe_client = _OwnedTypesafe(self)
        self._config_identity = self._configs()
        if self.logical_arm_id not in budget.arms:
            raise StudyBudgetStop('Study arm has no finite joint allowance')
        self._validate_configs()
        state = self.inspect()
        if not self._inspection_only and (any(row['status'] in {'pending', 'unknown'} for row in state['calls'])
                or any(row['status'] == 'pending' for row in state['stages'])
                or any(row['status'] == 'pending' for row in state['decisions'])):
            raise StudyBudgetStop('Interrupted study work cannot be retried or reconstructed')

    def _configs(self):
        from dataclasses import asdict
        def plain(value):
            return {key: str(item) if isinstance(item, Path) else
                plain(item) if isinstance(item, dict) else
                str(item) if type(item).__module__ == 'decimal' else item
                for key, item in value.items()}
        return {'groq': plain(asdict(self.groq_config)), 'typesafe': plain(asdict(self.typesafe_config))}

    def _validate_configs(self):
        from .controller_study import runtime_config_descriptor
        from issueintel.runtime.typesafe import MODEL, _price
        if self._configs() != self._config_identity:
            raise StudyBudgetStop('Frozen native provider configuration changed')
        if (self.groq_config.model != 'openai/gpt-oss-120b'
                or self.groq_config.reasoning_effort != 'low'
                or self.groq_config.max_output_tokens != 4096
                or self.typesafe_config.model != MODEL or self.typesafe_config.enabled is not True):
            raise StudyBudgetStop('Study native provider recipe changed')
        for provider, config in (('groq', self.groq_config), ('typesafe', self.typesafe_config)):
            row = self.budget.providers[provider]
            path = _safe(config.price_file)
            if not path.is_file() or path.stat().st_size > 262144 or sha256(path.read_bytes()).hexdigest() != row['price_sha256']:
                raise StudyBudgetStop('Study provider dated price bytes changed')
            ledger = _safe(config.ledger_file)
            if ledger != self.journal.study_dir / 'native' / f'{provider}.sqlite':
                raise StudyBudgetStop('Native provider ledger is outside its study scope')
            checked = config.price().checked_at if provider == 'groq' else _price(path)
            if checked != row['price_date']:
                raise StudyBudgetStop('Study native provider price date changed')
            try:
                descriptor = runtime_config_descriptor(self.journal.root, config,
                    provider=provider, study_id=self.policy['study_id'])
            except (ValueError, OSError):
                raise StudyBudgetStop('Native provider config cannot be bound to this study') from None
            if digest(descriptor) != row['config_sha256']:
                raise StudyBudgetStop('Native provider configuration differs from its frozen descriptor')

    def _guard(self):
        try:
            self.guard()
            self._validate_configs()
            if self.budget.inspect()['locked']:
                raise StudyBudgetStop('Study joint allowance is locked')
        except InterruptedError:
            raise
        except Exception:
            self._integrity_failure = True
            raise StudyBudgetStop('Fresh study source, authority or accounting guard failed') from None

    def _native_receipt(self, provider, receipt):
        config = self.groq_config if provider == 'groq' else self.typesafe_config
        path = _safe(config.ledger_file)
        if not path.is_file():
            raise StudyBudgetStop('Native provider ledger receipt is missing')
        db = None
        try:
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            if provider == 'typesafe':
                row = db.execute('SELECT receipt FROM typesafe_calls WHERE id=?', (receipt['reservation_id'],)).fetchone()
                actual = json.loads(row[0]) if row else None
            else:
                row = db.execute('SELECT c.created_at,c.model,c.input_bound,c.output_bound,c.charged_tokens,c.charged_micro_usd,c.status,c.price_date,x.input_tokens,x.output_tokens FROM calls c LEFT JOIN call_context x ON x.id=c.id WHERE c.id=?', (receipt['reservation_id'],)).fetchone()
                keys = ('created_at', 'model', 'input_bound', 'output_bound', 'tokens_charged_or_reserved',
                        'micro_usd_charged_or_reserved', 'status', 'price_date', 'observed_input_tokens', 'observed_output_tokens')
                actual = dict(zip(keys, row), reservation_id=receipt['reservation_id']) if row else None
            if actual != receipt:
                raise StudyBudgetStop('Callback receipt differs from actual native ledger')
        except (sqlite3.Error, ValueError, KeyError):
            raise StudyBudgetStop('Native provider receipt cannot be audited') from None
        finally:
            if db is not None:
                db.close()

    def _receipt(self, provider, call_id, receipt, *, fresh=True):
        try:
            self._native_receipt(provider, receipt)
            self.budget.publish_receipt(call_id, receipt)
            previous = self.journal.state()['calls'][call_id]['receipts']
            if not previous or previous[-1] != receipt:
                self.journal.append('receipt', {'call_id': call_id, 'receipt': receipt})
            if provider == 'typesafe':
                self._last_typesafe_receipt = deepcopy(receipt)
            if fresh:
                self._guard()
        except Exception:
            self._integrity_failure = True
            raise StudyBudgetStop('Native study receipt persistence or binding failed') from None

    def _transport(self, provider, call_id, body):
        from issueintel.investigation.providers import _open_provider
        from issueintel.runtime.typesafe import _transport
        from urllib.error import HTTPError
        delegate = self.provider_factories.get(provider + '_transport',
            _open_provider if provider == 'groq' else _transport)
        maximum = 4_000_001 if provider == 'groq' else 65537
        def capture(raw):
            try:
                if len(raw) > maximum:
                    raise StudyBudgetStop('Native transport exceeded its bounded read')
                self.journal.raw(call_id, raw)
                self._guard()
            except Exception:
                self._integrity_failure = True
                raise StudyBudgetStop('Private raw response persistence failed') from None
        def transport(request, timeout):
            self._guard()
            try:
                if json.loads(request.data) != body or digest(json.loads(request.data)) != digest(body):
                    raise StudyBudgetStop('Native HTTP request differs from its owned exact body')
                response = delegate(request, timeout=timeout)
            except HTTPError as error:
                # Groq reads a bounded HTTP error envelope for native repair
                # classification. Preserve those same bytes without headers.
                raw = error.read(64000)
                capture(raw)
                # Native Groq still consumes its original bounded error bytes
                # to classify the existing schema-repair slot. Typesafe does
                # not consume error bodies, so capture must precede its catch.
                replay = BytesIO(raw)
                error.read = replay.read
                raise
            except StudyBudgetStop:
                self._integrity_failure = True
                raise
            return _CapturedResponse(response, capture)
        return transport

    def _call(self, provider, body, inputs, incoming, outgoing, cost, invoke):
        if self._inspection_only:
            raise StudyBudgetStop('Inspection-only study execution cannot perform calls')
        from issueintel.investigation.providers import GroqProvider
        from issueintel.runtime.typesafe import TypesafeClient, TypesafeError
        self._guard()
        if provider == 'groq':
            key, kind = self._active_stage
            existing = self.journal.state()['stages'][key]['call_ids']
            ordinal = len(existing) + 1
            if ordinal > (2 if kind != 'verification' else 1):
                raise StudyBudgetStop('Native study stage call bound exceeded')
        else:
            kind, ordinal = 'decision', inputs['binding']['decision_index']
            key = digest({'policy_sha256': self.policy['sha256'], 'provider': provider,
                          'input': inputs, 'request': body})
            if key in self.journal.state()['decisions']:
                raise StudyBudgetStop('A controller decision cannot retry')
        stage = f'{kind}:{key[:16]}:{ordinal}'
        config = self.groq_config if provider == 'groq' else self.typesafe_config
        native = self.budget.providers[provider]
        if self.admit_call is not None:
            try:
                self.admit_call(provider, cost)
            except InterruptedError:
                raise
            except Exception:
                raise StudyBudgetStop('Study phase authority denied the exact next call bound') from None
        call_id = self.budget.reserve(self.logical_arm_id, stage, digest(body), provider,
            native['model'], native['config_sha256'], native['price_sha256'], incoming, outgoing, cost)
        self.journal.append('call_started', {'call_id': call_id,
            'arm_id': self.logical_arm_id, 'provider': provider, 'kind': kind,
            'stage_key': key, 'ordinal': ordinal, 'stage': stage,
            'request_sha256': digest(body), 'request': deepcopy(body), 'input': deepcopy(inputs)})
        self._integrity_failure = False
        started = time.monotonic()
        client = None
        try:
            # A fresh guard runs after the durable intent and before even the
            # provider constructor; no factory may consume an unowned call.
            self._guard()
            factory = self.provider_factories.get(provider,
                GroqProvider if provider == 'groq' else TypesafeClient)
            kwargs = {'on_receipt': lambda receipt: self._receipt(provider, call_id, receipt),
                      'transport': self._transport(provider, call_id, body)}
            if provider == 'groq':
                kwargs['call_context'] = None
            client = factory(config, **kwargs)
            value = invoke(client)
            self._guard()
            receipts = self.journal.state()['calls'][call_id]['receipts']
            if not receipts or receipts[-1]['status'] not in {'settled', 'missing_usage_bound'}:
                raise StudyBudgetStop('A native return needs its owned terminal receipt')
            self._native_receipt(provider, receipts[-1])
            self.budget.finish(call_id, outcome_sha256=digest(value))
            self.journal.append('call_finished', {'call_id': call_id, 'status': 'completed',
                'result': deepcopy(value), 'failure': None,
                'latency_seconds': max(0.0, time.monotonic() - started)})
            return value
        except BaseException as error:
            # Native Typesafe wraps callback OSError as TypesafeError. Keep its
            # actual final receipt, then rethrow the integrity stop outside the
            # native controller's measured service-error fallback.
            receipt = getattr(client, 'last_receipt', None)
            if provider == 'groq' and getattr(client, 'last_reservation', None):
                try:
                    receipt = config.ledger().receipt(client.last_reservation)
                except Exception:
                    self._integrity_failure = True
            if receipt is not None:
                if provider == 'typesafe':
                    self._last_typesafe_receipt = deepcopy(receipt)
                try:
                    self._receipt(provider, call_id, receipt, fresh=False)
                except Exception:
                    self._integrity_failure = True
            snapshot = self.budget.inspect()
            owned = next(row for row in snapshot['calls'] if row['call_id'] == call_id)
            if owned['outcome'] == 'completed':
                raise StudyBudgetStop('Native response closed before journal completion; no retry') from None
            status = 'failed' if owned['receipt'] and owned['receipt']['status'] in _TERMINAL else 'unknown'
            if owned['outcome'] == 'pending':
                self.budget.finish(call_id, status, reason='native_call_failure')
            row = self.journal.state()['calls'][call_id]
            if row['status'] == 'pending':
                self.journal.append('call_finished', {'call_id': call_id, 'status': status,
                    'result': None, 'failure': _failure(error),
                    'latency_seconds': max(0.0, time.monotonic() - started)})
            if (self._integrity_failure or snapshot['locked'] or status == 'unknown'
                    or isinstance(error, StudyBudgetStop)):
                raise StudyBudgetStop('Study call stopped for source or accounting review') from None
            if provider == 'typesafe' and not isinstance(error, (TypesafeError, InterruptedError)):
                raise StudyBudgetStop('Unexpected native controller failure') from None
            raise

    def stage(self, kind, binding, produce):
        return self.journal.stage(kind, binding, produce)

    def _stage(self, kind, binding, produce):
        if self._inspection_only:
            raise StudyBudgetStop('Inspection-only study execution cannot execute or reference stages')
        self._guard()
        if self._active_stage is not None or not callable(produce):
            raise StudyBudgetStop('Nested or invalid native study stage')
        key = digest(binding)
        existing = self.journal.state()['stages'].get(key)
        if existing is not None:
            if existing['kind'] != kind or existing['binding'] != binding:
                raise StudyBudgetStop('Shared study stage binding differs')
            if existing['status'] == 'pending':
                raise StudyBudgetStop('Interrupted stage has no reusable native payload')
            if (existing['status'] == 'failed'
                    and existing['failure'].get('error_type') in {'StudyBudgetStop', 'InterruptedError', 'KeyboardInterrupt'}):
                raise StudyBudgetStop('The saved native stage requires source or accounting review')
            calls = {row['call_id']: row for row in self.budget.inspect()['calls']}
            for identifier in existing['call_ids']:
                call = calls[identifier]
                if call['outcome'] in {'pending', 'unknown'}:
                    raise StudyBudgetStop('Shared stage contains unknown or pending work')
                self.budget.reference(identifier, self.logical_arm_id, call['stage'], call['request_sha256'])
            if self.logical_arm_id not in existing['references']:
                self.journal.append('stage_reference', {'key': key, 'arm_id': self.logical_arm_id})
            self._guard()
            if existing['status'] == 'failed':
                raise SavedSharedFailure('The exact native stage previously failed; audit costs retained')
            return deepcopy(existing['payload'])
        self.journal.append('stage_started', {'key': key, 'kind': kind,
            'binding': deepcopy(binding), 'owner_arm': self.logical_arm_id})
        self._active_stage = key, kind
        try:
            payload = produce()
            self._guard()
            if not isinstance(payload, dict):
                raise StudyBudgetStop('Native study stage returned no full payload')
            self.journal.append('stage_finished', {'key': key, 'status': 'completed',
                'payload': deepcopy(payload), 'payload_sha256': digest(payload), 'failure': None})
            return deepcopy(payload)
        except BaseException as error:
            current = self.journal.state()['stages'][key]
            if current['status'] == 'pending':
                self.journal.append('stage_finished', {'key': key, 'status': 'failed',
                    'payload': None, 'payload_sha256': None, 'failure': _failure(error)})
            raise
        finally:
            self._active_stage = None

    def on_decision(self, record):
        if self._inspection_only:
            raise StudyBudgetStop('Inspection-only study execution cannot publish decisions')
        self._guard()
        state = self.journal.state()
        candidates = [row for row in state['decisions'].values()
                      if row['binding'] == record.get('binding') and row['owner_arm'] == self.logical_arm_id]
        if len(candidates) != 1:
            raise StudyBudgetStop('Controller decision has no unique owned call')
        decision = candidates[0]
        call = state['calls'][decision['call_id']]
        if (record.get('request_sha256') != call['request_sha256']
                or not call['receipts'] or record.get('receipt') != call['receipts'][-1]
                or call['status'] not in {'completed', 'failed'}):
            raise StudyBudgetStop('Controller decision differs from its closed native call')
        self.journal.append('decision_finished', {'key': decision['key'], 'record': deepcopy(record)})
        self._guard()

    def inspect(self):
        """Audit local bytes and read-only native ledgers; never construct clients."""
        state = self.journal.state()
        ledger = self.budget.inspect()
        owned = {row['call_id']: row for row in ledger['calls']}
        self._audit_native_inventory(ledger['calls'])
        expected = {key for key, row in owned.items() if row['arm_id'] in
                    {f'{self.unit_id}:rules', f'{self.unit_id}:jev'}}
        if expected != set(state['calls']):
            raise StudyBudgetStop('Study budget has orphan or lost unit call owners')
        for identifier, row in state['calls'].items():
            call = owned[identifier]
            if (any(row[key] != call[key] for key in ('arm_id', 'provider', 'stage', 'request_sha256'))
                    or row['request_sha256'] != digest(row['request'])
                    or row['status'] != call['outcome']):
                raise StudyBudgetStop('Execution call differs from joint budget state')
            receipt = row['receipts'][-1] if row['receipts'] else None
            if receipt != call['receipt']:
                raise StudyBudgetStop('Execution and joint native receipts differ')
            if receipt is not None:
                self._native_receipt(row['provider'], receipt)
            if row['status'] == 'completed' and call['outcome_sha256'] != digest(row['result']):
                raise StudyBudgetStop('Completed native response digest differs')
        for row in state['stages'].values():
            for arm in row['references']:
                for identifier in row['call_ids']:
                    call = owned[identifier]
                    if not any(ref['arm_id'] == arm and ref['stage'] == call['stage']
                               and ref['request_sha256'] == call['request_sha256'] for ref in call['references']):
                        raise StudyBudgetStop('Shared stage audit cost reference is missing')
        return {'schema': 'controller_study_execution_inspection_v1',
            'unit_id': self.unit_id, 'policy_sha256': self.policy['sha256'],
            'stages': deepcopy(list(state['stages'].values())),
            'calls': deepcopy(list(state['calls'].values())),
            'decisions': deepcopy(list(state['decisions'].values())),
            'stage_refs': [{'key': row['key'], 'kind': row['kind'],
                'owner_arm': row['owner_arm'], 'status': row['status'],
                'payload_sha256': row['payload_sha256'], 'call_ids': row['call_ids']}
                for row in state['stages'].values() if self.logical_arm_id in row['references']]}

    def _audit_native_inventory(self, calls):
        """Every native reservation must have one joint owner, across all units."""
        self.audit_native_inventory((self.groq_config, self.typesafe_config), calls)

    @staticmethod
    def audit_native_inventory(configs, calls):
        """Audit ledgers even when no unit execution journal remains."""
        for provider, config in zip(('groq', 'typesafe'), configs):
            expected = {row['receipt']['reservation_id'] for row in calls
                        if row['provider'] == provider and row['receipt'] is not None}
            path = _safe(config.ledger_file)
            if not path.exists():
                if expected:
                    raise StudyBudgetStop('Owned native provider ledger was removed')
                continue
            db = None
            try:
                db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                table = 'calls' if provider == 'groq' else 'typesafe_calls'
                actual = {row[0] for row in db.execute(f'SELECT id FROM {table}')}
                if actual != expected:
                    raise StudyBudgetStop('Native ledger has an orphan or lost reservation owner')
            except sqlite3.Error:
                raise StudyBudgetStop('Native provider inventory cannot be audited') from None
            finally:
                if db is not None:
                    db.close()

    @property
    def call_ids(self):
        return [row['call_id'] for row in self.budget.inspect()['calls']
                if any(ref['arm_id'] == self.logical_arm_id for ref in row['references'])]

    @property
    def owned_call_ids(self):
        return [row['call_id'] for row in self.budget.inspect()['calls'] if row['arm_id'] == self.logical_arm_id]

    @property
    def stage_refs(self):
        return self.inspect()['stage_refs']

    @property
    def decisions(self):
        return [row['record'] for row in self.inspect()['decisions'] if row['owner_arm'] == self.logical_arm_id]
